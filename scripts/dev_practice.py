"""Add an editable practice event to an existing local demo database.

Run ``uv run python -m scripts.dev_practice`` after preparing a snapshot demo,
or pass ``--database instance/demo.db`` for the ordinary demo. This command does
not start the app, migrate/reseed a database, or reset an existing practice event.
Use a fresh ``--slug demo-practice-2`` to create another independent example.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
from uuid import NAMESPACE_URL, uuid5

from flask import Flask
import sqlalchemy as sa

from app.domain.enums import MatchStatus, RegistrationStatus, ScheduleType, SetType, WinnerSide
from app.services._common import Scope
from app.services.dual_write import set_head_ref_allowlist_ids, set_match_players, set_match_referees
from app.services.registration_service import RegistrationService
from app.services.sidecomp_service import SideCompService
from models import Field, League, Match, Player, Point, RegistrableConfig, Team, TO, Tournament, db
from scripts.dev_demo import DEFAULT_SNAPSHOT_DATABASE, DEMO_PASSWORD, LIVE_EVENT, ORGANIZER_ID, _demo_path

DEFAULT_SLUG = "demo-practice"
TEAMS = (
    ("otters", "Harbor Otters", "Otters", "Portland, Oregon"),
    ("kestrels", "Rain City Kestrels", "Kestrels", "Seattle, Washington"),
    ("lynx", "Valley Lynx", "Lynx", "Salem, Oregon"),
    ("herons", "Coastal Herons", "Herons", "Eugene, Oregon"),
    ("foxes", "Cascade Foxes", "Foxes", "Bend, Oregon"),
    ("badgers", "River Badgers", "Badgers", "Vancouver, Washington"),
)
PLAYER_NAMES = (
    "Avery Brooks",
    "Jordan Reed",
    "Morgan Vale",
    "Casey Linden",
    "Quinn Rowan",
    "Taylor West",
    "Alex Rivers",
    "Riley Hart",
    "Cameron Ash",
    "Jamie Lake",
    "Drew Morgan",
    "Skyler North",
    "Robin Moss",
    "Sam Alder",
    "Finley Stone",
    "Reese Willow",
    "Parker Fern",
    "Hayden Coast",
    "Rowan Chase",
    "Charlie Pine",
    "Kendall Grove",
    "Blair Meadow",
    "Sage Ellis",
    "Emery Brook",
    "Dakota Reed",
    "Jules Winters",
    "Arden Fox",
    "Remy Laurel",
    "Marin Wells",
    "Indigo Lane",
    "Toby Rowan",
    "Shay River",
    "Lee Cedar",
    "Devon Field",
    "Kit Haven",
    "Lane Birch",
)
WEAPONS = ["LONG", "STAFF", "QTIP", "BOARD", "FLORENTINE"]
# Local tooling bookkeeping, deliberately outside application model metadata.
# Keeping it in the same transaction makes repeats safe even after user edits or
# deletion of fixture records. No production migration or reset behavior is added.
FIXTURES = sa.Table(
    "_arctos_demo_practice",
    sa.MetaData(),
    sa.Column("slug", sa.Text, primary_key=True),
    sa.Column("manifest", sa.Text, nullable=False),
)


def _identifiers(slug: str) -> tuple[list[str], list[str], dict[str, str]]:
    if len(slug) > 35 or re.fullmatch(r"demo-practice(?:-[a-z0-9]+)*", slug) is None:
        raise ValueError("Use demo-practice or a fresh demo-practice-… slug, up to 35 characters.")
    teams = [f"{slug}-{suffix}" for suffix, *_ in TEAMS]
    players = [f"{slug}-p{index:02}" for index in range(1, len(PLAYER_NAMES) + 1)]
    matches = {
        key: str(uuid5(NAMESPACE_URL, f"arctos-demo-practice:{slug}:{key}"))
        for key in [*(f"completed-{index}" for index in range(1, 7)), "ready", "running", "scheduled"]
    }
    return teams, players, matches


def _check_destination(slug: str, teams: list[str], players: list[str], matches: dict[str, str]) -> None:
    """Refuse collisions, including opposite-type IDs used by legacy login."""
    organizer = db.session.get(Player, ORGANIZER_ID)
    demo_owner = TO.query.filter_by(event=LIVE_EVENT, user_id=ORGANIZER_ID, user_type="player").first()
    if organizer is None or db.session.get(Team, ORGANIZER_ID) is not None or demo_owner is None:
        raise ValueError("Prepare a local demo with scripts.dev_demo before adding the practice event.")
    ids = teams + players
    collisions = [f"player:{row.id}" for row in Player.query.filter(Player.id.in_(ids)).all()]
    collisions += [f"team:{row.id}" for row in Team.query.filter(Team.id.in_(ids)).all()]
    for model in (Tournament, League):
        if db.session.get(model, slug) is not None:
            collisions.append(f"{model.__tablename__}:{slug}")
    collisions += [f"match:{row.uuid}" for row in Match.query.filter(Match.uuid.in_(matches.values())).all()]
    if collisions:
        raise ValueError(
            "Practice IDs already exist; nothing was changed. Choose a fresh --slug. " + ", ".join(sorted(collisions))
        )


def _require_current_schema(connection) -> None:
    inspector = sa.inspect(connection)
    present = set(inspector.get_table_names())
    missing = []
    for table in db.metadata.sorted_tables:
        if table.name not in present:
            missing.append(table.name)
            continue
        columns = {column["name"] for column in inspector.get_columns(table.name)}
        missing.extend(f"{table.name}.{column.name}" for column in table.columns if column.name not in columns)
    if missing:
        raise ValueError(
            "The demo schema is out of date. Back up this working copy and run Alembic upgrade head "
            "with its explicit SQLALCHEMY_DATABASE_URI before adding practice data. Missing: " + ", ".join(missing)
        )


def _add_match(
    slug: str,
    match_id: str,
    name: str,
    start: datetime,
    status: MatchStatus,
    team1: str,
    team2: str,
    referee: str,
    rosters: dict[str, list[str]],
    *,
    field: str = "Field A",
    score: tuple[int, int] = (3, 2),
) -> None:
    completed = status == MatchStatus.COMPLETED
    playing = status == MatchStatus.IN_PROGRESS
    winners = (["TEAM1"] * score[0] + ["TEAM2"] * score[1]) if completed else ["TEAM1", "TEAM2", "TEAM1"]
    match = Match(
        uuid=match_id,
        event=slug,
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
        stones_remaining=0 if completed else (40 if playing else 100),
        scheduled_start_time=start,
        nominal_start_time=start,
        nominal_length=20,
        status=status,
        ready_to_start=status == MatchStatus.READY_TO_START,
        ready_to_start_at=start if status == MatchStatus.READY_TO_START else None,
        confirmed_start_time=start if completed or playing else None,
        started_at=start if completed or playing else None,
        started_by=ORGANIZER_ID if completed or playing else None,
        completed_time=start + timedelta(seconds=150) if completed else None,
        finalized_at=start + timedelta(minutes=4) if completed else None,
        finalized_by=ORGANIZER_ID if completed else None,
        match_winner=(WinnerSide.TEAM1 if score[0] > score[1] else WinnerSide.TEAM2) if completed else None,
        final_notes="Fictional practice result." if completed else None,
        initial_notes="Practice match: each team has five field players and one substitute.",
    )
    db.session.add(match)
    db.session.flush()
    set_match_referees(match, [referee], [referee])
    if completed or playing:
        set_match_players(match, rosters[team1][:5], rosters[team2][:5])
        for index, winner in enumerate(winners):
            stamp = start + timedelta(seconds=index * 30)
            db.session.add(
                Point(
                    match=match_id,
                    winner=winner,
                    stamp=stamp,
                    end_stamp=stamp + timedelta(seconds=30),
                    length=timedelta(seconds=30),
                    nstones=20,
                    stones_at_start=100 - index * 20,
                    set_number=1,
                    rerolled=False,
                )
            )


def _seed(slug: str, now: datetime, teams: list[str], players: list[str], matches: dict[str, str]) -> dict:
    rosters = {team_id: players[index * 6 : (index + 1) * 6] for index, team_id in enumerate(teams)}
    for index, (team_id, (_, name, _short, location)) in enumerate(zip(teams, TEAMS, strict=True)):
        team = Team(id=team_id, name=name, location=location, about="Fictional team for the local practice event.")
        team.set_password(DEMO_PASSWORD)
        db.session.add(team)
        for player_index, player_id in enumerate(rosters[team_id], start=index * 6):
            player = Player(
                id=player_id,
                name=PLAYER_NAMES[player_index],
                location=location,
                bio="Fictional player for trying match and side event workflows locally.",
            )
            player.set_password(DEMO_PASSWORD)
            db.session.add(player)
    tournament = Tournament(
        url=slug,
        name="Practice Open — Demo",
        start_date=(now - timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0),
        end_date=(now + timedelta(days=1)).replace(hour=23, minute=0, second=0, microsecond=0),
        location="Riverside Practice Fields, Portland, Oregon",
        published=True,
        schedule_published=True,
        max_field_size=10,
        head_refs_allow_anyone=False,
        head_refs_allow_reffing_teams=True,
        registrable_config=RegistrableConfig(
            team_reg_fee=0,
            player_reg_fee=0,
            team_registration_open=True,
            player_registration_open=True,
            n_max_teams=8,
            max_team_size_roster=8,
            max_team_size_field=5,
            payment_info="Fictional practice event; no payment is needed.",
        ),
        about=(
            "## Practice running an event\n"
            "Sign in as `demo-organizer` to start the ready match on Field A, continue the paused live match "
            "on Field B, or enter dueling results. Six completed matches already populate Results.\n\n"
            "Each team has six players: choose five for the field and keep one substitute. "
            "The dueling side event has 24 entrants; twelve have sample scores and twelve have no scores yet.\n\n"
            "All accounts, matches, and scores in this event are fictional. Your edits are preserved."
        ),
    )
    db.session.add(tournament)
    db.session.flush()
    db.session.add_all([Field(event=slug, name="Field A"), Field(event=slug, name="Field B")])
    db.session.add(TO(user_id=ORGANIZER_ID, user_type="player", event=slug))
    set_head_ref_allowlist_ids(tournament, [ORGANIZER_ID])
    db.session.commit()
    for team_id, (_, name, shortname, _location) in zip(teams, TEAMS, strict=True):
        RegistrationService.register_team(Scope.event(slug), team_id, name, shortname).unwrap()
        for jersey, player_id in enumerate(rosters[team_id], start=1):
            registration = RegistrationService.register_player(
                Scope.event(slug),
                player_id,
                team_id,
                jersey_number=str(jersey),
                jersey_name=PLAYER_NAMES[players.index(player_id)].split()[0],
            ).unwrap()
            registration.status = RegistrationStatus.CONFIRMED
    organizer_registration = RegistrationService.register_player(Scope.event(slug), ORGANIZER_ID, None).unwrap()
    organizer_registration.status = RegistrationStatus.CONFIRMED
    db.session.commit()

    # Earlier pool matches are sequential so playing/referee teams never overlap.
    for index, (first, second, referee, score) in enumerate(
        [
            (0, 1, 2, (3, 2)),
            (2, 3, 4, (4, 1)),
            (4, 5, 0, (3, 2)),
            (0, 2, 1, (2, 3)),
            (1, 4, 3, (3, 2)),
            (3, 5, 2, (4, 1)),
        ],
        start=1,
    ):
        _add_match(
            slug,
            matches[f"completed-{index}"],
            f"Pool match {index}",
            now - timedelta(minutes=240 - index * 30),
            MatchStatus.COMPLETED,
            teams[first],
            teams[second],
            teams[referee],
            rosters,
            score=score,
            field="Field A" if index % 2 else "Field B",
        )
    _add_match(
        slug,
        matches["ready"],
        "Ready on Field A",
        now - timedelta(minutes=1),
        MatchStatus.READY_TO_START,
        teams[0],
        teams[1],
        teams[4],
        rosters,
    )
    _add_match(
        slug,
        matches["running"],
        "In progress on Field B",
        now - timedelta(minutes=5),
        MatchStatus.IN_PROGRESS,
        teams[2],
        teams[3],
        teams[5],
        rosters,
        field="Field B",
    )
    _add_match(
        slug,
        matches["scheduled"],
        "Next round on Field A",
        now + timedelta(minutes=45),
        MatchStatus.NOT_STARTED,
        teams[4],
        teams[5],
        teams[1],
        rosters,
    )
    db.session.commit()

    competition = SideCompService.create(
        slug,
        actor_user_id=ORGANIZER_ID,
        actor_user_type="player",
        name="Open Dueling — Practice",
        type="DUELING",
        allowed_weapons=WEAPONS,
        description="24 fictional entrants. Continue the sample scores or enter a first result for an unscored player.",
    ).unwrap()
    competition.registration_open = True
    competition.active = True
    competition.track_losses = True
    db.session.commit()
    result_count = 0
    for index, player_id in enumerate(players[:24]):
        registration = SideCompService.register_player_as_to(
            competition.id,
            actor_user_id=ORGANIZER_ID,
            actor_user_type="player",
            player_id=player_id,
            weapon=WEAPONS[index % len(WEAPONS)],
        ).unwrap()
        if index < 12:
            for points in [1] * (1 + index % 6) + [-1] * (index % 3):
                SideCompService.log_result(
                    competition.id,
                    registration_id=registration.id,
                    points=points,
                    ref_user_id=ORGANIZER_ID,
                ).unwrap()
                result_count += 1
    return {
        "slug": slug,
        "teams": teams,
        "players": players,
        "matches": matches,
        "sidecomp_id": competition.id,
        "sidecomp_entrants": 24,
        "sidecomp_scored_entrants": 12,
        "sidecomp_results": result_count,
        "created_at": now.isoformat(),
    }


def prepare_practice_database(
    database_path: Path = DEFAULT_SNAPSHOT_DATABASE,
    *,
    slug: str = DEFAULT_SLUG,
    now: datetime | None = None,
) -> dict:
    """Add one fixture atomically; repeats return its manifest without edits."""
    path = _demo_path(database_path)
    if not path.is_file():
        raise ValueError("The demo database must already exist; run scripts.dev_demo first.")
    teams, players, matches = _identifiers(slug)
    bootstrap = Flask("arctos_practice_seed")
    bootstrap.config.update(SQLALCHEMY_DATABASE_URI=f"sqlite:///{path}", SQLALCHEMY_TRACK_MODIFICATIONS=False)
    db.init_app(bootstrap)
    with bootstrap.app_context():
        engine = db.engine
        try:
            with engine.connect() as connection:
                connection.exec_driver_sql("PRAGMA foreign_keys=ON")
                # Explicit BEGIN prevents SQLite's legacy SAVEPOINT behavior from
                # committing service writes outside the enclosing transaction.
                connection.exec_driver_sql("BEGIN IMMEDIATE")
                db.engines[None] = connection
                db.session().join_transaction_mode = "create_savepoint"
                try:
                    if sa.inspect(connection).has_table(FIXTURES.name):
                        saved = connection.execute(
                            sa.select(FIXTURES.c.manifest).where(FIXTURES.c.slug == slug)
                        ).scalar()
                        if saved is not None:
                            return {**json.loads(saved), "created": False}
                    _require_current_schema(connection)
                    _check_destination(slug, teams, players, matches)
                    timestamp = now or datetime.now(timezone.utc)
                    if timestamp.tzinfo is not None:
                        timestamp = timestamp.astimezone(timezone.utc).replace(tzinfo=None)
                    manifest = _seed(slug, timestamp, teams, players, matches)
                    # Reading expired ORM attributes while building the manifest
                    # may open a final read-only savepoint. Close it before the
                    # connection-level ledger write so its rollback cannot remove
                    # the idempotence marker along with that read transaction.
                    db.session.remove()
                    FIXTURES.create(connection, checkfirst=True)
                    connection.execute(FIXTURES.insert().values(slug=slug, manifest=json.dumps(manifest)))
                    connection.commit()
                    return {**manifest, "created": True}
                finally:
                    db.session.remove()
                    db.engines[None] = engine
                    if connection.in_transaction():
                        connection.rollback()
        finally:
            engine.dispose()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database", type=Path, default=DEFAULT_SNAPSHOT_DATABASE, help="Existing local demo.db to extend."
    )
    parser.add_argument("--slug", default=DEFAULT_SLUG, help="New demo-practice slug; repeat runs preserve all edits.")
    args = parser.parse_args(argv)
    manifest = prepare_practice_database(args.database, slug=args.slug)
    print(
        "Created practice event."
        if manifest["created"]
        else "Practice event already provisioned; your changes are preserved."
    )
    print(f"Database: {_demo_path(args.database)}")
    print(f"Organizer: {ORGANIZER_ID} (existing password unchanged)")
    print(
        "Initial fixture: 6 teams, 36 rostered players, 6 completed matches, 1 ready match, 1 live match, 1 scheduled match."
    )
    print("Dueling: 24 entrants, 12 scored and 12 unscored; 54 editable sample results.")
    slug = manifest["slug"]
    print(f"Event: /{slug}")
    print(f"Start: /{slug}/start-match/{manifest['matches']['ready']}")
    print(f"Run: /{slug}/run-match/{manifest['matches']['running']}")
    print(f"Side event: /{slug}/sidecomps/{manifest['sidecomp_id']}")
    print(f"Enter results: /{slug}/sidecomps/{manifest['sidecomp_id']}/enter-results")
    print(f"Manage results: /{slug}/sidecomps/{manifest['sidecomp_id']}/manage-results")


if __name__ == "__main__":
    main()
