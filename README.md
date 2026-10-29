# Arctos

[![Tests](https://github.com/reid23/arctos/actions/workflows/test.yml/badge.svg)](https://github.com/reid23/arctos/actions/workflows/test.yml)
[![Coverage floor](https://img.shields.io/badge/coverage%20floor-30%25-yellow)](TESTING.md#coverage)
[![Python](https://img.shields.io/badge/python-3.12%2B-blue)](pyproject.toml)
[![License: GPL v3](https://img.shields.io/badge/license-GPLv3-blue.svg)](LICENSE)

Centralized online results and event management for Jugger.

See [CONTRIBUTING](CONTRIBUTING.md) for how to get involved.

## New here?

1. **This README** - architecture, conventions, end-to-end request trace.
2. **[`app/README.md`](app/README.md)** - what the backend factory does
   and how a request flows through the route -> service -> model -> db
   layers.
3. **[`app/models/README.md`](app/models/README.md)** - the canonical
   domain shape (entity diagram, scope-column invariants, money / time
   conventions).
4. **[`docs/arctos-schedule-script.md`](docs/arctos-schedule-script.md)** -
   the ASS DSL.
5. **[`tests/README.md`](tests/README.md)** plus
   **[`TESTING.md`](TESTING.md)** - fixtures and how to write a test that
   pulls its weight.

After that, dip into the other per-directory READMEs as you encounter
their code.

## Architecture

```
┌─────────────────┐   /_api/...    ┌──────────────────┐    SQLAlchemy   ┌──────────┐
│  Dioxus SPA     │ ────────────-> │  Flask backend   │ ─────────────-> │  SQLite  │
│  (Rust -> WASM) │                │  (gunicorn)      │                 │  (WAL)   │
│  served by      │                │                  │                 └──────────┘
│  nginx at /     │                │  blueprints,     │
└─────────────────┘                │  services,       │
                                   │  models          │
                                   └──────────────────┘
```

- **Frontend.** Single-page app written in Rust with [Dioxus],
  compiled to WebAssembly. nginx serves the SPA at `/`.
- **Backend.** Python 3.12 [Flask] app run under gunicorn. Serves
  the application API, authentication callbacks, and static files. The SPA uses
  routes under `/_api/`; `/api/` (no underscore) is
  the public, OAuth-authenticated API for third-party apps, documented
  in [`docs/developers.md`](docs/developers.md).
- **Database.** A single SQLite file (`tournament.db`). WAL mode is
  enabled so background workers and HTTP handlers can share the file
  with fewer read/write conflicts. SQLite still allows only one writer at a
  time. Foreign keys are enforced via
  `PRAGMA foreign_keys = ON` on every new connection - without that
  pragma SQLite ignores `FOREIGN KEY` declarations entirely.
- **Schema migrations.** Managed by [Alembic]. See
  [`migrations/README.md`](migrations/README.md).

[Dioxus]: https://dioxuslabs.com/
[Flask]: https://flask.palletsprojects.com/
[Alembic]: https://alembic.sqlalchemy.org/

## Repository Structure

| Path | What lives here |
|------|-----------------|
| [`app/`](app/README.md) | Flask backend: factory, blueprints, services, models, utils. |
| [`app/models/`](app/models/README.md) | SQLAlchemy ORM models - the canonical domain shape. |
| [`app/routes/`](app/routes/README.md) | Flask blueprints: the SPA's `/_api/` routes and the public `/api/`. |
| [`app/services/`](app/services/README.md) | Application-workflow code that routes call into. |
| [`app/utils/`](app/utils/README.md) | Helpers: scheduling, the ASS Lisp DSL, datetime, video pipeline. |
| [`app/domain/`](app/domain/README.md) | Domain enums (`MatchStatus`, `ScheduleType`, ...). |
| [`app/serializers/`](app/serializers/README.md) | DB -> JSON shape conversion. |
| [`frontend/`](frontend/README.md) | Rust/Dioxus SPA. |
| [`tests/`](tests/README.md) | Pytest suite. See also [`TESTING.md`](TESTING.md). |
| [`scripts/`](scripts/README.md) | Operational scripts (backups, data-quality checks). |
| [`migrations/`](migrations/README.md) | Alembic migrations. |
| [`setup/`](setup/README.md) | Per-OS bootstrap (`just setup` shells out to these). |
| [`build_system/`](build_system/README.md) | Dockerfile used to build the Sphinx user docs. |
| [`docs/`](docs/README.md) | End-user / Sphinx documentation (deploy runbook, ASS reference). |
| [`static/`](static/README.md) | Static assets served by Flask. |

Top-level files:

- `run_app.py` - WSGI entry point. `gunicorn run_app:app` is what
  production runs; `python run_app.py` runs the dev server.
- `models.py` - re-exports `app.models.*` so both
  `from app.models import ...` and `from models import ...` work.
- `justfile` - the canonical command surface. Run `just` (or
  `just --list`) to see every recipe.
- `pyproject.toml` - dependencies and tool config (ruff, mypy, pytest).
- `alembic.ini` - Alembic config; the env file lives in
  `migrations/env.py`.
- `init_db.py`, `reset_password.py` - small CLI utilities (legacy;
  prefer the factory-based flow for new code).

## Running the app locally

The frontend uses Rust and Dioxus. Python packages are locked in `uv.lock` and
Rust packages in `frontend/Cargo.lock`. Bootstrap 5.3.3 and Font Awesome 6.0.0
are bundled locally with their licenses, so styling does not require a CDN.

Supported platforms: macOS on Apple Silicon and Ubuntu/Debian on x86_64
(Windows users can use WSL).

### Install dependencies

If starting on a fresh machine, install [uv](https://docs.astral.sh/uv/),
[just](https://just.systems/), and [Rust](https://rustup.rs/), or use the
platform bootstrap in `setup/README.md`. Then run:

```bash
uv sync --group test
just setup-frontend
```

`setup-frontend` installs the WebAssembly target and Dioxus CLI 0.7.10 if it
is missing or a different version is installed. If Rust was just installed,
open a new terminal or run
`source "$HOME/.cargo/env"` first.

### Try the interface with sample events

Run these in two terminals from the repository root:

```bash
just demo       # sample Flask backend, http://127.0.0.1:5006
```

```bash
just frontend   # Dioxus app, http://127.0.0.1:8080
```

Open **http://127.0.0.1:8080**. Sign in as `demo-organizer`, `demo-player`, or
`demo-team` with password `arctos-demo-only` (also printed at startup). The demo
includes events, a league, rosters, matches, and side competitions for exploring
the workflows. It uses `instance/demo.db`, preserves changes on restart, and
never writes to `instance/tournament.db`.

Before using an existing database, back it up and apply pending migrations.
See [migration and image-storage notes](migrations/README.md#profile-and-event-banners).
Existing demo databases are preserved on restart; they are not automatically
migrated. Fresh demos use the current models.

To preview an existing versioned SQLite snapshot, run the backend with:

```bash
uv run python -m scripts.dev_demo --snapshot /absolute/path/to/snapshot.db
```

This reads the source without modifying it, migrates a working copy at
`instance/snapshot-demo/demo.db`, and adds the fictional demo accounts and
examples. Repeating the command preserves the working copy and its edits.
The ordinary `instance/demo.db` is kept separately. Snapshots with reserved
demo IDs are rejected instead of changing existing accounts. The snapshot must
have a known Alembic revision; the launcher never stamps an unknown or
unversioned database. Add `--seed-only` to prepare the copy without starting
the server. Uploaded media is not copied by this command; make its existing
`static/uploads` files available separately.

For a populated event to practice organizing, run this after preparing and
migrating the working demo:

```bash
uv run python -m scripts.dev_practice --database instance/snapshot-demo/demo.db
```

This adds an organizer-owned practice event with teams, rosters, fields,
completed and runnable matches, and an active dueling side event. The command
prints links to its tools. It preserves edits on later runs; use a new
`--slug demo-practice-2` for an independent example. Use
`--database instance/demo.db` for the ordinary demo. See
[scripts/README.md](scripts/README.md#local-demo-data) for database restrictions.

For a phone on your local network, start the frontend with
`just frontend --addr 0.0.0.0`, then open your computer's LAN address on port
8080. The development server proxies `/_api/`, `/api/`, and `/static/` to
Flask; the phone does not need to reach a second port. Camera recording and
some browser APIs require HTTPS (or localhost), so test those on localhost
or a trusted HTTPS development setup.

### Use your own local database

Copy `.env.example` to `.env` and set `SECRET_KEY` to a random value. Keep
`ARCTOS_CORS_DEV=0` for the normal same-origin development setup. Initialize
or migrate the database, then start the backend:

```bash
just db-migrate-safe
just dev
```

Run `just frontend` separately. By default, API URLs use the page's origin
in both development and production. `ARCTOS_API_BASE` can be set **when
building the frontend** for an intentional separate backend; that setup
also needs the corresponding backend CORS/cookie configuration.

For Google sign-in through the development proxy, set `GOOGLE_REDIRECT_URI`
to the exact frontend callback URL registered with Google (for example,
`http://127.0.0.1:8080/_api/auth/google/callback`).

Google sign-in and YouTube upload require your own credentials; ordinary
local account login and event workflows do not. See [environment
variables](docs/env-vars.md) for integration configuration.

### Production

```bash
cd frontend
dx bundle --release
```

Serve the bundle behind nginx, proxying API/static requests to Flask as
described in [the deployment guide](docs/DEPLOY.md). `just run` starts
gunicorn using `.env`; it defaults to TLS on port 8081. Use
`just certfile= keyfile= run` when TLS is handled by your reverse proxy.
`just certs` creates local self-signed certificates if needed.

## Daily commands

| Goal | Command |
|------|---------|
| Run backend tests | `just test` |
| Run frontend unit tests | `just frontend-test` |
| Lint | `just lint` |
| Check typed Python APIs | `just typecheck` |
| Format | `just format` |
| Apply migrations | `just db-migrate-safe` |
| Generate a migration | `just db-revision "snake_case_message"` |
| Start dev backend | `just demo` (sample data), `just dev` (HTTP, :5006), or `just run` (TLS, :8081) |
| Start dev frontend | `just frontend` |

Coverage is configured under `[tool.coverage.*]` in `pyproject.toml`
with a `fail_under = 30` floor and branch coverage enabled. See
[`TESTING.md`](TESTING.md#coverage) for coverage commands and how to interpret the report.

## Conventions

- **Application API routes live under `/_api/`.** Browser page routes are
  declared in `frontend/src/main.rs`. The public API for third-party clients
  lives under `/api/` and uses bearer tokens.
- **Check each endpoint's response contract.** Application APIs generally
  return JSON; validation, authentication, permissions, missing records, and
  conflicts have distinct HTTP statuses. OAuth callbacks use redirects, and
  file/media endpoints may return a body other than JSON.
- **`Result` / `Option` for errors as values.** Services return
  `Result[T, ArctosError]`. The `.Q()` method (Rust's `?`) propagates
  errors when used inside `@allow_Q`-decorated functions. See
  [`app/error_values.py`](app/error_values.py).
- **Money is `Numeric(10, 2)`, never `float`.** Penny-exact
  reconciliation across many partial payments requires exact decimals.
- **Times are stored as naive UTC.** The model layer converts
  client-supplied times to UTC and strips the tzinfo before persisting.
  The frontend handles timezone display.
- **Join tables for multi-value data.** `MatchReferee`, `MatchPlayer`,
  `HeadRefAllowList`, `CameraTimepoint` are accessed through the
  helpers in [`app/services/dual_write.py`](app/services/dual_write.py),
  not by attribute on the parent model.
- **Tournaments belong to *either* a league *or* a `RegistrableConfig`.**
  This mutual exclusivity is enforced by a CHECK constraint. League
  events inherit the league's registration config; standalone events
  own theirs. Use `app.utils.helpers.get_registrable_config(tournament)`
  to dereference correctly in either case.

## End to End Requests

Tracing a single request is the fastest way to learn the codebase.
Take `POST /_api/<tournament_url>/register-team`:

1. **Frontend** (`frontend/src/`) builds a form, POSTs via `reqwest`
   with credentials.
2. **nginx** (in production) proxies anything starting with `/_api/`
   or `/api/` to gunicorn and serves static/media paths separately. The Dioxus
   development proxy also forwards API and static requests to Flask.
3. **Flask** routes the request to
   `app/routes/registration.py::register_team_for_tournament`.
4. The route does a thin auth/shape check, then calls
   `RegistrationService.register_team(...)` in
   `app/services/registration_service.py`.
5. The service returns a `Result[T, ArctosError]`. Routes
   pattern-match on the result and return JSON `{success, ...}` with
   the endpoint's success or error status.
6. The service mutates `TeamRegistration` rows via SQLAlchemy. ORM
   models live in `app/models/`. `db.session.commit()` writes to
   SQLite.

Four layers: **route -> service -> model -> db**. Routes stay thin;
business logic lives in services; persistence lives in models.

## FAQ

- *I want to understand domain shapes* -> `app/models/`.
- *I want to add an endpoint* -> `app/routes/` (and probably a service
  in `app/services/`).
- *I want to understand match scheduling* -> `app/utils/scheduling.py`
  and `app/utils/MatchGraph.py`.
- *I want to understand the skip-condition / ASS DSL* ->
  `app/utils/parser.py`, `app/utils/grammar.lark`, and the
  user-facing reference at `docs/arctos-schedule-script.md`.
- *I want to deploy* -> `docs/DEPLOY.md`.
- *I want to add a database column* -> `migrations/README.md`.
- *I want to understand video footage upload* ->
  `app/routes/tournaments/footage.py`, `app/utils/user_uploads.py`,
  `app/utils/youtube_upload.py`.

## Help

Bug reports and feature requests live on
[GitHub](https://github.com/reid23/arctos/issues). Branching, PR
process, and code-quality expectations live in
[`CONTRIBUTING.md`](CONTRIBUTING.md). Test guidance lives in
[`TESTING.md`](TESTING.md).
