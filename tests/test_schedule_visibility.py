"""Navigation availability follows the existing schedule access policy."""

import pytest

from app.domain.enums import MatchStatus, RegistrationStatus
from app.services.dual_write import set_head_ref_allowlist_ids, set_match_referees
from models import Field, League, Match, PlayerRegistration, TO, db
from tests.utils import login_as

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("published", [False, True])
@pytest.mark.parametrize(
    "actor", ["guest", "player", "pending_ref", "confirmed_ref", "allowlisted_ref", "to", "league_to", "league_ref"]
)
def test_schedule_flags_match_access(client, tournament, player, actor, published):
    tournament.schedule_published = published
    if actor.startswith("league_"):
        league = League(
            url="schedule-league",
            name="Schedule league",
            published=True,
            registrable_config_id=tournament.registrable_config_id,
        )
        db.session.add(league)
        db.session.flush()
        tournament.registrable_config_id = None
        tournament.league_id = league.url
    if actor in ("pending_ref", "confirmed_ref", "league_ref"):
        tournament.head_refs_allow_anyone = True
        db.session.add(
            PlayerRegistration(
                player=player.id,
                event=None if tournament.league_id else tournament.url,
                league_id=tournament.league_id,
                status=RegistrationStatus.PENDING_TEAM_APPROVAL
                if actor == "pending_ref"
                else RegistrationStatus.CONFIRMED,
            )
        )
    if actor == "allowlisted_ref":
        set_head_ref_allowlist_ids(tournament, [player.id])
    if actor in ("to", "league_to"):
        db.session.add(
            TO(
                user_id=player.id,
                user_type="player",
                event=None if tournament.league_id else tournament.url,
                league_id=tournament.league_id,
            )
        )
    match = Match(name="Visibility check", event=tournament.url)
    db.session.add(match)
    db.session.commit()
    if actor != "guest":
        login_as(client, player)

    allowed = published or actor in ("confirmed_ref", "allowlisted_ref", "to", "league_to", "league_ref")
    base = f"/_api/tournaments/{tournament.url}"
    for path in (base, f"{base}/match?id={match.uuid}"):
        response = client.get(path)
        assert response.status_code == 200, response.get_json()
        payload = response.get_json()
        assert payload["can_view_schedule"] is allowed
        if "match" in payload:
            assert payload["schedule_published"] is published
    for path in (f"{base}/schedule", f"{base}/schedule-setup"):
        response = client.get(path)
        assert response.status_code == (200 if allowed else 403), response.get_json()


def test_published_empty_schedule_remains_viewable(client, tournament):
    Field.query.filter_by(event=tournament.url).delete()
    db.session.commit()
    base = f"/_api/tournaments/{tournament.url}"
    assert client.get(base).get_json()["can_view_schedule"] is True
    for path in (f"{base}/schedule", f"{base}/schedule-setup"):
        response = client.get(path)
        assert response.status_code == 200
        assert response.get_json()["matches"] == []
        assert response.get_json()["fields"] == []


@pytest.mark.parametrize("published", [False, True])
@pytest.mark.parametrize("tournament_published", [False, True])
@pytest.mark.parametrize("ref_kind", ["general", "match_only"])
def test_operator_payloads_distinguish_match_permission_from_schedule_access(
    client, tournament, player, team, seeded_teams, published, tournament_published, ref_kind
):
    tournament.published = tournament_published
    tournament.schedule_published = published
    tournament.head_refs_allow_reffing_teams = ref_kind == "match_only"
    if ref_kind == "general":
        set_head_ref_allowlist_ids(tournament, [player.id])
    db.session.add(
        PlayerRegistration(
            event=tournament.url,
            player=player.id,
            team=team.id,
            status=RegistrationStatus.CONFIRMED,
        )
    )
    match = Match(
        event=tournament.url,
        name="Ready match",
        team1="team1",
        team2="team2",
        status=MatchStatus.READY_TO_START,
        schedule_type="STATIC",
        field="Field 1",
    )
    db.session.add(match)
    db.session.flush()
    set_match_referees(match, [team.id], [team.id])
    db.session.commit()
    login_as(client, player)

    allowed = tournament_published and (published or ref_kind == "general")
    base = f"/_api/tournaments/{tournament.url}"
    for action in ("start-match", "finalize-match"):
        response = client.get(f"{base}/{action}?match_id={match.uuid}")
        assert response.status_code == 200, response.get_json()
        assert response.get_json()["can_view_schedule"] is allowed
        assert response.get_json()["tournament"]["schedule_published"] is published
    response = client.get(f"{base}/schedule-setup")
    assert response.status_code == (404 if not tournament_published else 200 if allowed else 403)


def test_missing_tournament_stays_not_found(client):
    for suffix in ("", "/schedule", "/schedule-setup"):
        assert client.get("/_api/tournaments/deleted-event" + suffix).status_code == 404
