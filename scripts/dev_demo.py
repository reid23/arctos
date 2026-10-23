"""Run a local Arctos demo in a dedicated database.

Run ``uv run python -m scripts.dev_demo`` from the repository root, then use
Dioxus's same-origin development proxy. The API listens on 127.0.0.1:5006.
``--seed-only`` prepares the database without starting a server. No .env file,
Google credentials, external services, or ARCTOS_CORS_DEV setting is needed.

Without ``--snapshot``, fictional data is created in ``instance/demo.db``.
``--snapshot PATH`` copies an existing versioned SQLite database read-only into
``instance/snapshot-demo/demo.db``, migrates the copy, and adds fictional demo
accounts and events. The source and ordinary demo database are left untouched.
All demo accounts use the printed demo password.
A populated database is never reseeded: edits and newly created records survive
restarts. This command has no reset flag and never writes to tournament.db.
"""

from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import os
from pathlib import Path
import sqlite3
import tempfile

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from flask import Flask
from sqlalchemy import event

from app import create_app
from app.domain.enums import MatchStatus, RegistrationStatus, ScheduleType, SetType, WinnerSide
from app.services._common import Scope
from app.services.dual_write import set_head_ref_allowlist_ids, set_match_players, set_match_referees
from app.services.registration_service import RegistrationService
from app.services.sidecomp_service import SideCompService
from models import (
    Field,
    League,
    Match,
    Player,
    PlayerRegistration,
    Point,
    RegistrableConfig,
    SideComp,
    Team,
    TO,
    Tournament,
    db,
)

DEFAULT_DATABASE = Path(__file__).resolve().parents[1] / "instance" / "demo.db"
DEFAULT_SNAPSHOT_DATABASE = DEFAULT_DATABASE.parent / "snapshot-demo" / "demo.db"
DEMO_PASSWORD = "arctos-demo-only"
ORGANIZER_ID = "demo-organizer"
PLAYER_ID = "demo-player"
TEAM_ID = "demo-team"
LIVE_EVENT = "demo-open"
UPCOMING_EVENT = "demo-summer-cup"
PAST_EVENT = "demo-spring-cup"
LEAGUE_ID = "demo-coast-league"
LEAGUE_EVENT = "demo-league-round-one"
RESERVED_PLAYER_IDS = frozenset(
    [ORGANIZER_ID, PLAYER_ID, "demo-alex", "demo-riley", "demo-sam", "demo-jordan"]
    + [f"demo-duelist-{index:02}" for index in range(1, 11)]
)
RESERVED_TEAM_IDS = frozenset([TEAM_ID, "demo-foxes", "demo-owls", "demo-wolves"])


def _demo_path(database_path: Path) -> Path:
    """Reject ordinary production names and symlinks before touching SQLite."""
    path = Path(database_path).absolute()
    if path.name != "demo.db" or path.is_symlink():
        raise ValueError("The demo database must be a regular file named demo.db, never tournament.db or a symlink.")
    protected = DEFAULT_DATABASE.with_name("tournament.db")
    if path.exists() and protected.exists() and path.samefile(protected):
        raise ValueError("The demo database cannot refer to instance/tournament.db.")
    return path


def _has_data(path: Path) -> bool:
    """Inspect an existing SQLite file read-only, including non-Arctos tables."""
    if not path.exists() or path.stat().st_size == 0:
        return False
    with sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True) as connection:
        names = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        for (name,) in names:
            quoted_name = '"' + name.replace('"', '""') + '"'
            if connection.execute(f"SELECT 1 FROM {quoted_name} LIMIT 1").fetchone():
                return True
    return False


def _config() -> RegistrableConfig:
    return RegistrableConfig(
        team_reg_fee=Decimal("40.00"),
        player_reg_fee=Decimal("10.00"),
        payment_info="Demo only: payments are fictional. Use Manage registrations to try marking a payment received.",
        team_registration_open=True,
        player_registration_open=True,
        n_max_teams=8,
        max_team_size_roster=10,
        max_team_size_field=5,
    )


def _seed(now: datetime) -> None:
    """Add fictional records; callers guarantee every reserved demo ID is free."""
    players = [
        (ORGANIZER_ID, "Morgan Field", "Portland, Oregon"),
        (PLAYER_ID, "Casey Newcomer", "Seattle, Washington"),
        ("demo-alex", "Alex Rivers", "Portland, Oregon"),
        ("demo-riley", "Riley North", "Portland, Oregon"),
        ("demo-sam", "Sam Willow", "Seattle, Washington"),
        ("demo-jordan", "Jordan Coast", "Seattle, Washington"),
    ]
    teams = [
        (TEAM_ID, "Cedar Bears", "Portland, Oregon", "Bears"),
        ("demo-foxes", "Rain City Foxes", "Seattle, Washington", "Foxes"),
        ("demo-owls", "Coastal Owls", "Eugene, Oregon", "Owls"),
        ("demo-wolves", "Valley Wolves", "Salem, Oregon", "Wolves"),
    ]
    for player_id, name, location in players:
        player = Player(id=player_id, name=name, location=location, bio="Fictional player for the local Arctos demo.")
        player.set_password(DEMO_PASSWORD)
        db.session.add(player)
    for team_id, name, location, _shortname in teams:
        team = Team(id=team_id, name=name, location=location, about="A fictional team for exploring Arctos locally.")
        team.set_password(DEMO_PASSWORD)
        db.session.add(team)
    db.session.flush()

    league = League(
        url=LEAGUE_ID,
        name="Coast Jugger League — Demo",
        published=True,
        about="## One season, one registration\nThis demo league shares teams, rosters, fees, and standings across its events.",
        registrable_config=_config(),
    )
    db.session.add(league)
    db.session.flush()

    events = [
        (LIVE_EVENT, "Cedar Open — Demo", -1, 1),
        (UPCOMING_EVENT, "Summer Cup — Demo", 14, 15),
        (PAST_EVENT, "Spring Cup — Demo", -31, -30),
        (LEAGUE_EVENT, "Coast League: Round One — Demo", 28, 28),
    ]
    for slug, name, start_offset, end_offset in events:
        tournament = Tournament(
            url=slug,
            name=name,
            start_date=(now + timedelta(days=start_offset)).replace(hour=9, minute=0, second=0, microsecond=0),
            end_date=(now + timedelta(days=end_offset)).replace(hour=23, minute=0, second=0, microsecond=0),
            location="Riverside Sports Park, Portland, Oregon",
            published=True,
            schedule_published=True,
            max_field_size=10,
            head_refs_allow_anyone=False,
            about=(
                "## Welcome to the demo\nFictional event data for exploring registration, schedules, results, "
                "and live match operations.\n\n- Check in beside Field A.\n- Bring water and your pompfen.\n"
                "- The organizer account can try all event management tools."
            ),
            league_id=LEAGUE_ID if slug == LEAGUE_EVENT else None,
            registrable_config=None if slug == LEAGUE_EVENT else _config(),
        )
        db.session.add(tournament)
        db.session.flush()
        db.session.add_all([Field(event=slug, name="Field A"), Field(event=slug, name="Field B")])
        db.session.add(TO(user_id=ORGANIZER_ID, user_type="player", event=slug))
        set_head_ref_allowlist_ids(tournament, [ORGANIZER_ID])
    db.session.add(TO(user_id=ORGANIZER_ID, user_type="player", league_id=LEAGUE_ID))
    db.session.commit()

    scopes = [Scope.event(LIVE_EVENT), Scope.event(UPCOMING_EVENT), Scope.event(PAST_EVENT), Scope.league(LEAGUE_ID)]
    for scope in scopes:
        for team_id, name, _location, shortname in teams:
            registration = RegistrationService.register_team(scope, team_id, name, shortname).unwrap()
            registration.paid = team_id != "demo-owls" or scope.url == PAST_EVENT
            registration.amount_paid = Decimal("40.00") if registration.paid else Decimal("0.00")
            registration.paid_at = now - timedelta(days=2) if registration.paid else None
            registration.payment_method = "Demo payment" if registration.paid else None
        for index, (player_id, name, _location) in enumerate(players):
            team_id = None if player_id == ORGANIZER_ID else (TEAM_ID if index < 4 else "demo-foxes")
            registration = RegistrationService.register_player(
                scope, player_id, team_id, jersey_number=str(index + 1), jersey_name=name.split()[0]
            ).unwrap()
            pending = player_id == PLAYER_ID and scope.url in (LIVE_EVENT, UPCOMING_EVENT)
            registration.status = RegistrationStatus.PENDING_TEAM_APPROVAL if pending else RegistrationStatus.CONFIRMED
            registration.paid = not pending
            registration.amount_paid = Decimal("10.00") if registration.paid else Decimal("0.00")
            registration.paid_at = now - timedelta(days=2) if registration.paid else None
            registration.payment_method = "Demo payment" if registration.paid else None
        db.session.commit()

    _match(LIVE_EVENT, "Opening match", now - timedelta(minutes=60), MatchStatus.COMPLETED)
    _match(LIVE_EVENT, "Field A live match", now - timedelta(minutes=10), MatchStatus.IN_PROGRESS)
    _match(
        LIVE_EVENT,
        "Next on Field B",
        now + timedelta(minutes=90),
        MatchStatus.NOT_STARTED,
        team1="demo-owls",
        team2="demo-wolves",
        field="Field B",
    )
    _match(UPCOMING_EVENT, "Pool A opener", now + timedelta(days=14), MatchStatus.NOT_STARTED)
    _match(LEAGUE_EVENT, "League opener", now + timedelta(days=28), MatchStatus.NOT_STARTED)
    _match(PAST_EVENT, "Spring final", now - timedelta(days=31, minutes=-60), MatchStatus.COMPLETED)
    past = db.session.get(Tournament, PAST_EVENT)
    past.registrable_config.team_registration_open = False
    past.registrable_config.player_registration_open = False
    db.session.commit()

    competition = SideCompService.create(
        LIVE_EVENT,
        actor_user_id=ORGANIZER_ID,
        actor_user_type="player",
        name="Sideline Dueling — Demo",
        type="DUELING",
        description="Try side competition registration and results with the demo organizer account.",
        allowed_weapons=["LONG", "STAFF", "QTIP", "BOARD", "FLORENTINE"],
    ).unwrap()
    competition.registration_open = True
    competition.active = True
    db.session.commit()
    for player_id, weapon in [(ORGANIZER_ID, "LONG"), ("demo-alex", "STAFF"), ("demo-sam", "QTIP")]:
        SideCompService.register_player(competition.id, player_id=player_id, weapon=weapon).unwrap()

    add_practice_side_competitions()


def add_practice_side_competitions() -> list[SideComp]:
    """Add scoring examples once; never reset existing competitions or results.

    Intended only for the local demo app, including an existing demo database.
    The caller must already have an application context for that database.
    """
    examples = [
        ("Open Dueling — Practice", False),
        ("Dueling Standings — Practice", True),
    ]
    existing = {comp.name: comp for comp in SideComp.query.filter_by(event=LIVE_EVENT).all()}
    if all(name in existing for name, _ in examples):
        return [existing[name] for name, _ in examples]
    entrants = [ORGANIZER_ID, PLAYER_ID, "demo-alex", "demo-riley", "demo-sam", "demo-jordan"]
    names = [
        "Avery Brook",
        "Taylor Ash",
        "Quinn Lake",
        "Robin Vale",
        "Jamie Fern",
        "Drew Stone",
        "Skyler Reed",
        "Rowan Pine",
        "Charlie West",
        "Cameron Moss",
    ]
    for index, name in enumerate(names, start=1):
        player_id = f"demo-duelist-{index:02}"
        if db.session.get(Player, player_id) is None:
            player = Player(id=player_id, name=name, bio="Fictional player for trying side competition scoring.")
            player.set_password(DEMO_PASSWORD)
            db.session.add(player)
        entrants.append(player_id)
    db.session.commit()
    for player_id in entrants:
        if not PlayerRegistration.query.filter_by(event=LIVE_EVENT, player=player_id).first():
            registration = RegistrationService.register_player(Scope.event(LIVE_EVENT), player_id, None).unwrap()
            registration.status = RegistrationStatus.CONFIRMED
    db.session.commit()
    weapons = ["LONG", "STAFF", "QTIP", "BOARD", "FLORENTINE"]
    competitions = []
    for name, with_results in examples:
        if name in existing:
            competitions.append(existing[name])
            continue
        competition = SideCompService.create(
            LIVE_EVENT,
            actor_user_id=ORGANIZER_ID,
            actor_user_type="player",
            name=name,
            type="DUELING",
            allowed_weapons=weapons,
            description=(
                "Practice entering and correcting results. Sample scores are already filled in."
                if with_results
                else "Practice entering results from a clean start."
            ),
        ).unwrap()
        competition.registration_open = True
        competition.active = True
        competition.track_losses = True
        db.session.commit()
        for index, player_id in enumerate(entrants):
            registration = SideCompService.register_player_as_to(
                competition.id,
                actor_user_id=ORGANIZER_ID,
                actor_user_type="player",
                player_id=player_id,
                weapon=weapons[index % len(weapons)],
            ).unwrap()
            if with_results:
                for points in [1] * (1 + index % 6) + [-1] * (index % 4):
                    SideCompService.log_result(
                        competition.id,
                        registration_id=registration.id,
                        points=points,
                        ref_user_id=ORGANIZER_ID,
                    ).unwrap()
        competitions.append(competition)
    return competitions


def _match(
    event_slug: str,
    name: str,
    start: datetime,
    status: MatchStatus,
    *,
    team1: str = TEAM_ID,
    team2: str = "demo-foxes",
    field: str = "Field A",
) -> None:
    """Build coherent historical/live snapshots, including normalized rosters."""
    played = status in (MatchStatus.IN_PROGRESS, MatchStatus.COMPLETED)
    completed = status == MatchStatus.COMPLETED
    match = Match(
        event=event_slug,
        name=name,
        field=field,
        team1=team1,
        team2=team2,
        team1_initial=team1,
        team2_initial=team2,
        schedule_type=ScheduleType.STATIC,
        set_type=SetType.STONES,
        nsets=1,
        stones_per_set=100,
        stones_remaining=0 if completed else (40 if played else 100),
        scheduled_start_time=start,
        nominal_start_time=start,
        nominal_length=20,
        status=status,
        confirmed_start_time=start if played else None,
        started_at=start if played else None,
        started_by=ORGANIZER_ID if played else None,
        completed_time=start + timedelta(minutes=10) if completed else None,
        finalized_at=start + timedelta(minutes=10) if completed else None,
        finalized_by=ORGANIZER_ID if completed else None,
        match_winner=WinnerSide.TEAM1 if completed else None,
        final_notes="Completed demonstration match." if completed else None,
    )
    db.session.add(match)
    db.session.flush()
    if team1 == TEAM_ID:
        set_match_referees(match, ["demo-owls"], ["demo-owls"])
    if played:
        set_match_players(match, ["demo-alex", "demo-riley"], ["demo-sam", "demo-jordan"])
        for index in range(5 if completed else 3):
            stamp = start + timedelta(minutes=index + 1)
            db.session.add(
                Point(
                    match=match.uuid,
                    winner="TEAM1" if index % 2 == 0 else "TEAM2",
                    stamp=stamp,
                    end_stamp=stamp + timedelta(seconds=30),
                    length=timedelta(seconds=30),
                    nstones=20,
                    stones_at_start=100 - index * 20,
                    set_number=1,
                    rerolled=False,
                )
            )


def _seed_database(path: Path, now: datetime | None, *, create_schema: bool) -> None:
    """Seed without application startup, which recomputes existing matches."""
    bootstrap = Flask("arctos_demo_seed")
    bootstrap.config.update(SQLALCHEMY_DATABASE_URI=f"sqlite:///{path}", SQLALCHEMY_TRACK_MODIFICATIONS=False)
    db.init_app(bootstrap)
    with bootstrap.app_context():

        @event.listens_for(db.engine, "connect")
        def enable_foreign_keys(connection, _record):
            connection.execute("PRAGMA foreign_keys=ON")

        try:
            if create_schema:
                db.create_all()
            # All models store naive UTC timestamps.
            timestamp = now or datetime.now(timezone.utc)
            if timestamp.tzinfo is not None:
                timestamp = timestamp.astimezone(timezone.utc).replace(tzinfo=None)
            _seed(timestamp)
        finally:
            db.session.remove()
            db.engine.dispose()


def prepare_demo_database(database_path: Path = DEFAULT_DATABASE, *, now: datetime | None = None) -> bool:
    """Seed an empty dedicated DB; return False without edits when it has data."""
    path = _demo_path(database_path)
    if _has_data(path):
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    _seed_database(path, now, create_schema=True)
    return True


def _migration_config(path: Path) -> Config:
    """Use an explicit copy URL; environment settings must not select a DB."""
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "migrations"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{path}".replace("%", "%%"))
    return config


def _validate_snapshot_revision(path: Path, config: Config) -> None:
    with closing(sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)) as connection:
        try:
            versions = connection.execute("SELECT version_num FROM alembic_version").fetchall()
        except sqlite3.OperationalError as error:
            raise ValueError(
                "The snapshot must have an existing, known Alembic revision; unversioned databases are not stamped automatically."
            ) from error
        if len(versions) != 1:
            raise ValueError("The snapshot must have exactly one known Alembic revision.")
        revision = versions[0][0]
        scripts = ScriptDirectory.from_config(config)
        if revision not in {script.revision for script in scripts.walk_revisions()}:
            raise ValueError(f"Unknown snapshot Alembic revision: {revision!r}.")


def _check_reserved_ids(path: Path) -> None:
    """Check the migrated schema, including tables absent in older revisions."""
    with closing(sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)) as connection:
        # Legacy login lookup uses the ID alone, so an opposite-type account
        # would shadow a fixture just as an account of the intended type would.
        accounts = RESERVED_PLAYER_IDS | RESERVED_TEAM_IDS
        reserved = [
            ("players", "id", accounts),
            ("teams", "id", accounts),
            ("tournaments", "url", {LIVE_EVENT, UPCOMING_EVENT, PAST_EVENT, LEAGUE_EVENT}),
            ("leagues", "url", {LEAGUE_ID}),
        ]
        collisions = []
        for table, key, ids in reserved:
            placeholders = ",".join("?" for _ in ids)
            rows = connection.execute(f"SELECT {key} FROM {table} WHERE {key} IN ({placeholders})", sorted(ids))
            collisions.extend(f"{table}: {row[0]}" for row in rows)
        if collisions:
            raise ValueError("The snapshot contains reserved demo IDs: " + ", ".join(sorted(collisions)))


def prepare_snapshot_demo_database(
    snapshot_path: Path, database_path: Path = DEFAULT_SNAPSHOT_DATABASE, *, now: datetime | None = None
) -> bool:
    """Publish a migrated, additive copy once; never replace an existing copy."""
    path = _demo_path(database_path)
    source = Path(snapshot_path).absolute()
    if path.resolve() == DEFAULT_DATABASE.resolve() or (
        path.exists() and DEFAULT_DATABASE.exists() and path.samefile(DEFAULT_DATABASE)
    ):
        raise ValueError("Snapshot demos must use a separate directory from instance/demo.db.")
    if path.resolve() == source.resolve() or (path.exists() and source.exists() and path.samefile(source)):
        raise ValueError("The snapshot source and demo destination must be different files.")
    if path.exists():
        if _has_data(path):
            return False
        raise ValueError("The snapshot demo destination already exists but is empty; it will not be overwritten.")
    if not source.is_file():
        raise ValueError("The snapshot source must be an existing SQLite file.")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".snapshot-demo-", suffix=".db", dir=path.parent)
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        # SQLite backup includes committed WAL contents, unlike copying the
        # main file or opening it with immutable=1.
        with closing(sqlite3.connect(f"{source.as_uri()}?mode=ro", uri=True)) as original:
            with closing(sqlite3.connect(temporary)) as copy:
                original.backup(copy)
                copy.execute("PRAGMA journal_mode=DELETE")
        config = _migration_config(temporary)
        _validate_snapshot_revision(temporary, config)
        command.upgrade(config, "head")
        _check_reserved_ids(temporary)
        _seed_database(temporary, now, create_schema=False)
        # Same-filesystem hard linking is atomic and fails if another process
        # created the destination while this copy was being prepared.
        os.link(temporary, path)
    finally:
        for suffix in ("", "-journal", "-wal", "-shm"):
            temporary.with_name(temporary.name + suffix).unlink(missing_ok=True)
    return True


def create_demo_app(database_path: Path = DEFAULT_DATABASE) -> Flask:
    """Create the regular API against the demo DB with same-origin cookies."""
    path = _demo_path(database_path)
    seeded = prepare_demo_database(path)
    app = create_app(
        {
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{path}",
            "SECRET_KEY": "arctos-local-demo-session-key",
            "GOOGLE_CLIENT_ID": "",
            "GOOGLE_CLIENT_SECRET": "",
            "SESSION_COOKIE_SECURE": False,
            "SESSION_COOKIE_SAMESITE": "Lax",
            "REMEMBER_COOKIE_SECURE": False,
            "REMEMBER_COOKIE_SAMESITE": "Lax",
            "DEMO_DATABASE_SEEDED": seeded,
        }
    )

    @app.get("/_api/demo-info")
    def demo_info():
        """Let local smoke checks verify this is the isolated demo before login."""
        return {"demo": True, "organizer": ORGANIZER_ID}

    return app


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed-only", action="store_true", help="Create sample data if empty, then exit.")
    parser.add_argument(
        "--snapshot", type=Path, help="Copy a versioned SQLite snapshot once into instance/snapshot-demo/demo.db."
    )
    args = parser.parse_args(argv)
    database_path = DEFAULT_SNAPSHOT_DATABASE if args.snapshot is not None else DEFAULT_DATABASE
    if args.snapshot is not None:
        seeded = prepare_snapshot_demo_database(args.snapshot, database_path)
        if not args.seed_only:
            app = create_demo_app(database_path)
    elif args.seed_only:
        seeded = prepare_demo_database()
    else:
        app = create_demo_app()
        seeded = app.config["DEMO_DATABASE_SEEDED"]
    print(f"Demo database: {database_path}")
    print("Created sample data." if seeded else "Using existing demo data; your changes are preserved.")
    print(f"Demo-only password: {DEMO_PASSWORD}")
    print(f"Organizer: {ORGANIZER_ID} | Player: {PLAYER_ID} | Team: {TEAM_ID}")
    print(f"Try /{LIVE_EVENT}, /{UPCOMING_EVENT}, and /leagues/{LEAGUE_ID} in the frontend.")
    if not args.seed_only:
        app.run(host="127.0.0.1", port=5006, debug=False, use_reloader=False, load_dotenv=False)


if __name__ == "__main__":
    main()
