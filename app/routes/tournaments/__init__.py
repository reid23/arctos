"""Tournament-organiser routes - blueprint package.

Hosts the ``tournaments`` blueprint at ``/_api``.

Routes are split by sub-topic across submodules in this package; all
submodules share the same Blueprint object defined in this file, so
URL endpoint names are stable across moves:

- :mod:`app.routes.tournaments.management` - TO-side tournament/league
  CRUD, fields, tags, TO membership.
- :mod:`app.routes.tournaments.scheduling` - schedule editing, recompute,
  push-back, import/export, DSL validation.
- :mod:`app.routes.tournaments.recordings` - cameras, recording/preview
  endpoints, ffmpeg finalisation, user-upload pipeline.

The :data:`executor` is a Flask-Executor used to run ffmpeg finalisation
off the request thread; we only ever want one worker at a time because
ffmpeg already parallelises internally.
"""

from flask import Blueprint
from flask_executor import Executor

from models import (
    BracketPlacement,
    Camera,
    CameraTimepoint,
    Match,
    MatchNote,
    MatchPlayer,
    MatchReferee,
    Point,
    db,
)

# for finalizing recordings which calls ffmpeg
# only one worker bc ffmpeg does its own parallelism
# so we only ever want to run one at a time
executor = Executor()

bp = Blueprint("tournaments", __name__, url_prefix="/_api")


def _lookup_match_in(uid: str | None, tournament_url: str) -> Match | None:
    """Fetch a match by uuid scoped to *tournament_url*, or ``None`` for empty/missing."""
    if not uid:
        return None
    return Match.query.filter_by(uuid=uid, event=tournament_url).first()


def detach_match_from_chain(match: Match, tournament_url: str) -> None:
    """Remove *match* from its per-field doubly-linked chain.

    Closes up the gap left behind: ``old_prev.next_match`` is rewritten to
    ``match.next_match`` and ``old_next.previous_match`` to ``match.previous_match``,
    each guarded by a back-link consistency check so we never overwrite a
    pointer that wasn't actually aimed at *match*. Both of *match*'s own
    pointers are then cleared.
    """
    old_prev_id = match.previous_match
    old_next_id = match.next_match
    old_prev = _lookup_match_in(old_prev_id, tournament_url)
    old_next = _lookup_match_in(old_next_id, tournament_url)
    if old_prev is not None and old_prev.next_match == match.uuid:
        old_prev.next_match = old_next_id
    if old_next is not None and old_next.previous_match == match.uuid:
        old_next.previous_match = old_prev_id
    match.previous_match = None
    match.next_match = None


def update_match_previous_link(match: Match, prev_match_id: str, tournament_url: str, is_new: bool = False) -> None:
    """Insert *match* immediately after *prev_match_id* in the doubly-linked chain.

    The chain is the per-field ``previous_match`` / ``next_match`` linkage. The
    operation is two clean steps: (1) detach *match* from its current position
    so the surrounding nodes close up, then (2) splice it in between
    *prev_match* and *prev_match*'s current next.

    A no-op if *prev_match_id* doesn't resolve or points at *match* itself.

    Args:
        match: The match being moved (or, if ``is_new``, freshly created).
        prev_match_id: UUID of the match that should sit immediately before *match*.
        tournament_url: Tournament scope for the lookup.
        is_new: ``True`` if *match* is brand-new and currently has no chain links;
            skips the detach step.
    """
    if not prev_match_id or prev_match_id == match.uuid:
        return
    prev_match = _lookup_match_in(prev_match_id, tournament_url)
    if not prev_match:
        return

    if not is_new:
        detach_match_from_chain(match, tournament_url)

    # Splice *match* between prev_match and whatever currently sits after prev_match.
    new_next_id = prev_match.next_match
    if new_next_id == match.uuid:
        # Stale back-pointer guard.
        new_next_id = None
    match.previous_match = prev_match.uuid
    match.next_match = new_next_id
    prev_match.next_match = match.uuid
    if new_next_id:
        new_next = _lookup_match_in(new_next_id, tournament_url)
        if new_next is not None:
            new_next.previous_match = match.uuid


def delete_matches_with_children(match_uuids: list[str]) -> None:
    """Hard-delete matches and every row that references them.

    Deletes child rows (points, notes, referee/player join rows, cameras and
    their timepoints) and clears the self-referential chain links before
    deleting the matches themselves, so foreign-key constraints don't block the
    delete. Also scrubs soft bracket/diagram refs that pointed at the deleted
    matches by name. Does not commit; the caller owns the transaction.
    """
    if not match_uuids:
        return

    # Scrub diagram soft-refs (MatchName::winner/loser) before rows disappear.
    doomed = Match.query.filter(Match.uuid.in_(match_uuids)).all()
    by_event: dict[str, set[str]] = {}
    for m in doomed:
        if m.event and m.name:
            by_event.setdefault(m.event, set()).add(m.name)
    if by_event:
        from app.services.bracket_cleanup_service import scrub_deleted_matches

        for event_url, names in by_event.items():
            scrub_deleted_matches(event_url, names)

    camera_uuids = [c.uuid for c in Camera.query.filter(Camera.match_uuid.in_(match_uuids)).all()]
    if camera_uuids:
        CameraTimepoint.query.filter(CameraTimepoint.camera_uuid.in_(camera_uuids)).delete(synchronize_session=False)
    Camera.query.filter(Camera.match_uuid.in_(match_uuids)).delete(synchronize_session=False)

    Point.query.filter(Point.match.in_(match_uuids)).delete(synchronize_session=False)
    MatchNote.query.filter(MatchNote.match.in_(match_uuids)).delete(synchronize_session=False)
    MatchReferee.query.filter(MatchReferee.match_uuid.in_(match_uuids)).delete(synchronize_session=False)
    MatchPlayer.query.filter(MatchPlayer.match_uuid.in_(match_uuids)).delete(synchronize_session=False)
    BracketPlacement.query.filter(BracketPlacement.match.in_(match_uuids)).delete(synchronize_session=False)

    # Clear self-referential chain links so deleting the matches doesn't trip the
    # previous_match / next_match FKs (covers references from outside the batch).
    Match.query.filter(Match.previous_match.in_(match_uuids)).update(
        {"previous_match": None}, synchronize_session=False
    )
    Match.query.filter(Match.next_match.in_(match_uuids)).update({"next_match": None}, synchronize_session=False)

    Match.query.filter(Match.uuid.in_(match_uuids)).delete(synchronize_session=False)


# Register submodule handlers by importing them.
# This MUST be at the bottom so that bp and executor are already defined
# when submodules import them via `from . import bp`.
from app.routes.tournaments import (  # noqa: E402, F401
    brackets,
    management,
    matches_admin,
    read,
    recordings,
    scheduling,
)
