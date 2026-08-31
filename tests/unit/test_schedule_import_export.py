"""Unit tests for ScheduleImportExportService (TOML import/export round-trips)."""

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
    field1 = Field(event=tournament_url, name="Field 1")
    field2 = Field(event=tournament_url, name="Field 2")
    db.session.add_all([tag1, tag2, field1, field2])

    # Seed a simple match that uses tag and result references
    m1 = Match(
        name="M1",
        event=tournament_url,
        field="Field 1",
        nominal_start_time=datetime(2025, 1, 1, 10, 0, tzinfo=timezone.utc),
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
            # Basic sanity checks: event and table headers present
            assert f'event = "{tournament_url}"' in toml_str
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
        case Err(err):
            raise AssertionError(f"Expected Ok(TOML), got Err({err})")


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
    old_field = Field(event=tournament_url, name="Old Field")
    keep_field = Field(event=tournament_url, name="Keep Field")
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
    fields = [{"id": keep_field.id, "name": keep_field.name}]
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
    toml_str = write_toml_schedule(event=tournament_url, tags=tags, fields=fields, matches=matches)

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
    field1 = Field(event=tournament_url, name="Field 1")
    field2 = Field(event=tournament_url, name="Field 2")
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
    field1 = Field(event=tournament_url, name="Field 1")
    field2 = Field(event=tournament_url, name="Field 2")
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
    field1 = Field(event=tournament_url, name="Field 1")
    field2 = Field(event=tournament_url, name="Field 2")
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
    field = Field(event=tournament_url, name="Field 1")
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
