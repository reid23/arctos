"""Serialise tags, fields, and matches to TOML-compatible dicts (and back).

Used by ``app.services.schedule_import_export_service`` for the
round-trip between SQLAlchemy rows and the TOML schedule format.
Empty / null keys are omitted so the resulting TOML stays tidy.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.error_values import Err, Ok, Result
from app.exceptions import ValidationError
from app.utils.name_validation import match_name_char_error


@dataclass(frozen=True)
class MatchScheduleSerializer:
    """Serialize/deserialize schedule data (tags, fields, matches) to/from TOML-compatible dicts."""

    @staticmethod
    def tag_to_dict(tag) -> dict[str, Any]:
        """Serialise a :class:`~app.models.tournament.Tag` to a TOML-compatible dict.

        Omits keys whose values are empty or ``None``.

        Args:
            tag: The :class:`~app.models.tournament.Tag` ORM instance.

        Returns:
            Dict with up to three keys: ``id``, ``name``, ``team``.
        """
        result = {}
        if tag.id is not None:
            result["id"] = tag.id
        if tag.name:
            result["name"] = tag.name
        if getattr(tag, "team", None):
            result["team"] = tag.team
        return result

    @staticmethod
    def field_to_dict(field) -> dict[str, Any]:
        """Serialise a :class:`~app.models.tournament.Field` to a TOML-compatible dict.

        Omits keys whose values are empty or ``None``.

        Args:
            field: The :class:`~app.models.tournament.Field` ORM instance.

        Returns:
            Dict with up to two keys: ``id``, ``name``.
        """
        result = {}
        if field.id is not None:
            result["id"] = field.id
        if field.name:
            result["name"] = field.name
        return result

    @staticmethod
    def match_to_dict(match) -> dict[str, Any]:
        """Serialise a :class:`~app.models.match.Match` to a TOML-compatible dict.

        Prefers ``_initial`` fields (which carry the user's symbolic tokens
        such as ``tag::PoolA`` or ``MatchName::winner``) over the resolved
        ``team1`` / ``team2`` / ``refs`` columns.  Omits keys whose values are
        empty or ``None``.

        Args:
            match: The :class:`~app.models.match.Match` ORM instance.

        Returns:
            Dict with match attributes suitable for TOML serialisation.
        """
        result = {}

        # Always include uuid and name
        if match.uuid:
            result["uuid"] = match.uuid
        if match.name:
            result["name"] = match.name

        # Optional string fields - only include if non-empty.
        # Prefer _initial (user tokens: tag::X, Match::winner, or explicit id); fall back to
        # team1/team2/refs when set directly so export still includes team info.
        from app.services.dual_write import get_match_refs_csv, get_match_refs_initial_csv

        if match.team1_initial:
            result["team1_initial"] = match.team1_initial
        elif getattr(match, "team1", False):
            result["team1_initial"] = match.team1
        if match.team2_initial:
            result["team2_initial"] = match.team2_initial
        elif getattr(match, "team2", False):
            result["team2_initial"] = match.team2
        refs_initial_csv = get_match_refs_initial_csv(match)
        if refs_initial_csv:
            result["refs_initial"] = refs_initial_csv
        else:
            refs_csv = get_match_refs_csv(match)
            if refs_csv and any(s for s in refs_csv.split(",")):
                result["refs_initial"] = refs_csv
        if match.field:
            result["field"] = match.field
        if match.skip_condition:
            result["skip_condition"] = match.skip_condition

        # Convert UUIDs to match names for relationships
        if match.previous_match:
            from models import Match as MatchModel

            prev_match = MatchModel.query.filter_by(uuid=match.previous_match, event=match.event).first()
            if prev_match:
                result["previous_match"] = prev_match.name

        if match.next_match:
            from models import Match as MatchModel

            next_match = MatchModel.query.filter_by(uuid=match.next_match, event=match.event).first()
            if next_match:
                result["next_match"] = next_match.name

        # Numeric fields - include if not None
        if match.nominal_length is not None:
            result["nominal_length"] = match.nominal_length
        if match.nsets is not None:
            result["nsets"] = match.nsets
        if match.stones_per_set is not None:
            result["stones_per_set"] = match.stones_per_set

        # Enum fields - include if present (they're always valid values, not "empty")
        if match.schedule_type:
            result["schedule_type"] = match.schedule_type
        if match.set_type:
            result["set_type"] = match.set_type

        # Boolean - only include if True (False is default)
        if match.ribbon:
            result["ribbon"] = True

        # Datetime - only include if present
        if match.nominal_start_time:
            result["nominal_start_time"] = match.nominal_start_time

        return result

    @staticmethod
    def tag_from_dict(data: dict[str, Any], tournament_url: str) -> Result[dict[str, Any], ValidationError]:
        """Convert TOML dict to Tag creation data."""
        if "name" not in data:
            return Err(ValidationError("Tag missing required field: name"))

        name = str(data["name"]).strip()
        if not name:
            return Err(ValidationError("Tag name cannot be empty"))

        result = {
            "event": tournament_url,
            "name": name,
            "team": str(data.get("team", "")).strip() or None,
        }

        # Include id if present (for same-tournament updates)
        if "id" in data:
            try:
                result["id"] = int(data["id"])
            except (ValueError, TypeError):
                return Err(ValidationError(f"Invalid tag id: {data['id']}"))

        return Ok(result)

    @staticmethod
    def field_from_dict(data: dict[str, Any], tournament_url: str) -> Result[dict[str, Any], ValidationError]:
        """Convert TOML dict to Field creation data."""
        if "name" not in data:
            return Err(ValidationError("Field missing required field: name"))

        name = str(data["name"]).strip()
        if not name:
            return Err(ValidationError("Field name cannot be empty"))

        result = {
            "event": tournament_url,
            "name": name,
        }

        # Include id if present (for same-tournament updates)
        if "id" in data:
            try:
                result["id"] = int(data["id"])
            except (ValueError, TypeError):
                return Err(ValidationError(f"Invalid field id: {data['id']}"))

        return Ok(result)

    @staticmethod
    def match_from_dict(
        data: dict[str, Any],
        tournament_url: str,
        *,
        match_name_to_uuid: dict[str, str] | None = None,
        match_name_field_to_uuid: dict[tuple[str, str], str] | None = None,
    ) -> Result[dict[str, Any], ValidationError]:
        """
        Convert TOML dict to Match creation data.

        Args:
            data: TOML match dict
            tournament_url: Target tournament URL
            match_name_to_uuid: Optional mapping from match names to UUIDs (for resolving previous_match/next_match)
            match_name_field_to_uuid: Optional mapping from (name, field) tuples to UUIDs (for field-based resolution when duplicates exist)

        Returns:
            Result containing dict with match creation data
        """
        if "name" not in data:
            return Err(ValidationError("Match missing required field: name"))

        name = str(data["name"]).strip()
        if not name:
            return Err(ValidationError("Match name cannot be empty"))

        mn_err = match_name_char_error(name)
        if mn_err:
            return Err(ValidationError(mn_err))

        from app.utils.match_ref_resolution import (
            refs_string_to_tokens,
            resolve_refs_slots,
            resolve_team_column,
        )

        # Get _initial field values
        team1_initial = str(data.get("team1_initial", "")).strip() or None
        team2_initial = str(data.get("team2_initial", "")).strip() or None
        refs_initial = str(data.get("refs_initial", "")).strip() or None

        team1 = resolve_team_column(team1_initial, tournament_url)
        team2 = resolve_team_column(team2_initial, tournament_url)

        refs = None
        if refs_initial:
            r_csv, _ = resolve_refs_slots(refs_string_to_tokens(refs_initial), tournament_url)
            if r_csv and any(s.strip() for s in r_csv.split(",")):
                refs = r_csv

        schedule_type = str(data.get("schedule_type", "STATIC")).strip() or "STATIC"
        skip_condition_raw = str(data.get("skip_condition", "")).strip() or None
        # Only accept skip_condition for SAFE and FAST; ignore for other types
        skip_condition = skip_condition_raw if schedule_type in ("SAFE", "FAST") else None

        result = {
            "event": tournament_url,
            "name": name,
            "team1": team1,
            "team2": team2,
            "team1_initial": team1_initial,
            "team2_initial": team2_initial,
            "refs": refs,
            "refs_initial": refs_initial,
            "field": str(data.get("field", "")).strip() or None,
            "nominal_length": data.get("nominal_length"),
            "schedule_type": schedule_type,
            "set_type": str(data.get("set_type", "SETS")).strip() or "SETS",
            "ribbon": bool(data.get("ribbon", False)),
            "nsets": data.get("nsets"),
            "stones_per_set": data.get("stones_per_set"),
            "skip_condition": skip_condition,
            "previous_match": None,
            "next_match": None,
        }

        # Handle datetime
        if "nominal_start_time" in data and data["nominal_start_time"]:
            dt_value = data["nominal_start_time"]
            if isinstance(dt_value, datetime):
                result["nominal_start_time"] = dt_value
            elif isinstance(dt_value, str):
                try:
                    # Try parsing ISO format
                    result["nominal_start_time"] = datetime.fromisoformat(dt_value.replace("Z", "+00:00"))
                except ValueError:
                    return Err(ValidationError(f"Invalid datetime format: {dt_value}"))
            else:
                return Err(ValidationError(f"Invalid nominal_start_time type: {type(dt_value)}"))

        # Helper function to resolve match name to UUID
        def resolve_match_name(match_name: str, current_field: str | None) -> str | None:
            """Resolve a match name to UUID, using field-based resolution if duplicates exist."""
            if not match_name:
                return None

            # First try field-based resolution if mapping provided
            if match_name_field_to_uuid and current_field:
                key = (match_name, current_field)
                if key in match_name_field_to_uuid:
                    return match_name_field_to_uuid[key]

            # Fall back to name-only mapping
            if match_name_to_uuid:
                return match_name_to_uuid.get(match_name)

            return None

        # Resolve relationship references using match name mapping
        # previous_match and next_match are now match names, not UUIDs
        # When duplicates exist, resolve to match on same field
        current_field = result["field"]
        if match_name_to_uuid or match_name_field_to_uuid:
            if "previous_match" in data and data["previous_match"]:
                prev_match_name = str(data["previous_match"]).strip()
                if prev_match_name:
                    result["previous_match"] = resolve_match_name(prev_match_name, current_field)

            if "next_match" in data and data["next_match"]:
                next_match_name = str(data["next_match"]).strip()
                if next_match_name:
                    result["next_match"] = resolve_match_name(next_match_name, current_field)
        else:
            # No mapping provided - try to resolve by name from database
            # This handles same-tournament imports where matches already exist
            # If duplicates exist, prefer match on same field
            from models import Match as MatchModel

            if "previous_match" in data and data["previous_match"]:
                prev_match_name = str(data["previous_match"]).strip()
                if prev_match_name:
                    # Try to find by name and field first (for duplicates)
                    if current_field:
                        prev_match = MatchModel.query.filter_by(
                            event=tournament_url,
                            name=prev_match_name,
                            field=current_field,
                        ).first()
                    else:
                        prev_match = None

                    # Fall back to name-only if not found
                    if not prev_match:
                        prev_match = MatchModel.query.filter_by(event=tournament_url, name=prev_match_name).first()

                    if prev_match:
                        result["previous_match"] = prev_match.uuid

            if "next_match" in data and data["next_match"]:
                next_match_name = str(data["next_match"]).strip()
                if next_match_name:
                    # Try to find by name and field first (for duplicates)
                    if current_field:
                        next_match = MatchModel.query.filter_by(
                            event=tournament_url,
                            name=next_match_name,
                            field=current_field,
                        ).first()
                    else:
                        next_match = None

                    # Fall back to name-only if not found
                    if not next_match:
                        next_match = MatchModel.query.filter_by(event=tournament_url, name=next_match_name).first()

                    if next_match:
                        result["next_match"] = next_match.uuid

        # Include uuid if present (for same-tournament updates)
        if "uuid" in data and data["uuid"]:
            result["uuid"] = str(data["uuid"]).strip()

        return Ok(result)
