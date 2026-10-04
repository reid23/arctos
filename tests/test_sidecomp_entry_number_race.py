"""Tests for sidecomp entry_number collision retry."""

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from app.error_values import Ok
from app.services.sidecomp_service import SideCompService
from models import (
    PlayerRegistration,
    SideComp,
    SideCompRegistration,
    Tournament,
    Player,
    db,
)
from tests.utils import make_registrable_config


@pytest.fixture
def comp_setup(test_db):
    rc = make_registrable_config()
    t = Tournament(
        url="evt",
        name="Evt",
        registrable_config_id=rc.id,
        start_date=datetime.now(timezone.utc),
    )
    db.session.add(t)
    db.session.flush()
    sc = SideComp(event="evt", name="DT", type="DUELING", registration_open=True)
    db.session.add(sc)

    players = []
    for i in range(2):
        p = Player(id=f"p{i}", name=f"P{i}", pw_hash="x")
        p.set_password("pw")
        players.append(p)
        db.session.add(p)
        db.session.flush()
        db.session.add(PlayerRegistration(event="evt", player=p.id, status="CONFIRMED"))

    db.session.commit()
    return {"comp_id": sc.id, "players": players}


@pytest.mark.integration
def test_register_player_retries_on_entry_number_collision(comp_setup):
    """If _next_entry_number returns the same value twice in a row, the
    second register_player call retries internally and succeeds with a
    fresh number."""
    comp_id = comp_setup["comp_id"]
    p0, p1 = comp_setup["players"]

    res0 = SideCompService.register_player(comp_id, player_id=p0.id, weapon="CHAIN")
    assert isinstance(res0, Ok), repr(res0)

    # Force _next_entry_number to return 1 (collides) then a fresh 2.
    sequence = iter([1, 2])
    real_next = SideCompService._next_entry_number

    def fake_next(comp):
        try:
            return next(sequence)
        except StopIteration:
            return real_next(comp)

    with patch.object(SideCompService, "_next_entry_number", side_effect=fake_next):
        res1 = SideCompService.register_player(comp_id, player_id=p1.id, weapon="CHAIN")
    assert isinstance(res1, Ok), repr(res1)

    rows = SideCompRegistration.query.filter_by(comp=comp_id).order_by(SideCompRegistration.entry_number).all()
    assert [r.entry_number for r in rows] == [1, 2]
