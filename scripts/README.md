# Operational and development scripts

Read a script's module docstring and `--help` before running it. Database
maintenance commands may change data; take a backup and verify the target path.
New maintenance scripts should default to a dry run and require an explicit
write option. Dedicated demo commands are an exception: their purpose is to
create isolated sample data.

## Local demo data

```bash
just demo                    # Flask on 127.0.0.1:5006
# In another terminal:
just frontend                # Dioxus on 127.0.0.1:8080
```

`dev_demo.py` creates `instance/demo.db` if empty and preserves existing data
on restart. Accounts `demo-organizer`, `demo-player`, and `demo-team` use
`arctos-demo-only`; startup also prints the credentials. The launcher does not
load `.env` or use `instance/tournament.db`. Keep it local.

To start from an existing versioned SQLite snapshot:

```bash
just public-assets
uv run python -m scripts.dev_demo --snapshot /absolute/path/to/snapshot.db
```

This opens the source read-only, copies it through SQLite's backup API,
migrates the copy, and adds fictional fixtures at
`instance/snapshot-demo/demo.db`. It rejects unknown/unversioned schemas and
reserved demo-ID collisions. It does not overwrite an existing prepared copy.
Use `--seed-only` to prepare data without starting Flask. Uploaded media is not
copied; provide the corresponding `static/uploads` files separately.

Existing demo copies need explicit migrations when the schema changes; see
[migrations/README.md](../migrations/README.md#profile-and-event-banners).

For an additional populated practice event:

```bash
uv run python -m scripts.dev_practice --database instance/snapshot-demo/demo.db
```

This adds `/demo-practice`, owned by `demo-organizer`, with teams, rosters,
fields, matches in several states, and an active dueling competition. It prints
links to the relevant tools. Repeated runs preserve edits and deletions; a new
`--slug demo-practice-2` creates another independent example. Use
`--database instance/demo.db` for the ordinary demo. Both commands restrict
writes to a regular demo database; do not give them the original snapshot.

## Browser checks

```bash
uv run playwright install chromium
uv run python scripts/check_mobile_ui.py --screenshots /tmp/arctos-ui
```

Keep the demo and frontend running. The checks require a demo marker and guard
against real data writes. See [TESTING.md](../TESTING.md#mobile-browser-verification)
for focused suites and device/integration limits.

## Public HTML assets and browser checks

`prepare_public_assets.py` generates public CSS from the frontend sources,
validates the compiled module and Wasm references, creates gzip companions,
and writes the application manifest. `stage_frontend.py` publishes that build
while retaining assets used by open tabs. See [deployment](../docs/DEPLOY.md#public-html-rollout).

The [public entry verification guide](../docs/public-html-verification.md)
provides setup, commands, acceptance criteria, and measured results for:

| Script | Purpose |
| --- | --- |
| `check_public_html.py` | Readable content, usable controls, native navigation, and startup recovery |
| `check_public_reading_navigation.py` | Reading during startup, navigation races, history, focus, and handoff |
| `check_directory_account_state.py` | Account isolation, stale responses, Retry, and pagination |
| `compare_startup_profiles.py` | Cold/warm startup and repeat navigation under network/CPU profiles |

These tools run externally and write local reports. Production does not collect
performance telemetry.

## Database maintenance

```bash
just db-backup
./scripts/backup_db.sh before-migration
just db-check-duplicates
uv run python scripts/check_duplicates.py --db /path/to/copy.db
uv run python scripts/cleanup_data_quality.py          # report only
uv run python scripts/cleanup_data_quality.py --apply  # apply changes
```

Check each script's database argument or `SQLALCHEMY_DATABASE_URI` handling;
not every utility resolves its target in the same way.

`backfill_normalised_tables.py` is only for databases between migrations
`0002_phase1_additive` and `0003_phase4_cleanup`, while the legacy source columns
still exist. It is not a normal setup step for current databases. Inspect with
`--dry-run` first; running without that flag writes and validates the backfill.

## Adding a script

State its purpose, inputs, write behavior, and recovery expectations in the
module docstring. Make the database target explicit and test destructive or
irreversible behavior with temporary data. Add commonly used commands to the
root `justfile`; document migration helpers in the migration guide.
