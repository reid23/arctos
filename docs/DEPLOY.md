# Deploying Arctos

This runbook is for the person who runs deploys against the production
Arctos database. Read it top to bottom once; keep it open during a deploy.

## One-time setup (per database)

Before introducing Alembic to an unversioned database, back it up and inspect
its schema. Stamp the revision that matches the columns and tables already
present, then apply the remaining migrations:

```bash
uv run alembic stamp <matching_revision>
just db-migrate
```

Stamping records a revision; it does not add missing columns or transform data.
Use `just db-baseline` only when the verified schema already matches the current
head. For a database that already has an Alembic revision, use `just db-current`
and `just db-migrate` without stamping. See
[the migration guide](../migrations/README.md) for known schema transitions.

## Scoreboard overlay rendering

The scoreboard overlay endpoint renders frames with headless Chromium via
Playwright, so the server needs a Chromium build plus its system
libraries. Once per server (and after Playwright upgrades in `uv.lock`):

```bash
uv sync
uv run playwright install --with-deps chromium
```

`--with-deps` installs the shared libraries via apt and needs sudo. To
use an already-installed Chromium/Chrome instead, set
`ARCTOS_CHROMIUM_PATH`. Then set `ARCTOS_FRONTEND_URL` to the public
origin serving the SPA (see [env-vars.md](env-vars.md)) and restart.
The SPA build being served there must include the `/scoreboard-render`
route. Without this setup the endpoint returns 503; nothing else is
affected.

## The deploy loop

Every deploy follows the same shape:

1. **Pull** the latest code: `git pull origin dev`
2. **Backup** the database (always): `just db-backup <tag>`
3. **Apply** the change — depends on what kind of change it is (see below)
4. **Verify** — depends on the change
5. **Restart** the application service

Skip step 5 only when the PR description says explicitly "no restart needed".

## Picking a runbook by change type

Every PR that touches the database or schema must declare which of the
following sections to follow in its description. You should not need to
read the code to know how to deploy.

| Change type | When you see... | Follow |
|---|---|---|
| Schema migration | New file under `migrations/versions/` | [Schema migration](#schema-migration) |
| Data migration | New script under `scripts/` named `backfill_*.py` | [Data migration](#data-migration) |
| Application code only | Changes only under `app/`, no new migration | [Application code](#application-code) |
| Destructive migration | Migration containing `op.drop_column` / `op.drop_table` | [Destructive migration](#destructive-migration) |

A single PR may combine an application-code change with a schema migration.
In that case the PR description should list both sets of steps in the order
to run them — usually the schema migration first, then the restart.

---

## Schema migration

Adds tables, columns, indexes, or constraints. Reversible: `alembic
downgrade` undoes additive changes cleanly.

```bash
git pull origin dev

# Pre-flight: if this migration adds a UNIQUE constraint anywhere, the
# live data must be free of duplicates first. Skip this step if the PR
# description says no UNIQUE constraint is added.
just db-check-duplicates    # must exit 0 to continue

# Backup. Tag descriptively so you can find this snapshot later.
just db-backup pre-<short-name-of-change>

# Apply.
just db-migrate

# Verify the new revision is the head.
just db-current

# Restart.
```

**If the migration fails partway:** SQLite + alembic does its best to keep
DDL in a transaction; usually nothing was applied and you can investigate,
fix the migration, and re-run. If `just db-current` shows a half-applied
state, restore the backup (see [Rollback reference](#rollback-reference)).

**If `db-check-duplicates` reports duplicates:** the unique constraint
*will* fail at migration time. Resolve the duplicates (merge or delete
the offending rows in SQL) before continuing. Coordinate with the PR
author — the policy for which row to keep is a product decision.

---

## Data migration

A standalone Python script that reads from the existing schema and writes
to tables added in a previous schema migration. Does not change the schema
itself, and does not require an application restart — the running app
doesn't know about the new tables yet.

```bash
git pull origin dev

just db-backup pre-<short-name-of-change>

uv run python scripts/<backfill_script_name>.py
```

The script prints progress and exits non-zero on validation failure. Its
module docstring tells you what to verify after it finishes (usually a
few SQL queries that compare row counts between the old and new
representations).

**If the script fails partway:** re-running is safe. Backfill scripts are
written to be idempotent — every insert is guarded by a uniqueness check,
so existing rows are skipped on the second run rather than duplicated.

**If validation queries report a mismatch:** stop. Do not proceed to any
later phase that depends on the new tables being correct. Talk to the PR
author; the fix is usually in the script, not in the data.

---

## Application code

No database change. Just a code deploy.

---

## Destructive migration

Drops columns or tables. **Irreversible at the data level** — once
applied, the only path back is restoring the backup. There is no
`alembic downgrade` that recovers dropped column data.

**Pre-conditions** (the PR description must confirm both):

1. A previous application-code deploy has removed every read and every
   write of the columns being dropped.

If missing, do not deploy. Talk to the PR author.

```bash
git pull origin dev

# Backup with a final-sounding tag — this is the one that matters.
just db-backup pre-cutover-FINAL

# Stop the app. SQLite's DROP COLUMN rebuilds the table internally and
# does not coexist well with concurrent writers.

just db-migrate
just db-current
```

**If anything is wrong after this:** stop the app, restore the backup,
restart. There is no alternative recovery.

---

## Rollback reference

| Step that failed | Recovery |
|---|---|
| Schema migration (additive) | Usually nothing was applied; investigate and re-run. If state is partial, restore the backup. |
| Data migration script | Re-run after fixing the script — writes are idempotent. |
| Application code deploy | `git revert <sha>` and restart. |
| Destructive migration | Stop app, restore backup, restart. No alternative. |

### Restoring a backup

```bash
cp backups/tournament-<tag>-<ts>.db instance/tournament.db

# Required: these sidecars belong to the old database file.
# SQLite recreates them on the next connection.
rm -f instance/tournament.db-shm instance/tournament.db-wal
```

Verify with `just db-current` (should print whichever revision the backup
was taken at) and a smoke test before considering the rollback complete.

---

## Quick command reference

| Command | Purpose |
|---|---|
| `just db-baseline` | Record head only for an unversioned DB whose schema has been verified to match head |
| `just db-backup [tag]` | Snapshot to `backups/tournament-<tag>-<unix_ts>.db` |
| `just db-check-duplicates` | Report rows that would block a future UNIQUE constraint |
| `just db-migrate` | Apply all pending migrations (`alembic upgrade head`) |
| `just db-current` | Show the revision currently applied to the database |
| `just db-history` | Show the full revision history |

---

## PR-description template

PR authors should include a block in this format at the top of every PR
that touches the database or schema:

```markdown
## Deploy

**Type:** schema migration
**Pre-flight:** `just db-check-duplicates` must exit 0
**Steps:**
1. `just db-backup pre-add-unique-emails`
2. `just db-migrate`
3. `just db-current` → expect `0003_add_unique_emails (head)`
4. `sudo systemctl restart arctos`
5. Smoke test: register a player, then register a second player with the
   same email — expect 4xx.

**Rollback:** restore the backup taken in step 1.
```

If a PR is missing a Deploy block, ask the author to add one before merging.
A clear Deploy block is a hard merge-blocking requirement for any PR that
the production database owner will execute.
