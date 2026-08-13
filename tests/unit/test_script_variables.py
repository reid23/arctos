"""Tests for tournament-scoped ASS script variables.

Variables are usable as identifiers in any ASS expression evaluated in the
tournament (skip conditions, tag expressions, other variables). They're
seeded into the interpreter environment by ``get_parser(event)``.
"""

import pytest

from app.domain.enums import MatchStatus
from app.utils.helpers import resolve_tag_to_team
from app.utils.parser import get_parser
from models import TO, Field, Match, ScriptVariable, Tag, db
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
def test_variable_usable_in_expression(test_db, tournament, app):
    db.session.add(ScriptVariable(event=tournament.url, name="threshold", expression="(+ 1 2)"))
    db.session.commit()

    parser = get_parser(tournament.url)
    assert parser.parse("(+ threshold 10)") == 13
    assert parser.parse("(> threshold 2)") is True


@pytest.mark.unit
def test_variable_referencing_variable(test_db, tournament, app):
    db.session.add(ScriptVariable(event=tournament.url, name="base", expression="2"))
    db.session.add(ScriptVariable(event=tournament.url, name="doubled", expression="(* base 2)"))
    db.session.commit()

    parser = get_parser(tournament.url)
    assert parser.parse("doubled") == 4
    assert parser.parse("(+ doubled base)") == 6


@pytest.mark.unit
def test_lambda_valued_variable_is_callable(test_db, tournament, app):
    db.session.add(ScriptVariable(event=tournament.url, name="square", expression="(lambda (x) (* x x))"))
    db.session.commit()

    parser = get_parser(tournament.url)
    assert parser.parse("(square 5)") == 25


@pytest.mark.unit
def test_cyclic_variables_stay_unbound_at_eval_time(test_db, tournament, app):
    # Bypass write-time validation; the env builder must not blow up and the
    # cyclic names simply stay unbound.
    db.session.add(ScriptVariable(event=tournament.url, name="a", expression="(+ b 1)"))
    db.session.add(ScriptVariable(event=tournament.url, name="b", expression="(+ a 1)"))
    db.session.add(ScriptVariable(event=tournament.url, name="ok", expression="7"))
    db.session.commit()

    parser = get_parser(tournament.url)
    assert parser.parse("ok") == 7
    # The cyclic variable is an unresolved identifier — arithmetic on it preserves.
    result = parser.parse("(+ a 1)")
    assert not isinstance(result, int)


@pytest.mark.unit
def test_variable_in_skip_condition_changes_solver_behavior(test_db, tournament, app, seeded_teams):
    from app.utils.scheduling import recompute_scheduled_and_nominal_times

    db.session.add(ScriptVariable(event=tournament.url, name="always-skip", expression="true"))
    _make_completed_match(tournament.url)
    m = Match(
        name="Maybe Skipped",
        event=tournament.url,
        field="Field 1",
        schedule_type="SAFE",
        set_type="SETS",
        nominal_length=60,
        team1="team1",
        team2="team2",
        status=MatchStatus.NOT_STARTED,
        skip_condition="always-skip",
    )
    db.session.add(m)
    db.session.commit()
    match_id = m.uuid

    recompute_scheduled_and_nominal_times(tournament.url)
    assert Match.query.get(match_id).status == MatchStatus.SKIPPED


@pytest.mark.unit
def test_tag_expression_using_variable_resolves(test_db, tournament, app, seeded_teams):
    _make_completed_match(tournament.url)
    db.session.add(ScriptVariable(event=tournament.url, name="semi-winner", expression="(winner {Semi A})"))
    db.session.add(Tag(event=tournament.url, name="Champ", expression="semi-winner"))
    db.session.commit()

    assert resolve_tag_to_team("tag::Champ", tournament.url) == "team1"


class TestScriptVariableCrud:
    """Write-time validation via the script-variable endpoints."""

    @pytest.fixture(autouse=True)
    def _setup(self, app, client, test_db, tournament, player, seeded_teams):
        self.client = client
        self.url = tournament.url
        db.session.add(TO(user_id=player.id, user_type="player", event=self.url))
        db.session.add(Field(event=self.url, name="Field 1"))
        db.session.commit()
        login_as(client, player)

    def _create(self, name, expression):
        return self.client.post(
            f"/_api/tournaments/{self.url}/script-variables",
            json={"name": name, "expression": expression},
        )

    def test_create_and_list(self):
        resp = self._create("threshold", "(+ 1 2)")
        assert resp.status_code == 200, resp.get_json()
        var_id = resp.get_json()["id"]

        resp = self.client.get(f"/_api/tournaments/{self.url}/script-variables")
        assert resp.status_code == 200
        rows = resp.get_json()["script_variables"]
        assert rows == [{"id": var_id, "name": "threshold", "expression": "(+ 1 2)"}]

    def test_builtin_name_collision_rejected(self):
        for name in ("winner", "if", "lambda", "quote", "true", "nil", "+"):
            resp = self._create(name, "1")
            assert resp.status_code == 400, name
            assert "builtin" in resp.get_json()["error"]

    def test_invalid_identifier_rejected(self):
        for name in ("1abc", "has space", "-9", "a]b", "tag::x", ""):
            resp = self._create(name, "1")
            assert resp.status_code == 400, name

    def test_duplicate_name_rejected(self):
        assert self._create("x", "1").status_code == 200
        resp = self._create("x", "2")
        assert resp.status_code == 400
        assert "already exists" in resp.get_json()["error"]

    def test_unparseable_expression_rejected(self):
        resp = self._create("bad", "(+ 1")
        assert resp.status_code == 400

    def test_self_reference_rejected(self):
        resp = self._create("loop", "(+ loop 1)")
        assert resp.status_code == 400
        assert "Cyclic" in resp.get_json()["error"]

    def test_cycle_via_update_rejected(self):
        assert self._create("a", "1").status_code == 200
        resp = self._create("b", "(+ a 1)")
        assert resp.status_code == 200
        a_id = ScriptVariable.query.filter_by(event=self.url, name="a").first().id

        # Updating a to reference b closes the cycle a -> b -> a.
        resp = self.client.put(
            f"/_api/tournaments/{self.url}/script-variables/{a_id}",
            json={"expression": "(+ b 1)"},
        )
        assert resp.status_code == 400
        assert "Cyclic" in resp.get_json()["error"]

    def test_update_and_delete(self):
        resp = self._create("x", "1")
        var_id = resp.get_json()["id"]

        resp = self.client.put(
            f"/_api/tournaments/{self.url}/script-variables/{var_id}",
            json={"name": "y", "expression": "(+ 1 1)"},
        )
        assert resp.status_code == 200, resp.get_json()
        var = ScriptVariable.query.get(var_id)
        assert (var.name, var.expression) == ("y", "(+ 1 1)")

        resp = self.client.delete(f"/_api/tournaments/{self.url}/script-variables/{var_id}")
        assert resp.status_code == 200
        assert ScriptVariable.query.get(var_id) is None

    def test_validate_dsl_accepts_variable(self):
        assert self._create("threshold", "(+ 1 2)").status_code == 200
        resp = self.client.post(
            f"/_api/{self.url}/validate-dsl",
            json={"expression": "(> threshold 2)"},
        )
        data = resp.get_json()
        assert data["valid"] is True
        assert data["result_type"] == ["BOOL"]

    def test_variable_with_team_type_infers_through(self):
        _make_completed_match(self.url)
        db.session.commit()
        assert self._create("champ", "(winner {Semi A})").status_code == 200
        resp = self.client.post(
            f"/_api/{self.url}/validate-dsl",
            json={"expression": "champ"},
        )
        data = resp.get_json()
        assert data["valid"] is True
        assert data["result_type"] == ["TEAM"]
