"""The roster UI shares the backend's absent/zero/positive limit contract."""

import pytest

from app.domain.enums import MatchStatus
from app.utils.helpers import get_registrable_config
from models import Match, Player, db
from tests.utils import login_as

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("limit,expected", [(None, 200), (0, 200), (2, 200), (1, 400)])
def test_match_start_honors_only_positive_roster_limits(
    client, tournament, head_ref_player, seeded_teams, limit, expected
):
    get_registrable_config(tournament).max_team_size_field = limit
    db.session.add_all([Player(id="fixture-one", name="One"), Player(id="fixture-two", name="Two")])
    match = Match(
        name="Roster fixture",
        event=tournament.url,
        team1="team1",
        team2="team2",
        field="Field 1",
        schedule_type="SAFE",
        set_type="SETS",
        status=MatchStatus.READY_TO_START,
        nominal_length=60,
    )
    db.session.add(match)
    db.session.commit()
    login_as(client, head_ref_player)
    response = client.post(
        f"/_api/{tournament.url}/start-match",
        data={
            "match_id": match.uuid,
            "team1_players": "fixture-one,fixture-two",
            "team2_players": "",
            "match_notes": "",
        },
    )
    assert response.status_code == expected
    db.session.refresh(match)
    assert match.status == (MatchStatus.IN_PROGRESS if expected == 200 else MatchStatus.READY_TO_START)
