"""Tests for HistoricalRecordsService and HistoricalClaimsService."""

import pytest

from app.domain.enums import MatchStatus, RegistrationStatus, SetType
from app.services.historical_claims_service import HistoricalClaimsService
from app.services.historical_records_service import PLACEHOLDER_ID_PREFIX, HistoricalRecordsService
from app.services.registration_resolver import team_registrations_for_tournament
from models import (
    TO,
    Match,
    MatchReferee,
    Player,
    PlayerRegistration,
    Point,
    Team,
    TeamClaimInvitation,
    TeamRegistration,
    db,
)

ORGANIZER = {"actor_user_id": "organizer", "actor_user_type": "player"}


@pytest.fixture
def organizer(test_db):
    player = Player(id="organizer", name="Organizer", pw_hash="x")
    player.set_password("pw")
    db.session.add(player)
    db.session.commit()
    return player


@pytest.fixture
def historical(organizer):
    return HistoricalRecordsService.create_tournament(
        **ORGANIZER,
        name="Old Cup",
        url="old-cup",
        location="Berlin",
        start_date="2019-06-01",
        end_date="2019-06-02",
        team_names=["Alpha", "Beta", "Gamma", "Delta"],
    ).unwrap()


@pytest.fixture
def real_team(test_db):
    team = Team(id="rigor", name="Rigor Mortis", pw_hash="x")
    team.set_password("pw")
    db.session.add(team)
    db.session.commit()
    return team


@pytest.fixture
def roster_player(test_db):
    player = Player(id="runner", name="Runner", pw_hash="x")
    player.set_password("pw")
    db.session.add(player)
    db.session.commit()
    return player


def team_ids(tournament) -> dict[str, str]:
    return {registration.pseudonym: registration.team for registration in team_registrations_for_tournament(tournament)}


def save(tournament, *, name, team1, team2, winner="TEAM1", points=None, referees=None, match_uuid=None, set_type=None):
    return HistoricalRecordsService.save_match(
        tournament.url,
        **ORGANIZER,
        match_uuid=match_uuid,
        name=name,
        team1=team1,
        team2=team2,
        referees=referees,
        winner=winner,
        points=points,
        set_type=set_type,
    )


def match_named(tournament, name) -> Match:
    return Match.query.filter_by(event=tournament.url, name=name).one()


def save_bracket(tournament):
    """Two semis (Alpha and Delta win) and a final between their winners."""
    save(tournament, name="Semi 1", team1="Alpha", team2="Beta", winner="TEAM1").unwrap()
    save(tournament, name="Semi 2", team1="Gamma", team2="Delta", winner="TEAM2").unwrap()
    save(tournament, name="Final", team1="Semi 1::winner", team2="Semi 2::winner").unwrap()


@pytest.mark.unit
def test_create_tournament_adds_organizer_and_placeholder_teams(organizer):
    tournament = HistoricalRecordsService.create_tournament(
        **ORGANIZER,
        name="Old Cup",
        url="old-cup",
        location="",
        start_date="2019-06-01",
        end_date="",
        team_names=["Alpha", " alpha ", "", "Beta"],
    ).unwrap()

    assert tournament.historical
    assert not tournament.published
    assert not tournament.registrable_config.team_registration_open
    assert not tournament.registrable_config.player_registration_open
    assert tournament.end_date == tournament.start_date
    assert TO.query.filter_by(event="old-cup", user_id="organizer").count() == 1
    ids = team_ids(tournament)
    assert set(ids) == {"Alpha", "Beta"}
    for team_id in ids.values():
        assert team_id.startswith(PLACEHOLDER_ID_PREFIX)
        assert Team.query.get(team_id).placeholder_event == "old-cup"


@pytest.mark.unit
@pytest.mark.parametrize(
    "start_date, end_date, message",
    [
        ("2999-01-01", "", "future"),
        ("2019-06-02", "2019-06-01", "before the start date"),
        ("June 2019", "", "must be a date"),
    ],
)
def test_create_tournament_rejects_bad_dates(organizer, start_date, end_date, message):
    result = HistoricalRecordsService.create_tournament(
        **ORGANIZER,
        name="Old Cup",
        url="old-cup",
        location=None,
        start_date=start_date,
        end_date=end_date,
        team_names=[],
    )

    assert message in result.unwrap_err().message


@pytest.mark.unit
def test_organizer_check_rejects_other_users(historical, roster_player):
    result = HistoricalRecordsService.organizer_tournament(historical.url, roster_player.id, "player")

    assert result.unwrap_err().status_code == 403


@pytest.mark.unit
def test_save_match_records_teams_points_and_winner(historical):
    ids = team_ids(historical)
    points = [
        {"set": 1, "winner": "TEAM1"},
        {"set": 2, "winner": "TEAM2"},
        {"set": 1, "winner": "TEAM1"},
    ]

    match = save(
        historical, name="Pool 1", team1="alpha", team2=ids["Beta"], points=points, referees=["Gamma"]
    ).unwrap()

    assert (match.team1, match.team2) == (ids["Alpha"], ids["Beta"])
    assert match.team1_initial == ids["Alpha"]
    assert match.status == MatchStatus.COMPLETED
    assert match.match_winner == "TEAM1"
    assert match.nsets == 2
    stored = Point.query.filter_by(match=match.uuid).order_by(Point.stamp).all()
    assert [(point.set_number, point.winner) for point in stored] == [(1, "TEAM1"), (1, "TEAM1"), (2, "TEAM2")]
    assert [row.team_id for row in MatchReferee.query.filter_by(match_uuid=match.uuid)] == [ids["Gamma"]]


@pytest.mark.unit
@pytest.mark.parametrize(
    "overrides, message",
    [
        ({"team2": "Alpha"}, "same team on both sides"),
        ({"referees": ["Beta"]}, "playing team refereeing"),
        ({"team2": "Nobody"}, "isn't one of this tournament's teams"),
        ({"team2": "Missing::winner"}, "no other match named Missing"),
        ({"winner": ""}, "Choose who won"),
        ({"points": [{"set": 0, "winner": "TEAM1"}]}, "at least 1"),
        ({"name": "Pool/1"}, "/"),
    ],
)
def test_save_match_rejects_invalid_input(historical, overrides, message):
    arguments = {"name": "Pool 1", "team1": "Alpha", "team2": "Beta", **overrides}

    result = save(historical, **arguments)

    assert message in result.unwrap_err().message
    assert Match.query.filter_by(event=historical.url).count() == 0


@pytest.mark.unit
def test_save_match_rejects_duplicate_name(historical):
    save(historical, name="Pool 1", team1="Alpha", team2="Beta").unwrap()

    result = save(historical, name="Pool 1", team1="Gamma", team2="Delta")

    assert "already a match named Pool 1" in result.unwrap_err().message


@pytest.mark.unit
def test_references_resolve_from_the_referenced_result(historical):
    ids = team_ids(historical)

    save_bracket(historical)

    final = match_named(historical, "Final")
    assert (final.team1, final.team2) == (ids["Alpha"], ids["Delta"])
    assert (final.team1_initial, final.team2_initial) == ("Semi 1::winner", "Semi 2::winner")


@pytest.mark.unit
def test_changing_a_result_updates_matches_that_reference_it(historical):
    ids = team_ids(historical)
    save_bracket(historical)
    save(historical, name="Bronze", team1="Semi 1::loser", team2="Semi 2::loser", referees=["Final::loser"]).unwrap()
    semi = match_named(historical, "Semi 1")

    save(historical, match_uuid=semi.uuid, name="Semi 1", team1="Alpha", team2="Beta", winner="TEAM2").unwrap()

    assert match_named(historical, "Final").team1 == ids["Beta"]
    bronze = match_named(historical, "Bronze")
    assert bronze.team1 == ids["Alpha"]
    referee = MatchReferee.query.filter_by(match_uuid=bronze.uuid).one()
    assert (referee.team_id, referee.initial) == (ids["Delta"], "Final::loser")


@pytest.mark.unit
def test_renaming_a_match_rewrites_references_to_it(historical):
    save_bracket(historical)
    semi = match_named(historical, "Semi 1")

    save(historical, match_uuid=semi.uuid, name="SF1", team1="Alpha", team2="Beta").unwrap()

    assert match_named(historical, "Final").team1_initial == "SF1::winner"


@pytest.mark.unit
def test_reference_cycles_are_rejected(historical):
    save(historical, name="A", team1="Alpha", team2="Beta").unwrap()
    save(historical, name="B", team1="A::winner", team2="Gamma").unwrap()
    first = match_named(historical, "A")

    result = save(historical, match_uuid=first.uuid, name="A", team1="Alpha", team2="B::loser")

    assert "depends on it" in result.unwrap_err().message


@pytest.mark.unit
def test_change_that_breaks_a_dependent_match_writes_nothing(historical):
    save(historical, name="Semi 1", team1="Alpha", team2="Beta", winner="TEAM1").unwrap()
    save(historical, name="Final", team1="Semi 1::winner", team2="Beta").unwrap()
    semi = match_named(historical, "Semi 1")

    result = save(historical, match_uuid=semi.uuid, name="Semi 1", team1="Alpha", team2="Beta", winner="TEAM2")

    assert "would give Final the same team on both sides" in result.unwrap_err().message
    db.session.rollback()
    assert match_named(historical, "Semi 1").match_winner == "TEAM1"


@pytest.mark.unit
def test_referenced_match_cannot_be_deleted(historical):
    save_bracket(historical)

    blocked = HistoricalRecordsService.deletable_match(
        historical.url, **ORGANIZER, match_uuid=match_named(historical, "Semi 1").uuid
    )
    allowed = HistoricalRecordsService.deletable_match(
        historical.url, **ORGANIZER, match_uuid=match_named(historical, "Final").uuid
    )

    assert "referenced by: Final" in blocked.unwrap_err().message
    assert allowed.is_ok()


@pytest.mark.unit
def test_add_teams_rejects_names_already_in_the_tournament(historical):
    result = HistoricalRecordsService.add_teams(historical.url, **ORGANIZER, names=["Epsilon", "alpha"])

    assert "Already in this tournament: alpha" in result.unwrap_err().message
    assert "Epsilon" not in team_ids(historical)


@pytest.mark.unit
def test_rename_team_renames_the_placeholder(historical):
    alpha = team_ids(historical)["Alpha"]

    HistoricalRecordsService.rename_team(historical.url, **ORGANIZER, team_id=alpha, name="Alpha Squad").unwrap()

    assert team_ids(historical)["Alpha Squad"] == alpha
    assert Team.query.get(alpha).name == "Alpha Squad"


@pytest.mark.unit
def test_remove_team_requires_it_to_be_in_no_match(historical):
    ids = team_ids(historical)
    save(historical, name="Pool 1", team1="Alpha", team2="Beta", referees=["Gamma"]).unwrap()

    blocked = HistoricalRecordsService.remove_team(historical.url, **ORGANIZER, team_id=ids["Gamma"])
    removed = HistoricalRecordsService.remove_team(historical.url, **ORGANIZER, team_id=ids["Delta"])

    assert "still in: Pool 1" in blocked.unwrap_err().message
    assert removed.is_ok()
    assert Team.query.get(ids["Delta"]) is None
    assert "Delta" not in team_ids(historical)


@pytest.mark.unit
def test_editor_state_reports_teams_and_matches(historical, real_team):
    ids = team_ids(historical)
    save(
        historical, name="Pool 1", team1="Alpha", team2="Beta", points=[{"set": 1, "winner": "TEAM2"}], winner="TEAM2"
    ).unwrap()
    HistoricalClaimsService.invite_team_to_claim(
        historical.url, **ORGANIZER, placeholder_team_id=ids["Gamma"], invited_team_id=real_team.id
    ).unwrap()

    state = HistoricalRecordsService.editor_state(historical)

    teams = {team["name"]: team for team in state["teams"]}
    assert teams["Alpha"]["match_count"] == 1
    assert teams["Alpha"]["placeholder"] is True
    assert teams["Gamma"]["claim_invitation"] == {"team_id": "rigor", "team_name": "Rigor Mortis"}
    [match] = state["matches"]
    assert match["winner"] == "TEAM2"
    assert match["set_type"] == "SETS"
    assert match["points"] == [{"set": 1, "winner": "TEAM2"}]


@pytest.mark.unit
def test_match_scoring_can_be_stones(historical):
    saved = save(
        historical,
        name="Pool 1",
        team1="Alpha",
        team2="Beta",
        set_type="STONES",
        points=[{"set": 1, "winner": "TEAM1"}, {"set": 1, "winner": "TEAM1"}, {"set": 2, "winner": "TEAM2"}],
    ).unwrap()

    assert saved.set_type == SetType.STONES
    [match] = HistoricalRecordsService.editor_state(historical)["matches"]
    assert match["set_type"] == "STONES"

    rejected = save(historical, name="Pool 2", team1="Gamma", team2="Delta", set_type="best of three")
    assert "sets or stones" in rejected.unwrap_err().message


@pytest.mark.unit
def test_accepting_a_claim_merges_the_placeholder_into_the_team(historical, real_team, roster_player):
    ids = team_ids(historical)
    save_bracket(historical)
    save(historical, name="Bronze", team1="Beta", team2="Gamma", referees=[ids["Alpha"]]).unwrap()
    HistoricalClaimsService.invite_player(
        historical.url, ids["Alpha"], **ORGANIZER, player_id=roster_player.id, jersey_name=None, jersey_number="7"
    ).unwrap()
    invitation = HistoricalClaimsService.invite_team_to_claim(
        historical.url, **ORGANIZER, placeholder_team_id=ids["Alpha"], invited_team_id=real_team.id
    ).unwrap()

    HistoricalClaimsService.respond_to_claim(invitation.id, team_id=real_team.id, accept=True).unwrap()

    assert Team.query.get(ids["Alpha"]) is None
    assert team_ids(historical)["Alpha"] == real_team.id
    assert match_named(historical, "Semi 1").team1 == real_team.id
    assert match_named(historical, "Final").team1 == real_team.id
    bronze = match_named(historical, "Bronze")
    referee = MatchReferee.query.filter_by(match_uuid=bronze.uuid).one()
    assert (referee.team_id, referee.initial) == (real_team.id, real_team.id)
    assert PlayerRegistration.query.filter_by(event=historical.url, player=roster_player.id).one().team == real_team.id
    assert TeamClaimInvitation.query.count() == 0


@pytest.mark.unit
def test_claiming_one_placeholder_withdraws_the_teams_other_invitations(historical, real_team):
    ids = team_ids(historical)
    first = HistoricalClaimsService.invite_team_to_claim(
        historical.url, **ORGANIZER, placeholder_team_id=ids["Alpha"], invited_team_id=real_team.id
    ).unwrap()
    HistoricalClaimsService.invite_team_to_claim(
        historical.url, **ORGANIZER, placeholder_team_id=ids["Beta"], invited_team_id=real_team.id
    ).unwrap()

    HistoricalClaimsService.respond_to_claim(first.id, team_id=real_team.id, accept=True).unwrap()

    assert TeamClaimInvitation.query.count() == 0
    again = HistoricalClaimsService.invite_team_to_claim(
        historical.url, **ORGANIZER, placeholder_team_id=ids["Beta"], invited_team_id=real_team.id
    )
    assert "already in this tournament" in again.unwrap_err().message


@pytest.mark.unit
def test_declining_a_claim_keeps_the_placeholder(historical, real_team):
    alpha = team_ids(historical)["Alpha"]
    invitation = HistoricalClaimsService.invite_team_to_claim(
        historical.url, **ORGANIZER, placeholder_team_id=alpha, invited_team_id=real_team.id
    ).unwrap()

    HistoricalClaimsService.respond_to_claim(invitation.id, team_id=real_team.id, accept=False).unwrap()

    assert Team.query.get(alpha) is not None
    assert TeamClaimInvitation.query.count() == 0


@pytest.mark.unit
def test_only_the_invited_team_can_respond_to_a_claim(historical, real_team):
    invitation = HistoricalClaimsService.invite_team_to_claim(
        historical.url, **ORGANIZER, placeholder_team_id=team_ids(historical)["Alpha"], invited_team_id=real_team.id
    ).unwrap()

    result = HistoricalClaimsService.respond_to_claim(invitation.id, team_id="someone-else", accept=True)

    assert result.unwrap_err().status_code == 404


@pytest.mark.unit
def test_roster_invitation_lifecycle(historical, roster_player):
    alpha = team_ids(historical)["Alpha"]
    registration = HistoricalClaimsService.invite_player(
        historical.url, alpha, **ORGANIZER, player_id=roster_player.id, jersey_name="", jersey_number=""
    ).unwrap()
    assert registration.status == RegistrationStatus.INVITED
    assert registration.jersey_name == "Runner"
    assert HistoricalClaimsService.pending_invitations(roster_player.id, "player")["roster_invitations"] == []

    historical.published = True
    db.session.commit()
    pending = HistoricalClaimsService.pending_invitations(roster_player.id, "player")
    assert [item["team_name"] for item in pending["roster_invitations"]] == ["Alpha"]

    HistoricalClaimsService.respond_to_roster_invitation(
        registration.id, player_id=roster_player.id, accept=True
    ).unwrap()
    assert registration.status == RegistrationStatus.CONFIRMED
    assert HistoricalClaimsService.pending_invitations(roster_player.id, "player")["roster_invitations"] == []

    HistoricalClaimsService.remove_player(historical.url, alpha, **ORGANIZER, player_id=roster_player.id).unwrap()
    assert registration.status == RegistrationStatus.CANCELLED

    again = HistoricalClaimsService.invite_player(
        historical.url, alpha, **ORGANIZER, player_id=roster_player.id, jersey_name=None, jersey_number=None
    ).unwrap()
    assert again.id == registration.id
    assert again.status == RegistrationStatus.INVITED


@pytest.mark.unit
def test_player_cannot_be_on_two_rosters(historical, roster_player):
    ids = team_ids(historical)
    HistoricalClaimsService.invite_player(
        historical.url, ids["Alpha"], **ORGANIZER, player_id=roster_player.id, jersey_name=None, jersey_number=None
    ).unwrap()

    result = HistoricalClaimsService.invite_player(
        historical.url, ids["Beta"], **ORGANIZER, player_id=roster_player.id, jersey_name=None, jersey_number=None
    )

    assert "another team's roster" in result.unwrap_err().message


def _roster_statuses(event: str, player_id: str) -> set[tuple[str, str]]:
    return {
        (registration.team, registration.status.value)
        for registration in PlayerRegistration.query.filter_by(event=event, player=player_id).all()
    }


@pytest.mark.unit
def test_requesting_the_inviting_team_accepts_and_sets_jersey(historical, roster_player):
    historical.published = True
    db.session.commit()
    alpha = team_ids(historical)["Alpha"]
    invitation = HistoricalClaimsService.invite_player(
        historical.url, alpha, **ORGANIZER, player_id=roster_player.id, jersey_name="Old", jersey_number="1"
    ).unwrap()

    HistoricalClaimsService.request_roster(
        historical.url, player_id=roster_player.id, team_id=alpha, jersey_name="", jersey_number=""
    ).unwrap()
    assert invitation.status == RegistrationStatus.CONFIRMED
    assert invitation.jersey_name == "Old"
    assert invitation.jersey_number == "1"

    HistoricalClaimsService.remove_player(historical.url, alpha, **ORGANIZER, player_id=roster_player.id).unwrap()
    invitation = HistoricalClaimsService.invite_player(
        historical.url, alpha, **ORGANIZER, player_id=roster_player.id, jersey_name="Old", jersey_number="1"
    ).unwrap()
    HistoricalClaimsService.request_roster(
        historical.url, player_id=roster_player.id, team_id=alpha, jersey_name="New", jersey_number="9"
    ).unwrap()

    assert invitation.status == RegistrationStatus.CONFIRMED
    assert invitation.jersey_name == "New"
    assert invitation.jersey_number == "9"
    assert PlayerRegistration.query.filter_by(event=historical.url, player=roster_player.id).count() == 1


@pytest.mark.unit
def test_request_beside_another_teams_invitation(historical, roster_player):
    historical.published = True
    db.session.commit()
    ids = team_ids(historical)
    HistoricalClaimsService.invite_player(
        historical.url, ids["Alpha"], **ORGANIZER, player_id=roster_player.id, jersey_name="Old", jersey_number="1"
    ).unwrap()
    HistoricalClaimsService.request_roster(
        historical.url, player_id=roster_player.id, team_id=ids["Beta"], jersey_name="New", jersey_number="2"
    ).unwrap()

    rows = {
        registration.team: registration
        for registration in PlayerRegistration.query.filter_by(event=historical.url, player=roster_player.id).all()
    }
    assert rows[ids["Alpha"]].status == RegistrationStatus.INVITED
    assert rows[ids["Beta"]].status == RegistrationStatus.REQUESTED
    assert rows[ids["Beta"]].jersey_name == "New"

    HistoricalClaimsService.respond_to_roster_invitation(
        rows[ids["Alpha"]].id, player_id=roster_player.id, accept=False
    ).unwrap()
    assert rows[ids["Alpha"]].status == RegistrationStatus.REJECTED
    assert rows[ids["Beta"]].status == RegistrationStatus.REQUESTED


@pytest.mark.unit
def test_accepting_either_roster_row_declines_the_other(historical, roster_player):
    historical.published = True
    db.session.commit()
    ids = team_ids(historical)
    invitation = HistoricalClaimsService.invite_player(
        historical.url, ids["Alpha"], **ORGANIZER, player_id=roster_player.id, jersey_name=None, jersey_number=None
    ).unwrap()
    HistoricalClaimsService.request_roster(
        historical.url, player_id=roster_player.id, team_id=ids["Beta"], jersey_name="New", jersey_number="2"
    ).unwrap()
    HistoricalClaimsService.respond_to_roster_invitation(
        invitation.id, player_id=roster_player.id, accept=True
    ).unwrap()
    assert _roster_statuses(historical.url, roster_player.id) == {
        (ids["Alpha"], "CONFIRMED"),
        (ids["Beta"], "REJECTED"),
    }

    PlayerRegistration.query.filter_by(event=historical.url, player=roster_player.id).delete()
    db.session.commit()
    HistoricalClaimsService.invite_player(
        historical.url, ids["Alpha"], **ORGANIZER, player_id=roster_player.id, jersey_name=None, jersey_number=None
    ).unwrap()
    HistoricalClaimsService.request_roster(
        historical.url, player_id=roster_player.id, team_id=ids["Beta"], jersey_name="New", jersey_number="2"
    ).unwrap()
    request = PlayerRegistration.query.filter_by(
        event=historical.url, player=roster_player.id, status=RegistrationStatus.REQUESTED
    ).one()
    HistoricalClaimsService.decide_roster_request(
        historical.url, ids["Beta"], request.id, **ORGANIZER, approve=True
    ).unwrap()
    assert _roster_statuses(historical.url, roster_player.id) == {
        (ids["Alpha"], "REJECTED"),
        (ids["Beta"], "CONFIRMED"),
    }


@pytest.mark.unit
def test_inviting_a_player_keeps_their_request_for_another_team(historical, roster_player):
    historical.published = True
    db.session.commit()
    ids = team_ids(historical)
    HistoricalClaimsService.request_roster(
        historical.url, player_id=roster_player.id, team_id=ids["Beta"], jersey_name="New", jersey_number="2"
    ).unwrap()
    HistoricalClaimsService.invite_player(
        historical.url, ids["Alpha"], **ORGANIZER, player_id=roster_player.id, jersey_name=None, jersey_number=None
    ).unwrap()

    assert _roster_statuses(historical.url, roster_player.id) == {
        (ids["Alpha"], "INVITED"),
        (ids["Beta"], "REQUESTED"),
    }


@pytest.mark.unit
def test_teams_can_have_shortnames(organizer):
    tournament = HistoricalRecordsService.create_tournament(
        **ORGANIZER,
        name="Old Cup",
        url="old-cup",
        location="",
        start_date="2019-06-01",
        end_date="",
        team_names=[{"name": "Alpha Squad", "shortname": "ALP"}, "Beta"],
    ).unwrap()
    registrations = {
        registration.pseudonym: registration for registration in team_registrations_for_tournament(tournament)
    }
    assert registrations["Alpha Squad"].shortname == "ALP"
    assert registrations["Beta"].shortname is None
    listed = {team["name"]: team["shortname"] for team in HistoricalRecordsService.editor_state(tournament)["teams"]}
    assert listed["Alpha Squad"] == "ALP"

    renamed = HistoricalRecordsService.rename_team(
        tournament.url,
        **ORGANIZER,
        team_id=registrations["Alpha Squad"].team,
        name="Alpha Squad",
        shortname="AS",
        update_shortname=True,
    ).unwrap()
    assert renamed.shortname == "AS"

    duplicate = HistoricalRecordsService.add_teams(
        tournament.url, **ORGANIZER, names=[{"name": "Other", "shortname": "as"}]
    )
    assert "Short name already used" in duplicate.unwrap_err().message


@pytest.mark.unit
def test_teams_manage_only_their_own_roster(historical, real_team, roster_player):
    ids = team_ids(historical)
    invitation = HistoricalClaimsService.invite_team_to_claim(
        historical.url, **ORGANIZER, placeholder_team_id=ids["Alpha"], invited_team_id=real_team.id
    ).unwrap()
    HistoricalClaimsService.respond_to_claim(invitation.id, team_id=real_team.id, accept=True).unwrap()
    team_actor = {"actor_user_id": real_team.id, "actor_user_type": "team"}

    own = HistoricalClaimsService.invite_player(
        historical.url, real_team.id, **team_actor, player_id=roster_player.id, jersey_name=None, jersey_number=None
    )
    other = HistoricalClaimsService.roster(historical.url, ids["Beta"], **team_actor)

    assert own.is_ok()
    assert other.unwrap_err().status_code == 403


@pytest.mark.unit
def test_historical_tournaments_reject_organizer_registration(historical, real_team, roster_player):
    from app.services.registration_service import RegistrationService

    team_result = RegistrationService.register_team_as_to(historical.url, **ORGANIZER, team_id=real_team.id)
    player_result = RegistrationService.register_player_as_to(
        historical.url, **ORGANIZER, player_id=roster_player.id, team_id=None
    )

    assert "records editor" in team_result.unwrap_err().message
    assert "roster" in player_result.unwrap_err().message
    assert TeamRegistration.query.filter_by(event=historical.url, team=real_team.id).count() == 0


@pytest.mark.unit
def test_unclaim_moves_the_spot_back_onto_a_placeholder(historical, real_team, roster_player):
    ids = team_ids(historical)
    save_bracket(historical)
    invited = HistoricalClaimsService.invite_player(
        historical.url, ids["Alpha"], **ORGANIZER, player_id=roster_player.id, jersey_name="Runner", jersey_number="7"
    ).unwrap()
    HistoricalClaimsService.respond_to_roster_invitation(invited.id, player_id=roster_player.id, accept=True).unwrap()
    invitation = HistoricalClaimsService.invite_team_to_claim(
        historical.url, **ORGANIZER, placeholder_team_id=ids["Alpha"], invited_team_id=real_team.id
    ).unwrap()
    HistoricalClaimsService.respond_to_claim(invitation.id, team_id=real_team.id, accept=True).unwrap()

    HistoricalClaimsService.unclaim_team(
        historical.url, real_team.id, actor_user_id=real_team.id, actor_user_type="team"
    ).unwrap()

    restored = team_ids(historical)["Alpha"]
    assert restored.startswith(PLACEHOLDER_ID_PREFIX)
    assert restored != real_team.id
    assert Team.query.get(real_team.id).placeholder_event is None
    final = match_named(historical, "Final")
    assert final.team1 == restored
    assert final.team1_initial == "Semi 1::winner"
    registration = PlayerRegistration.query.filter_by(event=historical.url, player=roster_player.id).one()
    assert registration.team == restored
    assert registration.status == RegistrationStatus.CONFIRMED


@pytest.mark.unit
def test_claim_and_roster_requests(historical, real_team, roster_player):
    ids = team_ids(historical)
    hidden = HistoricalClaimsService.request_claim(
        historical.url, team_id=real_team.id, placeholder_team_id=ids["Alpha"]
    )
    assert "not published" in hidden.unwrap_err().message
    assert HistoricalClaimsService.pending_invitations(real_team.id, "team")["team_claims"] == []

    historical.published = True
    db.session.commit()
    HistoricalClaimsService.request_claim(
        historical.url, team_id=real_team.id, placeholder_team_id=ids["Alpha"]
    ).unwrap()
    [outgoing] = HistoricalClaimsService.pending_invitations(real_team.id, "team")["outgoing_team_requests"]
    assert outgoing["placeholder_name"] == "Alpha"

    HistoricalClaimsService.decide_claim_request(historical.url, outgoing["id"], **ORGANIZER, approve=True).unwrap()
    assert team_ids(historical)["Alpha"] == real_team.id

    HistoricalClaimsService.request_roster(
        historical.url,
        player_id=roster_player.id,
        team_id=real_team.id,
        jersey_name="Runner",
        jersey_number="4",
    ).unwrap()
    [incoming] = HistoricalClaimsService.pending_invitations(real_team.id, "team")["incoming_roster_requests"]
    HistoricalClaimsService.decide_roster_request(
        historical.url,
        real_team.id,
        incoming["id"],
        actor_user_id=real_team.id,
        actor_user_type="team",
        approve=True,
    ).unwrap()
    registration = PlayerRegistration.query.filter_by(event=historical.url, player=roster_player.id).one()
    assert registration.status == RegistrationStatus.CONFIRMED
    assert registration.jersey_number == "4"


@pytest.mark.unit
def test_invitations_appear_only_once_published(historical, real_team):
    ids = team_ids(historical)
    HistoricalClaimsService.invite_team_to_claim(
        historical.url, **ORGANIZER, placeholder_team_id=ids["Beta"], invited_team_id=real_team.id
    ).unwrap()

    assert HistoricalClaimsService.pending_invitations(real_team.id, "team")["team_claims"] == []

    historical.published = True
    db.session.commit()
    [claim] = HistoricalClaimsService.pending_invitations(real_team.id, "team")["team_claims"]
    assert claim["placeholder_name"] == "Beta"
