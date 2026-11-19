"""Tests for the per-field doubly-linked match chain helpers.

Covers ``detach_match_from_chain`` (used by every "remove from chain" site:
delete, switch to STATIC, clear previous_match, force-start) and
``update_match_previous_link`` (move within the chain). Each test asserts both
forward and backward links so back-link rot can't slip through.
"""

from __future__ import annotations

import pytest

from app.domain.enums import ScheduleType
from app.utils.match_chain import detach_match_from_chain, update_match_previous_link
from models import Match, db


def _mk(tournament_url: str, name: str, *, schedule_type: ScheduleType = ScheduleType.SAFE) -> Match:
    m = Match(name=name, event=tournament_url, schedule_type=schedule_type, field="F1")
    db.session.add(m)
    db.session.flush()
    return m


def _link(prev: Match, nxt: Match) -> None:
    prev.next_match = nxt.uuid
    nxt.previous_match = prev.uuid


def _chain(*matches: Match) -> None:
    for a, b in zip(matches, matches[1:]):
        _link(a, b)


def _assert_links(*matches: Match) -> None:
    """Walk forward and backward; every consecutive pair must be mutually linked."""
    for a, b in zip(matches, matches[1:]):
        assert a.next_match == b.uuid, f"{a.name}.next != {b.name}"
        assert b.previous_match == a.uuid, f"{b.name}.prev != {a.name}"
    assert matches[0].previous_match is None
    assert matches[-1].next_match is None


@pytest.mark.unit
def test_detach_middle_node_closes_gap(test_db, tournament):
    a = _mk(tournament.url, "A")
    b = _mk(tournament.url, "B")
    c = _mk(tournament.url, "C")
    _chain(a, b, c)

    detach_match_from_chain(b, tournament.url)
    db.session.flush()

    assert b.previous_match is None
    assert b.next_match is None
    _assert_links(a, c)


@pytest.mark.unit
def test_detach_head_clears_next_prev(test_db, tournament):
    a = _mk(tournament.url, "A")
    b = _mk(tournament.url, "B")
    _chain(a, b)

    detach_match_from_chain(a, tournament.url)
    db.session.flush()

    assert a.previous_match is None
    assert a.next_match is None
    assert b.previous_match is None
    assert b.next_match is None


@pytest.mark.unit
def test_detach_tail_clears_prev_next(test_db, tournament):
    a = _mk(tournament.url, "A")
    b = _mk(tournament.url, "B")
    _chain(a, b)

    detach_match_from_chain(b, tournament.url)
    db.session.flush()

    assert b.previous_match is None
    assert b.next_match is None
    assert a.previous_match is None
    assert a.next_match is None


@pytest.mark.unit
def test_detach_singleton_is_noop(test_db, tournament):
    a = _mk(tournament.url, "A")
    detach_match_from_chain(a, tournament.url)
    db.session.flush()
    assert a.previous_match is None
    assert a.next_match is None


@pytest.mark.unit
def test_detach_with_inconsistent_back_link_does_not_overwrite(test_db, tournament):
    """If old_prev's next pointer doesn't actually point at us, we leave it alone."""
    a = _mk(tournament.url, "A")
    b = _mk(tournament.url, "B")
    c = _mk(tournament.url, "C")
    # B claims A is its previous, but A already points at C.
    b.previous_match = a.uuid
    a.next_match = c.uuid
    c.previous_match = a.uuid
    db.session.flush()

    detach_match_from_chain(b, tournament.url)
    db.session.flush()

    # B's pointers cleared; A's link to C is preserved (we never owned it).
    assert b.previous_match is None
    assert b.next_match is None
    assert a.next_match == c.uuid
    assert c.previous_match == a.uuid


@pytest.mark.unit
def test_insert_into_middle(test_db, tournament):
    a = _mk(tournament.url, "A")
    c = _mk(tournament.url, "C")
    _chain(a, c)
    new = _mk(tournament.url, "NEW")

    update_match_previous_link(new, a.uuid, tournament.url, is_new=True)
    db.session.flush()

    _assert_links(a, new, c)


@pytest.mark.unit
def test_insert_at_head_when_prev_has_no_next(test_db, tournament):
    a = _mk(tournament.url, "A")
    new = _mk(tournament.url, "NEW")

    update_match_previous_link(new, a.uuid, tournament.url, is_new=True)
    db.session.flush()

    _assert_links(a, new)


@pytest.mark.unit
def test_move_node_across_chain(test_db, tournament):
    """Moving a node between chains: B from A→B→X to D→B (with B's old next dropped)."""
    a = _mk(tournament.url, "A")
    b = _mk(tournament.url, "B")
    x = _mk(tournament.url, "X")
    d = _mk(tournament.url, "D")
    _chain(a, b, x)

    update_match_previous_link(b, d.uuid, tournament.url, is_new=False)
    db.session.flush()

    # Old chain has closed up: A → X
    _assert_links(a, x)
    # New chain: D → B
    _assert_links(d, b)


@pytest.mark.unit
def test_move_node_within_same_chain(test_db, tournament):
    """A→B→C→D, move B to be after C → A→C→B→D."""
    a = _mk(tournament.url, "A")
    b = _mk(tournament.url, "B")
    c = _mk(tournament.url, "C")
    d = _mk(tournament.url, "D")
    _chain(a, b, c, d)

    update_match_previous_link(b, c.uuid, tournament.url, is_new=False)
    db.session.flush()

    _assert_links(a, c, b, d)


@pytest.mark.unit
def test_no_op_self_link(test_db, tournament):
    a = _mk(tournament.url, "A")
    update_match_previous_link(a, a.uuid, tournament.url, is_new=True)
    db.session.flush()
    assert a.previous_match is None
    assert a.next_match is None


@pytest.mark.unit
def test_re_anchor_to_current_prev_is_idempotent(test_db, tournament):
    """A→B (B's prev is already A); calling update with prev=A leaves the chain unchanged."""
    a = _mk(tournament.url, "A")
    b = _mk(tournament.url, "B")
    _chain(a, b)

    update_match_previous_link(b, a.uuid, tournament.url, is_new=False)
    db.session.flush()

    _assert_links(a, b)
