# `scripts/` - operational scripts

Scripts for backups, data-quality checks, and one-off backfills. Most
are `uv run`-able; the database backup is a shell script.

Each script's top-level docstring states what it does and when to run
it. The "Examples" section below shows the most common invocations.

## Conventions

- All Python scripts include a top-level docstring explaining **why
  they exist** and **when to run them.** Read it before running. If
  you write a new one, do the same.
- Default to dry-run for anything that can mutate data. Require an
  explicit flag (`--apply`, `--commit`, `--yes`) before writing.
- Resolve the DB the same way the app does. Most scripts honour
  `SQLALCHEMY_DATABASE_URI` from the environment so you can point
  them at a snapshot.

## Examples

**Try the mobile interface with isolated sample data:**

```bash
just demo                    # creates instance/demo.db on the first run
just frontend                # run in a second terminal
uv run python scripts/check_mobile_ui.py --screenshots /tmp/arctos-ui
```

The demo uses dedicated local accounts and never opens `tournament.db`.
The browser check requires the demo marker endpoint and blocks data changes.
See [`TESTING.md`](../TESTING.md#mobile-browser-verification) for Chromium
setup, supported viewport sizes, and integration testing limits.

**Backup before a migration:**

```bash
just db-backup                # writes backups/tournament-pre-migration-<ts>.db
# or with a tag
./scripts/backup_db.sh "before-phase1"
```

**Check for duplicates before adding a UNIQUE constraint:**

```bash
just db-check-duplicates
# or directly
uv run python scripts/check_duplicates.py
uv run python scripts/check_duplicates.py --db /path/to/snapshot.db
```

**Clean up data quality issues (dry-run, then apply):**

```bash
uv run python scripts/cleanup_data_quality.py             # report only
uv run python scripts/cleanup_data_quality.py --apply     # actually delete / fix
```

**Backfill the join tables (only ever run once per DB):**

```bash
uv run python scripts/backfill_normalised_tables.py
```

## Adding a script

1. Single-file. Top-level docstring explaining what, why, and when.
2. Default to dry-run if it can mutate data; require an explicit flag
   to commit.
3. Resolve the DB via `SQLALCHEMY_DATABASE_URI` (or `--db`) for
   testability.
4. If it's a migration helper, document it in
   [`migrations/README.md`](../migrations/README.md) too.
5. If it should appear in `make`, add a target to the root
   [`Makefile`](../Makefile).
