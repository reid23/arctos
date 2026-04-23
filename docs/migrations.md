# Database Migration Runbook

## Overview

Arctos uses app-managed, idempotent migrations in `app/db_migrations.py`.
Applied migration IDs are tracked in `schema_migrations`.

## Deployment steps

1. Deploy application code.
2. Run `make migrate` once per environment.
3. Start/restart app processes.
4. Verify key invariants:
   - No duplicate registrations for `(event, team)` / `(event, player)`.
   - `matches.field_id` and `cameras.field_id` are populated for existing rows.
   - `points.winner` values are only `TEAM1`, `TEAM2`, or `NULL`.
   - Match-core 1NF tables are populated:
     - `match_ref_slots`
     - `match_roster_entries`
     - `match_camera_stream_starts`

## Match-core 1NF transition

The migration `20260423_008_match_core_1nf_backfill` backfills normalized rows
from legacy multi-value columns on `matches`:

- `refs` / `refs_initial` -> `match_ref_slots`
- `team1_players` / `team2_players` -> `match_roster_entries`
- `camera_stream_starts` -> `match_camera_stream_starts`

Compatibility behavior during rollout:

- API/routes/services dual-write both normalized rows and legacy columns.
- Reads prefer normalized rows with fallback to legacy columns.
- Existing API payload shapes are unchanged.

## Rollback notes

- Migrations are additive and data-cleaning focused.
- For rollback, restore from database backup taken before `make migrate`.
- Re-deploying old app code without restoring data can violate older assumptions.
