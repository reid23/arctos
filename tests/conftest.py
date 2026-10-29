"""
Pytest configuration and fixtures for testing the tournament site.
"""

import pytest
import tempfile
import os
from datetime import datetime, timedelta, timezone
from flask_login import FlaskLoginClient

# Import app components
from app import create_app
from app.domain.enums import MatchStatus, ScheduleType
from models import (
    db,
    init_db,
    Tournament,
    Match,
    Player,
    Team,
    TeamRegistration,
    Field,
)
from tests.utils import make_registrable_config
from scripts.prepare_public_assets import prepare_presentation


def pytest_sessionstart():
    """Prepare ignored CSS before test collection or any application creation."""
    prepare_presentation()


@pytest.fixture(scope="session")
def app():
    """Create app instance with testing configuration and temporary database."""
    db_fd, db_path = tempfile.mkstemp(suffix=".db")

    app_instance = create_app(
        config={
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{db_path}",
            "WTF_CSRF_ENABLED": False,
            "SECRET_KEY": "test-secret-key",  # pragma: allowlist secret -- test-only Flask signing key
        }
    )

    with app_instance.app_context():
        db.create_all()
        init_db(db)
        db.session.commit()

    yield app_instance

    with app_instance.app_context():
        db.session.remove()
        db.drop_all()

    os.close(db_fd)
    if os.path.exists(db_path):
        os.unlink(db_path)


@pytest.fixture(scope="function")
def test_db(app):
    """Ensure clean database state for each test.

    Keeps the app context open for the duration of the test so that ORM
    instances created by other fixtures remain attached to the session.
    """
    with app.app_context():
        db.drop_all()
        db.create_all()
        init_db(db)
        db.session.commit()
        yield db
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app, test_db):
    """Create a test client.

    test_db already holds an active app context for the current test, so no
    extra context push is needed here.
    """
    app.test_client_class = FlaskLoginClient
    with app.test_client() as c:
        yield c


# ---------------------------------------------------------------------------
# Fixtures – all depend on test_db which already provides an app context,
# so no nested `with app.app_context()` is needed.
# ---------------------------------------------------------------------------


@pytest.fixture
def tournament(test_db):
    """Create a test tournament with two fields and open registration."""
    cfg = make_registrable_config(
        team_registration_open=True,
        player_registration_open=True,
        n_max_teams=8,
        max_team_size_roster=10,
        max_team_size_field=7,
    )
    tourn = Tournament(
        url="test-tournament",
        name="Test Tournament",
        start_date=datetime.now(timezone.utc),
        end_date=datetime.now(timezone.utc) + timedelta(days=1),
        location="Test Location",
        max_field_size=14,
        published=True,
        schedule_published=True,
        registrable_config_id=cfg.id,
    )
    db.session.add(tourn)
    db.session.flush()
    db.session.add(Field(event=tourn.url, name="Field 1"))
    db.session.add(Field(event=tourn.url, name="Field 2"))
    db.session.commit()
    db.session.refresh(tourn)
    return tourn


@pytest.fixture
def player(test_db):
    """Create a test player."""
    p = Player(
        id="test_player",
        name="Test Player",
        pw_hash="dummy_hash",
        phone="1234567890",
    )
    p.set_password("testpass")
    db.session.add(p)
    db.session.commit()
    db.session.refresh(p)
    return p


@pytest.fixture
def team(test_db):
    """Create a test team."""
    t = Team(id="test_team", name="Test Team", pw_hash="dummy_hash")
    t.set_password("testpass")
    db.session.add(t)
    db.session.commit()
    db.session.refresh(t)
    return t


@pytest.fixture
def team_registration(test_db, tournament, team):
    """Create a team registration."""
    reg = TeamRegistration(
        event=tournament.url,
        team=team.id,
        pseudonym="Test Team Pseudonym",
        status="CONFIRMED",
        paid=True,
    )
    db.session.add(reg)
    db.session.commit()
    db.session.refresh(reg)
    return reg


@pytest.fixture
def head_ref_player(test_db, tournament):
    """Create a head ref player and register them on the tournament's allow-list."""
    from app.services.dual_write import set_head_ref_allowlist_ids

    p = Player(id="test_ref1", name="Head Ref Player", pw_hash="dummy_hash")
    p.set_password("testpass")
    db.session.add(p)
    db.session.flush()
    set_head_ref_allowlist_ids(tournament, [p.id])
    db.session.commit()
    db.session.refresh(p)
    return p


@pytest.fixture
def seeded_teams(test_db):
    """Seed the dummy ``Team`` rows that older tests reference by string ID.

    Many tests insert ``Match.team1``/``team2`` or ``TeamRegistration.team``
    values like ``"team1"``, ``"team_1"``, etc. without ever creating the
    corresponding ``Team`` row. SQLite enforces the foreign keys on those
    columns (see ``app.set_sqlite_pragmas``), so the inserts raise
    ``IntegrityError`` unless the parent ``Team`` rows already exist.

    Opting into this fixture seeds a superset of the IDs those tests use, so
    they can keep referencing string literals without each one having to
    create teams individually. New tests should prefer the explicit ``team``
    fixture (or build their own teams).

    Returns:
        The tuple of team IDs that were ensured to exist.
    """
    ids = (
        "team1",
        "team2",
        "team3",
        "team_1",
        "team_2",
        "t1",
        "t2",
        "t3",
        "explicit_team",
        "tag_resolved_team",
        "resolved_team_a",
        "resolved_team_b",
        "resolved_tag_team",
    )
    existing = {t.id for t in Team.query.filter(Team.id.in_(ids)).all()}
    for tid in ids:
        if tid in existing:
            continue
        db.session.add(Team(id=tid, name=tid, pw_hash="dummy_hash"))
    db.session.commit()
    return ids


def create_match(
    tournament_url,
    name,
    field,
    nominal_start_time,
    dynamic=True,
    team1_initial=None,
    team2_initial=None,
    nominal_length=60,
):
    """Helper function to create a match within the current session."""
    match = Match(
        name=name,
        event=tournament_url,
        field=field,
        nominal_start_time=nominal_start_time,
        schedule_type=ScheduleType.SAFE if dynamic else ScheduleType.STATIC,
        team1_initial=team1_initial,
        team2_initial=team2_initial,
        nominal_length=nominal_length,
        status=MatchStatus.NOT_STARTED,
        set_type="SETS",
    )
    db.session.add(match)
    db.session.flush()
    return match
