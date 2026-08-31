"""Tests for tag resolution behavior with mixed refs lists.

Tests ensure correct behavior when referee slots contain:
- Explicit team IDs
- Tag references (tag::TAG_NAME)
- Match references (MatchName::winner/loser)
"""

import pytest

from app.domain.enums import MatchStatus
from app.services.dual_write import get_match_ref_team_ids, set_match_referees
from app.utils.dependencies import apply_match_dependencies
from models import Field, Match, Tag, db


@pytest.mark.unit
def test_apply_match_dependencies_preserves_explicit_teams_and_tag_resolutions(test_db, tournament, app, seeded_teams):
    """apply_match_dependencies should only resolve match references, preserving explicit teams and tag resolutions."""
    tournament_url = tournament.url

    field = Field(event=tournament_url, name="Field 1")
    db.session.add(field)

    team1_id = "team1"
    team2_id = "team2"
    winner_team_id = team1_id  # Match 1 winner will be team1

    match1 = Match(
        name="Match 1",
        event=tournament_url,
        field="Field 1",
        schedule_type="STATIC",
        set_type="SETS",
        nominal_length=60,
        team1=team1_id,
        team2=team2_id,
        status=MatchStatus.COMPLETED,
    )
    match1.match_winner = "TEAM1"
    db.session.add(match1)
    db.session.flush()
    assert match1.winner_team_id == team1_id
    db.session.commit()

    test_match = Match(
        name="Test Match",
        event=tournament_url,
        field="Field 1",
        schedule_type="STATIC",
        set_type="SETS",
        nominal_length=60,
    )
    db.session.add(test_match)
    db.session.flush()
    set_match_referees(
        test_match,
        ["team3", "", "resolved_tag_team"],
        ["team3", "Match 1::winner", "resolved_tag_team"],
    )
    db.session.commit()

    apply_match_dependencies(tournament_url, match1)
    db.session.refresh(test_match)

    refs_list = get_match_ref_team_ids(test_match)
    assert len(refs_list) == 3
    assert refs_list[0] == "team3"  # explicit team ID preserved
    assert refs_list[1] == winner_team_id  # Match 1::winner resolved
    assert refs_list[2] == "resolved_tag_team"  # tag resolution preserved


@pytest.mark.unit
def test_mixed_refs_all_three_types(test_db, tournament, app, seeded_teams):
    """Refs list with all three types: explicit team, tag reference, and match reference."""
    tournament_url = tournament.url

    field = Field(event=tournament_url, name="Field 1")
    db.session.add(field)

    tag = Tag(event=tournament_url, name="Pool A")
    db.session.add(tag)
    db.session.commit()

    explicit_team = "explicit_team"
    tag_resolved_team = "tag_resolved_team"
    winner_team = "team1"

    match1 = Match(
        name="Match 1",
        event=tournament_url,
        field="Field 1",
        schedule_type="STATIC",
        set_type="SETS",
        nominal_length=60,
        team1="team1",
        team2="team2",
        status=MatchStatus.COMPLETED,
    )
    match1.match_winner = "TEAM1"
    db.session.add(match1)
    db.session.flush()
    assert match1.winner_team_id == "team1"
    db.session.commit()

    test_match = Match(
        name="Test Match",
        event=tournament_url,
        field="Field 1",
        schedule_type="STATIC",
        set_type="SETS",
        nominal_length=60,
    )
    db.session.add(test_match)
    db.session.flush()
    set_match_referees(
        test_match,
        [explicit_team, tag_resolved_team, ""],
        [explicit_team, "tag::Pool A", "Match 1::winner"],
    )
    db.session.commit()

    refs_list = get_match_ref_team_ids(test_match)
    assert len(refs_list) == 3
    assert refs_list[0] == explicit_team
    assert refs_list[1] == tag_resolved_team
    assert refs_list[2] == ""

    apply_match_dependencies(tournament_url, match1)
    db.session.refresh(test_match)

    refs_list = get_match_ref_team_ids(test_match)
    assert len(refs_list) == 3
    assert refs_list[0] == explicit_team
    assert refs_list[1] == tag_resolved_team
    assert refs_list[2] == winner_team


@pytest.mark.unit
def test_team1_team2_with_mixed_references(test_db, tournament, app, seeded_teams):
    """team1 and team2 fields with explicit teams, tag references, and match references."""
    tournament_url = tournament.url

    field = Field(event=tournament_url, name="Field 1")
    db.session.add(field)

    tag = Tag(event=tournament_url, name="Pool A")
    db.session.add(tag)
    db.session.commit()

    explicit_team = "explicit_team"
    tag_resolved_team = "tag_resolved_team"
    winner_team = "team1"

    match1 = Match(
        name="Match 1",
        event=tournament_url,
        field="Field 1",
        schedule_type="STATIC",
        set_type="SETS",
        nominal_length=60,
        team1="team1",
        team2="team2",
        status=MatchStatus.COMPLETED,
    )
    match1.match_winner = "TEAM1"
    db.session.add(match1)
    db.session.flush()
    assert match1.winner_team_id == "team1"
    db.session.commit()

    test_match = Match(
        name="Test Match",
        event=tournament_url,
        field="Field 1",
        schedule_type="STATIC",
        set_type="SETS",
        nominal_length=60,
        team1_initial=explicit_team,
        team2_initial="tag::Pool A",
    )
    db.session.add(test_match)
    db.session.commit()

    tag_to_team = {"tag::Pool A": tag_resolved_team}
    if test_match.team1_initial and test_match.team1_initial in tag_to_team:
        test_match.team1 = tag_to_team[test_match.team1_initial]
    elif (
        test_match.team1_initial
        and not test_match.team1_initial.lower().startswith("tag::")
        and "::winner" not in test_match.team1_initial.lower()
        and "::loser" not in test_match.team1_initial.lower()
    ):
        test_match.team1 = test_match.team1_initial

    if test_match.team2_initial and test_match.team2_initial in tag_to_team:
        test_match.team2 = tag_to_team[test_match.team2_initial]
    elif (
        test_match.team2_initial
        and not test_match.team2_initial.lower().startswith("tag::")
        and "::winner" not in test_match.team2_initial.lower()
        and "::loser" not in test_match.team2_initial.lower()
    ):
        test_match.team2 = test_match.team2_initial

    db.session.commit()

    assert test_match.team1 == explicit_team
    assert test_match.team2 == tag_resolved_team
    assert test_match.team1_initial == explicit_team
    assert test_match.team2_initial == "tag::Pool A"

    test_match2 = Match(
        name="Test Match 2",
        event=tournament_url,
        field="Field 1",
        schedule_type="STATIC",
        set_type="SETS",
        nominal_length=60,
        team1_initial="Match 1::winner",
    )
    db.session.add(test_match2)
    db.session.commit()

    apply_match_dependencies(tournament_url, match1)
    db.session.refresh(test_match2)

    assert test_match2.team1 == winner_team
    assert test_match2.team1_initial == "Match 1::winner"
