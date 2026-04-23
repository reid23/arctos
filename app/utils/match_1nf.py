"""Helpers for normalized Match multi-value attributes (1NF bridge)."""

from __future__ import annotations

import json


def _split_csv_slots(raw: str | None) -> list[str]:
    if raw is None:
        return []
    if not str(raw).strip():
        return []
    return [part.strip() for part in str(raw).split(",")]


def _join_csv_slots(parts: list[str]) -> str | None:
    if not parts:
        return None
    if not any((p or "").strip() for p in parts):
        return None
    return ",".join(parts)


def replace_match_ref_slots(
    match, refs_csv: str | None, refs_initial_csv: str | None
) -> None:
    """Replace normalized ref slots and keep legacy columns synchronized."""
    from models import MatchRefSlot

    refs_parts = _split_csv_slots(refs_csv)
    initial_parts = _split_csv_slots(refs_initial_csv)
    n = max(len(refs_parts), len(initial_parts))
    refs_parts += [""] * (n - len(refs_parts))
    initial_parts += [""] * (n - len(initial_parts))

    MatchRefSlot.query.filter_by(match_uuid=match.uuid).delete(
        synchronize_session=False
    )
    for idx in range(n):
        db_ref = refs_parts[idx] or None
        init_ref = initial_parts[idx] or None
        if db_ref is None and init_ref is None:
            continue
        slot = MatchRefSlot(
            match_uuid=match.uuid,
            slot_index=idx,
            resolved_team_id=db_ref,
            initial_token=init_ref,
        )
        from models import db

        db.session.add(slot)

    match.refs = _join_csv_slots(refs_parts)
    match.refs_initial = _join_csv_slots(initial_parts)


def read_match_ref_slots(match) -> tuple[str | None, str | None]:
    """Read refs from normalized slots, falling back to legacy columns."""
    from models import MatchRefSlot

    slots = (
        MatchRefSlot.query.filter_by(match_uuid=match.uuid)
        .order_by(MatchRefSlot.slot_index.asc())
        .all()
    )
    if not slots:
        return match.refs, match.refs_initial
    refs_parts = [s.resolved_team_id or "" for s in slots]
    init_parts = [s.initial_token or "" for s in slots]
    return _join_csv_slots(refs_parts), _join_csv_slots(init_parts)


def replace_match_roster(match, side: str, player_ids: list[str]) -> None:
    """Replace normalized roster rows for one side and sync legacy JSON text."""
    from models import MatchRosterEntry, db

    side_norm = side.lower()
    MatchRosterEntry.query.filter_by(match_uuid=match.uuid, side=side_norm).delete(
        synchronize_session=False
    )
    for idx, player_id in enumerate(player_ids):
        if not player_id:
            continue
        db.session.add(
            MatchRosterEntry(
                match_uuid=match.uuid,
                side=side_norm,
                player_id=player_id,
                slot_index=idx,
            )
        )

    raw_json = json.dumps([p for p in player_ids if p]) if player_ids else None
    if side_norm == "team1":
        match.team1_players = raw_json
    elif side_norm == "team2":
        match.team2_players = raw_json
    else:
        raise ValueError("side must be 'team1' or 'team2'")


def read_match_roster(match, side: str) -> list[str]:
    """Read roster for one side from normalized rows, fallback to legacy JSON."""
    from models import MatchRosterEntry

    side_norm = side.lower()
    rows = (
        MatchRosterEntry.query.filter_by(match_uuid=match.uuid, side=side_norm)
        .order_by(MatchRosterEntry.slot_index.asc())
        .all()
    )
    if rows:
        return [r.player_id for r in rows if r.player_id]

    raw = match.team1_players if side_norm == "team1" else match.team2_players
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return [str(p).strip() for p in parsed if str(p).strip()]
    except Exception:
        pass
    return []


def replace_match_stream_starts(match, stream_starts: dict[str, str]) -> None:
    """Replace normalized stream-start rows and synchronize legacy JSON map."""
    from models import MatchCameraStreamStart, db

    MatchCameraStreamStart.query.filter_by(match_uuid=match.uuid).delete(
        synchronize_session=False
    )
    normalized: dict[str, str] = {}
    for raw_idx, raw_stamp in (stream_starts or {}).items():
        try:
            idx = int(raw_idx)
        except Exception:
            continue
        stamp = str(raw_stamp).strip()
        if not stamp:
            continue
        normalized[str(idx)] = stamp
        db.session.add(
            MatchCameraStreamStart(
                match_uuid=match.uuid,
                camera_index=idx,
                stream_start_iso=stamp,
            )
        )

    match.camera_stream_starts = json.dumps(normalized) if normalized else None


def read_match_stream_starts(match) -> dict[str, str]:
    """Read stream starts from normalized rows, falling back to legacy JSON."""
    from models import MatchCameraStreamStart

    rows = (
        MatchCameraStreamStart.query.filter_by(match_uuid=match.uuid)
        .order_by(MatchCameraStreamStart.camera_index.asc())
        .all()
    )
    if rows:
        return {
            str(r.camera_index): r.stream_start_iso
            for r in rows
            if r.stream_start_iso is not None
        }

    if not match.camera_stream_starts:
        return {}
    try:
        loaded = json.loads(match.camera_stream_starts)
        if isinstance(loaded, dict):
            out: dict[str, str] = {}
            for k, v in loaded.items():
                if isinstance(v, (dict, list)):
                    continue
                vv = str(v).strip()
                if not vv:
                    continue
                out[str(k)] = vv
            return out
    except Exception:
        pass
    return {}
