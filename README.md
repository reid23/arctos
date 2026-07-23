# Arctos

[![Tests](https://github.com/reid23/arctos/actions/workflows/test.yml/badge.svg)](https://github.com/reid23/arctos/actions/workflows/test.yml)
[![Coverage floor](https://img.shields.io/badge/coverage%20floor-30%25-yellow)](TESTING.md#coverage)
[![Python](https://img.shields.io/badge/python-3.12%2B-blue)](pyproject.toml)
[![License: GPL v3](https://img.shields.io/badge/license-GPLv3-blue.svg)](LICENSE)

Centralized online results and event management for Jugger.

Or, *what the fog site always wanted to be*

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
- **Backend.** Python 3.12 [Flask] app run under gunicorn. Serves only
  JSON, exclusively under the `/_api/` prefix. `/api/` (no underscore)
  is reserved for a future public API and is not used.
- **Database.** A single SQLite file (`tournament.db`). WAL mode is
  enabled so the finalize-recording worker and HTTP handlers can share
  the file without blocking each other. Foreign keys are enforced via
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
| [`app/routes/`](app/routes/README.md) | Flask blueprints. Every route lives under `/_api/`. |
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

## Running the app

### Backend

> Supported platforms: macOS on Apple Silicon and Ubuntu/Debian on
> x86_64. Windows isn't supported directly; use WSL.

1. Install [uv](https://docs.astral.sh/uv/).
2. Set up your SSL certs. If you're using nginx you can do this there
   and use [certbot](https://certbot.eff.org/). If you're just testing, you can
   generate self-signed certs with:

```bash
openssl req -x509 -newkey rsa:4096 -keyout key.pem -out cert.pem -sha256 -days 365
```

or

```bash
just certs
```

   This writes `cert.pem` and `key.pem` to the repo root (valid for
   365 days, `CN=localhost`). Override with
   `just cert_days=730 cert_subject=/CN=arctos.example.com certs`,
   and pass `force=1` to overwrite existing certs.

3. Create a `.env` file at the repo root with the variables you need:

```bash
ARCTOS_CORS_DEV=1
ARCTOS_API_BASE=http://127.0.0.1:8081
YOUTUBE_API_KEY=your_youtube_api_key
GOOGLE_CLIENT_SECRET=your_google_client_secret
GOOGLE_CLIENT_ID=your_google_client_id
SECRET_KEY=your_app_secret_key
```

If you don't have some of these, you can leave them empty; they are
only needed for the sign in with google and youtube auto-seek
features. The `SECRET_KEY` variable must be a random value for
security reasons. You can get one by running

```bash
python -c "import os; print(os.urandom(12).hex())"
```

> [!IMPORTANT]
>
> The `ARCTOS_CORS_DEV` and `ARCTOS_API_BASE` are only for dev
> environments where you don't have a reverse proxy set up to direct
> traffic and are thus hosting the frontend and backend on different
> ports.

1. Start the app:

```bash
just run
```

This loads `.env`, runs `uv sync`, and starts gunicorn. The defaults
match the example above (5 workers, binding `0.0.0.0:8081`, using
`cert.pem`/`key.pem`). Override any of them on the command line, e.g.:

```bash
just workers=10 bind=0.0.0.0:9000 run
just certfile= keyfile= run          # if you handle SSL elsewhere
just env_file=.env.prod run          # use a different env file
```

#### Video storage

To store finalized match recordings in an s3 compatible bucket (I use
Backblaze B2) instead of local disk, set these environment variables
in your `run` script:

| Variable | Required | Description |
|----------|----------|-------------|
| `S3_VIDEO_BUCKET` | Yes | bucket name (create a private bucket in the B2 dashboard). |
| `S3_ENDPOINT_URL` | Yes (for B2) | B2 S3-compatible endpoint, e.g. `https://s3.us-west-002.backblazeb2.com`. Use the endpoint for the region where you created the bucket. |
| `AWS_REGION` | Yes (for B2) | Must match the endpoint region, e.g. `us-west-002` or `us-east-005`. |
| `AWS_ACCESS_KEY_ID` | Yes | Application Key ID. Needs R/W access. |
| `AWS_SECRET_ACCESS_KEY` | Yes | corresponding secret key |
| `S3_PRESIGNED_EXPIRY_SECONDS` | No | Presigned URL lifetime in seconds (default `3600`). |

### Frontend

Install the Dioxus CLI:

```bash
cargo install dioxus-cli
```

then (for development) serve the app:

```bash
just frontend            # equivalent to `cd frontend && dx serve`
```

In production, you should run `dx bundle --release` and copy the
output files to somewhere that your reverse proxy can serve.

## Daily commands

| Goal | Command |
|------|---------|
| Run all tests | `just test` |
| Lint | `just lint` |
| Format | `just format` |
| Apply migrations | `just db-migrate-safe` |
| Generate a migration | `just db-revision "snake_case_message"` |
| Start dev backend | `just dev` (HTTP, :5006) or `just run` (TLS, :8081) |
| Start dev frontend | `just frontend` |

Coverage is configured under `[tool.coverage.*]` in `pyproject.toml`
with a soft `fail_under = 30` floor; current actual is around 33% with
branch coverage on. See [`TESTING.md`](TESTING.md#coverage) for the
HTML report path and how the threshold is meant to move.

## Conventions

- **All API routes live under `/_api/`.** Tests and the frontend always
  hit `/_api/...`.
- **Routes return JSON, not redirects.** Success is `200`; validation
  failure is `400`; unauthenticated is `401`. Don't assert on redirect
  codes in tests.
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
   to gunicorn; everything else is the SPA. In dev with
   `ARCTOS_CORS_DEV=1` the browser hits Flask directly.
3. **Flask** routes the request to
   `app/routes/registration.py::register_team_for_tournament`.
4. The route does a thin auth/shape check, then calls
   `RegistrationService.register_team(...)` in
   `app/services/registration_service.py`.
5. The service returns a `Result[T, ArctosError]`. Routes
   pattern-match on the result and return JSON `{success, ...}` with
   HTTP 200/400.
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
- *I want to understand video upload / finalisation* ->
  `app/utils/footage.py`, `app/utils/s3_video.py`,
  `app/utils/youtube_upload.py`.

## Help

Bug reports and feature requests live on
[GitHub](https://github.com/reid23/arctos/issues). Branching, PR
process, and code-quality expectations live in
[`CONTRIBUTING.md`](CONTRIBUTING.md). Test guidance lives in
[`TESTING.md`](TESTING.md).
