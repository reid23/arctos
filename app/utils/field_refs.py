"""Utilities for dual-read/dual-write field references during migration."""

from __future__ import annotations


def resolve_match_field_obj(tournament_url: str, match):
    """Return the field row for a match using ``field_id`` first, then legacy name."""
    from models import Field

    if getattr(match, "field_id", None):
        field_obj = Field.query.filter_by(
            event=tournament_url, id=match.field_id
        ).first()
        if field_obj:
            return field_obj
    if getattr(match, "field", None):
        return Field.query.filter_by(event=tournament_url, name=match.field).first()
    return None


def sync_match_field_ref(tournament_url: str, match) -> None:
    """Populate missing match ``field``/``field_id`` using existing reference."""
    field_obj = resolve_match_field_obj(tournament_url, match)
    if not field_obj:
        return
    match.field_id = field_obj.id
    match.field = field_obj.name


def set_match_field_from_name(
    tournament_url: str, match, field_name: str | None
) -> None:
    """Set legacy and FK field refs together from a field name."""
    from models import Field

    name = (field_name or "").strip()
    match.field = name or None
    if not name:
        match.field_id = None
        return
    field_obj = Field.query.filter_by(event=tournament_url, name=name).first()
    match.field_id = field_obj.id if field_obj else None
