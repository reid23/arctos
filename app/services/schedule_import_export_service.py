"""TOML-based import / export of a tournament's schedule.

Round-trips tags, fields, and matches between SQLAlchemy rows and the
TOML format used to seed events from the CLI or a snapshot.  Pairs with
``app.serializers.match_schedule_serializer`` for the row -> dict
conversion and with ``app.utils.toml_helpers`` for the parse / write.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from app.error_values import Err, Ok, Result, allow_Q
from app.exceptions import ArctosError, ValidationError
from app.serializers.match_schedule_serializer import MatchScheduleSerializer
from app.utils.match_ref_resolution import (
    refs_string_to_tokens,
    resolve_refs_slots,
    resolve_team_column,
)
from app.utils.toml_helpers import parse_toml_schedule, write_toml_schedule

if TYPE_CHECKING:  # pragma: no cover
    pass


def _is_explicit_team_id(token: str) -> bool:
    """Return ``True`` if *token* is a direct team ID rather than a reference.

    Returns ``False`` for:

    * ``tag::<name>`` — tag references
    * Strings containing ``::winner`` or ``::loser`` — match references

    Args:
        token: A team slot string from the schedule.

    Returns:
        ``True`` when *token* is an explicit team ID.
    """
    if not token or not str(token).strip():
        return False
    t = str(token).strip()
    if t.lower().startswith("tag::"):
        return False
    if "::winner" in t.lower() or "::loser" in t.lower():
        return False
    return True


def _create_match_from_dict(match_dict: dict) -> "Match":
    """Persist a new ``Match`` from an import dict and populate its referee slots.

    ``refs`` and ``refs_initial`` are stripped from the constructor arguments
    because they live in the ``MatchReferee`` join table. After flushing the
    match (so its uuid is assigned), the referee rows are written via the
    join-table helper.
    """
    from app.domain.enums import MatchStatus, ScheduleType
    from app.services.dual_write import set_match_referees_from_csv
    from models import Match, db

    refs_csv = match_dict.get("refs") or ""
    refs_initial_csv = match_dict.get("refs_initial") or ""
    create_dict = {
        k: v for k, v in match_dict.items() if k not in ("previous_match", "next_match", "refs", "refs_initial")
    }
    match = Match(**create_dict)
    # Keep plan + live anchors aligned when the TOML only carried one of them.
    if match.scheduled_start_time is None and match.nominal_start_time is not None:
        match.scheduled_start_time = match.nominal_start_time
    if match.nominal_start_time is None and match.scheduled_start_time is not None:
        match.nominal_start_time = match.scheduled_start_time
    if not match.status:
        if match.schedule_type == ScheduleType.STATIC:
            match.status = MatchStatus.READY_TO_START
        else:
            match.status = MatchStatus.NOT_STARTED
    db.session.add(match)
    db.session.flush()  # Flush to assign uuid before writing the referee rows.
    if refs_csv or refs_initial_csv:
        set_match_referees_from_csv(match, refs_csv, refs_initial_csv)
    return match


@dataclass(frozen=True)
class ImportResult:
    """Result of a schedule import operation.

    Attributes:
        tags_created: Number of new :class:`~app.models.tournament.Tag`
            records created.
        tags_updated: Number of existing tag records updated.
        fields_created: Number of new :class:`~app.models.tournament.Field`
            records created.
        fields_updated: Number of existing field records updated.
        matches_created: Number of new :class:`~app.models.match.Match`
            records created.
        matches_updated: Number of existing match records updated.
        errors: List of human-readable error strings encountered during
            import.  Non-empty indicates a partial or failed import.
    """

    tags_created: int = 0
    tags_updated: int = 0
    fields_created: int = 0
    fields_updated: int = 0
    matches_created: int = 0
    matches_updated: int = 0
    errors: list[str] = None

    def __post_init__(self) -> None:
        """Initialise the mutable *errors* list on a frozen dataclass."""
        if self.errors is None:
            object.__setattr__(self, "errors", [])


@dataclass(frozen=True)
class ScheduleImportExportService:
    """Service for importing and exporting tournament schedules."""

    # ----------------------------
    # Internal helpers
    # ----------------------------

    @staticmethod
    def _validate_semantics(
        tags_data: list[dict],
        fields_data: list[dict],
        matches_data: list[dict],
    ) -> list[str]:
        """
        Perform higher-level semantic validation on the uploaded schedule.

        - Ensure match.field (if set) refers to a field listed in [[fields]].
        - Ensure team1_initial / team2_initial / refs_initial use only:
          - explicit team id: any non-empty string that is not a special
            'tag::' or match reference pattern
          - tag reference: 'tag::TAG_NAME' where TAG_NAME exists in [[tags]]
          - match reference: "[match name]::winner" or "[match name]::loser"
            where match name exists in the uploaded [[matches]] section.
        """
        errors: list[str] = []

        # Build lookup sets from uploaded data
        field_names: set[str] = set()
        for f in fields_data:
            name = str(f.get("name", "")).strip()
            if name:
                field_names.add(name)

        tag_names: set[str] = set()
        for t in tags_data:
            name = str(t.get("name", "")).strip()
            if name:
                tag_names.add(name)

        match_names: set[str] = set()
        for m in matches_data:
            name = str(m.get("name", "")).strip()
            if name:
                match_names.add(name)

        def _validate_initial_token(token: str, context: str) -> None:
            """
            Validate a single initial token.

            Allowed forms:
            - explicit team id: any non-empty string that is not a special
              'tag::' or match reference pattern
            - tag reference: 'tag::TAG_NAME' where TAG_NAME exists in tags_data
            - match reference: '[match name]::winner' or '[match name]::loser'
              where match name exists in uploaded matches.
            """
            tok = (token or "").strip()
            if not tok:
                return

            # Tag reference: tag::TAG_NAME
            if tok.lower().startswith("tag::"):
                tag_name = tok[5:].strip()
                if not tag_name:
                    errors.append(f"{context}: missing tag name in reference '{tok}'")
                    return
                if tag_name not in tag_names:
                    errors.append(f"{context}: referenced tag '{tag_name}' not found in [[tags]] section")
                return

            base, sep, suffix = tok.partition("::")
            if not sep:
                # Plain explicit team id
                # probably should check that there's a team with this name
                # registered for the tournament,
                # but in normal match creation there's no such constraint
                # so we'll fail silently for now :thumbsup_all: lmao
                # (since maybe the team just hasn't registered yet)
                return

            base = base.strip()
            suffix = suffix.strip().lower()

            if suffix not in ("winner", "loser"):
                errors.append(
                    f"{context}: invalid reference suffix '{suffix}' in '{tok}' (must be 'winner' or 'loser')"
                )
                return

            if not base:
                errors.append(f"{context}: missing match name in reference '{tok}'")
                return

            if base not in match_names:
                errors.append(f"{context}: referenced match '{base}' not found in uploaded matches")

        # Validate each match entry
        for m in matches_data:
            match_name = str(m.get("name", "")).strip() or "<unnamed match>"

            # 1) field must exist in [[fields]] if provided
            field_val = str(m.get("field", "")).strip()
            if field_val and field_val not in field_names:
                errors.append(f"Match '{match_name}': field '{field_val}' not found in [[fields]] section")

            # 2) team1_initial / team2_initial
            t1_init = str(m.get("team1_initial", "")).strip()
            if t1_init:
                _validate_initial_token(t1_init, f"Match '{match_name}' team1_initial")

            t2_init = str(m.get("team2_initial", "")).strip()
            if t2_init:
                _validate_initial_token(t2_init, f"Match '{match_name}' team2_initial")

            # 3) refs_initial: comma-separated list of tokens
            refs_init = str(m.get("refs_initial", "")).strip()
            if refs_init:
                for part in refs_init.split(","):
                    part_tok = part.strip()
                    if not part_tok:
                        continue
                    _validate_initial_token(part_tok, f"Match '{match_name}' refs_initial")

        return errors

    @staticmethod
    def _validate_teams_registered(
        tournament_url: str,
        tags_data: list[dict],
        matches_data: list[dict],
    ) -> list[str]:
        """
        Ensure every team referenced by ID (in tags or matches) is registered for the tournament.
        Returns a list of error messages; empty if all referenced teams are registered.
        """
        from models import Tournament
        from app.services.registration_resolver import team_registrations_for_tournament

        # Collect all team IDs referenced by ID (not tag:: or match::winner/loser)
        team_ids: set[str] = set()

        for tag in tags_data:
            team_val = str(tag.get("team", "")).strip()
            if team_val:
                team_ids.add(team_val)

        for m in matches_data:
            for field_name in ("team1_initial", "team2_initial"):
                tok = str(m.get(field_name, "")).strip()
                if tok and _is_explicit_team_id(tok):
                    team_ids.add(tok)
            refs_raw = str(m.get("refs_initial", "")).strip()
            if refs_raw:
                for part in refs_raw.split(","):
                    tok = part.strip()
                    if tok and _is_explicit_team_id(tok):
                        team_ids.add(tok)

        if not team_ids:
            return []

        tournament = Tournament.query.filter_by(url=tournament_url).first()
        if tournament is None:
            return [f"Tournament '{tournament_url}' not found"]

        registered_team_ids: set[str] = {
            reg.team
            for reg in team_registrations_for_tournament(
                tournament,
                exclude_cancelled=True,
            )
            if reg.team
        }

        unregistered = team_ids - registered_team_ids
        if not unregistered:
            return []

        errors: list[str] = []
        for tid in sorted(unregistered):
            errors.append(f"Team '{tid}' is not registered for this tournament.")
        return errors

    @staticmethod
    @allow_Q
    def export_schedule(tournament_url: str) -> Result[str, ArctosError]:
        """
        Export schedule (tags, fields, matches) to TOML string.

        Args:
            tournament_url: Tournament to export

        Returns:
            Result containing TOML string
        """
        from models import Field, Match, Tag

        # Verify tournament exists
        from app.services._common import get_tournament_or_err

        tournament = get_tournament_or_err(tournament_url).Q()

        # Fetch all tags, fields, and matches
        tags = Tag.query.filter_by(event=tournament_url).all()
        fields = Field.query.filter_by(event=tournament_url).all()
        matches = Match.query.filter_by(event=tournament_url).order_by(Match.nominal_start_time).all()

        # Serialize to dicts
        tag_dicts = [MatchScheduleSerializer.tag_to_dict(tag) for tag in tags]
        field_dicts = [MatchScheduleSerializer.field_to_dict(field) for field in fields]
        match_dicts = [MatchScheduleSerializer.match_to_dict(match) for match in matches]

        # Generate TOML
        metadata = {
            "exported_from": tournament_url,
            "export_date": datetime.now(timezone.utc).isoformat(),
            "tags_count": len(tag_dicts),
            "fields_count": len(field_dicts),
            "matches_count": len(match_dicts),
        }

        toml_content = write_toml_schedule(
            event=tournament_url,
            tags=tag_dicts,
            fields=field_dicts,
            matches=match_dicts,
            metadata=metadata,
        )

        return Ok(toml_content)

    @staticmethod
    @allow_Q
    def import_schedule(
        tournament_url: str,
        toml_content: str,
    ) -> Result[ImportResult, ArctosError]:
        """
        Import schedule from TOML string.

        Handles both same-tournament (update) and different-tournament (create) scenarios.
        All validation is performed before any database changes. If any error occurs during
        import, the transaction is rolled back.

        Args:
            tournament_url: Target tournament URL
            toml_content: TOML schedule content

        Returns:
            Result containing ImportResult with counts and errors
        """
        from models import Field, Match, Tag, db

        # Parse TOML
        parsed = parse_toml_schedule(toml_content).Q()
        source_event = parsed["event"]
        tags_data = parsed["tags"]
        fields_data = parsed["fields"]
        matches_data = parsed["matches"]

        # Verify target tournament exists
        from app.services._common import get_tournament_or_err

        tournament = get_tournament_or_err(tournament_url).Q()

        is_same_tournament = source_event == tournament_url

        tags_created = 0
        tags_updated = 0
        fields_created = 0
        fields_updated = 0
        matches_created = 0
        matches_updated = 0
        errors: list[str] = []

        # Perform all validation before making any database changes
        # 1. High-level semantic validation
        semantic_errors = ScheduleImportExportService._validate_semantics(tags_data, fields_data, matches_data)
        errors.extend(semantic_errors)

        # 2. Validate all tags
        for tag_data in tags_data:
            res = MatchScheduleSerializer.tag_from_dict(tag_data, tournament_url)
            if isinstance(res, Err):
                errors.append(f"Tag validation error: {res.value.message}")

        # 3. Validate all fields
        for field_data in fields_data:
            res = MatchScheduleSerializer.field_from_dict(field_data, tournament_url)
            if isinstance(res, Err):
                errors.append(f"Field validation error: {res.value.message}")

        # 4. Validate all matches
        for match_data in matches_data:
            res = MatchScheduleSerializer.match_from_dict(match_data, tournament_url)
            if isinstance(res, Err):
                errors.append(f"Match validation error: {res.value.message}")

        # 5. Ensure all teams referenced by ID (tags and matches) are registered
        errors.extend(ScheduleImportExportService._validate_teams_registered(tournament_url, tags_data, matches_data))

        # If any validation errors, abort before making any changes
        if errors:
            error_count = len(errors)
            if error_count == 1:
                error_message = f"Validation failed: {errors[0]}"
            else:
                # Format multiple errors as a bulleted list
                error_list = "\n".join(f"• {err}" for err in errors)
                error_message = f"Validation failed with {error_count} errors:\n{error_list}"
            return Err(ValidationError(error_message))

        # All validation passed - proceed with import
        # Single commit at end keeps tags/fields/matches and deletions atomic (partial import
        # on failure would leave inconsistent schedule vs file). SQLite WAL reduces lock contention.
        # Wrap in transaction so any error rolls back all changes
        try:
            # Keep track of which objects are present in the uploaded file for this tournament.
            # Anything NOT in these sets will be deleted at the end of a successful import.
            kept_tag_names: set[str] = set()
            kept_field_names: set[str] = set()
            kept_match_uuids: set[str] = set()

            # Build UUID mapping for matches (old_uuid -> new_uuid for different tournament)
            match_uuid_map: dict[str, str] = {}  # old_uuid -> new_uuid
            match_name_to_uuid: dict[str, str] = {}  # name -> uuid (for resolving relationships)

            # Pre-build UUID map for different tournament
            if not is_same_tournament:
                for match_data in matches_data:
                    old_uuid = match_data.get("uuid", "")
                    if old_uuid:
                        new_uuid = str(uuid.uuid4())
                        match_uuid_map[old_uuid] = new_uuid

            # Import tags
            for tag_data in tags_data:
                tag_res = MatchScheduleSerializer.tag_from_dict(tag_data, tournament_url).Q()
                tag_dict = tag_res

                # Track by name; IDs may differ across tournaments and inserts.
                if "name" in tag_dict and tag_dict["name"]:
                    kept_tag_names.add(tag_dict["name"])

                if is_same_tournament and "id" in tag_dict:
                    # Same tournament: update by ID
                    tag = Tag.query.filter_by(id=tag_dict["id"], event=tournament_url).first()
                    if tag:
                        tag.name = tag_dict["name"]
                        tag.team = tag_dict.get("team")
                        tags_updated += 1
                    else:
                        # ID doesn't exist, create new (don't include id in creation)
                        create_dict = {k: v for k, v in tag_dict.items() if k != "id"}
                        tag = Tag(**create_dict)
                        db.session.add(tag)
                        tags_created += 1
                else:
                    # Different tournament: always create new (don't include id)
                    create_dict = {k: v for k, v in tag_dict.items() if k != "id"}
                    tag = Tag(**create_dict)
                    db.session.add(tag)
                    tags_created += 1

            # Import fields
            for field_data in fields_data:
                field_res = MatchScheduleSerializer.field_from_dict(field_data, tournament_url).Q()
                field_dict = field_res

                # Track by name; IDs may differ across tournaments and inserts.
                if "name" in field_dict and field_dict["name"]:
                    kept_field_names.add(field_dict["name"])

                if is_same_tournament and "id" in field_dict:
                    # Same tournament: update by ID
                    field = Field.query.filter_by(id=field_dict["id"], event=tournament_url).first()
                    if field:
                        field.name = field_dict["name"]
                        field.camera = field_dict["camera"]
                        fields_updated += 1
                    else:
                        # ID doesn't exist, create new (don't include id in creation)
                        create_dict = {k: v for k, v in field_dict.items() if k != "id"}
                        field = Field(**create_dict)
                        db.session.add(field)
                        fields_created += 1
                else:
                    # Different tournament: always create new (don't include id)
                    create_dict = {k: v for k, v in field_dict.items() if k != "id"}
                    field = Field(**create_dict)
                    db.session.add(field)
                    fields_created += 1

            db.session.flush()  # Flush to get IDs for fields

            # Import matches - first pass: create/update without relationships
            # Build match_name_to_uuid and match_name_field_to_uuid mappings as we go
            match_name_field_to_uuid: dict[tuple[str, str], str] = {}
            for match_data in matches_data:
                # Prepare match data with new UUID if different tournament
                old_uuid = match_data.get("uuid", "")
                if not is_same_tournament and old_uuid:
                    # Use pre-generated UUID from map
                    new_uuid = match_uuid_map.get(old_uuid, str(uuid.uuid4()))
                    match_data = {**match_data, "uuid": new_uuid}

                # First pass: create/update without relationships (match_name_to_uuid not yet complete)
                match_res = MatchScheduleSerializer.match_from_dict(
                    match_data,
                    tournament_url,
                    match_name_to_uuid=None,  # Will resolve in second pass
                    match_name_field_to_uuid=None,  # Will resolve in second pass
                ).Q()
                match_dict = match_res

                match_name = match_dict["name"]

                if is_same_tournament and "uuid" in match_dict:
                    # Same tournament: update by UUID
                    match = Match.query.filter_by(uuid=match_dict["uuid"], event=tournament_url).first()
                    if match:
                        from app.services.dual_write import (
                            clear_match_referees,
                            get_match_refs_initial_csv,
                            set_match_referees_from_csv,
                        )

                        # Check if refs_initial changed - if so, repopulate slots
                        old_refs_initial = get_match_refs_initial_csv(match)
                        new_refs_initial = match_dict.get("refs_initial") or ""
                        refs_initial_changed = old_refs_initial != new_refs_initial

                        # Check if team1_initial or team2_initial changed
                        old_team1_initial = match.team1_initial or ""
                        new_team1_initial = match_dict.get("team1_initial") or ""
                        team1_initial_changed = old_team1_initial != new_team1_initial

                        old_team2_initial = match.team2_initial or ""
                        new_team2_initial = match_dict.get("team2_initial") or ""
                        team2_initial_changed = old_team2_initial != new_team2_initial

                        # Update model columns (excluding relationships, refs/refs_initial which live
                        # in the join table, and event/uuid which identify the row).
                        for key, value in match_dict.items():
                            if key not in (
                                "uuid",
                                "event",
                                "previous_match",
                                "next_match",
                                "refs",
                                "refs_initial",
                            ):
                                setattr(match, key, value)

                        # Align plan/live anchors when the file only carried one of them.
                        if match.scheduled_start_time is None and match.nominal_start_time is not None:
                            match.scheduled_start_time = match.nominal_start_time
                        if match.nominal_start_time is None and match.scheduled_start_time is not None:
                            match.nominal_start_time = match.scheduled_start_time

                        # If _initial fields changed, refresh the corresponding resolved values.
                        if team1_initial_changed:
                            match.team1 = resolve_team_column(new_team1_initial, tournament_url)
                        if team2_initial_changed:
                            match.team2 = resolve_team_column(new_team2_initial, tournament_url)
                        if refs_initial_changed:
                            if new_refs_initial:
                                r_csv, _ = resolve_refs_slots(
                                    refs_string_to_tokens(new_refs_initial),
                                    tournament_url,
                                )
                                set_match_referees_from_csv(match, r_csv, new_refs_initial)
                            else:
                                clear_match_referees(match)
                        match_name_to_uuid[match_name] = match.uuid
                        # Also add to field-based mapping for duplicate resolution (use actual match field)
                        match_field = match.field or ""
                        if match_field:
                            match_name_field_to_uuid[(match_name, match_field)] = match.uuid
                        kept_match_uuids.add(match.uuid)
                        matches_updated += 1
                    else:
                        match = _create_match_from_dict(match_dict)
                        match_name_to_uuid[match_name] = match.uuid
                        match_field = match.field or ""
                        if match_field:
                            match_name_field_to_uuid[(match_name, match_field)] = match.uuid
                        kept_match_uuids.add(match.uuid)
                        matches_created += 1
                else:
                    # Different tournament: always create new
                    match = _create_match_from_dict(match_dict)
                    match_name_to_uuid[match_name] = match.uuid
                    match_field = match.field or ""
                    if match_field:
                        match_name_field_to_uuid[(match_name, match_field)] = match.uuid
                    kept_match_uuids.add(match.uuid)
                    matches_created += 1

            db.session.flush()  # Flush to get UUIDs for matches

            # Second pass: resolve relationships (previous_match/next_match) using match names
            # When duplicates exist, resolve to match on same field
            for match_data in matches_data:
                old_uuid = match_data.get("uuid", "")
                match_name = match_data.get("name", "")
                match_field_from_data = str(match_data.get("field", "")).strip() or ""

                # Find the match we just created/updated
                # Use field to disambiguate if duplicates exist
                if is_same_tournament and old_uuid:
                    match = Match.query.filter_by(uuid=old_uuid, event=tournament_url).first()
                else:
                    # Different tournament: use new UUID from map
                    if old_uuid and old_uuid in match_uuid_map:
                        new_uuid = match_uuid_map[old_uuid]
                        match = Match.query.filter_by(uuid=new_uuid, event=tournament_url).first()
                    else:
                        # Try to find by name and field first (for duplicates)
                        if match_field_from_data:
                            match = Match.query.filter_by(
                                name=match_name,
                                event=tournament_url,
                                field=match_field_from_data,
                            ).first()
                        else:
                            match = None

                        # Fall back to name-only if not found
                        if not match:
                            match = Match.query.filter_by(name=match_name, event=tournament_url).first()

                if not match:
                    continue

                match_field = match.field or ""

                # Helper to resolve match name to UUID, preferring same-field matches when duplicates exist
                def resolve_match_name(ref_name: str, current_field: str) -> str | None:
                    """Resolve match name to UUID, using field-based resolution if duplicates exist."""
                    if not ref_name:
                        return None

                    # If we have field info and there's a match with this name on the same field, use it
                    if current_field and (ref_name, current_field) in match_name_field_to_uuid:
                        return match_name_field_to_uuid[(ref_name, current_field)]

                    # Fall back to name-only mapping
                    return match_name_to_uuid.get(ref_name)

                # Resolve previous_match by name (preferring same field)
                if "previous_match" in match_data and match_data["previous_match"]:
                    prev_match_name = str(match_data["previous_match"]).strip()
                    match.previous_match = resolve_match_name(prev_match_name, match_field)

                # Resolve next_match by name (preferring same field)
                if "next_match" in match_data and match_data["next_match"]:
                    next_match_name = str(match_data["next_match"]).strip()
                    match.next_match = resolve_match_name(next_match_name, match_field)

            # Delete any tags, fields, or matches for this tournament that are
            # NOT present in the uploaded file. This makes the uploaded schedule
            # authoritative for these three tables.
            # Tags: match by name within this tournament.
            if kept_tag_names:
                Tag.query.filter_by(event=tournament_url).filter(~Tag.name.in_(kept_tag_names)).delete(
                    synchronize_session=False
                )
            else:
                # No tags in file -> delete all tags for this event
                Tag.query.filter_by(event=tournament_url).delete(synchronize_session=False)

            # Fields: match by name within this tournament.
            if kept_field_names:
                Field.query.filter_by(event=tournament_url).filter(~Field.name.in_(kept_field_names)).delete(
                    synchronize_session=False
                )
            else:
                # No fields in file -> delete all fields for this event
                Field.query.filter_by(event=tournament_url).delete(synchronize_session=False)

            # Matches: match by UUID within this tournament.
            if kept_match_uuids:
                Match.query.filter_by(event=tournament_url).filter(~Match.uuid.in_(kept_match_uuids)).delete(
                    synchronize_session=False
                )
            else:
                # No matches in file -> delete all matches for this event
                Match.query.filter_by(event=tournament_url).delete(synchronize_session=False)

            db.session.commit()

            result = ImportResult(
                tags_created=tags_created,
                tags_updated=tags_updated,
                fields_created=fields_created,
                fields_updated=fields_updated,
                matches_created=matches_created,
                matches_updated=matches_updated,
                errors=errors,
            )

            return Ok(result)

        except Exception as e:
            db.session.rollback()
            return Err(ValidationError(f"Import failed: {str(e)}"))
