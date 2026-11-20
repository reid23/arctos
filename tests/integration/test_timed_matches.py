"""Timed matches keep their set length in minutes through create and update."""

import pytest

from app.domain.enums import SetType
from models import TO, Match, db
from tests.utils import login_as


@pytest.fixture
def organizer(client, tournament, player):
    db.session.add(TO(event=tournament.url, user_id=player.id, user_type="player"))
    db.session.commit()
    login_as(client, player)


@pytest.mark.integration
def test_create_timed_match_stores_set_minutes(client, tournament, organizer):
    response = client.post(
        f"/_api/tournaments/{tournament.url}/matches",
        json={"name": "Halves", "field": "Field 1", "set_type": "TIMED", "nsets": 2, "stones_per_set": 12},
    )

    assert response.status_code == 200, response.get_json()
    match = Match.query.filter_by(event=tournament.url, name="Halves").one()
    assert match.set_type == SetType.TIMED
    assert match.nsets == 2
    assert match.stones_per_set == 12


@pytest.mark.integration
def test_update_keeps_set_minutes_for_timed_and_clears_them_for_sets(client, tournament, organizer):
    match = Match(name="Halves", event=tournament.url, field="Field 1", schedule_type="STATIC", set_type="SETS")
    db.session.add(match)
    db.session.commit()

    timed = client.put(
        f"/_api/tournaments/{tournament.url}/matches/{match.uuid}",
        json={"set_type": "TIMED", "stones_per_set": 12},
    )
    assert timed.status_code == 200, timed.get_json()
    db.session.refresh(match)
    assert (match.set_type, match.stones_per_set) == (SetType.TIMED, 12)

    sets = client.put(f"/_api/tournaments/{tournament.url}/matches/{match.uuid}", json={"set_type": "SETS"})
    assert sets.status_code == 200, sets.get_json()
    db.session.refresh(match)
    assert (match.set_type, match.stones_per_set) == (SetType.SETS, None)


@pytest.mark.integration
def test_add_match_form_stores_set_minutes_for_timed(client, tournament, organizer):
    response = client.post(
        f"/_api/{tournament.url}/add-match",
        data={"match_name": "Halves", "field": "Field 1", "match_type": "TIMED", "nsets": "2", "stones_per_set": "12"},
    )

    assert response.status_code == 200, response.get_json()
    match = Match.query.filter_by(event=tournament.url, name="Halves").one()
    assert (match.set_type, match.stones_per_set) == (SetType.TIMED, 12)
