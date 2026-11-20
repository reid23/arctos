"""
Comprehensive tests for the DSL parser used in skip-condition expressions.
Tests all language features including arithmetic, logical operations,
team/match operations, lambdas, and list operations.
"""

import pytest

from app.utils.parser import get_parser, DSLValidationError, Nil, _infer_types
from app.domain.enums import MatchStatus, WinnerSide
from models import Match, Team, Point, Tag, db


@pytest.fixture
def tournament_with_data(app, test_db, tournament):
    """Create a tournament with teams, matches, and points for testing."""
    tournament_url = tournament.url

    with app.app_context():
        # Create teams
        team1 = Team(id="team1", name="Team One", pw_hash="hash1")
        team2 = Team(id="team2", name="Team Two", pw_hash="hash2")
        team3 = Team(id="team3", name="Team Three", pw_hash="hash3")
        db.session.add_all([team1, team2, team3])
        db.session.flush()

        # Create matches
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
        db.session.add_all([match1, match2, match3])
        db.session.flush()

        # Create points for match1 (team1 wins 3-1)
        points_match1 = [
            Point(match=match1.uuid, winner=WinnerSide.TEAM1, rerolled=False),
            Point(match=match1.uuid, winner=WinnerSide.TEAM1, rerolled=False),
            Point(match=match1.uuid, winner=WinnerSide.TEAM1, rerolled=False),
            Point(match=match1.uuid, winner=WinnerSide.TEAM2, rerolled=False),
        ]

        # Create points for match2 (team3 wins 2-0)
        points_match2 = [
            Point(match=match2.uuid, winner=WinnerSide.TEAM2, rerolled=False),
            Point(match=match2.uuid, winner=WinnerSide.TEAM2, rerolled=False),
        ]

        db.session.add_all(points_match1 + points_match2)
        db.session.commit()

        # Store names and UUIDs as strings to avoid DetachedInstanceError
        return {
            "tournament_url": tournament_url,
            "team1_id": team1.id,
            "team2_id": team2.id,
            "team3_id": team3.id,
            "match1_name": match1.name,
            "match1_uuid": match1.uuid,
            "match2_name": match2.name,
            "match2_uuid": match2.uuid,
            "match3_name": match3.name,
            "match3_uuid": match3.uuid,
        }


class TestBasicOperations:
    """Test basic arithmetic and comparison operations."""

    @pytest.mark.unit
    def test_arithmetic_operations(self, app, tournament):
        with app.app_context():
            parser = get_parser(tournament.url)

            assert parser.parse("(+ 2 3)") == 5
            assert parser.parse("(- 10 4)") == 6
            assert parser.parse("(* 3 4)") == 12
            assert parser.parse("(/ 15 3)") == 5
            assert parser.parse("(/ 10 3)") == 3  # Integer division

    @pytest.mark.unit
    def test_comparison_operations(self, app, tournament):
        with app.app_context():
            parser = get_parser(tournament.url)

            assert parser.parse("(> 5 3)") is True
            assert parser.parse("(> 3 5)") is False
            assert parser.parse("(< 2 5)") is True
            assert parser.parse("(>= 5 5)") is True
            assert parser.parse("(<= 3 5)") is True
            assert parser.parse("(== 5 5)") is True
            assert parser.parse("(== 5 3)") is False

    @pytest.mark.unit
    def test_logical_operations(self, app, tournament):
        with app.app_context():
            parser = get_parser(tournament.url)

            assert parser.parse("(or true false)") is True
            assert parser.parse("(or false false)") is False
            assert parser.parse("(and true true)") is True
            assert parser.parse("(and true false)") is False

    @pytest.mark.unit
    def test_division_by_zero(self, app, tournament):
        with app.app_context():
            parser = get_parser(tournament.url)

            with pytest.raises(DSLValidationError, match="Division by zero"):
                parser.parse("(/ 10 0)")

    @pytest.mark.unit
    def test_boolean_literals(self, app, tournament):
        with app.app_context():
            parser = get_parser(tournament.url)

            assert parser.parse("true") is True
            assert parser.parse("false") is False

    @pytest.mark.unit
    def test_nil_literal(self, app, tournament):
        with app.app_context():
            parser = get_parser(tournament.url)

            assert parser.parse("nil") == Nil()


class TestConditionalExpressions:
    """Test if expressions."""

    @pytest.mark.unit
    def test_if_true_branch(self, app, tournament):
        with app.app_context():
            parser = get_parser(tournament.url)

            assert parser.parse("(if true 10 20)") == 10

    @pytest.mark.unit
    def test_if_false_branch(self, app, tournament):
        with app.app_context():
            parser = get_parser(tournament.url)

            assert parser.parse("(if false 10 20)") == 20

    @pytest.mark.unit
    def test_if_with_comparison(self, app, tournament):
        with app.app_context():
            parser = get_parser(tournament.url)

            assert parser.parse("(if (> 5 3) 100 200)") == 100
            assert parser.parse("(if (< 5 3) 100 200)") == 200


class TestTeamOperations:
    """Test team-related operations."""

    @pytest.mark.unit
    def test_wins(self, app, tournament_with_data):
        with app.app_context():
            parser = get_parser(tournament_with_data["tournament_url"])

            # team1 won match1
            assert parser.parse("(wins [team1])") == 1
            # team2 won 0 matches (lost match1, lost match2)
            assert parser.parse("(wins [team2])") == 0
            # team3 won match2
            assert parser.parse("(wins [team3])") == 1

    @pytest.mark.unit
    def test_losses(self, app, tournament_with_data):
        with app.app_context():
            parser = get_parser(tournament_with_data["tournament_url"])

            # team1 lost 0 matches
            assert parser.parse("(losses [team1])") == 0
            # team2 lost both matches
            assert parser.parse("(losses [team2])") == 2
            # team3 lost 0 matches
            assert parser.parse("(losses [team3])") == 0

    @pytest.mark.unit
    def test_points_won(self, app, tournament_with_data):
        with app.app_context():
            parser = get_parser(tournament_with_data["tournament_url"])
            match1_name = tournament_with_data["match1_name"]

            # team1 won 3 points in match1
            assert parser.parse(f"(points-won [team1] {{{match1_name}}})") == 3
            # team2 won 1 point in match1
            assert parser.parse(f"(points-lost [team1] {{{match1_name}}})") == 1

    @pytest.mark.unit
    def test_points_won_total(self, app, tournament_with_data):
        with app.app_context():
            parser = get_parser(tournament_with_data["tournament_url"])

            # team1 won 3 points total (all in match1)
            assert parser.parse("(points-won [team1])") == 3
            # team2 won 1 point total (in match1)
            assert parser.parse("(points-won [team2])") == 1
            # team3 won 2 points total (in match2)
            assert parser.parse("(points-won [team3])") == 2


class TestMatchOperations:
    """Test match-related operations."""

    @pytest.mark.unit
    def test_winner(self, app, tournament_with_data):
        with app.app_context():
            parser = get_parser(tournament_with_data["tournament_url"])
            match1_name = tournament_with_data["match1_name"]

            # Match1 winner is team1
            winner = parser.parse(f"(winner {{{match1_name}}})")
            assert hasattr(winner, "obj")
            assert winner.obj.id == "team1"

    @pytest.mark.unit
    def test_loser(self, app, tournament_with_data):
        with app.app_context():
            parser = get_parser(tournament_with_data["tournament_url"])
            match1_name = tournament_with_data["match1_name"]

            # Match1 loser is team2
            loser = parser.parse(f"(loser {{{match1_name}}})")
            assert hasattr(loser, "obj")
            assert loser.obj.id == "team2"

    @pytest.mark.unit
    def test_is_skipped(self, app, tournament_with_data):
        """(is-skipped MATCH) returns True if status SKIPPED, False if IN_PROGRESS/COMPLETED, symbolic otherwise."""
        from app.domain.enums import MatchStatus

        with app.app_context():
            parser = get_parser(tournament_with_data["tournament_url"])
            tournament_url = tournament_with_data["tournament_url"]
            match3_name = tournament_with_data["match3_name"]
            match3 = Match.query.filter_by(event=tournament_url, name=match3_name).first()

            # SKIPPED -> True
            match3.status = MatchStatus.SKIPPED
            db.session.commit()
            assert parser.parse(f"(is-skipped {{{match3_name}}})") is True

            # IN_PROGRESS -> False
            match3.status = MatchStatus.IN_PROGRESS
            db.session.commit()
            assert parser.parse(f"(is-skipped {{{match3_name}}})") is False

            # COMPLETED -> False
            match3.status = MatchStatus.COMPLETED
            db.session.commit()
            assert parser.parse(f"(is-skipped {{{match3_name}}})") is False

            # NOT_STARTED -> symbolic (stays as expression)
            match3.status = MatchStatus.NOT_STARTED
            db.session.commit()
            result = parser.parse(f"(is-skipped {{{match3_name}}})")
            assert isinstance(result, list) and result[0] == "is-skipped"


class TestListOperations:
    """Test list manipulation operations."""

    @pytest.mark.unit
    def test_quote(self, app, tournament):
        with app.app_context():
            parser = get_parser(tournament.url)

            assert parser.parse("'(1 2 3)") == [1, 2, 3]
            assert parser.parse("(quote (1 2 3))") == [1, 2, 3]

    @pytest.mark.unit
    def test_car(self, app, tournament):
        with app.app_context():
            parser = get_parser(tournament.url)

            assert parser.parse("(car '(1 2 3))") == 1

    @pytest.mark.unit
    def test_cdr(self, app, tournament):
        with app.app_context():
            parser = get_parser(tournament.url)

            result = parser.parse("(cdr '(1 2 3))")
            assert result == [2, 3]

    @pytest.mark.unit
    def test_get(self, app, tournament):
        with app.app_context():
            parser = get_parser(tournament.url)

            assert parser.parse("(get 0 '(10 20 30))") == 10
            assert parser.parse("(get 1 '(10 20 30))") == 20
            assert parser.parse("(get 2 '(10 20 30))") == 30
            # Out of bounds returns nil
            assert parser.parse("(get 5 '(10 20 30))") == Nil()

    @pytest.mark.unit
    def test_len(self, app, tournament):
        with app.app_context():
            parser = get_parser(tournament.url)

            assert parser.parse("(len '(1 2 3))") == 3
            assert parser.parse("(len '())") == 0

    @pytest.mark.unit
    def test_or_default(self, app, tournament):
        with app.app_context():
            parser = get_parser(tournament.url)

            assert parser.parse("(or-default 10 20)") == 10
            assert parser.parse("(or-default nil 20)") == 20

    @pytest.mark.unit
    def test_max(self, app, tournament):
        with app.app_context():
            parser = get_parser(tournament.url)

            assert parser.parse("(max '(1 5 3 2))") == 5

    @pytest.mark.unit
    def test_min(self, app, tournament):
        with app.app_context():
            parser = get_parser(tournament.url)

            assert parser.parse("(min '(5 1 3 2))") == 1


class TestLambdaFunctions:
    """Test lambda function definitions and calls."""

    @pytest.mark.unit
    def test_lambda_identity(self, app, tournament):
        with app.app_context():
            parser = get_parser(tournament.url)

            # Identity function
            result = parser.parse("((lambda (x) x) 42)")
            assert result == 42

    @pytest.mark.unit
    def test_lambda_add(self, app, tournament):
        with app.app_context():
            parser = get_parser(tournament.url)

            # Add 5 to a number
            result = parser.parse("((lambda (x) (+ x 5)) 10)")
            assert result == 15

    @pytest.mark.unit
    def test_lambda_multiple_params(self, app, tournament):
        with app.app_context():
            parser = get_parser(tournament.url)

            # Function with two parameters
            result = parser.parse("((lambda (x y) (+ x y)) 3 4)")
            assert result == 7

    @pytest.mark.unit
    def test_map(self, app, tournament):
        with app.app_context():
            parser = get_parser(tournament.url)

            # Map add 1 to each element
            result = parser.parse("(map '(1 2 3) (lambda (x) (+ x 1)))")
            assert result == [2, 3, 4]

    @pytest.mark.unit
    def test_reduce(self, app, tournament):
        with app.app_context():
            parser = get_parser(tournament.url)

            # Sum a list
            result = parser.parse("(reduce '(1 2 3 4) (lambda (acc x) (+ acc x)))")
            assert result == 10

    @pytest.mark.unit
    def test_max_by(self, app, tournament):
        with app.app_context():
            parser = get_parser(tournament.url)

            # Find element with maximum value when doubled
            result = parser.parse("(max-by '(1 3 2) (lambda (x) (* x 2)))")
            assert result == 3  # 3*2=6 is the max

    @pytest.mark.unit
    def test_min_by(self, app, tournament):
        with app.app_context():
            parser = get_parser(tournament.url)

            # Find element with minimum value when doubled
            result = parser.parse("(min-by '(3 1 2) (lambda (x) (* x 2)))")
            assert result == 1  # 1*2=2 is the min


class TestTagReferences:
    """Test tag-based team references."""

    @pytest.mark.unit
    def test_tag_reference(self, app, tournament_with_data):
        with app.app_context():
            tournament_url = tournament_with_data["tournament_url"]
            team1_id = tournament_with_data["team1_id"]

            # Create a tag pointing to team1
            tag = Tag(event=tournament_url, name="PoolA", team=team1_id)
            db.session.add(tag)
            db.session.commit()

            parser = get_parser(tournament_url)

            # Tag reference should resolve to team1
            team = parser.parse("[tag::PoolA]")
            assert hasattr(team, "obj")
            assert team.obj.id == "team1"

    @pytest.mark.unit
    def test_tag_reference_unset(self, app, tournament_with_data):
        with app.app_context():
            tournament_url = tournament_with_data["tournament_url"]

            # Create a tag without a team
            tag = Tag(event=tournament_url, name="PoolB", team=None)
            db.session.add(tag)
            db.session.commit()

            parser = get_parser(tournament_url)

            # Unset tag should return symbolic team
            result = parser.parse("[tag::PoolB]")
            # Should be a preserved expression or symbolic
            assert isinstance(result, list) or hasattr(result, "literal")


class TestMatchReferences:
    """Test match-based team references (winner/loser)."""

    @pytest.mark.unit
    def test_winner_reference(self, app, tournament_with_data):
        with app.app_context():
            parser = get_parser(tournament_with_data["tournament_url"])
            match1_name = tournament_with_data["match1_name"]

            # Match1::winner should be team1
            team = parser.parse(f"[{match1_name}::winner]")
            assert hasattr(team, "obj")
            assert team.obj.id == "team1"

    @pytest.mark.unit
    def test_loser_reference(self, app, tournament_with_data):
        with app.app_context():
            parser = get_parser(tournament_with_data["tournament_url"])
            match1_name = tournament_with_data["match1_name"]

            # Match1::loser should be team2
            team = parser.parse(f"[{match1_name}::loser]")
            assert hasattr(team, "obj")
            assert team.obj.id == "team2"


class TestComplexExpressions:
    """Test complex nested expressions."""

    @pytest.mark.unit
    def test_nested_arithmetic(self, app, tournament):
        with app.app_context():
            parser = get_parser(tournament.url)

            assert parser.parse("(+ (* 2 3) (/ 10 2))") == 11
            assert parser.parse("(- (+ 5 3) 2)") == 6

    @pytest.mark.unit
    def test_complex_condition(self, app, tournament_with_data):
        with app.app_context():
            parser = get_parser(tournament_with_data["tournament_url"])

            # Skip if team has 0 losses
            result = parser.parse("(== 0 (losses [team1]))")
            assert result is True  # team1 has 0 losses

            result = parser.parse("(== 0 (losses [team2]))")
            assert result is False  # team2 has 2 losses

    @pytest.mark.unit
    def test_logical_combination(self, app, tournament):
        with app.app_context():
            parser = get_parser(tournament.url)

            assert parser.parse("(and (> 5 3) (< 2 4))") is True
            assert parser.parse("(or (> 1 3) (< 2 4))") is True
            assert parser.parse("(and (> 1 3) (< 2 4))") is False

    @pytest.mark.unit
    def test_nested_if(self, app, tournament):
        with app.app_context():
            parser = get_parser(tournament.url)

            result = parser.parse("(if (> 5 3) (if (< 2 1) 10 20) 30)")
            assert result == 20


class TestSymbolicExpressions:
    """Test expressions with unresolved symbolic values."""

    @pytest.mark.unit
    def test_preserved_expression_with_unresolved_team(self, app, tournament_with_data):
        with app.app_context():
            tournament_url = tournament_with_data["tournament_url"]
            # Create a tag without a team — its team is unknown, so the expression should preserve.
            db.session.add(Tag(event=tournament_url, name="UnsetTagPreserve", team=None))
            db.session.commit()

            parser = get_parser(tournament_url)
            result = parser.parse("(== 0 (losses [tag::UnsetTagPreserve]))")
            assert isinstance(result, list)
            assert result[0] == "=="

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "expression",
        [
            "(map unbound_matches (lambda (m) (winner m)))",
            "(map-indexed unbound_matches (lambda (i m) m))",
            "(filter unbound_matches (lambda (m) (is-skipped m)))",
            "(reduce unbound_matches 0 (lambda (total m) total))",
            "(sort-by unbound_matches (lambda (t) (wins t)))",
            "(len unbound_matches)",
            "(car unbound_matches)",
            "(get 0 unbound_matches)",
            "(member? 1 unbound_matches)",
            "(max unbound_matches)",
        ],
    )
    def test_unbound_list_argument_is_preserved_not_iterated(self, app, tournament, expression):
        with app.app_context():
            parser = get_parser(tournament.url)

            result = parser.parse(expression)

            assert isinstance(result, list)
            assert result[0] == expression[1:].split(" ", 1)[0]
            assert "unbound_matches" in result

    @pytest.mark.unit
    def test_unknown_tag_errors(self, app, tournament_with_data):
        with app.app_context():
            parser = get_parser(tournament_with_data["tournament_url"])
            with pytest.raises(DSLValidationError, match="Tag .* does not exist"):
                parser.parse("[tag::NonExistent]")

    @pytest.mark.unit
    def test_preserved_expression_with_unresolved_match(self, app, tournament_with_data):
        with app.app_context():
            parser = get_parser(tournament_with_data["tournament_url"])

            # Match that doesn't exist should preserve expression
            result = parser.parse("(winner {NonExistentMatch})")
            # Should be a preserved expression (list)
            assert isinstance(result, list)
            assert result[0] == "winner"

    @pytest.mark.unit
    def test_preserved_expression_nested(self, app, tournament_with_data):
        with app.app_context():
            tournament_url = tournament_with_data["tournament_url"]
            # The tag exists but has no team — preserve, don't error.
            db.session.add(Tag(event=tournament_url, name="UnsetTagNested", team=None))
            db.session.commit()

            parser = get_parser(tournament_url)
            result = parser.parse("(== 0 (losses [tag::UnsetTagNested]))")
            assert isinstance(result, list)
            # The inner expression should also be preserved.
            assert isinstance(result[2], list)


class TestErrorHandling:
    """Test error handling for invalid expressions."""

    @pytest.mark.unit
    def test_undefined_symbol(self, app, tournament):
        with app.app_context():
            parser = get_parser(tournament.url)

            with pytest.raises(DSLValidationError, match="Unknown function"):
                parser.parse("(undefined_function 1 2)")

    @pytest.mark.unit
    def test_wrong_argument_count(self, app, tournament):
        with app.app_context():
            parser = get_parser(tournament.url)

            with pytest.raises(DSLValidationError, match="expects.*argument"):
                parser.parse("(+ 1 2 3)")  # + expects 2 args

    @pytest.mark.unit
    def test_wrong_type_arithmetic(self, app, tournament):
        with app.app_context():
            parser = get_parser(tournament.url)

            # This should preserve the expression if it contains unresolved identifiers
            # But if we try to add a boolean to an int, it should error
            with pytest.raises(DSLValidationError, match="must be INT"):
                parser.parse("(+ true 5)")

    @pytest.mark.unit
    def test_empty_list_operations(self, app, tournament):
        with app.app_context():
            parser = get_parser(tournament.url)

            with pytest.raises(DSLValidationError, match="empty list"):
                parser.parse("(car '())")

            with pytest.raises(DSLValidationError, match="empty list"):
                parser.parse("(cdr '())")

    @pytest.mark.unit
    def test_lambda_wrong_arg_count(self, app, tournament):
        with app.app_context():
            parser = get_parser(tournament.url)

            # Lambda expects 1 arg but gets 2
            with pytest.raises(DSLValidationError, match="expects.*argument"):
                parser.parse("((lambda (x) x) 1 2)")


class TestSkipConditionIntegration:
    """Test skip_condition expressions that use is-skipped."""

    @pytest.mark.unit
    def test_skip_condition_with_is_skipped(self, app, tournament_with_data):
        """Parsing a match's skip_condition (is-skipped Other) reflects Other's status."""
        from app.domain.enums import MatchStatus

        with app.app_context():
            parser = get_parser(tournament_with_data["tournament_url"])
            tournament_url = tournament_with_data["tournament_url"]
            match3_name = tournament_with_data["match3_name"]
            match1_name = tournament_with_data["match1_name"]

            # match3's skip_condition: skip if match1 is skipped
            match3 = Match.query.filter_by(event=tournament_url, name=match3_name).first()
            match1 = Match.query.filter_by(event=tournament_url, name=match1_name).first()
            match3.skip_condition = f"(is-skipped {{{match1_name}}})"
            db.session.commit()

            match1.status = MatchStatus.SKIPPED
            db.session.commit()
            result = parser.parse(match3.skip_condition)
            assert result is True

            match1.status = MatchStatus.COMPLETED
            db.session.commit()
            result = parser.parse(match3.skip_condition)
            assert result is False

    @pytest.mark.unit
    def test_skip_condition_or_is_skipped(self, app, tournament_with_data):
        """skip_condition (or (is-skipped A) (is-skipped B)) evaluates from status."""
        from app.domain.enums import MatchStatus

        with app.app_context():
            parser = get_parser(tournament_with_data["tournament_url"])
            tournament_url = tournament_with_data["tournament_url"]
            match1_name = tournament_with_data["match1_name"]
            match2_name = tournament_with_data["match2_name"]
            match3_name = tournament_with_data["match3_name"]

            match3 = Match.query.filter_by(event=tournament_url, name=match3_name).first()
            match3.skip_condition = f"(or (is-skipped {{{match1_name}}}) (is-skipped {{{match2_name}}}))"
            db.session.commit()

            # Neither skipped -> False
            Match.query.filter_by(event=tournament_url, name=match1_name).update({"status": MatchStatus.COMPLETED})
            Match.query.filter_by(event=tournament_url, name=match2_name).update({"status": MatchStatus.COMPLETED})
            db.session.commit()
            result = parser.parse(match3.skip_condition)
            assert result is False

            # match1 skipped -> True
            Match.query.filter_by(event=tournament_url, name=match1_name).update({"status": MatchStatus.SKIPPED})
            db.session.commit()
            result = parser.parse(match3.skip_condition)
            assert result is True


class TestLambdaAdvanced:
    """Test advanced lambda features."""

    @pytest.mark.unit
    def test_nested_lambdas(self, app, tournament):
        with app.app_context():
            parser = get_parser(tournament.url)

            # Apply a function that returns another function
            result = parser.parse("(((lambda (x) (lambda (y) (+ x y))) 5) 3)")
            assert result == 8

    @pytest.mark.unit
    def test_lambda_with_list_operations(self, app, tournament):
        with app.app_context():
            parser = get_parser(tournament.url)

            # Double each element
            result = parser.parse("(map '(1 2 3) (lambda (x) (* x 2)))")
            assert result == [2, 4, 6]

    @pytest.mark.unit
    def test_lambda_filter_pattern(self, app, tournament):
        with app.app_context():
            parser = get_parser(tournament.url)

            # Find elements greater than 2 (using map and filtering concept)
            # Note: We don't have filter, but we can use map with conditional
            # This is a simplified example
            result = parser.parse("(map '(1 2 3 4) (lambda (x) (if (> x 2) x 0)))")
            assert result == [0, 0, 3, 4]


class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    @pytest.mark.unit
    def test_empty_expression(self, app, tournament):
        with app.app_context():
            parser = get_parser(tournament.url)

            # Empty list
            result = parser.parse("()")
            assert result == []

    @pytest.mark.unit
    def test_single_element_list(self, app, tournament):
        with app.app_context():
            parser = get_parser(tournament.url)

            # Single element (not a function call, just a list)
            result = parser.parse("'(42)")
            assert result == [42]

    @pytest.mark.unit
    def test_deeply_nested(self, app, tournament):
        with app.app_context():
            parser = get_parser(tournament.url)

            result = parser.parse("(+ (+ 1 2) (+ 3 4))")
            assert result == 10

    @pytest.mark.unit
    def test_equality_with_different_types(self, app, tournament):
        with app.app_context():
            parser = get_parser(tournament.url)

            assert parser.parse("(== 5 5)") is True
            assert parser.parse("(== true true)") is True
            assert parser.parse("(== true false)") is False
            assert parser.parse("(== nil nil)") is True

    @pytest.mark.unit
    def test_equality_team_by_value_not_identity(self, app, tournament_with_data):
        """(== [Match::winner] [TeamId]) and (== (winner {Match}) [TeamId]) use value equality.
        Same team from different code paths (bracket vs winner call) must compare equal.
        """
        with app.app_context():
            parser = get_parser(tournament_with_data["tournament_url"])
            match1_name = tournament_with_data["match1_name"]

            # Match1 winner is team1: bracket form vs literal must be True
            assert parser.parse(f"(== [{match1_name}::winner] [team1])") is True
            assert parser.parse(f"(== (winner {{{match1_name}}}) [team1])") is True
            # Different team must be False
            assert parser.parse(f"(== [{match1_name}::winner] [team2])") is False


class TestInferTypes:
    """Type inference for the validate-dsl response.

    Concrete values map directly; preserved expressions look up their head
    function's declared return type and recurse where it depends on args
    (`if`, `or-default`).
    """

    @pytest.mark.unit
    def test_concrete_atoms(self, app, tournament_with_data):
        with app.app_context():
            parser = get_parser(tournament_with_data["tournament_url"])
            assert _infer_types(parser.parse("true")) == frozenset({"BOOL"})
            assert _infer_types(parser.parse("false")) == frozenset({"BOOL"})
            assert _infer_types(parser.parse("42")) == frozenset({"INT"})
            assert _infer_types(parser.parse("nil")) == frozenset({"NIL"})
            assert _infer_types(parser.parse("[team1]")) == frozenset({"TEAM"})

    @pytest.mark.unit
    def test_arithmetic_returns_int(self, app, tournament_with_data):
        with app.app_context():
            parser = get_parser(tournament_with_data["tournament_url"])
            assert _infer_types(parser.parse("(+ 1 2)")) == frozenset({"INT"})

    @pytest.mark.unit
    def test_comparison_and_logic_return_bool(self, app, tournament):
        with app.app_context():
            parser = get_parser(tournament.url)
            assert _infer_types(parser.parse("(> 3 2)")) == frozenset({"BOOL"})
            assert _infer_types(parser.parse("(and true false)")) == frozenset({"BOOL"})
            assert _infer_types(parser.parse("(not true)")) == frozenset({"BOOL"})

    @pytest.mark.unit
    def test_winner_loser_return_team(self, app, tournament_with_data):
        with app.app_context():
            parser = get_parser(tournament_with_data["tournament_url"])
            match3_name = tournament_with_data["match3_name"]
            # match3 hasn't started, so winner stays preserved.
            assert _infer_types(parser.parse(f"(winner {{{match3_name}}})")) == frozenset({"TEAM"})

    @pytest.mark.unit
    def test_preserved_unresolved_team(self, app, tournament_with_data):
        """Expressions over a SymbolicTeam stay as preserved-list expressions whose
        head's return type still pins down the resulting type."""
        with app.app_context():
            tournament_url = tournament_with_data["tournament_url"]
            db.session.add(Tag(event=tournament_url, name="UnsetTagInfer", team=None))
            db.session.commit()
            parser = get_parser(tournament_url)
            assert _infer_types(parser.parse("(losses [tag::UnsetTagInfer])")) == frozenset({"INT"})
            assert _infer_types(parser.parse("(== 0 (losses [tag::UnsetTagInfer]))")) == frozenset({"BOOL"})

    @pytest.mark.unit
    def test_if_unions_branches(self, app, tournament_with_data):
        with app.app_context():
            tournament_url = tournament_with_data["tournament_url"]
            db.session.add(Tag(event=tournament_url, name="UnsetTagIf", team=None))
            db.session.commit()
            parser = get_parser(tournament_url)
            # condition is symbolic so the if stays preserved; both branches return INT.
            assert _infer_types(parser.parse("(if (== 0 (losses [tag::UnsetTagIf])) 1 2)")) == frozenset({"INT"})
            # mixed branches → union.
            assert _infer_types(parser.parse("(if (== 0 (losses [tag::UnsetTagIf])) true 0)")) == frozenset(
                {"BOOL", "INT"}
            )

    @pytest.mark.unit
    def test_or_default_unions_non_nil(self, app, tournament_with_data):
        """(or-default val default) — type is val's possible types minus NIL, plus default's types."""
        with app.app_context():
            parser = get_parser(tournament_with_data["tournament_url"])
            assert _infer_types(parser.parse("(or-default nil 5)")) == frozenset({"INT"})
            assert _infer_types(parser.parse("(or-default 5 7)")) == frozenset({"INT"})

    @pytest.mark.unit
    def test_lambda_returns_func(self, app, tournament):
        with app.app_context():
            parser = get_parser(tournament.url)
            assert _infer_types(parser.parse("(lambda (x) (+ x 1))")) == frozenset({"FUNC"})

    @pytest.mark.unit
    def test_quote_returns_list(self, app, tournament):
        with app.app_context():
            parser = get_parser(tournament.url)
            assert _infer_types(parser.parse("'(1 2 3)")) == frozenset({"LIST"})

    @pytest.mark.unit
    def test_get_infers_element_types_from_quoted_list(self, app, tournament_with_data):
        """(get index '(...)) — when get stays preserved (symbolic index), the
        quoted-list element types are still recoverable so or-default can drop the NIL."""
        with app.app_context():
            tournament_url = tournament_with_data["tournament_url"]
            db.session.add(Tag(event=tournament_url, name="UnsetTagGet", team=None))
            db.session.commit()
            parser = get_parser(tournament_url)
            # The original failing example from the bug report — `(wins [tag::UnsetTagGet])`
            # is symbolic so `get` stays preserved; element-type inference makes or-default
            # collapse to {BOOL} instead of {BOOL, UNKNOWN}.
            assert _infer_types(
                parser.parse("(or-default (get (wins [tag::UnsetTagGet]) '(true false true)) false)")
            ) == frozenset({"BOOL"})
            # Without or-default, the preserved get exposes element-types ∪ NIL.
            assert _infer_types(parser.parse("(get (wins [tag::UnsetTagGet]) '(true false 1))")) == frozenset(
                {"BOOL", "INT", "NIL"}
            )
