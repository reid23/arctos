"""Match-note CRUD routes (head refs only).

Hosts the ``notes`` blueprint.

Endpoints here let the head ref of a match attach, edit, or remove notes targeting the match itself, one
side, or a specific player.

The serialised shape is produced by ``app.serializers.match_note_serializer`` so it stays consistent with
the rest of the API.
"""

from flask import Blueprint, request
from flask_login import login_required, current_user
from models import Match, MatchNote, Point, Tournament, db
from app.domain.enums import MatchNoteTarget
from app.filters import is_head_ref
from app.utils.helpers import can_head_ref_match, match_event_urls_for_penalties
from app.serializers.match_note_serializer import MatchNoteSerializer
from app.utils.responses import json_error, json_success
from app.utils.user_helpers import is_player

bp = Blueprint("notes", __name__, url_prefix="/_api")


def _match_in_tournament_scope(match, tournament_url: str) -> bool:
    """Return ``True`` if *match* falls within the scope of *tournament_url*.

    For standalone tournaments the match must belong to that event.  For
    league-affiliated tournaments the match may belong to any event in the
    same league.

    Args:
        match: A :class:`~app.models.match.Match` instance to check.
        tournament_url: The URL slug of the tournament being accessed.

    Returns:
        ``True`` if the match is in scope; ``False`` otherwise.
    """
    tournament = Tournament.query.filter_by(url=tournament_url).first()
    if not tournament:
        return False
    event_urls = match_event_urls_for_penalties(tournament)
    return match.event in event_urls


@bp.route("/<tournament_url>/get-notes")
@login_required
def get_notes(tournament_url: str):
    """Return all notes for a match, optionally filtered by point.

    ``GET /_api/<tournament_url>/get-notes?match_id=<uuid>[&point_id=<uuid>]``

    Requires the caller to be a head ref for the match.  For league events
    the match may belong to any tournament in the league.

    Args:
        tournament_url: Tournament URL slug from the path.

    Query Args:
        match_id: UUID of the match to retrieve notes for.
        point_id: Optional UUID; when supplied, returns notes for that point
            plus any unassigned notes on the match.

    Returns:
        JSON ``{"success": true, "notes": [...]}``, or an error body.
    """
    match_id = request.args.get("match_id")

    if not match_id:
        return json_error("Match ID required", status_code=400)

    match = Match.query.get(match_id)
    if not match or not _match_in_tournament_scope(match, tournament_url):
        return json_error("Match not found", status_code=404)

    if not can_head_ref_match(tournament_url, current_user.id, match=match):
        return json_error("Not authorized", status_code=403)

    point_id = request.args.get("point_id")

    if point_id:
        notes = (
            MatchNote.query.filter_by(match=match_id)
            .filter((MatchNote.point_id == point_id) | (MatchNote.point_id.is_(None)))
            .order_by(MatchNote.created_at.desc())
            .all()
        )
    else:
        notes = MatchNote.query.filter_by(match=match_id, point_id=None).order_by(MatchNote.created_at.desc()).all()

    notes_data = []
    for note in notes:
        notes_data.append(MatchNoteSerializer.to_dict(note, tournament_url, match=match))

    return json_success({"notes": notes_data})


@bp.route("/<tournament_url>/add-note", methods=["POST"])
@login_required
def add_note(tournament_url: str):
    """Add a new note to a match.

    ``POST /_api/<tournament_url>/add-note``

    Requires the caller to be a head ref for the match.

    Args:
        tournament_url: Tournament URL slug from the path.

    Request JSON:
        match_id (str): UUID of the target match.
        text (str): Note content.
        target (str): Note target (default ``"MATCH"``).
        player_id (str | None): Optional player the note concerns.

    Returns:
        JSON ``{"success": true, "note_id": "<uuid>"}``, or an error body.
    """
    match_id = request.json.get("match_id")
    text = request.json.get("text")
    target = request.json.get("target", "MATCH")
    player_id = request.json.get("player_id")

    if not match_id or not text:
        return json_error("Match ID and text required", status_code=400)

    match = Match.query.get(match_id)
    if not match or not _match_in_tournament_scope(match, tournament_url):
        return json_error("Match not found", status_code=404)

    if not can_head_ref_match(tournament_url, current_user.id, match=match):
        return json_error("Not authorized", status_code=403)

    note = MatchNote(
        match=match_id,
        text=text,
        target=target,
        created_by=current_user.id,
        player_id=player_id if player_id else None,
    )
    db.session.add(note)
    db.session.commit()

    return json_success({"note_id": note.uuid})


@bp.route("/<tournament_url>/assign-notes-to-point", methods=["POST"])
@login_required
def assign_notes_to_point(tournament_url: str):
    """Assign one or more unassigned match notes to a specific point.

    ``POST /_api/<tournament_url>/assign-notes-to-point``

    Requires head-ref status for the tournament.  Only notes not yet linked
    to a point (``point_id IS NULL``) are modified.

    Args:
        tournament_url: Tournament URL slug from the path.

    Request JSON:
        point_id (str): UUID of the target point.
        note_ids (list[str]): UUIDs of notes to assign.

    Returns:
        JSON ``{"success": true, "assigned_count": int}``, or an error body.
    """
    point_id = request.json.get("point_id")
    note_ids = request.json.get("note_ids", [])

    if not point_id or not note_ids:
        return json_error("Point ID and note IDs required", status_code=400)

    point = Point.query.get(point_id)
    if not point:
        return json_error("Point not found", status_code=404)

    match = Match.query.get(point.match)
    if not match or not _match_in_tournament_scope(match, tournament_url):
        return json_error("Match not found", status_code=404)

    if not is_head_ref(tournament_url, current_user.id):
        return json_error("Not authorized", status_code=403)

    assigned_count = 0
    for note_id in note_ids:
        note = MatchNote.query.get(note_id)
        if note and note.match == point.match and note.point_id is None:
            note.point_id = point_id
            assigned_count += 1

    db.session.commit()

    return json_success({"assigned_count": assigned_count})


@bp.route("/<tournament_url>/get-point-notes")
def get_point_notes(tournament_url: str):
    """Return notes associated with a specific point.

    ``GET /_api/<tournament_url>/get-point-notes?match_id=<uuid>&point_id=<uuid>``

    Match-target notes (``target == "match"``) are visible to all users.
    Team and player notes are only returned when the caller is a head ref.

    Args:
        tournament_url: Tournament URL slug from the path.

    Query Args:
        match_id: UUID of the parent match.
        point_id: UUID of the target point.

    Returns:
        JSON ``{"success": true, "notes": [...]}``, or an error body.
    """
    match_id = request.args.get("match_id")
    point_id = request.args.get("point_id")

    if not match_id or not point_id:
        return json_error("Match ID and Point ID required", status_code=400)

    match = Match.query.get(match_id)
    if not match or not _match_in_tournament_scope(match, tournament_url):
        return json_error("Match not found", status_code=404)

    # Check if user is a head ref (for full access to all notes)
    is_head_ref = False
    if current_user.is_authenticated and is_player(current_user):
        is_head_ref = can_head_ref_match(tournament_url, current_user.id, match=match)

    # Get all notes for this point
    notes = MatchNote.query.filter_by(match=match_id, point_id=point_id).order_by(MatchNote.created_at.desc()).all()

    notes_data = []
    for note in notes:
        # Filter: only show point notes (target='match') to everyone
        # Team and player notes are only visible to head refs
        if not is_head_ref and note.target != "match":
            continue
        notes_data.append(MatchNoteSerializer.to_dict(note, tournament_url, match=match))

    return json_success({"notes": notes_data})


@bp.route("/<tournament_url>/add-point-note", methods=["POST"])
@login_required
def add_point_note(tournament_url: str):
    """Add a note directly linked to a scored point.

    ``POST /_api/<tournament_url>/add-point-note``

    Requires head-ref status.  Either *text* or *penalty_type_id* must be
    supplied.

    Args:
        tournament_url: Tournament URL slug from the path.

    Request JSON:
        match_id (str): UUID of the parent match.
        point_id (str): UUID of the target point.
        text (str): Note content (optional if penalty_type_id supplied).
        target (str): Note target (default ``"MATCH"``).
        player_id (str | None): Optional player the note concerns.
        penalty_type_id (int | None): Optional penalty type FK.

    Returns:
        JSON ``{"success": true, "note_id": "<uuid>"}``, or an error body.
    """
    match_id = request.json.get("match_id")
    point_id = request.json.get("point_id")
    text = request.json.get("text", "")
    target = request.json.get("target", "MATCH")
    player_id = request.json.get("player_id")
    penalty_type_id = request.json.get("penalty_type_id")

    if not match_id or not point_id:
        return json_error("Match ID and Point ID required", status_code=400)

    if not text and not penalty_type_id:
        return json_error("Text or penalty type required", status_code=400)

    match = Match.query.get(match_id)
    if not match or not _match_in_tournament_scope(match, tournament_url):
        return json_error("Match not found", status_code=404)

    if not can_head_ref_match(tournament_url, current_user.id, match=match):
        return json_error("Not authorized", status_code=403)

    point = Point.query.get(point_id)
    if not point or point.match != match_id:
        return json_error("Point not found", status_code=404)

    note = MatchNote(
        match=match_id,
        point_id=point_id,
        text=text,
        target=target,
        created_by=current_user.id,
        player_id=player_id if player_id else None,
        penalty_type_id=penalty_type_id if penalty_type_id else None,
    )
    db.session.add(note)
    db.session.commit()

    return json_success({"note_id": note.uuid})


@bp.route("/<tournament_url>/set-point-note", methods=["POST"])
@login_required
def set_point_note(tournament_url: str):
    """Replace the single match-target note on a point.

    ``POST /_api/<tournament_url>/set-point-note``

    Deletes any existing ``target=match`` notes for the point, then creates
    a new one when *text* is non-empty.  Requires head-ref status.

    Args:
        tournament_url: Tournament URL slug from the path.

    Request JSON:
        match_id (str): UUID of the parent match.
        point_id (str): UUID of the target point.
        text (str): Replacement text (empty string removes the note).

    Returns:
        JSON ``{"success": true}``, or an error body.
    """
    match_id = request.json.get("match_id")
    point_id = request.json.get("point_id")
    text = (request.json.get("text") or "").strip()

    if not match_id or not point_id:
        return json_error("Match ID and Point ID required", status_code=400)

    match = Match.query.get(match_id)
    if not match or not _match_in_tournament_scope(match, tournament_url):
        return json_error("Match not found", status_code=404)

    if not can_head_ref_match(tournament_url, current_user.id, match=match):
        return json_error("Not authorized", status_code=403)

    point = Point.query.get(point_id)
    if not point or point.match != match_id:
        return json_error("Point not found", status_code=404)

    # Remove existing point note(s) for this point (target=match only)
    MatchNote.query.filter_by(
        match=match_id,
        point_id=point_id,
        target=MatchNoteTarget.MATCH,
    ).delete()

    if text:
        note = MatchNote(
            match=match_id,
            point_id=point_id,
            text=text,
            target=MatchNoteTarget.MATCH,
            created_by=current_user.id,
        )
        db.session.add(note)

    db.session.commit()
    return json_success()


@bp.route("/<tournament_url>/delete-point-note", methods=["POST"])
@login_required
def delete_point_note(tournament_url: str):
    """Permanently delete a match note.

    ``POST /_api/<tournament_url>/delete-point-note``

    Requires head-ref status for the match.

    Args:
        tournament_url: Tournament URL slug from the path.

    Request JSON:
        note_id (str): UUID of the note to delete.

    Returns:
        JSON ``{"success": true}``, or an error body.
    """
    note_id = request.json.get("note_id")

    if not note_id:
        return json_error("Note ID required", status_code=400)

    note = MatchNote.query.get(note_id)
    if not note:
        return json_error("Note not found", status_code=404)

    match = Match.query.get(note.match)
    if not match or not _match_in_tournament_scope(match, tournament_url):
        return json_error("Match not found", status_code=404)

    from app.utils.helpers import can_head_ref_match

    if not can_head_ref_match(tournament_url, current_user.id, match):
        return json_error("Not authorized", status_code=403)

    db.session.delete(note)
    db.session.commit()

    return json_success()


@bp.route("/<tournament_url>/unassign-notes-from-point", methods=["POST"])
@login_required
def unassign_notes_from_point(tournament_url: str):
    """Detach one or more notes from a point, returning them to the match level.

    ``POST /_api/<tournament_url>/unassign-notes-from-point``

    Requires head-ref status for the tournament.

    Args:
        tournament_url: Tournament URL slug from the path.

    Request JSON:
        point_id (str): UUID of the point to unassign from.
        note_ids (list[str]): UUIDs of notes to detach.

    Returns:
        JSON ``{"success": true, "unassigned_count": int}``, or an error body.
    """
    point_id = request.json.get("point_id")
    note_ids = request.json.get("note_ids", [])

    if not point_id or not note_ids:
        return json_error("Point ID and note IDs required", status_code=400)

    point = Point.query.get(point_id)
    if not point:
        return json_error("Point not found", status_code=404)

    match = Match.query.get(point.match)
    if not match or not _match_in_tournament_scope(match, tournament_url):
        return json_error("Match not found", status_code=404)

    if not is_head_ref(tournament_url, current_user.id):
        return json_error("Not authorized", status_code=403)

    unassigned_count = 0
    for note_id in note_ids:
        note = MatchNote.query.get(note_id)
        if note and note.point_id == point_id:
            note.point_id = None
            unassigned_count += 1

    db.session.commit()

    return json_success({"unassigned_count": unassigned_count})
