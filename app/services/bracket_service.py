"""Organizer writes to a tournament's bracket diagram.

Publishing and saving the canvas layout (match placements, text, labeled
teams, and images). Shared by the SPA's session routes in
:mod:`app.routes.tournaments.brackets` and the public API. Callers check
organizer permission.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

from app.domain.enums import BracketPortMode, ScheduleType, parse_enum
from app.error_values import Err, Ok, Result
from app.exceptions import ArctosError, ValidationError
from app.models.bracket import (
    DEFAULT_BRACKET_HEIGHT,
    DEFAULT_BRACKET_WIDTH,
    DEFAULT_IMAGE_HEIGHT,
    DEFAULT_IMAGE_WIDTH,
    DEFAULT_TEXT_SIZE,
    BracketImage,
    BracketLabeledTeam,
    BracketPlacement,
    BracketText,
)
from models import Match, db

MATCH_REFERENCE_PATTERN = re.compile(r"^(?P<name>.+)::(?P<qual>winner|loser)$", re.IGNORECASE)


def parse_match_ref(initial: str | None) -> tuple[str, str] | None:
    """Return ``(match_name, 'winner'|'loser')`` if *initial* is a match ref."""
    if not initial:
        return None
    m = MATCH_REFERENCE_PATTERN.match(initial.strip())
    if not m:
        return None
    return m.group("name").strip(), m.group("qual").lower()


def port_mode(value) -> BracketPortMode:
    parsed = parse_enum(BracketPortMode, value)
    if parsed.is_some():
        return parsed.unwrap()
    if isinstance(value, str) and value.strip().upper() == "NET":
        return BracketPortMode.NET
    return BracketPortMode.LABEL


def playable_matches(tournament_url: str) -> list[Match]:
    """Matches that can appear on a bracket (exclude BREAK/STATBREAK/JOIN)."""
    rows = Match.query.filter_by(event=tournament_url).order_by(Match.name).all()
    out = []
    for m in rows:
        st = m.schedule_type
        if st in (ScheduleType.BREAK, ScheduleType.STATBREAK, ScheduleType.JOIN):
            continue
        # Also skip stringy legacy values just in case
        if isinstance(st, str) and st.upper() in ("BREAK", "JOIN", "STATBREAK"):
            continue
        out.append(m)
    return out


def delete_bracket_image_file(rel_path: str | None) -> None:
    """Best-effort delete of a bracket image under static/."""
    from flask import current_app

    if not rel_path:
        return
    # Only allow paths under uploads/brackets/
    norm = rel_path.replace("\\", "/").lstrip("/")
    if not norm.startswith("uploads/brackets/"):
        return
    root = os.path.join(current_app.root_path, "../static")
    full = os.path.normpath(os.path.join(root, norm))
    static_root = os.path.normpath(root)
    if not full.startswith(static_root + os.sep):
        return
    try:
        if os.path.isfile(full):
            os.remove(full)
    except OSError:
        pass


@dataclass(frozen=True)
class BracketService:
    @staticmethod
    def set_published(tournament, published: bool) -> None:
        """Show or hide the bracket from everyone but the organizers."""
        tournament.bracket_published = published
        db.session.commit()

    @staticmethod
    def save_layout(tournament_url: str, data: dict) -> Result[None, ArctosError]:
        """Upsert the canvas from a layout document.

        ``placements`` (required) places or unplaces playable matches;
        ``texts``, ``labeled_teams``, and ``images`` (optional) upsert by id.
        With ``clear_missing``, rows not in the document are deleted.
        """
        items = data.get("placements")
        if not isinstance(items, list):
            return Err(ValidationError("placements must be a list"))

        clear_missing = bool(data.get("clear_missing", False))

        playable = {m.uuid: m for m in playable_matches(tournament_url)}
        existing = {p.match: p for p in BracketPlacement.query.filter_by(event=tournament_url).all()}
        seen: set[str] = set()

        for raw in items:
            if not isinstance(raw, dict):
                continue
            match_id = (raw.get("match") or raw.get("uuid") or "").strip()
            if not match_id or match_id not in playable:
                continue
            seen.add(match_id)

            x_raw = raw.get("x_pos", raw.get("x"))
            y_raw = raw.get("y_pos", raw.get("y"))
            try:
                x_pos = float(x_raw) if x_raw is not None else None
                y_pos = float(y_raw) if y_raw is not None else None
            except (TypeError, ValueError):
                x_pos, y_pos = None, None

            # Both-or-neither: partial coords mean unplaced.
            if x_pos is None or y_pos is None:
                x_pos, y_pos = None, None

            try:
                width = float(raw.get("width", DEFAULT_BRACKET_WIDTH) or DEFAULT_BRACKET_WIDTH)
            except (TypeError, ValueError):
                width = DEFAULT_BRACKET_WIDTH
            try:
                height = float(raw.get("height", DEFAULT_BRACKET_HEIGHT) or DEFAULT_BRACKET_HEIGHT)
            except (TypeError, ValueError):
                height = DEFAULT_BRACKET_HEIGHT
            width = max(160.0, min(width, 800.0))
            height = max(70.0, min(height, 400.0))

            team1 = port_mode(raw.get("team1", "LABEL"))
            team2 = port_mode(raw.get("team2", "LABEL"))
            inputs_flipped = bool(raw.get("inputs_flipped", False))

            # NET is only valid for winner/loser refs; coerce otherwise.
            m = playable[match_id]
            if team1 == BracketPortMode.NET and parse_match_ref(m.team1_initial) is None:
                team1 = BracketPortMode.LABEL
            if team2 == BracketPortMode.NET and parse_match_ref(m.team2_initial) is None:
                team2 = BracketPortMode.LABEL

            row = existing.get(match_id)
            if row is None:
                row = BracketPlacement(event=tournament_url, match=match_id)
                db.session.add(row)
                existing[match_id] = row
            row.x_pos = x_pos
            row.y_pos = y_pos
            row.width = width
            row.height = height
            row.team1 = team1
            row.team2 = team2
            row.inputs_flipped = inputs_flipped

        if clear_missing:
            for mid, row in list(existing.items()):
                if mid not in seen:
                    db.session.delete(row)

        # --- texts ---
        texts_in = data.get("texts")
        if isinstance(texts_in, list):
            existing_texts = {t.id: t for t in BracketText.query.filter_by(event=tournament_url).all()}
            seen_t: set[str] = set()
            for raw in texts_in:
                if not isinstance(raw, dict):
                    continue
                tid = (raw.get("id") or "").strip()
                if not tid:
                    continue
                seen_t.add(tid)
                row = existing_texts.get(tid)
                if row is None:
                    row = BracketText(id=tid, event=tournament_url)
                    db.session.add(row)
                row.text = str(raw.get("text") or "")
                try:
                    row.x_pos = float(raw.get("x_pos", 40) or 40)
                    row.y_pos = float(raw.get("y_pos", 40) or 40)
                    row.size = float(raw.get("size", DEFAULT_TEXT_SIZE) or DEFAULT_TEXT_SIZE)
                except (TypeError, ValueError):
                    row.x_pos = row.x_pos or 40.0
                    row.y_pos = row.y_pos or 40.0
                    row.size = row.size or DEFAULT_TEXT_SIZE
                row.size = max(8.0, min(row.size, 200.0))
            if clear_missing:
                for tid, row in list(existing_texts.items()):
                    if tid not in seen_t:
                        db.session.delete(row)

        # --- labeled teams ---
        lts_in = data.get("labeled_teams")
        if isinstance(lts_in, list):
            existing_lt = {t.id: t for t in BracketLabeledTeam.query.filter_by(event=tournament_url).all()}
            seen_lt: set[str] = set()
            for raw in lts_in:
                if not isinstance(raw, dict):
                    continue
                tid = (raw.get("id") or "").strip()
                if not tid:
                    continue
                seen_lt.add(tid)
                row = existing_lt.get(tid)
                if row is None:
                    row = BracketLabeledTeam(id=tid, event=tournament_url)
                    db.session.add(row)
                team_token = str(raw.get("team") or "").strip()
                kind = port_mode(raw.get("kind", "LABEL"))
                if kind == BracketPortMode.NET and parse_match_ref(team_token) is None:
                    kind = BracketPortMode.LABEL
                row.label = str(raw.get("label") or "")[:50]
                row.team = team_token
                row.kind = kind
                try:
                    row.x_pos = float(raw.get("x_pos", 40) or 40)
                    row.y_pos = float(raw.get("y_pos", 40) or 40)
                except (TypeError, ValueError):
                    row.x_pos = row.x_pos or 40.0
                    row.y_pos = row.y_pos or 40.0
            if clear_missing:
                for tid, row in list(existing_lt.items()):
                    if tid not in seen_lt:
                        db.session.delete(row)

        # --- images ---
        imgs_in = data.get("images")
        if isinstance(imgs_in, list):
            existing_img = {i.id: i for i in BracketImage.query.filter_by(event=tournament_url).all()}
            seen_i: set[str] = set()
            for raw in imgs_in:
                if not isinstance(raw, dict):
                    continue
                iid = (raw.get("id") or "").strip()
                if not iid:
                    continue
                seen_i.add(iid)
                row = existing_img.get(iid)
                if row is None:
                    row = BracketImage(id=iid, event=tournament_url)
                    db.session.add(row)
                new_path = str(raw.get("image") or row.image or "").strip()
                if row.image and new_path and new_path != row.image:
                    delete_bracket_image_file(row.image)
                row.image = new_path
                try:
                    row.x_pos = float(raw.get("x_pos", 40) or 40)
                    row.y_pos = float(raw.get("y_pos", 40) or 40)
                    row.width = float(raw.get("width", DEFAULT_IMAGE_WIDTH) or DEFAULT_IMAGE_WIDTH)
                    row.height = float(raw.get("height", DEFAULT_IMAGE_HEIGHT) or DEFAULT_IMAGE_HEIGHT)
                except (TypeError, ValueError):
                    pass
                row.width = max(20.0, min(row.width or DEFAULT_IMAGE_WIDTH, 4000.0))
                row.height = max(20.0, min(row.height or DEFAULT_IMAGE_HEIGHT, 4000.0))
            if clear_missing:
                for iid, row in list(existing_img.items()):
                    if iid not in seen_i:
                        delete_bracket_image_file(row.image)
                        db.session.delete(row)

        db.session.commit()
        return Ok(None)
