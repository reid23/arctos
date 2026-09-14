# Arctos task runner. Run `just` or `just --list` to see available recipes.

# ── Tunables ──────────────────────────────────────────────────────────────────
# Override at the CLI, e.g. `just workers=10 bind=0.0.0.0:9000 run`.

workers      := "5"
bind         := "0.0.0.0:8081"
certfile     := "cert.pem"
keyfile      := "key.pem"
env_file     := ".env"
cert_days    := "365"
cert_subject := "/CN=localhost"

# Default recipe: show the list.
default:
    @just --list

# ── Setup ─────────────────────────────────────────────────────────────────────

# Auto-detect OS and run the matching system setup.
setup:
    #!/usr/bin/env bash
    set -euo pipefail
    case "$(uname -s)" in
      Darwin) just setup-macos ;;
      Linux)  just setup-ubuntu ;;
      *)      echo "Unsupported OS: $(uname -s). Only macOS and Linux (Ubuntu) are supported."; exit 1 ;;
    esac

# Install macOS system dependencies (Homebrew, Xcode CLI, packages, Python).
setup-macos:
    @chmod +x setup/setup-macos.sh
    @setup/setup-macos.sh

# Install Ubuntu system dependencies (apt packages, uv, just, Python).
setup-ubuntu:
    @chmod +x setup/setup-ubuntu.sh
    @setup/setup-ubuntu.sh

# Install the project's Python toolchain via uv.
setup-python:
    @chmod +x setup/setup-python.sh
    @setup/setup-python.sh

# Sync Python dependencies and install pre-commit hooks.
install:
    @uv sync
    @uv run pre-commit install

# Install the Dioxus CLI required to build/serve the frontend if missing.
setup-frontend:
    #!/usr/bin/env bash
    set -euo pipefail
    if ! command -v dx >/dev/null 2>&1; then
      cargo install dioxus-cli
      echo "Installed Dioxus CLI (dx)"
    else
      echo "Dioxus CLI (dx) is already installed"
    fi

# ── Lint & Format ─────────────────────────────────────────────────────────────

# Run ruff.
lint:
    @uv run ruff check .

# Run ruff with autofix.
lint-fix:
    @uv run ruff check --fix .

# Auto-format code with ruff.
format:
    @uv run ruff format .

# ── Test ──────────────────────────────────────────────────────────────────────

# Run all tests. Pass extra pytest args after the recipe name: `just test -k foo`.
[positional-arguments]
test *ARGS:
    @uv run pytest tests/ "$@"

# Run unit tests only.
[positional-arguments]
unit *ARGS:
    @uv run pytest tests/ -m unit "$@"

# Run integration tests only.
[positional-arguments]
integration *ARGS:
    @uv run pytest tests/ -m integration "$@"

# Run a coverage report. Pass extra pytest args after the recipe name.
[positional-arguments]
coverage *ARGS:
    @uv run pytest tests/ --cov=app --cov-report=term-missing --cov-fail-under=0 "$@"

# Run coverage with the configured fail-under threshold.
[positional-arguments]
coverage-check *ARGS:
    @uv run pytest tests/ --cov=app --cov-report=term-missing "$@"

# ── Run ───────────────────────────────────────────────────────────────────────

# Run the backend with gunicorn. Loads {{env_file}} if present.
run:
    #!/usr/bin/env bash
    set -euo pipefail
    uv sync
    if [ ! -f "{{env_file}}" ]; then
      echo "warning: {{env_file}} not found; continuing with current environment only"
    fi
    set -a
    [ -f "{{env_file}}" ] && . "{{env_file}}"
    set +a
    export PYTHONPATH="$(pwd):${PYTHONPATH:-}"
    args=(--workers={{workers}} --bind {{bind}} --log-level debug)
    [ -n "{{certfile}}" ] && args+=(--certfile={{certfile}})
    [ -n "{{keyfile}}" ]  && args+=(--keyfile={{keyfile}})
    uv run gunicorn "${args[@]}" run_app:app

# Run the backend for local dev: HTTP on :5006, no TLS.
dev:
    @ARCTOS_CORS_DEV=1 just bind=0.0.0.0:5006 certfile= keyfile= run

# Serve the Dioxus frontend.
frontend:
    @cd frontend && dx serve

# Run frontend unit tests on the host target (`.cargo/config.toml` defaults to wasm32).
frontend-test:
    #!/usr/bin/env bash
    set -euo pipefail
    host="$(rustc -vV | sed -n 's/^host: //p')"
    cd frontend && cargo test --target "$host"

# ── Database ──────────────────────────────────────────────────────────────────

# One-shot: stamp an existing database at the current alembic head.
db-baseline:
    @uv run alembic stamp head

# Apply all outstanding migrations. Run `just db-backup` first in production.
db-migrate:
    @uv run alembic upgrade head

# Backup then migrate. Safer default for shared environments.
db-migrate-safe: db-backup db-migrate

# Autogenerate a migration. Usage: `just db-revision "snake_case_message"`.
db-revision MSG:
    @uv run alembic revision --autogenerate -m "{{MSG}}"

# Print the revision currently applied to the database.
db-current:
    @uv run alembic current

# Print the full alembic revision history.
db-history:
    @uv run alembic history

# Snapshot the live SQLite database to backups/. Pass an optional tag.
db-backup tag="":
    @chmod +x scripts/backup_db.sh
    @scripts/backup_db.sh {{tag}}

# Report rows that violate would-be-unique column groups.
db-check-duplicates:
    @uv run python scripts/check_duplicates.py

# Open a sqlite3 shell against the local DB.
db-shell:
    @sqlite3 instance/tournament.db

# ── Docs ──────────────────────────────────────────────────────────────────────

docs_image := "arctos-sphinx"

# Build the Sphinx docs Docker image.
docs-image:
    @docker build -t {{docs_image}} -f build_system/Dockerfile .

# Build the Sphinx docs in Docker (no local Python deps needed).
docs: docs-image
    #!/usr/bin/env bash
    set -euo pipefail
    docker run --rm \
        --user "$(id -u):$(id -g)" \
        -v "$(pwd):/project" \
        -w /project \
        {{docs_image}} \
        sphinx-build --color -j auto -b html docs docs/_build/html
    echo "Docs built: docs/_build/html/index.html"

# Delete the Sphinx build output.
docs-clean:
    @rm -rf docs/_build
    @echo "Cleaned docs/_build"

# ── Misc ──────────────────────────────────────────────────────────────────────

# Generate self-signed SSL certs. Pass `force=1` to overwrite existing ones.
certs force="":
    #!/usr/bin/env bash
    set -euo pipefail
    if [ -z "{{force}}" ] && { [ -f "{{certfile}}" ] || [ -f "{{keyfile}}" ]; }; then
      echo "refusing to overwrite existing {{certfile}} / {{keyfile}}; pass force=1 to regenerate"
      exit 1
    fi
    command -v openssl >/dev/null || { echo "openssl not found in PATH"; exit 1; }
    openssl req -x509 -newkey rsa:4096 \
      -keyout "{{keyfile}}" -out "{{certfile}}" \
      -sha256 -days {{cert_days}} -nodes \
      -subj "{{cert_subject}}"
    chmod 600 "{{keyfile}}"
    echo "Generated {{certfile}} and {{keyfile}} (valid {{cert_days}} days, subject {{cert_subject}})"
