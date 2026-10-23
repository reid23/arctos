# Alembic migrations

This directory holds the versioned schema migrations for Arctos. Every
schema change after the initial baseline is shipped as a numbered
revision file under `versions/`.

## What is a migration, and why do we need them?

A **schema migration** is a small, versioned script that brings the
database from one shape to another (e.g. adding a column, renaming a
table, backfilling a value). Once code is shipped that *expects* a new
column, every running database - dev, staging, production, every
contributor's laptop - has to grow that column. A migration is how
that change is captured, code-reviewed, and applied deterministically
in the same order everywhere.

[Alembic](https://alembic.sqlalchemy.org/) is the migration tool that
ships with SQLAlchemy. It tracks which revisions have been applied to
a given database (in an `alembic_version` table), runs the ones that
have not, and supports auto-generating a draft revision by diffing the
SQLAlchemy models against the live schema. The
[Alembic tutorial](https://alembic.sqlalchemy.org/en/latest/tutorial.html)
and the
[autogenerate guide](https://alembic.sqlalchemy.org/en/latest/autogenerate.html)
are the two pages worth reading before writing your first migration.

`env.py` wires Alembic to `app.models.db`; `script.py.mako` is the
template used when generating new revisions; `versions/` holds the
numbered revision files starting from `0001_baseline.py`. The Alembic
config (`alembic.ini`) lives at the repository root.

## Day-to-day commands

Run everything via `uv run` so the project's pinned alembic version is used.

| Goal | Command (justfile shortcut in parens) |
|---|---|
| Record an unversioned DB's verified schema revision | `uv run alembic stamp <matching_revision>`; use `head` / `just db-baseline` only if the schema already matches head |
| Apply all outstanding migrations | `uv run alembic upgrade head` (`just db-migrate`, or `just db-migrate-safe` for backup-then-migrate) |
| Roll back the most recent migration | `uv run alembic downgrade -1` |
| Generate a new migration from model changes | `uv run alembic revision --autogenerate -m "snake_case_message"` (`just db-revision "..."`) |
| Inspect the current revision applied to the DB | `uv run alembic current` (`just db-current`) |
| Show the full revision history | `uv run alembic history` (`just db-history`) |

## Pointing alembic at a different database

By default the env file resolves to `instance/tournament.db` (matching how
Flask's instance-folder convention expands `sqlite:///tournament.db`).
Override with the `SQLALCHEMY_DATABASE_URI` environment variable:

```bash
SQLALCHEMY_DATABASE_URI=sqlite:////tmp/test.db uv run alembic upgrade head
```

## Documentation requirements

Every migration file **must** have a module-level docstring explaining *why* the change is being made.

Pre-commit hooks and CI may enforce this; in any case, treat it as a hard
review-time requirement.


## Profile and event banners

These migrations are required before running the updated backend against an
existing database:

| Revision | Addition |
|---|---|
| `0023_profile_banners` | Nullable `banner_photo` columns on `players` and `teams` |
| `0024_event_banners` | Nullable `banner_photo` columns on `tournaments` and `leagues` |
| `0025_player_profile_details` | Optional player `pronouns` and a `pompfen` JSON list, initially empty |

Existing rows and avatar paths stay intact. Existing players have no pronouns
or selected pompfen until they choose to add them. Profiles and events display the
theme background until an authorized user uploads a cover. Run `uv sync` to
install Pillow, back up the database, then apply pending migrations to the
intended database before starting Flask:

```bash
SQLALCHEMY_DATABASE_URI=sqlite:////absolute/path/to/database.db uv run alembic current
SQLALCHEMY_DATABASE_URI=sqlite:////absolute/path/to/database.db uv run alembic upgrade head
```

Banner files are stored under `static/uploads/banners`; avatars remain under
`static/uploads/profiles`. Preserve both directories with database backups and
across deployments. The database stores relative paths rather than image
bytes. Avatar validation/storage improvements need no additional columns and
do not rewrite existing uploads during migration. New uploads are validated,
normalized, and stripped of metadata; replacements use fresh filenames and
remove old files only after the database update succeeds.

A database created directly by `db.create_all()` may have no Alembic revision.
Do not stamp it at the new head to skip missing columns. Inspect its schema
first: if it matches revision `0022_oauth_provider` and lacks all four banner
columns, stamp **0022_oauth_provider**, then upgrade. A fresh database created
from the updated models already contains all listed additions; verify that schema
before stamping it at head. Always point these commands at the intended
local/demo database using `SQLALCHEMY_DATABASE_URI`.

The optional `scripts.dev_demo --snapshot PATH` launcher handles known,
versioned snapshots separately: it reads the source through SQLite's backup
API, migrates a temporary copy with an explicit database URL, then adds
fictional fixtures only when reserved IDs are free. The prepared database is
published at `instance/snapshot-demo/demo.db` without overwriting an existing
copy. Repeated runs preserve the copy and its edits. Unknown or unversioned
snapshots are rejected rather than stamped; inspect their schema separately
before choosing any revision. The launcher does not copy uploaded media.
