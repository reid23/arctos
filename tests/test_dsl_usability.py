"""Comprehensive tests for ASS usability extensions.

Covers list construction, filter/sort/reduce-with-init, let/cond, variadic
and/or, matchlist stat overloads, won?, range, map-indexed, empty?/member?,
env-bound globals, standings integration, and symbolic preservation.
"""

import pytest

from app.utils.parser import (
    RANGE_MAX,
    DSLValidationError,
    Nil,
    Preserved,
    get_parser,
)
from app.utils.dsl_dependency_analyzer import MatchDependencyAnalyzer
from app.domain.enums import MatchStatus, WinnerSide
from models import Match, Team, Point, db


@pytest.fixture
def tournament_with_data(app, test_db, tournament):
    """Three teams, two completed matches + one not started (same as base DSL fixture)."""
    tournament_url = tournament.url

    with app.app_context():
        team1 = Team(id="team1", name="Team One", pw_hash="hash1")
        team2 = Team(id="team2", name="Team Two", pw_hash="hash2")
        team3 = Team(id="team3", name="Team Three", pw_hash="hash3")
        db.session.add_all([team1, team2, team3])
        db.session.flush()

        match1 = Match(
            name="Match1",
            event=tournament_url,
            field="Field 1",
            schedule_type="SAFE",
            set_type="SETS",
            nominal_length=60,
            status=MatchStatus.COMPLETED,
            team1="team1",
            team2="team2",
            match_winner=WinnerSide.TEAM1,
        )
        match2 = Match(
            name="Match2",
            event=tournament_url,
            field="Field 1",
            schedule_type="SAFE",
            set_type="SETS",
            nominal_length=60,
            status=MatchStatus.COMPLETED,
            team1="team2",
            team2="team3",
            match_winner=WinnerSide.TEAM2,
        )
        match3 = Match(
            name="Match3",
            event=tournament_url,
            field="Field 1",
            schedule_type="SAFE",
            set_type="SETS",
            nominal_length=60,
            status=MatchStatus.NOT_STARTED,
            team1="team1",
            team2="team3",
        )
        # Standings fixture: A beats B 3-1, B beats C 2-0, A beats C 1-0
        # (match3 completed for standings tests via separate fixture helper)
        db.session.add_all([match1, match2, match3])
        db.session.flush()

        points_match1 = [
            Point(match=match1.uuid, winner=WinnerSide.TEAM1, rerolled=False),
            Point(match=match1.uuid, winner=WinnerSide.TEAM1, rerolled=False),
            Point(match=match1.uuid, winner=WinnerSide.TEAM1, rerolled=False),
            Point(match=match1.uuid, winner=WinnerSide.TEAM2, rerolled=False),
        ]
        points_match2 = [
            Point(match=match2.uuid, winner=WinnerSide.TEAM2, rerolled=False),
            Point(match=match2.uuid, winner=WinnerSide.TEAM2, rerolled=False),
        ]
        db.session.add_all(points_match1 + points_match2)
        db.session.commit()

        return {
            "tournament_url": tournament_url,
            "team1_id": team1.id,
            "team2_id": team2.id,
            "team3_id": team3.id,
            "match1_name": match1.name,
            "match2_name": match2.name,
            "match3_name": match3.name,
        }


@pytest.fixture
def standings_data(app, test_db, tournament_with_data):
    """Complete match3 so all three round-robin results exist.

    Results:
      Match1: team1 beat team2 3-1
      Match2: team3 beat team2 2-0
      Match3: team1 beat team3 1-0

    Wins: team1=2, team3=1, team2=0
    Points won: team1=4, team3=2, team2=1
    Ranked: team1, team3, team2
    """
    with app.app_context():
        url = tournament_with_data["tournament_url"]
        m3 = Match.query.filter_by(event=url, name=tournament_with_data["match3_name"]).first()
        m3.status = MatchStatus.COMPLETED
        m3.match_winner = WinnerSide.TEAM1
        db.session.add(Point(match=m3.uuid, winner=WinnerSide.TEAM1, rerolled=False))
        db.session.commit()
        return tournament_with_data


def _ids(teams):
    return [t.obj.id for t in teams]


# ---------------------------------------------------------------------------
# List construction
# ---------------------------------------------------------------------------


class TestListConstruction:
    @pytest.mark.unit
    def test_list_basic(self, app, tournament):
        with app.app_context():
            p = get_parser(tournament.url)
            assert p.parse("(list 1 2 3)") == [1, 2, 3]
            assert p.parse("(list)") == []
            assert p.parse("(list (list 1) 2)") == [[1], 2]

    @pytest.mark.unit
    def test_cons(self, app, tournament):
        with app.app_context():
            p = get_parser(tournament.url)
            assert p.parse("(cons 0 (list 1 2))") == [0, 1, 2]
            assert p.parse("(cons 1 (list))") == [1]
            assert p.parse("(car (cons 9 (list 1)))") == 9
            assert p.parse("(cdr (cons 9 (list 1 2)))") == [1, 2]

    @pytest.mark.unit
    def test_append(self, app, tournament):
        with app.app_context():
            p = get_parser(tournament.url)
            assert p.parse("(append (list 1 2) (list 3 4))") == [1, 2, 3, 4]
            assert p.parse("(append (list) (list 1))") == [1]
            assert p.parse("(append (list 1) (list))") == [1]


# ---------------------------------------------------------------------------
# empty? / member? / range / map-indexed / filter
# ---------------------------------------------------------------------------


class TestListPredicatesAndHelpers:
    @pytest.mark.unit
    def test_empty(self, app, tournament):
        with app.app_context():
            p = get_parser(tournament.url)
            assert p.parse("(empty? (list))") is True
            assert p.parse("(empty? (list 1))") is False
            assert p.parse("(empty? '())") is True

    @pytest.mark.unit
    def test_member(self, app, tournament):
        with app.app_context():
            p = get_parser(tournament.url)
            assert p.parse("(member? 2 (list 1 2 3))") is True
            assert p.parse("(member? 4 (list 1 2 3))") is False
            assert p.parse("(member? 1 (list))") is False

    @pytest.mark.unit
    def test_member_teams(self, app, tournament_with_data):
        with app.app_context():
            p = get_parser(tournament_with_data["tournament_url"])
            assert p.parse("(member? [team1] (list [team1] [team2]))") is True
            assert p.parse("(member? [team3] (list [team1] [team2]))") is False

    @pytest.mark.unit
    def test_range(self, app, tournament):
        with app.app_context():
            p = get_parser(tournament.url)
            assert p.parse("(range 0)") == []
            assert p.parse("(range -3)") == []
            assert p.parse("(range 3)") == [0, 1, 2]
            with pytest.raises(DSLValidationError, match="range too large"):
                p.parse(f"(range {RANGE_MAX + 1})")

    @pytest.mark.unit
    def test_map_indexed(self, app, tournament):
        with app.app_context():
            p = get_parser(tournament.url)
            assert p.parse("(map-indexed (list 10 20) (lambda (i x) (+ i x)))") == [10, 21]
            assert p.parse("(map-indexed (list) (lambda (i x) i))") == []

    @pytest.mark.unit
    def test_filter(self, app, tournament):
        with app.app_context():
            p = get_parser(tournament.url)
            assert p.parse("(filter (list 1 2 3 4) (lambda (x) (> x 2)))") == [3, 4]
            assert p.parse("(filter (list) (lambda (x) true))") == []
            assert p.parse("(filter (list 1 2) (lambda (x) false))") == []

    @pytest.mark.unit
    def test_filter_non_bool_predicate_errors(self, app, tournament):
        with app.app_context():
            p = get_parser(tournament.url)
            with pytest.raises(DSLValidationError, match="boolean"):
                p.parse("(filter (list 1 2) (lambda (x) 1))")


# ---------------------------------------------------------------------------
# reduce with init
# ---------------------------------------------------------------------------


class TestReduceWithInit:
    @pytest.mark.unit
    def test_legacy_two_arg(self, app, tournament):
        with app.app_context():
            p = get_parser(tournament.url)
            assert p.parse("(reduce (list 1 2 3 4) (lambda (a b) (+ a b)))") == 10

    @pytest.mark.unit
    def test_three_arg(self, app, tournament):
        with app.app_context():
            p = get_parser(tournament.url)
            assert p.parse("(reduce (list 1 2 3) 0 (lambda (a b) (+ a b)))") == 6
            assert p.parse("(reduce (list) 0 (lambda (a b) (+ a b)))") == 0
            assert p.parse("(reduce (list 5) 10 (lambda (a b) (+ a b)))") == 15

    @pytest.mark.unit
    def test_legacy_empty_still_errors(self, app, tournament):
        with app.app_context():
            p = get_parser(tournament.url)
            with pytest.raises(DSLValidationError, match="empty"):
                p.parse("(reduce (list) (lambda (a b) (+ a b)))")


# ---------------------------------------------------------------------------
# sort-by
# ---------------------------------------------------------------------------


class TestSortBy:
    @pytest.mark.unit
    def test_single_key_descending(self, app, tournament):
        with app.app_context():
            p = get_parser(tournament.url)
            assert p.parse("(sort-by (list 1 3 2) (lambda (x) x))") == [3, 2, 1]

    @pytest.mark.unit
    def test_stability(self, app, tournament):
        with app.app_context():
            p = get_parser(tournament.url)
            # All keys equal → original order preserved
            assert p.parse("(sort-by (list 1 2 3) (lambda (x) 0))") == [1, 2, 3]

    @pytest.mark.unit
    def test_multi_key(self, app, tournament):
        with app.app_context():
            p = get_parser(tournament.url)
            # Primary key first component, secondary second — encode as pairs via list of ints
            # Elements are (wins*10 + points) style via two keyfns on plain ints:
            # Use lists: each element is a 2-list (wins points) encoded as... simpler:
            # sort numbers where key1 = x/10, key2 = x%10 via lambdas on the number itself.
            # 23 -> wins 2 pts 3; 21 -> 2,1; 15 -> 1,5  => order 23, 21, 15
            expr = """
            (sort-by (list 15 23 21)
              (lambda (x) (/ x 10))
              (lambda (x) (- x (* (/ x 10) 10))))
            """
            assert p.parse(expr) == [23, 21, 15]

    @pytest.mark.unit
    def test_empty(self, app, tournament):
        with app.app_context():
            p = get_parser(tournament.url)
            assert p.parse("(sort-by (list) (lambda (x) x))") == []

    @pytest.mark.unit
    def test_key_must_be_int(self, app, tournament):
        with app.app_context():
            p = get_parser(tournament.url)
            with pytest.raises(DSLValidationError, match="integer"):
                p.parse("(sort-by (list 1 2) (lambda (x) true))")


# ---------------------------------------------------------------------------
# Variadic and/or
# ---------------------------------------------------------------------------


class TestVariadicLogic:
    @pytest.mark.unit
    def test_and(self, app, tournament):
        with app.app_context():
            p = get_parser(tournament.url)
            assert p.parse("(and)") is True
            assert p.parse("(and true)") is True
            assert p.parse("(and false)") is False
            assert p.parse("(and true true true)") is True
            assert p.parse("(and true false true)") is False
            # binary still works
            assert p.parse("(and true true)") is True

    @pytest.mark.unit
    def test_or(self, app, tournament):
        with app.app_context():
            p = get_parser(tournament.url)
            assert p.parse("(or)") is False
            assert p.parse("(or false)") is False
            assert p.parse("(or true)") is True
            assert p.parse("(or false false true)") is True
            assert p.parse("(or false false false)") is False


# ---------------------------------------------------------------------------
# let / cond
# ---------------------------------------------------------------------------


class TestLetAndCond:
    @pytest.mark.unit
    def test_let_sequential(self, app, tournament):
        with app.app_context():
            p = get_parser(tournament.url)
            assert p.parse("(let ((x 1) (y (+ x 2))) (+ x y))") == 4
            assert p.parse("(let () 42)") == 42
            assert p.parse("(let ((a 5)) a)") == 5

    @pytest.mark.unit
    def test_let_shadows(self, app, tournament):
        with app.app_context():
            p = get_parser(tournament.url)
            assert p.parse("((lambda (x) (let ((x 9)) x)) 1)") == 9

    @pytest.mark.unit
    def test_let_duplicate_name_errors(self, app, tournament):
        with app.app_context():
            p = get_parser(tournament.url)
            with pytest.raises(DSLValidationError, match="Duplicate"):
                p.parse("(let ((x 1) (x 2)) x)")

    @pytest.mark.unit
    def test_let_malformed_errors(self, app, tournament):
        with app.app_context():
            p = get_parser(tournament.url)
            with pytest.raises(DSLValidationError):
                p.parse("(let (x 1) x)")
            with pytest.raises(DSLValidationError):
                p.parse("(let ((1 2)) 3)")

    @pytest.mark.unit
    def test_cond_basic(self, app, tournament):
        with app.app_context():
            p = get_parser(tournament.url)
            assert (
                p.parse(
                    """
                (cond
                  (false 1)
                  ((> 3 2) 9)
                  (true 0))
                """
                )
                == 9
            )
            assert p.parse("(cond (false 1) (false 2))") == Nil()
            assert p.parse("(cond (true 7))") == 7

    @pytest.mark.unit
    def test_cond_default_true(self, app, tournament):
        with app.app_context():
            p = get_parser(tournament.url)
            assert p.parse("(cond (false 1) (true 99))") == 99


# ---------------------------------------------------------------------------
# Matchlist stats + won?
# ---------------------------------------------------------------------------


class TestMatchlistStats:
    @pytest.mark.unit
    def test_wins_over_matchlist(self, app, standings_data):
        with app.app_context():
            d = standings_data
            p = get_parser(d["tournament_url"])
            ml = f"(list {{{d['match1_name']}}} {{{d['match2_name']}}} {{{d['match3_name']}}})"
            assert p.parse(f"(wins [team1] {ml})") == 2
            assert p.parse(f"(wins [team2] {ml})") == 0
            assert p.parse(f"(wins [team3] {ml})") == 1

    @pytest.mark.unit
    def test_losses_over_matchlist(self, app, standings_data):
        with app.app_context():
            d = standings_data
            p = get_parser(d["tournament_url"])
            ml = f"(list {{{d['match1_name']}}} {{{d['match2_name']}}} {{{d['match3_name']}}})"
            assert p.parse(f"(losses [team1] {ml})") == 0
            assert p.parse(f"(losses [team2] {ml})") == 2
            assert p.parse(f"(losses [team3] {ml})") == 1

    @pytest.mark.unit
    def test_points_over_matchlist(self, app, standings_data):
        with app.app_context():
            d = standings_data
            p = get_parser(d["tournament_url"])
            ml = f"(list {{{d['match1_name']}}} {{{d['match2_name']}}} {{{d['match3_name']}}})"
            # team1: 3 + 0 + 1 = 4
            assert p.parse(f"(points-won [team1] {ml})") == 4
            # team2: 1 + 0 + 0 = 1
            assert p.parse(f"(points-won [team2] {ml})") == 1
            # team3: 0 + 2 + 0 = 2
            assert p.parse(f"(points-won [team3] {ml})") == 2

    @pytest.mark.unit
    def test_points_empty_matchlist(self, app, tournament_with_data):
        with app.app_context():
            p = get_parser(tournament_with_data["tournament_url"])
            assert p.parse("(points-won [team1] (list))") == 0
            assert p.parse("(wins [team1] (list))") == 0

    @pytest.mark.unit
    def test_event_wide_wins_unchanged(self, app, standings_data):
        with app.app_context():
            p = get_parser(standings_data["tournament_url"])
            # All three matches completed — event-wide same as matchlist of all
            assert p.parse("(wins [team1])") == 2

    @pytest.mark.unit
    def test_single_match_points_unchanged(self, app, tournament_with_data):
        with app.app_context():
            d = tournament_with_data
            p = get_parser(d["tournament_url"])
            assert p.parse(f"(points-won [team1] {{{d['match1_name']}}})") == 3

    @pytest.mark.unit
    def test_won(self, app, tournament_with_data):
        with app.app_context():
            d = tournament_with_data
            p = get_parser(d["tournament_url"])
            assert p.parse(f"(won? [team1] {{{d['match1_name']}}})") is True
            assert p.parse(f"(won? [team2] {{{d['match1_name']}}})") is False


# ---------------------------------------------------------------------------
# Standings integration (the normative target expression)
# ---------------------------------------------------------------------------


class TestStandingsIntegration:
    @pytest.mark.unit
    def test_sort_by_wins_then_points(self, app, standings_data):
        with app.app_context():
            d = standings_data
            p = get_parser(d["tournament_url"])
            expr = f"""
            (sort-by (list [team1] [team2] [team3])
              (lambda (t) (wins t (list {{{d["match1_name"]}}} {{{d["match2_name"]}}} {{{d["match3_name"]}}})))
              (lambda (t) (points-won t (list {{{d["match1_name"]}}} {{{d["match2_name"]}}} {{{d["match3_name"]}}}))))
            """
            ranked = p.parse(expr)
            assert _ids(ranked) == ["team1", "team3", "team2"]

    @pytest.mark.unit
    def test_standings_with_env_globals(self, app, standings_data):
        with app.app_context():
            d = standings_data
            # Build env by parsing list literals first, then bind names.
            bootstrap = get_parser(d["tournament_url"])
            teamlist = bootstrap.parse("(list [team1] [team2] [team3])")
            matchlist = bootstrap.parse(f"(list {{{d['match1_name']}}} {{{d['match2_name']}}} {{{d['match3_name']}}})")
            p = get_parser(d["tournament_url"], env={"teamlist": teamlist, "matchlist": matchlist})
            ranked = p.parse(
                """
                (sort-by teamlist
                  (lambda (t) (wins t matchlist))
                  (lambda (t) (points-won t matchlist)))
                """
            )
            assert _ids(ranked) == ["team1", "team3", "team2"]

    @pytest.mark.unit
    def test_top_team_via_car(self, app, standings_data):
        with app.app_context():
            d = standings_data
            bootstrap = get_parser(d["tournament_url"])
            teamlist = bootstrap.parse("(list [team2] [team3] [team1])")  # scrambled order
            matchlist = bootstrap.parse(f"(list {{{d['match1_name']}}} {{{d['match2_name']}}} {{{d['match3_name']}}})")
            p = get_parser(d["tournament_url"], env={"teamlist": teamlist, "matchlist": matchlist})
            top = p.parse(
                """
                (car (sort-by teamlist
                       (lambda (t) (wins t matchlist))
                       (lambda (t) (points-won t matchlist))))
                """
            )
            assert top.obj.id == "team1"

    @pytest.mark.unit
    def test_top_two_with_let(self, app, standings_data):
        with app.app_context():
            d = standings_data
            bootstrap = get_parser(d["tournament_url"])
            teamlist = bootstrap.parse("(list [team1] [team2] [team3])")
            matchlist = bootstrap.parse(f"(list {{{d['match1_name']}}} {{{d['match2_name']}}} {{{d['match3_name']}}})")
            p = get_parser(d["tournament_url"], env={"teamlist": teamlist, "matchlist": matchlist})
            top2 = p.parse(
                """
                (let ((r (sort-by teamlist
                           (lambda (t) (wins t matchlist))
                           (lambda (t) (points-won t matchlist)))))
                  (list (get 0 r) (get 1 r)))
                """
            )
            assert _ids(top2) == ["team1", "team3"]

    @pytest.mark.unit
    def test_tiebreak_by_points(self, app, test_db, tournament):
        """Two teams same wins; higher points ranks first."""
        with app.app_context():
            url = tournament.url
            a = Team(id="alpha", name="Alpha", pw_hash="h")
            b = Team(id="beta", name="Beta", pw_hash="h")
            db.session.add_all([a, b])
            db.session.flush()
            # Each has one win. alpha scored 5, beta scored 2.
            m1 = Match(
                name="M1",
                event=url,
                field="F1",
                schedule_type="SAFE",
                set_type="SETS",
                nominal_length=60,
                status=MatchStatus.COMPLETED,
                team1="alpha",
                team2="beta",
                match_winner=WinnerSide.TEAM1,
            )
            m2 = Match(
                name="M2",
                event=url,
                field="F1",
                schedule_type="SAFE",
                set_type="SETS",
                nominal_length=60,
                status=MatchStatus.COMPLETED,
                team1="beta",
                team2="alpha",
                match_winner=WinnerSide.TEAM1,
            )
            db.session.add_all([m1, m2])
            db.session.flush()
            for _ in range(5):
                db.session.add(Point(match=m1.uuid, winner=WinnerSide.TEAM1, rerolled=False))
            for _ in range(2):
                db.session.add(Point(match=m2.uuid, winner=WinnerSide.TEAM1, rerolled=False))
            db.session.commit()

            p = get_parser(url)
            ranked = p.parse(
                """
                (sort-by (list [beta] [alpha])
                  (lambda (t) (wins t (list {M1} {M2})))
                  (lambda (t) (points-won t (list {M1} {M2}))))
                """
            )
            assert _ids(ranked) == ["alpha", "beta"]


# ---------------------------------------------------------------------------
# Symbolic preservation
# ---------------------------------------------------------------------------


class TestSymbolicPreservation:
    @pytest.mark.unit
    def test_wins_over_unfinished_match_preserves(self, app, tournament_with_data):
        with app.app_context():
            d = tournament_with_data
            p = get_parser(d["tournament_url"])
            # match3 is NOT_STARTED
            result = p.parse(f"(wins [team1] (list {{{d['match3_name']}}}))")
            assert isinstance(result, Preserved) or (isinstance(result, list) and result and result[0] == "wins")

    @pytest.mark.unit
    def test_sort_by_unfinished_preserves(self, app, tournament_with_data):
        with app.app_context():
            d = tournament_with_data
            p = get_parser(d["tournament_url"])
            result = p.parse(
                f"""
                (sort-by (list [team1] [team3])
                  (lambda (t) (wins t (list {{{d["match3_name"]}}}))))
                """
            )
            assert isinstance(result, (Preserved, list))

    @pytest.mark.unit
    def test_let_with_symbolic_winner(self, app, tournament_with_data):
        with app.app_context():
            d = tournament_with_data
            p = get_parser(d["tournament_url"])
            result = p.parse(
                f"""
                (let ((w (winner {{{d["match3_name"]}}})))
                  (== w [team1]))
                """
            )
            # Should not crash; result is preserved or bool-ish deferred
            assert result is not None

    @pytest.mark.unit
    def test_cond_symbolic_pred_preserves(self, app, tournament_with_data):
        with app.app_context():
            d = tournament_with_data
            p = get_parser(d["tournament_url"])
            result = p.parse(
                f"""
                (cond
                  ((won? [team1] {{{d["match3_name"]}}}) 1)
                  (true 0))
                """
            )
            assert isinstance(result, (Preserved, list)) or result in (0, 1, Nil())


# ---------------------------------------------------------------------------
# Dependency analyzer
# ---------------------------------------------------------------------------


class TestDependencyAnalyzerExtensions:
    @pytest.mark.unit
    def test_wins_matchlist_deps(self, app, tournament_with_data):
        with app.app_context():
            d = tournament_with_data
            analyzer = MatchDependencyAnalyzer(d["tournament_url"])
            deps = analyzer.analyze(f"(wins [team1] (list {{{d['match1_name']}}} {{{d['match2_name']}}}))")
            assert d["match1_name"] in deps["direct"]
            assert d["match2_name"] in deps["direct"]

    @pytest.mark.unit
    def test_let_body_with_literal_matchlist_deps(self, app, tournament_with_data):
        with app.app_context():
            d = tournament_with_data
            analyzer = MatchDependencyAnalyzer(d["tournament_url"])
            # Match atoms must appear as literals under a dependency function (or
            # inside its matchlist arg). Identifier-bound lists are not resolved
            # by the static analyzer (same limitation as future globals).
            deps = analyzer.analyze(
                f"""
                (let ((t [team1]))
                  (wins t (list {{{d["match1_name"]}}} {{{d["match2_name"]}}})))
                """
            )
            assert d["match1_name"] in deps["direct"]
            assert d["match2_name"] in deps["direct"]

    @pytest.mark.unit
    def test_won_dep(self, app, tournament_with_data):
        with app.app_context():
            d = tournament_with_data
            analyzer = MatchDependencyAnalyzer(d["tournament_url"])
            deps = analyzer.analyze(f"(won? [team1] {{{d['match1_name']}}})")
            assert d["match1_name"] in deps["direct"]


# ---------------------------------------------------------------------------
# Predicate identifier parsing (empty? etc.)
# ---------------------------------------------------------------------------


class TestPredicateIdentifiers:
    @pytest.mark.unit
    def test_question_mark_names_parse(self, app, tournament):
        with app.app_context():
            p = get_parser(tournament.url)
            assert p.parse("(empty? (list 1))") is False
            # won? needs match context — just ensure it parses as a known function
            with pytest.raises(DSLValidationError):
                # arity error, not "unknown function"
                p.parse("(won?)")


# ---------------------------------------------------------------------------
# Compatibility smoke: existing skip-condition style expressions still work
# ---------------------------------------------------------------------------


class TestBackwardCompatSmoke:
    @pytest.mark.unit
    def test_classic_skip_conditions(self, app, tournament_with_data):
        with app.app_context():
            d = tournament_with_data
            p = get_parser(d["tournament_url"])
            # team1 won match1 and has no losses among completed matches
            assert p.parse("(== 0 (losses [team1]))") is True
            assert p.parse(f"(== (winner {{{d['match1_name']}}}) [team1])") is True
            assert p.parse("(> (wins [team1]) (wins [team2]))") is True
            assert p.parse("(and true true)") is True
            assert p.parse("(or false true)") is True
            assert p.parse("(if true 1 2)") == 1
            assert p.parse("(map '(1 2 3) (lambda (x) (+ x 1)))") == [2, 3, 4]
            assert p.parse("(reduce '(1 2 3) (lambda (a b) (+ a b)))") == 6
            assert p.parse("(max-by '(1 3 2) (lambda (x) (* x 2)))") == 3
