"""Unit tests for ScheduleImportExportService (TOML import/export round-trips)."""

import re
import textwrap
from datetime import datetime, timezone

import pytest
import sqlalchemy as sa

from app.error_values import Err, Ok
from app.services.schedule_import_export_service import ScheduleImportExportService
from app.utils.toml_helpers import write_toml_schedule
from models import Field, Match, Tag, db


@pytest.mark.unit
def test_export_schedule_includes_tags_fields_and_matches(test_db, tournament):
    """Exported TOML should contain tags, fields, and matches with expected structure."""
    tournament_url = tournament.url

    # Seed tags and fields
    tag1 = Tag(event=tournament_url, name="Pool A")
    tag2 = Tag(event=tournament_url, name="Pool B")
    field1 = Field(event=tournament_url, name="Field 1", camera=None)
    field2 = Field(event=tournament_url, name="Field 2", camera="[]")
    db.session.add_all([tag1, tag2, field1, field2])

    # Seed a simple match that uses tag and result references
    m1 = Match(
        name="M1",
        event=tournament_url,
        field="Field 1",
        nominal_start_time=datetime(2025, 1, 1, 10, 0, tzinfo=timezone.utc),
        scheduled_start_time=datetime(2025, 1, 1, 10, 0, tzinfo=timezone.utc),
        nominal_length=60,
        schedule_type="STATIC",
        set_type="SETS",
        team1_initial="tag::Pool A",
        team2_initial="tag::Pool B",
    )
    db.session.add(m1)
    db.session.flush()
    from app.services.dual_write import set_match_referees

    set_match_referees(m1, ["", ""], ["tag::Pool A", "tag::Pool B"])
    db.session.commit()

    res = ScheduleImportExportService.export_schedule(tournament_url)
    match res:
        case Ok(toml_str):
            # Basic sanity checks: table headers present, no event key (the
            # target tournament comes from the import route, not the file)
            assert not re.search(r"^event\s*=", toml_str, re.MULTILINE)
            assert "[[tags]]" in toml_str
            assert "[[fields]]" in toml_str
            assert "[[matches]]" in toml_str
            # Ensure tag names and field names are present
            assert 'name = "Pool A"' in toml_str
            assert 'name = "Pool B"' in toml_str
            assert 'name = "Field 1"' in toml_str
            # Ensure match record contains key fields
            assert 'name = "M1"' in toml_str
            assert 'field = "Field 1"' in toml_str
            assert 'team1_initial = "tag::Pool A"' in toml_str
            assert 'refs_initial = "tag::Pool A,tag::Pool B"' in toml_str
            # Plan anchor must round-trip so re-import does not lose STATIC scheduled times.
            assert "scheduled_start_time" in toml_str
        case Err(err):
            raise AssertionError(f"Expected Ok(TOML), got Err({err})")


@pytest.mark.unit
def test_import_seeds_scheduled_from_nominal_when_missing(test_db, tournament):
    """Legacy TOML with only nominal_start_time must still populate the plan anchor."""
    from app.serializers.match_schedule_serializer import MatchScheduleSerializer

    tournament_url = tournament.url
    # Unit-test the serializer seed path directly (avoids team-registration checks on full import).
    res = MatchScheduleSerializer.match_from_dict(
        {
            "name": "LegacyStatic",
            "field": "Field 1",
            "schedule_type": "STATIC",
            "nominal_length": 60,
            "nominal_start_time": "2025-06-01T09:00:00",
            "team1_initial": "tag::PoolA",
            "team2_initial": "tag::PoolB",
        },
        tournament_url,
    )
    assert isinstance(res, Ok), getattr(res, "val", res)
    d = res.val
    assert d["nominal_start_time"] is not None
    assert d["scheduled_start_time"] is not None
    assert d["scheduled_start_time"] == d["nominal_start_time"]


@pytest.mark.unit
def test_import_schedule_rejects_invalid_tag_and_field(test_db, tournament):
    """Import should fail validation when tag::NAME or field reference is invalid."""
    tournament_url = tournament.url

    # Construct a minimal invalid TOML schedule:
    # - references tag::Missing (no such tag)
    # - match.field = "Missing Field" (no such field)
    toml_content = textwrap.dedent(
        f"""
        event = "{tournament_url}"

        [[tags]]
        id = 1
        name = "Existing"

        [[fields]]
        id = 1
        name = "Field 1"

        [[matches]]
        uuid = "00000000-0000-0000-0000-000000000001"
        name = "M1"
        field = "Missing Field"
        schedule_type = "STATIC"
        set_type = "SETS"
        nominal_length = 60
        team1_initial = "tag::Missing"
        """
    ).strip()

    res = ScheduleImportExportService.import_schedule(tournament_url, toml_content)
    match res:
        case Ok(_):
            raise AssertionError("Expected Err(ValidationError) for invalid tag/field")
        case Err(err):
            # We don't assert exact message, but ensure it's a validation error
            from app.exceptions import ValidationError

            assert isinstance(err, ValidationError)


@pytest.mark.unit
def test_import_schedule_rejects_invalid_match_reference(test_db, tournament):
    """Import should fail validation when a MATCH::winner/loser reference targets a non-existent match."""
    tournament_url = tournament.url

    toml_content = textwrap.dedent(
        f"""
        event = "{tournament_url}"

        [[tags]]
        id = 1
        name = "Pool A"

        [[fields]]
        id = 1
        name = "Field 1"

        [[matches]]
        uuid = "00000000-0000-0000-0000-000000000001"
        name = "M1"
        field = "Field 1"
        schedule_type = "STATIC"
        set_type = "SETS"
        nominal_length = 60
        team1_initial = "Nonexistent Match::winner"
        """
    ).strip()

    res = ScheduleImportExportService.import_schedule(tournament_url, toml_content)
    match res:
        case Ok(_):
            raise AssertionError("Expected Err(ValidationError) for invalid match reference")
        case Err(err):
            from app.exceptions import ValidationError

            assert isinstance(err, ValidationError)


@pytest.mark.unit
def test_import_schedule_replaces_existing_objects_and_deletes_missing(test_db, tournament):
    """
    Real import should:
    - create new tags/fields/matches present in TOML,
    - update same-tournament by id/uuid,
    - delete any tags/fields/matches in DB that are not present in the TOML.
    """
    tournament_url = tournament.url

    # Seed two tags/fields/matches; only one of each will appear in the TOML
    old_tag = Tag(event=tournament_url, name="Old Tag")
    keep_tag = Tag(event=tournament_url, name="Keep Tag")
    old_field = Field(event=tournament_url, name="Old Field", camera=None)
    keep_field = Field(event=tournament_url, name="Keep Field", camera=None)
    db.session.add_all([old_tag, keep_tag, old_field, keep_field])
    db.session.flush()

    old_match = Match(
        name="Old Match",
        event=tournament_url,
        field="Old Field",
        nominal_length=30,
        schedule_type="STATIC",
        set_type="SETS",
    )
    keep_match = Match(
        name="Keep Match",
        event=tournament_url,
        field="Keep Field",
        nominal_length=60,
        schedule_type="STATIC",
        set_type="SETS",
    )
    db.session.add_all([old_match, keep_match])
    db.session.commit()

    # Build TOML that only contains keep_tag / keep_field / keep_match
    tags = [{"id": keep_tag.id, "name": keep_tag.name}]
    fields = [{"id": keep_field.id, "name": keep_field.name, "camera": ""}]
    matches = [
        {
            "uuid": keep_match.uuid,
            "name": keep_match.name,
            "field": keep_field.name,
            "nominal_length": keep_match.nominal_length,
            "schedule_type": keep_match.schedule_type,
            "set_type": keep_match.set_type,
        }
    ]
    toml_str = write_toml_schedule(tags=tags, fields=fields, matches=matches)

    res = ScheduleImportExportService.import_schedule(tournament_url, toml_str)
    match res:
        case Ok(result):
            # One of each object should have been "updated", the old ones deleted.
            assert result.tags_created >= 0
            assert result.tags_updated >= 0
            assert result.fields_created >= 0
            assert result.fields_updated >= 0
            assert result.matches_created >= 0
            assert result.matches_updated >= 0
        case Err(err):
            raise AssertionError(f"Expected Ok(ImportResult), got Err({err})")

    # Verify DB state: only keep_* objects remain for this tournament
    tag_names = {t.name for t in Tag.query.filter_by(event=tournament_url).all()}
    field_names = {f.name for f in Field.query.filter_by(event=tournament_url).all()}
    match_names = {m.name for m in Match.query.filter_by(event=tournament_url).all()}

    assert tag_names == {"Keep Tag"}
    assert field_names == {"Keep Field"}
    assert match_names == {"Keep Match"}


@pytest.mark.unit
def test_break_join_matches_can_have_duplicate_names_on_different_fields(test_db, tournament, app):
    """BREAK and JOIN matches can have the same name on different fields."""
    tournament_url = tournament.url

    # Create two fields
    field1 = Field(event=tournament_url, name="Field 1", camera=None)
    field2 = Field(event=tournament_url, name="Field 2", camera=None)
    db.session.add_all([field1, field2])
    db.session.commit()

    # Create two BREAK matches with the same name on different fields
    break1 = Match(
        name="Lunch Break",
        event=tournament_url,
        field="Field 1",
        schedule_type="BREAK",
        nominal_length=60,
    )
    break2 = Match(
        name="Lunch Break",
        event=tournament_url,
        field="Field 2",
        schedule_type="BREAK",
        nominal_length=60,
    )
    db.session.add_all([break1, break2])
    db.session.commit()

    # Both should exist
    breaks = Match.query.filter_by(event=tournament_url, name="Lunch Break", schedule_type="BREAK").all()
    assert len(breaks) == 2
    assert {b.field for b in breaks} == {"Field 1", "Field 2"}

    # Create two JOIN matches with the same name on different fields
    join1 = Match(
        name="Morning End",
        event=tournament_url,
        field="Field 1",
        schedule_type="JOIN",
        nominal_length=0,
    )
    join2 = Match(
        name="Morning End",
        event=tournament_url,
        field="Field 2",
        schedule_type="JOIN",
        nominal_length=0,
    )
    db.session.add_all([join1, join2])
    db.session.commit()

    # Both should exist
    joins = Match.query.filter_by(event=tournament_url, name="Morning End", schedule_type="JOIN").all()
    assert len(joins) == 2
    assert {j.field for j in joins} == {"Field 1", "Field 2"}


@pytest.mark.unit
def test_regular_matches_cannot_have_duplicate_names(test_db, tournament, app):
    """Regular matches (STATIC/SAFE/FAST) are DB-enforced unique within a tournament."""
    tournament_url = tournament.url

    # Create a field
    field1 = Field(event=tournament_url, name="Field 1", camera=None)
    field2 = Field(event=tournament_url, name="Field 2", camera=None)
    db.session.add_all([field1, field2])
    db.session.commit()

    # Create a STATIC match
    match1 = Match(
        name="Match A",
        event=tournament_url,
        field="Field 1",
        schedule_type="STATIC",
        set_type="SETS",
        nominal_length=60,
    )
    db.session.add(match1)
    db.session.commit()

    # Try to create another STATIC match with the same name (even on different
    # field) — the partial unique index should reject it.
    match2 = Match(
        name="Match A",
        event=tournament_url,
        field="Field 2",  # Different field
        schedule_type="STATIC",
        set_type="SETS",
        nominal_length=60,
    )
    db.session.add(match2)
    with pytest.raises(sa.exc.IntegrityError):
        db.session.commit()
    db.session.rollback()

    matches = Match.query.filter_by(event=tournament_url, name="Match A", schedule_type="STATIC").all()
    assert len(matches) == 1


@pytest.mark.unit
def test_import_resolves_duplicate_match_names_by_field(test_db, tournament):
    """When importing matches with duplicate names, previous_match/next_match should resolve to match on same field."""
    tournament_url = tournament.url

    # Create fields
    field1 = Field(event=tournament_url, name="Field 1", camera=None)
    field2 = Field(event=tournament_url, name="Field 2", camera=None)
    db.session.add_all([field1, field2])
    db.session.commit()

    # Create TOML with duplicate BREAK match names on different fields
    # Each break should reference the previous match on its own field
    toml_content = textwrap.dedent(
        f"""
        event = "{tournament_url}"

        [[fields]]
        name = "Field 1"

        [[fields]]
        name = "Field 2"

        [[matches]]
        name = "Match 1"
        field = "Field 1"
        schedule_type = "STATIC"
        set_type = "SETS"
        nominal_length = 60

        [[matches]]
        name = "Lunch Break"
        field = "Field 1"
        schedule_type = "BREAK"
        nominal_length = 60
        previous_match = "Match 1"

        [[matches]]
        name = "Match 2"
        field = "Field 2"
        schedule_type = "STATIC"
        set_type = "SETS"
        nominal_length = 60

        [[matches]]
        name = "Lunch Break"
        field = "Field 2"
        schedule_type = "BREAK"
        nominal_length = 60
        previous_match = "Match 2"
        """
    ).strip()

    res = ScheduleImportExportService.import_schedule(tournament_url, toml_content)
    match res:
        case Ok(result):
            assert result.matches_created == 4
        case Err(err):
            raise AssertionError(f"Expected Ok(ImportResult), got Err({err})")

    # Verify matches were created
    matches = Match.query.filter_by(event=tournament_url).all()
    assert len(matches) == 4

    # Find the two "Lunch Break" matches
    breaks = [m for m in matches if m.name == "Lunch Break"]
    assert len(breaks) == 2

    # Verify each break's previous_match points to the match on its own field
    break1 = next((b for b in breaks if b.field == "Field 1"), None)
    break2 = next((b for b in breaks if b.field == "Field 2"), None)

    assert break1 is not None
    assert break2 is not None

    # Get the previous matches
    match1 = next((m for m in matches if m.name == "Match 1"), None)
    match2 = next((m for m in matches if m.name == "Match 2"), None)

    assert match1 is not None
    assert match2 is not None

    # Verify field-based resolution
    assert break1.previous_match == match1.uuid  # Break on Field 1 references Match 1
    assert break2.previous_match == match2.uuid  # Break on Field 2 references Match 2


@pytest.mark.unit
def test_tags_with_spaces_work_correctly(test_db, tournament):
    """Tags with spaces in their names should work correctly in export/import and references."""
    tournament_url = tournament.url

    # Create a field
    field = Field(event=tournament_url, name="Field 1", camera=None)
    db.session.add(field)

    # Create a tag with spaces in the name
    tag_with_spaces = Tag(event=tournament_url, name="Pool A Teams")
    db.session.add(tag_with_spaces)
    db.session.commit()

    # Create a match that references this tag
    match = Match(
        name="Test Match",
        event=tournament_url,
        field="Field 1",
        schedule_type="STATIC",
        set_type="SETS",
        nominal_length=60,
        team1_initial="tag::Pool A Teams",
    )
    db.session.add(match)
    db.session.flush()
    from app.services.dual_write import set_match_referees

    set_match_referees(match, [""], ["tag::Pool A Teams"])
    db.session.commit()

    # Export should include the tag and the reference
    res = ScheduleImportExportService.export_schedule(tournament_url)
    match res:
        case Ok(toml_str):
            # Tag should be exported
            assert 'name = "Pool A Teams"' in toml_str
            # Reference should be exported correctly
            assert 'team1_initial = "tag::Pool A Teams"' in toml_str
            assert 'refs_initial = "tag::Pool A Teams"' in toml_str
        case Err(err):
            raise AssertionError(f"Expected Ok(TOML), got Err({err})")

    # Import should work correctly
    res = ScheduleImportExportService.import_schedule(tournament_url, toml_str)
    match res:
        case Ok(result):
            assert result.matches_created >= 0 or result.matches_updated >= 0
        case Err(err):
            raise AssertionError(f"Expected Ok(ImportResult), got Err({err})")

    # Verify the imported match has the correct reference
    from app.services.dual_write import get_match_refs_initial_csv

    imported_match = Match.query.filter_by(event=tournament_url, name="Test Match").first()
    assert imported_match is not None
    assert imported_match.team1_initial == "tag::Pool A Teams"
    assert get_match_refs_initial_csv(imported_match) == "tag::Pool A Teams"


def _register_team(tournament_url: str, team_id: str, pseudonym: str | None = None) -> None:
    """Register (and create if needed) a team for a tournament."""
    from app.domain.enums import TeamRegistrationStatus
    from models import Team, TeamRegistration

    if Team.query.get(team_id) is None:
        db.session.add(Team(id=team_id, name=team_id, pw_hash="x"))
        db.session.flush()
    db.session.add(
        TeamRegistration(
            event=tournament_url,
            team=team_id,
            pseudonym=pseudonym or team_id,
            status=TeamRegistrationStatus.CONFIRMED,
        )
    )
    db.session.commit()


@pytest.mark.unit
def test_export_has_no_event_key_and_import_without_event_succeeds(test_db, tournament):
    """Exports carry no 'event' key, and imports work without one (route decides)."""
    tournament_url = tournament.url

    toml_content = textwrap.dedent(
        """
        [[fields]]
        name = "Field 1"

        [[matches]]
        name = "M1"
        field = "Field 1"
        schedule_type = "STATIC"
        set_type = "SETS"
        nominal_length = 60
        """
    ).strip()

    res = ScheduleImportExportService.import_schedule(tournament_url, toml_content)
    match res:
        case Ok(result):
            assert result.matches_created == 1
            assert result.warnings == []
        case Err(err):
            raise AssertionError(f"Expected Ok(ImportResult), got Err({err})")

    assert Match.query.filter_by(event=tournament_url, name="M1").first() is not None

    export_res = ScheduleImportExportService.export_schedule(tournament_url)
    match export_res:
        case Ok(toml_str):
            assert not re.search(r"^event\s*=", toml_str, re.MULTILINE)
        case Err(err):
            raise AssertionError(f"Expected Ok(TOML), got Err({err})")


@pytest.mark.unit
def test_import_rewrites_unknown_team_tokens_to_tag_references(test_db, tournament):
    """Unknown team1/team2/refs tokens become tag:: references with warnings; known/symbolic tokens are untouched."""
    tournament_url = tournament.url
    _register_team(tournament_url, "known-team")

    toml_content = textwrap.dedent(
        """
        [[tags]]
        name = "Existing"

        [[fields]]
        name = "Field 1"

        [[matches]]
        name = "M1"
        field = "Field 1"
        schedule_type = "STATIC"
        set_type = "SETS"
        nominal_length = 60
        team1_initial = "Mystery Team"
        team2_initial = "known-team"
        refs_initial = "Mystery Team,known-team"

        [[matches]]
        name = "M2"
        field = "Field 1"
        schedule_type = "STATIC"
        set_type = "SETS"
        nominal_length = 60
        team1_initial = "M1::winner"
        team2_initial = "tag::Existing"
        """
    ).strip()

    res = ScheduleImportExportService.import_schedule(tournament_url, toml_content)
    match res:
        case Ok(result):
            expected_warning = "Team 'Mystery Team' not found; imported as tag reference 'tag::Mystery Team'"
            # Deduplicated by token: team1 + refs slot yield a single warning.
            assert result.warnings == [expected_warning]
        case Err(err):
            raise AssertionError(f"Expected Ok(ImportResult), got Err({err})")

    from app.services.dual_write import get_match_refs_initial_csv

    m1 = Match.query.filter_by(event=tournament_url, name="M1").first()
    assert m1.team1_initial == "tag::Mystery Team"
    assert m1.team2_initial == "known-team"  # registered team untouched
    assert get_match_refs_initial_csv(m1) == "tag::Mystery Team,known-team"

    m2 = Match.query.filter_by(event=tournament_url, name="M2").first()
    assert m2.team1_initial == "M1::winner"  # match reference untouched
    assert m2.team2_initial == "tag::Existing"  # existing tag reference untouched

    # Unassigned tag auto-created for the rewritten token
    auto_tag = Tag.query.filter_by(event=tournament_url, name="Mystery Team").first()
    assert auto_tag is not None
    assert auto_tag.team is None


@pytest.mark.unit
def test_import_rewrites_unknown_team_literals_in_skip_condition(test_db, tournament):
    """Unknown [Team] literals in skip_condition ASS expressions become [tag::Team]."""
    tournament_url = tournament.url
    _register_team(tournament_url, "known-team")

    toml_content = textwrap.dedent(
        """
        [[tags]]
        name = "Existing"

        [[fields]]
        name = "Field 1"

        [[matches]]
        name = "M1"
        field = "Field 1"
        schedule_type = "STATIC"
        set_type = "SETS"
        nominal_length = 60

        [[matches]]
        name = "M2"
        field = "Field 1"
        schedule_type = "SAFE"
        set_type = "SETS"
        nominal_length = 60
        skip_condition = "[Ghost Team] == [M1::winner] or [tag::Existing] == [known-team]"
        """
    ).strip()

    res = ScheduleImportExportService.import_schedule(tournament_url, toml_content)
    match res:
        case Ok(result):
            assert any("Ghost Team" in w for w in result.warnings)
        case Err(err):
            raise AssertionError(f"Expected Ok(ImportResult), got Err({err})")

    m2 = Match.query.filter_by(event=tournament_url, name="M2").first()
    assert m2.skip_condition == "[tag::Ghost Team] == [M1::winner] or [tag::Existing] == [known-team]"

    auto_tag = Tag.query.filter_by(event=tournament_url, name="Ghost Team").first()
    assert auto_tag is not None
    assert auto_tag.team is None


@pytest.mark.unit
def test_import_unknown_team_in_tags_section_imports_unassigned(test_db, tournament):
    """A tag assigning an unknown team imports the tag unassigned with a warning instead of failing."""
    tournament_url = tournament.url

    toml_content = textwrap.dedent(
        """
        [[tags]]
        name = "Pool X"
        team = "no-such-team"
        """
    ).strip()

    res = ScheduleImportExportService.import_schedule(tournament_url, toml_content)
    match res:
        case Ok(result):
            assert any("no-such-team" in w and "Pool X" in w for w in result.warnings)
        case Err(err):
            raise AssertionError(f"Expected Ok(ImportResult), got Err({err})")

    tag = Tag.query.filter_by(event=tournament_url, name="Pool X").first()
    assert tag is not None
    assert tag.team is None


@pytest.mark.unit
def test_reimport_of_rewritten_schedule_is_idempotent(test_db, tournament):
    """Re-importing an export produced after a rewrite must not double-tag (no tag::tag::X)."""
    tournament_url = tournament.url

    toml_content = textwrap.dedent(
        """
        [[fields]]
        name = "Field 1"

        [[matches]]
        name = "M1"
        field = "Field 1"
        schedule_type = "STATIC"
        set_type = "SETS"
        nominal_length = 60
        team1_initial = "Mystery Team"
        """
    ).strip()

    res = ScheduleImportExportService.import_schedule(tournament_url, toml_content)
    assert isinstance(res, Ok)
    assert len(res.val.warnings) == 1

    export_res = ScheduleImportExportService.export_schedule(tournament_url)
    assert isinstance(export_res, Ok)
    exported = export_res.val
    assert 'team1_initial = "tag::Mystery Team"' in exported
    assert "tag::tag::" not in exported

    # Re-import the rewritten file: no new warnings, no double-tagging, one tag row.
    res2 = ScheduleImportExportService.import_schedule(tournament_url, exported)
    assert isinstance(res2, Ok)
    assert res2.val.warnings == []

    m1 = Match.query.filter_by(event=tournament_url, name="M1").first()
    assert m1.team1_initial == "tag::Mystery Team"
    tags = Tag.query.filter_by(event=tournament_url, name="Mystery Team").all()
    assert len(tags) == 1


@pytest.mark.unit
def test_tag_expression_roundtrips_through_toml(test_db, tournament):
    """Tag expressions survive export -> wipe -> import, alongside a team override."""
    tournament_url = tournament.url
    _register_team(tournament_url, "known-team")

    field = Field(event=tournament_url, name="Field 1", camera=None)
    m1 = Match(
        name="Semi A",
        event=tournament_url,
        field="Field 1",
        schedule_type="STATIC",
        set_type="SETS",
        nominal_length=60,
    )
    expr_tag = Tag(event=tournament_url, name="SemiWinner", expression="(winner {Semi A})")
    both_tag = Tag(event=tournament_url, name="Champ", team="known-team", expression="(winner {Semi A})")
    db.session.add_all([field, m1, expr_tag, both_tag])
    db.session.commit()

    export_res = ScheduleImportExportService.export_schedule(tournament_url)
    assert isinstance(export_res, Ok), getattr(export_res, "value", export_res)
    exported = export_res.val
    assert 'expression = "(winner {Semi A})"' in exported
    assert 'team = "known-team"' in exported

    # Wipe the tags to simulate a fresh tournament state, then re-import.
    Tag.query.filter_by(event=tournament_url).delete()
    db.session.commit()

    res = ScheduleImportExportService.import_schedule(tournament_url, exported)
    assert isinstance(res, Ok), getattr(res, "value", res)
    assert res.val.warnings == []

    restored = Tag.query.filter_by(event=tournament_url, name="SemiWinner").first()
    assert restored is not None
    assert restored.expression == "(winner {Semi A})"
    assert restored.team is None

    # A tag with BOTH an expression and a manual team override keeps both.
    restored_both = Tag.query.filter_by(event=tournament_url, name="Champ").first()
    assert restored_both is not None
    assert restored_both.expression == "(winner {Semi A})"
    assert restored_both.team == "known-team"


@pytest.mark.unit
def test_script_variables_roundtrip_through_toml(test_db, tournament):
    """Variables export as a [[variables]] section and survive export -> wipe -> import."""
    from models import ScriptVariable

    tournament_url = tournament.url
    db.session.add(ScriptVariable(event=tournament_url, name="base", expression="2"))
    db.session.add(ScriptVariable(event=tournament_url, name="doubled", expression="(* base 2)"))
    db.session.commit()

    export_res = ScheduleImportExportService.export_schedule(tournament_url)
    assert isinstance(export_res, Ok), getattr(export_res, "value", export_res)
    exported = export_res.val
    assert "[[variables]]" in exported
    assert 'name = "base"' in exported
    assert 'expression = "(* base 2)"' in exported

    # Wipe all variables, then re-import the exported file.
    ScriptVariable.query.filter_by(event=tournament_url).delete()
    db.session.commit()

    res = ScheduleImportExportService.import_schedule(tournament_url, exported)
    assert isinstance(res, Ok), getattr(res, "value", res)
    assert res.val.variables_created == 2
    assert res.val.warnings == []

    rows = {v.name: v.expression for v in ScriptVariable.query.filter_by(event=tournament_url).all()}
    assert rows == {"base": "2", "doubled": "(* base 2)"}


@pytest.mark.unit
def test_variables_export_section_placed_before_tags(test_db, tournament):
    """[[variables]] is written near (before) the [[tags]] section, and only when variables exist."""
    from models import ScriptVariable

    tournament_url = tournament.url

    # Without variables the section is omitted entirely.
    db.session.add(Tag(event=tournament_url, name="Pool A"))
    db.session.commit()
    export_res = ScheduleImportExportService.export_schedule(tournament_url)
    assert isinstance(export_res, Ok)
    assert "[[variables]]" not in export_res.val

    db.session.add(ScriptVariable(event=tournament_url, name="threshold", expression="(+ 1 2)"))
    db.session.commit()
    export_res = ScheduleImportExportService.export_schedule(tournament_url)
    assert isinstance(export_res, Ok)
    exported = export_res.val
    assert exported.index("[[variables]]") < exported.index("[[tags]]")


@pytest.mark.unit
def test_import_reconciles_variables_and_deletes_missing(test_db, tournament):
    """Import updates variables by name, creates new ones, and deletes those absent from the file."""
    from models import ScriptVariable

    tournament_url = tournament.url
    db.session.add(ScriptVariable(event=tournament_url, name="keep", expression="1"))
    db.session.add(ScriptVariable(event=tournament_url, name="stale", expression="2"))
    db.session.commit()

    toml_content = textwrap.dedent(
        """
        [[variables]]
        name = "keep"
        expression = "(+ 1 1)"

        [[variables]]
        name = "fresh"
        expression = "3"
        """
    ).strip()

    res = ScheduleImportExportService.import_schedule(tournament_url, toml_content)
    assert isinstance(res, Ok), getattr(res, "value", res)
    assert res.val.variables_updated == 1
    assert res.val.variables_created == 1

    rows = {v.name: v.expression for v in ScriptVariable.query.filter_by(event=tournament_url).all()}
    assert rows == {"keep": "(+ 1 1)", "fresh": "3"}

    # A file without a [[variables]] section is authoritative too: all
    # variables for the tournament are deleted (same semantics as tags).
    res = ScheduleImportExportService.import_schedule(tournament_url, "")
    assert isinstance(res, Ok)
    assert ScriptVariable.query.filter_by(event=tournament_url).count() == 0


@pytest.mark.unit
def test_import_rejects_invalid_variable_entries(test_db, tournament):
    """Bad identifiers, reserved names, cycles, and unparseable expressions fail the import."""
    from app.exceptions import ValidationError
    from models import ScriptVariable

    bad_files = {
        "bad identifier": '[[variables]]\nname = "has space"\nexpression = "1"',
        "reserved name": '[[variables]]\nname = "winner"\nexpression = "1"',
        "missing expression": '[[variables]]\nname = "x"',
        "duplicate name": (
            '[[variables]]\nname = "x"\nexpression = "1"\n\n[[variables]]\nname = "x"\nexpression = "2"'
        ),
        "cycle": (
            '[[variables]]\nname = "a"\nexpression = "(+ b 1)"\n\n[[variables]]\nname = "b"\nexpression = "(+ a 1)"'
        ),
        "self reference": '[[variables]]\nname = "loop"\nexpression = "(+ loop 1)"',
        "unparseable expression": '[[variables]]\nname = "bad"\nexpression = "(+ 1"',
    }

    for label, toml_content in bad_files.items():
        res = ScheduleImportExportService.import_schedule(tournament.url, toml_content)
        match res:
            case Ok(_):
                raise AssertionError(f"Expected Err(ValidationError) for {label}")
            case Err(err):
                assert isinstance(err, ValidationError), label
        # Failed imports must not leave any variables behind.
        assert ScriptVariable.query.filter_by(event=tournament.url).count() == 0, label


@pytest.mark.unit
def test_import_orders_variables_before_dependent_tag_expressions(test_db, tournament):
    """A tag expression referencing a variable (which references an in-file match) imports cleanly."""
    from models import ScriptVariable

    tournament_url = tournament.url

    toml_content = textwrap.dedent(
        """
        [[variables]]
        name = "semi-winner"
        expression = "(winner {Semi A})"

        [[tags]]
        name = "Champ"
        expression = "semi-winner"

        [[fields]]
        name = "Field 1"

        [[matches]]
        name = "Semi A"
        field = "Field 1"
        schedule_type = "STATIC"
        set_type = "SETS"
        nominal_length = 60
        """
    ).strip()

    res = ScheduleImportExportService.import_schedule(tournament_url, toml_content)
    match res:
        case Ok(result):
            assert result.variables_created == 1
            assert result.tags_created == 1
            assert result.warnings == []
        case Err(err):
            raise AssertionError(f"Expected Ok(ImportResult), got Err({err})")

    var = ScriptVariable.query.filter_by(event=tournament_url, name="semi-winner").first()
    assert var is not None and var.expression == "(winner {Semi A})"
    tag = Tag.query.filter_by(event=tournament_url, name="Champ").first()
    assert tag is not None and tag.expression == "semi-winner"


@pytest.mark.unit
def test_import_rewrites_unknown_team_literals_in_tag_and_variable_expressions(test_db, tournament):
    """Unknown [Team] literals in tag / variable expressions become [tag::Team] with warnings + auto-tags."""
    from models import ScriptVariable

    tournament_url = tournament.url
    _register_team(tournament_url, "known-team")

    toml_content = textwrap.dedent(
        """
        [[variables]]
        name = "ghost"
        expression = "[Ghost Team]"

        [[tags]]
        name = "Phantom"
        expression = "[Phantom Squad]"

        [[tags]]
        name = "Known"
        expression = "[known-team]"
        """
    ).strip()

    res = ScheduleImportExportService.import_schedule(tournament_url, toml_content)
    match res:
        case Ok(result):
            assert any("Ghost Team" in w for w in result.warnings)
            assert any("Phantom Squad" in w for w in result.warnings)
            assert not any("known-team" in w for w in result.warnings)
        case Err(err):
            raise AssertionError(f"Expected Ok(ImportResult), got Err({err})")

    var = ScriptVariable.query.filter_by(event=tournament_url, name="ghost").first()
    assert var.expression == "[tag::Ghost Team]"
    assert Tag.query.filter_by(event=tournament_url, name="Phantom").first().expression == "[tag::Phantom Squad]"
    # Registered team literal untouched.
    assert Tag.query.filter_by(event=tournament_url, name="Known").first().expression == "[known-team]"

    # Auto-created unassigned tags back the rewritten references.
    for auto_name in ("Ghost Team", "Phantom Squad"):
        auto_tag = Tag.query.filter_by(event=tournament_url, name=auto_name).first()
        assert auto_tag is not None
        assert auto_tag.team is None


@pytest.mark.unit
def test_reimport_of_rewritten_expressions_is_idempotent(test_db, tournament):
    """Re-importing an export produced after expression rewrites must not double-tag (no tag::tag::X)."""
    from models import ScriptVariable

    tournament_url = tournament.url

    toml_content = textwrap.dedent(
        """
        [[variables]]
        name = "ghost"
        expression = "[Ghost Team]"

        [[tags]]
        name = "Phantom"
        expression = "[Phantom Squad]"
        """
    ).strip()

    res = ScheduleImportExportService.import_schedule(tournament_url, toml_content)
    assert isinstance(res, Ok), getattr(res, "value", res)
    assert len(res.val.warnings) == 2

    export_res = ScheduleImportExportService.export_schedule(tournament_url)
    assert isinstance(export_res, Ok)
    exported = export_res.val
    assert 'expression = "[tag::Ghost Team]"' in exported
    assert "tag::tag::" not in exported

    res2 = ScheduleImportExportService.import_schedule(tournament_url, exported)
    assert isinstance(res2, Ok), getattr(res2, "value", res2)
    assert res2.val.warnings == []

    assert ScriptVariable.query.filter_by(event=tournament_url, name="ghost").first().expression == "[tag::Ghost Team]"
    assert Tag.query.filter_by(event=tournament_url, name="Phantom").first().expression == "[tag::Phantom Squad]"
    assert len(Tag.query.filter_by(event=tournament_url, name="Ghost Team").all()) == 1
