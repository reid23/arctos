"""Tests for tags defined by ASS expressions.

A tag resolves to a team in this order:
1. The manually assigned ``team`` column (an override).
2. Evaluating the tag's ASS ``expression`` to a concrete team.
3. Otherwise unresolved (None).
"""

import pytest

from app.domain.enums import MatchStatus
from app.utils.helpers import resolve_tag_to_team
from models import TO, Field, Match, Tag, db
from tests.utils import login_as


def _make_completed_match(tournament_url, name="Semi A", winner="team1", loser="team2"):
    m = Match(
        name=name,
        event=tournament_url,
        field="Field 1",
        schedule_type="STATIC",
        set_type="SETS",
        nominal_length=60,
        team1=winner,
        team2=loser,
        status=MatchStatus.COMPLETED,
    )
    m.match_winner = "TEAM1"
    db.session.add(m)
    db.session.flush()
    return m


@pytest.mark.unit
def test_tag_expression_resolves_winner_of_completed_match(test_db, tournament, app, seeded_teams):
    _make_completed_match(tournament.url)
    tag = Tag(event=tournament.url, name="SemiWinner", expression="(winner {Semi A})")
    db.session.add(tag)
    db.session.commit()

    assert resolve_tag_to_team("tag::SemiWinner", tournament.url) == "team1"


@pytest.mark.unit
def test_tag_manual_team_overrides_expression(test_db, tournament, app, seeded_teams):
    _make_completed_match(tournament.url)
    tag = Tag(
        event=tournament.url,
        name="SemiWinner",
        team="team3",
        expression="(winner {Semi A})",
    )
    db.session.add(tag)
    db.session.commit()

    # Manual assignment wins over the expression (which would say team1).
    assert resolve_tag_to_team("tag::SemiWinner", tournament.url) == "team3"


@pytest.mark.unit
def test_tag_expression_unresolved_while_match_incomplete(test_db, tournament, app, seeded_teams):
    m = Match(
        name="Semi A",
        event=tournament.url,
        field="Field 1",
        schedule_type="STATIC",
        set_type="SETS",
        nominal_length=60,
        team1="team1",
        team2="team2",
        status=MatchStatus.NOT_STARTED,
    )
    db.session.add(m)
    tag = Tag(event=tournament.url, name="SemiWinner", expression="(winner {Semi A})")
    db.session.add(tag)
    db.session.commit()

    assert resolve_tag_to_team("tag::SemiWinner", tournament.url) is None


@pytest.mark.unit
def test_cyclic_tag_expressions_resolve_to_none(test_db, tournament, app, seeded_teams):
    tag_a = Tag(event=tournament.url, name="A", expression="[tag::B]")
    tag_b = Tag(event=tournament.url, name="B", expression="[tag::A]")
    db.session.add_all([tag_a, tag_b])
    db.session.commit()

    assert resolve_tag_to_team("tag::A", tournament.url) is None
    assert resolve_tag_to_team("tag::B", tournament.url) is None


@pytest.mark.unit
def test_self_referencing_tag_expression_resolves_to_none(test_db, tournament, app, seeded_teams):
    tag = Tag(event=tournament.url, name="Loop", expression="[tag::Loop]")
    db.session.add(tag)
    db.session.commit()

    assert resolve_tag_to_team("tag::Loop", tournament.url) is None


@pytest.mark.unit
def test_tag_expression_chains_through_other_tag(test_db, tournament, app, seeded_teams):
    _make_completed_match(tournament.url)
    inner = Tag(event=tournament.url, name="Inner", expression="(winner {Semi A})")
    outer = Tag(event=tournament.url, name="Outer", expression="[tag::Inner]")
    db.session.add_all([inner, outer])
    db.session.commit()

    assert resolve_tag_to_team("tag::Outer", tournament.url) == "team1"


@pytest.mark.unit
def test_tag_expression_error_resolves_to_none(test_db, tournament, app, seeded_teams):
    # Unbalanced expression stored directly in the DB (bypassing write-time
    # validation) must resolve to None, not raise.
    tag = Tag(event=tournament.url, name="Broken", expression="(winner {Semi A}")
    db.session.add(tag)
    db.session.commit()

    assert resolve_tag_to_team("tag::Broken", tournament.url) is None


@pytest.mark.unit
def test_slot_resolved_uses_tag_expression(test_db, tournament, app, seeded_teams):
    from app.utils.scheduling import _slot_resolved

    _make_completed_match(tournament.url)
    tag = Tag(event=tournament.url, name="SemiWinner", expression="(winner {Semi A})")
    unresolved = Tag(event=tournament.url, name="Nothing")
    db.session.add_all([tag, unresolved])
    db.session.commit()

    assert _slot_resolved(None, "tag::SemiWinner", tournament.url, {}) is True
    assert _slot_resolved(None, "tag::Nothing", tournament.url, {}) is False


class TestTagExpressionWriteValidation:
    """Write-time validation via the tag CRUD endpoints."""

    @pytest.fixture(autouse=True)
    def _setup(self, app, client, test_db, tournament, player, seeded_teams):
        self.client = client
        self.url = tournament.url
        db.session.add(TO(user_id=player.id, user_type="player", event=self.url))
        db.session.add(Field(event=self.url, name="Field 1"))
        db.session.commit()
        login_as(client, player)

    def test_create_tag_with_valid_team_expression(self):
        _make_completed_match(self.url)
        db.session.commit()
        resp = self.client.post(
            f"/_api/tournaments/{self.url}/tags",
            json={"name": "SemiWinner", "expression": "(winner {Semi A})"},
        )
        assert resp.status_code == 200, resp.get_json()
        tag = Tag.query.filter_by(event=self.url, name="SemiWinner").first()
        assert tag.expression == "(winner {Semi A})"

    def test_create_tag_rejects_non_team_expression(self):
        resp = self.client.post(
            f"/_api/tournaments/{self.url}/tags",
            json={"name": "Bad", "expression": "(+ 1 2)"},
        )
        assert resp.status_code == 400
        assert "TEAM" in resp.get_json()["error"]

    def test_create_tag_rejects_unparseable_expression(self):
        resp = self.client.post(
            f"/_api/tournaments/{self.url}/tags",
            json={"name": "Bad", "expression": "(winner {Oops"},
        )
        assert resp.status_code == 400

    def test_create_tag_rejects_unknown_match_reference(self):
        resp = self.client.post(
            f"/_api/tournaments/{self.url}/tags",
            json={"name": "Bad", "expression": "(winner {No Such Match})"},
        )
        assert resp.status_code == 400
        assert "Unknown match" in resp.get_json()["error"]

    def test_update_tags_sets_and_clears_expression(self):
        _make_completed_match(self.url)
        tag = Tag(event=self.url, name="SemiWinner")
        db.session.add(tag)
        db.session.commit()
        tag_id = tag.id

        resp = self.client.post(
            f"/_api/tournaments/{self.url}/update-tags",
            json={"tag_id": tag_id, "expression": "(winner {Semi A})"},
        )
        assert resp.status_code == 200, resp.get_json()
        assert Tag.query.get(tag_id).expression == "(winner {Semi A})"

        # Empty expression clears it.
        resp = self.client.post(
            f"/_api/tournaments/{self.url}/update-tags",
            json={"tag_id": tag_id, "expression": ""},
        )
        assert resp.status_code == 200
        assert Tag.query.get(tag_id).expression is None

    def test_update_tags_rejects_bool_expression(self):
        tag = Tag(event=self.url, name="SemiWinner")
        db.session.add(tag)
        db.session.commit()

        resp = self.client.post(
            f"/_api/tournaments/{self.url}/update-tags",
            json={"tag_id": tag.id, "expression": "(== 1 1)"},
        )
        assert resp.status_code == 400
        assert "TEAM" in resp.get_json()["error"]

    def test_update_tags_expression_fills_matches(self):
        """Setting a resolving expression eagerly fills matches that use the tag."""
        _make_completed_match(self.url)
        tag = Tag(event=self.url, name="SemiWinner")
        db.session.add(tag)
        m = Match(
            name="Final",
            event=self.url,
            field="Field 1",
            schedule_type="STATIC",
            set_type="SETS",
            nominal_length=60,
            team1_initial="tag::SemiWinner",
            team2_initial="team3",
            team2="team3",
            status=MatchStatus.NOT_STARTED,
        )
        db.session.add(m)
        db.session.commit()
        match_id = m.uuid

        resp = self.client.post(
            f"/_api/tournaments/{self.url}/update-tags",
            json={"tag_id": tag.id, "expression": "(winner {Semi A})"},
        )
        assert resp.status_code == 200, resp.get_json()
        assert Match.query.get(match_id).team1 == "team1"
