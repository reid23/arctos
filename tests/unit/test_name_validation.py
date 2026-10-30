"""Reserved punctuation and error handling for match names and team pseudonyms."""

from types import SimpleNamespace

import pytest

from app.error_values import Err, Null, Some
from app.exceptions import ValidationError
from app.serializers.match_schedule_serializer import MatchScheduleSerializer
from app.utils.name_validation import match_name_char_error, team_pseudonym_char_error
from app.utils.scheduling import validate_match_input


@pytest.mark.unit
@pytest.mark.parametrize("name", ["", " ", "0", "Pool A: Final", "Bären – Final"])
def test_match_name_allows_text_without_reserved_punctuation(name):
    assert isinstance(match_name_char_error(name), Null)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("name", "message"),
    [
        ("Pool A, Final", 'Match names cannot contain ",".'),
        ("Pool A::winner", 'Match names cannot contain "::".'),
        ("Pool A/Final", 'Match names cannot contain "/".'),
        ("Pool A::winner,/Final", 'Match names cannot contain ",".'),
        ("Pool A/Final::winner", 'Match names cannot contain "::".'),
    ],
)
def test_match_name_reports_first_reserved_punctuation(name, message):
    result = match_name_char_error(name)

    assert isinstance(result, Some)
    assert result.val == message


@pytest.mark.unit
@pytest.mark.parametrize("pseudonym", ["", " ", "0", "North/South", "Team: Bären"])
def test_team_pseudonym_allows_text_without_reserved_punctuation(pseudonym):
    assert isinstance(team_pseudonym_char_error(pseudonym), Null)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("pseudonym", "message"),
    [
        ("North, South", 'Team pseudonyms cannot contain ",".'),
        ("North::South", 'Team pseudonyms cannot contain "::".'),
        ("North::South, East", 'Team pseudonyms cannot contain ",".'),
    ],
)
def test_team_pseudonym_reports_first_reserved_punctuation(pseudonym, message):
    result = team_pseudonym_char_error(pseudonym)

    assert isinstance(result, Some)
    assert result.val == message


@pytest.mark.unit
@pytest.mark.parametrize(
    ("name", "message"),
    [
        (" ", "Match name cannot be empty"),
        ("Final/Replay", 'Match names cannot contain "/".'),
    ],
)
def test_schedule_import_returns_validation_error_for_invalid_name(name, message):
    result = MatchScheduleSerializer.match_from_dict({"name": name}, "test-tournament")

    assert isinstance(result, Err)
    assert isinstance(result.val, ValidationError)
    assert result.val.message == message


@pytest.mark.unit
@pytest.mark.parametrize(
    ("name", "message"),
    [
        (" ", "Match name is required."),
        ("Final::winner", 'Match names cannot contain "::".'),
    ],
)
def test_match_input_returns_error_text_for_invalid_name(name, message):
    match = SimpleNamespace(name=name)

    assert validate_match_input(match, "test-tournament") == (False, message)
