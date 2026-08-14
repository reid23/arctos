"""
TOML parsing and writing utilities for schedule import/export.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import tomli

from app.error_values import Err, Ok, Result
from app.exceptions import ArctosError, ValidationError


def parse_toml_schedule(content: str) -> Result[dict[str, Any], ArctosError]:
    """Parse and structurally validate a TOML schedule string.

    Expects a TOML document with:

    * ``event`` (str, optional, deprecated) — tournament URL slug. Accepted
      for backward compatibility with older exports; the importing route
      determines the target tournament.
    * ``variables`` (array of tables, optional) — tournament script
      variables (``name`` + ``expression``).
    * ``tags`` (array of tables, optional).
    * ``fields`` (array of tables, optional).
    * ``matches`` (array of tables, optional).

    Args:
        content: Raw TOML string.

    Returns:
        :class:`~app.error_values.Ok` wrapping a dict with keys
        ``"event"`` (str or ``None``), ``"variables"``, ``"tags"``,
        ``"fields"``, ``"matches"``; or :class:`~app.error_values.Err`
        wrapping a :class:`~app.exceptions.ValidationError` on parse /
        structure error.
    """
    try:
        data = tomli.loads(content)
    except Exception as e:
        return Err(ValidationError(f"Invalid TOML format: {str(e)}"))

    # Validate structure
    if not isinstance(data, dict):
        return Err(ValidationError("TOML root must be a table"))

    # Extract event (optional, legacy). Non-string values are ignored.
    event = data.get("event")
    if not isinstance(event, str) or not event:
        event = None

    # Extract script variables (optional, defaults to empty list)
    variables = data.get("variables", [])
    if not isinstance(variables, list):
        return Err(ValidationError("'variables' must be an array of tables"))

    # Extract tags (optional, defaults to empty list)
    tags = data.get("tags", [])
    if not isinstance(tags, list):
        return Err(ValidationError("'tags' must be an array of tables"))

    # Extract fields (optional, defaults to empty list)
    fields = data.get("fields", [])
    if not isinstance(fields, list):
        return Err(ValidationError("'fields' must be an array of tables"))

    # Extract matches (optional, defaults to empty list)
    matches = data.get("matches", [])
    if not isinstance(matches, list):
        return Err(ValidationError("'matches' must be an array of tables"))

    return Ok(
        {
            "event": event,
            "variables": variables,
            "tags": tags,
            "fields": fields,
            "matches": matches,
        }
    )


def write_toml_schedule(
    tags: list[dict[str, Any]],
    fields: list[dict[str, Any]],
    matches: list[dict[str, Any]],
    *,
    variables: list[dict[str, Any]] | None = None,
    metadata: dict[str, Any] | None = None,
) -> str:
    """Serialise a tournament schedule to a TOML string.

    Produces a human-readable TOML document suitable for download or
    re-import.  An optional metadata comment header is prepended when
    *metadata* is provided.  The document intentionally carries no ``event``
    key — the target tournament is determined by the importing route.

    Args:
        tags: List of tag dicts with ``id``, ``name``, and optional
            ``team`` (team ID) and ``expression`` (ASS expression).
        fields: List of field dicts with ``id``, ``name``, and ``camera``.
        matches: List of match attribute dicts.
        variables: Optional list of script-variable dicts with ``name`` and
            ``expression``. Written before the tags section (tag / match
            expressions may reference variables) and omitted when empty.
        metadata: Optional key-value pairs written as TOML comments at the
            top (e.g. ``{"export_date": "2024-06-01", "version": "1"}``)

    Returns:
        A TOML-formatted string.
    """
    lines = []

    # Add metadata comment header
    if metadata:
        lines.append("# Schedule Export")
        for key, value in metadata.items():
            lines.append(f"# {key}: {value}")
        lines.append("")

    # Script variables (before tags: tag expressions may reference them)
    if variables:
        lines.append("# Script variables")
        for variable in variables:
            lines.append("[[variables]]")
            if "name" in variable and variable["name"]:
                lines.append(f'name = "{_escape_toml_string(variable["name"])}"')
            if "expression" in variable and variable["expression"]:
                lines.append(f'expression = "{_escape_toml_string(variable["expression"])}"')
            lines.append("")

    # Tags
    if tags:
        lines.append("# Tags")
        for tag in tags:
            lines.append("[[tags]]")
            if "id" in tag and tag["id"] is not None:
                lines.append(f"id = {tag['id']}")
            if "name" in tag and tag["name"]:
                lines.append(f'name = "{_escape_toml_string(tag["name"])}"')
            if "team" in tag and tag["team"]:
                lines.append(f'team = "{_escape_toml_string(tag["team"])}"')
            if "expression" in tag and tag["expression"]:
                lines.append(f'expression = "{_escape_toml_string(tag["expression"])}"')
            lines.append("")

    # Fields
    if fields:
        lines.append("# Fields")
        for field in fields:
            lines.append("[[fields]]")
            if "id" in field and field["id"] is not None:
                lines.append(f"id = {field['id']}")
            if "name" in field and field["name"]:
                lines.append(f'name = "{_escape_toml_string(field["name"])}"')
            if "camera" in field and field["camera"]:
                lines.append(f'camera = "{_escape_toml_string(field["camera"])}"')
            lines.append("")

    # Matches
    if matches:
        lines.append("# Matches")
        for match in matches:
            lines.append("[[matches]]")

            # UUID
            if "uuid" in match and match["uuid"]:
                lines.append(f'uuid = "{match["uuid"]}"')

            # Name (required)
            if "name" in match:
                lines.append(f'name = "{_escape_toml_string(match["name"])}"')

            # Optional string fields - only include if non-empty
            # Note: team1, team2, refs are NOT exported - they are derived from _initial fields
            for field_name in [
                "team1_initial",
                "team2_initial",
                "refs_initial",
                "field",
                "skip_condition",
            ]:
                if field_name in match and match[field_name]:
                    lines.append(f'{field_name} = "{_escape_toml_string(str(match[field_name]))}"')

            # Datetimes: plan anchor first, then live estimate.
            for field_name in ("scheduled_start_time", "nominal_start_time"):
                if field_name in match and match[field_name]:
                    dt = match[field_name]
                    if isinstance(dt, datetime):
                        lines.append(f'{field_name} = "{dt.isoformat()}"')
                    else:
                        lines.append(f'{field_name} = "{dt}"')

            # Integer fields
            for field_name in ["nominal_length", "nsets", "stones_per_set"]:
                if field_name in match and match[field_name] is not None:
                    lines.append(f"{field_name} = {match[field_name]}")

            # String enum fields - only include if present
            for field_name in ["schedule_type", "set_type"]:
                if field_name in match and match[field_name]:
                    lines.append(f'{field_name} = "{match[field_name]}"')

            # Boolean - only include if True (False is default)
            if "ribbon" in match and match["ribbon"]:
                lines.append("ribbon = true")

            # Relationship references - only include if present
            for field_name in ["previous_match", "next_match"]:
                if field_name in match and match[field_name]:
                    lines.append(f'{field_name} = "{_escape_toml_string(match[field_name])}"')

            lines.append("")

    return "\n".join(lines)


def _escape_toml_string(s: str) -> str:
    """Escape special characters in TOML strings."""
    s = str(s)
    s = s.replace("\\", "\\\\")
    s = s.replace('"', '\\"')
    s = s.replace("\n", "\\n")
    s = s.replace("\t", "\\t")
    return s
