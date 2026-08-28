"""Scrub bracket diagram references when related entities are deleted.

Bracket canvas elements and legacy TOML overlays store soft string tokens
(``MatchName::winner``, ``tag::Foo``, bare team ids). Those are not FK-backed.
This module clears or rewrites those tokens when the underlying match, tag, or
team registration goes away, and deletes uploaded bracket image files when a
tournament is removed.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable

from flask import current_app

from app.domain.enums import BracketPortMode
from app.models.bracket import BracketImage, BracketLabeledTeam, BracketPlacement
from models import Match, MatchReferee, Tag, Tournament

_MATCH_REF_RE = re.compile(r"^(?P<name>.+)::(?P<qual>winner|loser)$", re.IGNORECASE)


def _parse_match_ref(token: str | None) -> tuple[str, str] | None:
    if not token:
        return None
    m = _MATCH_REF_RE.match(token.strip())
    if not m:
        return None
    return m.group("name").strip(), m.group("qual").lower()


def _is_match_ref_to(token: str | None, match_names_lower: set[str]) -> bool:
    ref = _parse_match_ref(token)
    if not ref:
        return False
    return ref[0].lower() in match_names_lower


def _is_tag_ref_to(token: str | None, tag_names_lower: set[str]) -> bool:
    if not token:
        return False
    t = token.strip()
    if not t.lower().startswith("tag::"):
        # bare tag name is also used in some legacy overlays
        return t.lower() in tag_names_lower
    name = t[5:].strip()
    return bool(name) and name.lower() in tag_names_lower


def _is_team_token(token: str | None, team_ids: set[str], pseudonyms_lower: set[str]) -> bool:
    if not token:
        return False
    t = token.strip()
    if not t:
        return False
    if t in team_ids:
        return True
    if t.lower() in pseudonyms_lower:
        return True
    return False


def _delete_image_file(rel_path: str | None) -> None:
    """Best-effort delete of a bracket image under static/uploads/brackets/."""
    if not rel_path:
        return
    norm = rel_path.replace("\\", "/").lstrip("/")
    if not norm.startswith("uploads/brackets/"):
        return
    try:
        root = os.path.join(current_app.root_path, "../static")
    except RuntimeError:
        # Outside app context — skip file IO.
        return
    full = os.path.normpath(os.path.join(root, norm))
    static_root = os.path.normpath(root)
    if not full.startswith(static_root + os.sep):
        return
    try:
        if os.path.isfile(full):
            os.remove(full)
    except OSError:
        pass


def _parse_legacy_brackets_raw(tournament: Tournament) -> list:
    if not getattr(tournament, "bracket", None):
        return []
    try:
        import tomli

        parsed = tomli.loads(tournament.bracket)
    except Exception:
        return []
    brackets = parsed.get("brackets") or []
    return brackets if isinstance(brackets, list) else []


def _serialize_legacy_brackets(brackets: list) -> str:
    def escape_toml_string(s):
        s = str(s)
        s = s.replace("\\", "\\\\")
        s = s.replace('"', '\\"')
        s = s.replace("\n", "\\n")
        s = s.replace("\t", "\\t")
        return s

    toml_lines = []
    for bracket in brackets:
        if not isinstance(bracket, dict):
            continue
        name = (bracket.get("name") or "").strip()
        image = (bracket.get("image") or "").strip()
        if not name or not image:
            continue
        toml_lines.append("[[brackets]]")
        toml_lines.append(f'name = "{escape_toml_string(name)}"')
        toml_lines.append(f'image = "{escape_toml_string(image)}"')
        toml_lines.append("")
        for team in bracket.get("teams") or []:
            if not isinstance(team, dict):
                continue
            team_ref = (team.get("team") or "").strip()
            if not team_ref:
                continue
            try:
                x = int(team.get("x", 0) or 0)
                y = int(team.get("y", 0) or 0)
                halign = (team.get("halign") or "center").strip() or "center"
                valign = (team.get("valign") or "center").strip() or "center"
                size = int(team.get("size", 20) or 20)
            except (ValueError, TypeError):
                continue
            toml_lines.append("[[brackets.teams]]")
            toml_lines.append(f'team = "{escape_toml_string(team_ref)}"')
            toml_lines.append(f"x = {x}")
            toml_lines.append(f"y = {y}")
            toml_lines.append(f'halign = "{escape_toml_string(halign)}"')
            toml_lines.append(f'valign = "{escape_toml_string(valign)}"')
            toml_lines.append(f"size = {size}")
            toml_lines.append("")
    return "\n".join(toml_lines)


def _scrub_legacy_tokens(tournament: Tournament, should_drop) -> None:
    """Drop legacy overlay team entries for which *should_drop(token)* is true."""
    brackets = _parse_legacy_brackets_raw(tournament)
    if not brackets:
        return
    dirty = False
    for bracket in brackets:
        if not isinstance(bracket, dict):
            continue
        teams = bracket.get("teams") or []
        if not isinstance(teams, list):
            continue
        kept = []
        for entry in teams:
            if not isinstance(entry, dict):
                dirty = True
                continue
            token = (entry.get("team") or "").strip()
            if token and should_drop(token):
                dirty = True
                continue
            kept.append(entry)
        bracket["teams"] = kept
    if dirty:
        tournament.bracket = _serialize_legacy_brackets(brackets)


def _clear_match_initial_if(match: Match, attr: str, predicate) -> None:
    val = getattr(match, attr, None)
    if val and predicate(val):
        setattr(match, attr, None)


def _scrub_match_side_tokens(tournament_url: str, predicate) -> None:
    """Clear team1/team2 initials and referee slot initials matching *predicate*."""
    matches = Match.query.filter_by(event=tournament_url).all()
    match_uuids = [m.uuid for m in matches]
    for m in matches:
        _clear_match_initial_if(m, "team1_initial", predicate)
        _clear_match_initial_if(m, "team2_initial", predicate)
    if match_uuids:
        for row in MatchReferee.query.filter(MatchReferee.match_uuid.in_(match_uuids)).all():
            if row.initial and predicate(row.initial):
                row.initial = None
                row.team_id = None


def scrub_deleted_matches(tournament_url: str, match_names: Iterable[str]) -> None:
    """Clear diagram + soft refs that pointed at deleted match names.

    - Remaining matches: clear ``teamN_initial`` match-winner/loser refs.
    - Remaining placements: NET ports whose initial pointed at a deleted match
      fall back to LABEL.
    - Labeled-team chips: clear token and force LABEL.
    - Legacy image overlays: drop matching team entries.
    """
    names_lower = {n.strip().lower() for n in match_names if n and str(n).strip()}
    if not names_lower:
        return

    def hits(token: str | None) -> bool:
        return _is_match_ref_to(token, names_lower)

    matches = Match.query.filter_by(event=tournament_url).all()
    matches_by_uuid = {m.uuid: m for m in matches}
    deleting_uuids = {m.uuid for m in matches if m.name and m.name.strip().lower() in names_lower}
    remaining = [m for m in matches if m.uuid not in deleting_uuids]
    remaining_uuids = [m.uuid for m in remaining]

    # Convert NET ports / labeled chips first (while initials still hold the ref),
    # then clear the soft string tokens on remaining matches.
    for p in BracketPlacement.query.filter_by(event=tournament_url).all():
        m = matches_by_uuid.get(p.match)
        if m is None or m.uuid in deleting_uuids:
            continue
        if hits(m.team1_initial):
            p.team1 = BracketPortMode.LABEL
        if hits(m.team2_initial):
            p.team2 = BracketPortMode.LABEL

    for row in BracketLabeledTeam.query.filter_by(event=tournament_url).all():
        if hits(row.team):
            row.team = ""
            row.kind = BracketPortMode.LABEL

    for m in remaining:
        _clear_match_initial_if(m, "team1_initial", hits)
        _clear_match_initial_if(m, "team2_initial", hits)
    if remaining_uuids:
        for row in MatchReferee.query.filter(MatchReferee.match_uuid.in_(remaining_uuids)).all():
            if row.initial and hits(row.initial):
                row.initial = None
                row.team_id = None

    tournament = Tournament.query.filter_by(url=tournament_url).first()
    if tournament is not None:
        _scrub_legacy_tokens(tournament, hits)


def scrub_deleted_tags(tournament_url: str, tag_names: Iterable[str]) -> None:
    """Clear diagram tokens that referenced deleted tags."""
    names_lower = {n.strip().lower() for n in tag_names if n and str(n).strip()}
    if not names_lower:
        return

    def hits(token: str | None) -> bool:
        return _is_tag_ref_to(token, names_lower)

    _scrub_match_side_tokens(tournament_url, hits)

    for row in BracketLabeledTeam.query.filter_by(event=tournament_url).all():
        if hits(row.team):
            row.team = ""
            row.kind = BracketPortMode.LABEL

    tournament = Tournament.query.filter_by(url=tournament_url).first()
    if tournament is not None:
        _scrub_legacy_tokens(tournament, hits)


def scrub_deleted_teams(
    tournament_url: str,
    team_ids: Iterable[str],
    *,
    pseudonyms: Iterable[str] | None = None,
) -> None:
    """Clear diagram tokens that referenced deregistered teams.

    Also clears ``Tag.team`` assignments pointing at those teams so tag chips
    no longer resolve to a removed registration.
    """
    ids = {t.strip() for t in team_ids if t and str(t).strip()}
    pseuds_lower = {p.strip().lower() for p in (pseudonyms or []) if p and str(p).strip()}
    if not ids and not pseuds_lower:
        return

    def hits(token: str | None) -> bool:
        if _is_team_token(token, ids, pseuds_lower):
            return True
        # tag::Name whose Tag.team is one of the removed teams
        if not token:
            return False
        t = token.strip()
        if t.lower().startswith("tag::"):
            name = t[5:].strip()
            if name:
                tag = Tag.query.filter_by(event=tournament_url, name=name).first()
                if tag and tag.team and tag.team in ids:
                    return True
        return False

    _scrub_match_side_tokens(tournament_url, hits)

    for row in BracketLabeledTeam.query.filter_by(event=tournament_url).all():
        if hits(row.team):
            row.team = ""
            row.kind = BracketPortMode.LABEL

    # Detach tags from removed teams (tag name stays; assignment clears).
    if ids:
        Tag.query.filter(
            Tag.event == tournament_url,
            Tag.team.in_(list(ids)),
        ).update({Tag.team: None}, synchronize_session=False)

    tournament = Tournament.query.filter_by(url=tournament_url).first()
    if tournament is not None:
        _scrub_legacy_tokens(tournament, hits)


def scrub_deleted_teams_for_league(
    league_url: str,
    team_ids: Iterable[str],
    *,
    pseudonyms: Iterable[str] | None = None,
) -> None:
    """Run :func:`scrub_deleted_teams` for every event in a league."""
    ids = list(team_ids)
    pseuds = list(pseudonyms or [])
    for t in Tournament.query.filter_by(league_id=league_url).all():
        scrub_deleted_teams(t.url, ids, pseudonyms=pseuds)


def cleanup_tournament_bracket_assets(tournament_url: str) -> None:
    """Delete uploaded image files referenced by canvas + legacy brackets.

    Call **before** deleting the tournament row (or its bracket rows). Does not
    commit; does not delete DB rows (FK cascade / explicit deletes handle that).
    """
    for img in BracketImage.query.filter_by(event=tournament_url).all():
        _delete_image_file(img.image)

    tournament = Tournament.query.filter_by(url=tournament_url).first()
    if tournament is None:
        return
    for bracket in _parse_legacy_brackets_raw(tournament):
        if isinstance(bracket, dict):
            _delete_image_file((bracket.get("image") or "").strip() or None)
