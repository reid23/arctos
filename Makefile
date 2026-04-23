.DEFAULT_GOAL := help

SETUP_DIR := setup
UNAME_S   := $(shell uname -s)
UNAME_M   := $(shell uname -m)
REPO_ROOT := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
ENV_FILE  ?= .env

# Defaults for the gunicorn invocation; override on the command line, e.g.
#   make run WORKERS=10 BIND=0.0.0.0:9000 CERTFILE= KEYFILE=
WORKERS  ?= 5
BIND     ?= 0.0.0.0:8081
CERTFILE ?= cert.pem
KEYFILE  ?= key.pem

# Self-signed cert defaults; override on the command line, e.g.
#   make certs CERT_DAYS=730 CERT_SUBJECT=/CN=arctos.example.com
CERT_DAYS    ?= 365
CERT_SUBJECT ?= /CN=localhost

.PHONY: help setup setup-os setup-macos setup-ubuntu setup-python setup-frontend install all run migrate certs test unit integration format

help: ## Show available targets
	@awk 'BEGIN {FS = ":.*##"; printf "Usage: make <target>\n\nTargets:\n"} \
		/^[a-zA-Z_-]+:.*?##/ { printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2 }' $(MAKEFILE_LIST)

all: setup install setup-frontend ## Run full setup: system deps, python deps, and frontend tools

setup: setup-os ## Auto-detect OS and run the appropriate system setup

setup-os:
ifeq ($(UNAME_S),Darwin)
	@$(MAKE) --no-print-directory setup-macos
else ifeq ($(UNAME_S),Linux)
	@$(MAKE) --no-print-directory setup-ubuntu
else
	@echo "Unsupported OS: $(UNAME_S). Only macOS and Linux (Ubuntu) are supported."; exit 1
endif

setup-macos: ## Install macOS system dependencies (Homebrew, Rust/Cargo, uv, Python)
	@chmod +x $(SETUP_DIR)/setup-macos.sh
	@$(SETUP_DIR)/setup-macos.sh

setup-ubuntu: ## Install Ubuntu system dependencies (apt packages incl. Rust/Cargo, uv, Python)
	@chmod +x $(SETUP_DIR)/setup-ubuntu.sh
	@$(SETUP_DIR)/setup-ubuntu.sh

setup-python: ## Install the project's Python toolchain via uv
	@chmod +x $(SETUP_DIR)/setup-python.sh
	@$(SETUP_DIR)/setup-python.sh

install: ## Sync backend Python dependencies with uv
	@uv sync

setup-frontend: ## Install the Dioxus CLI required to build/serve the frontend
	@command -v cargo >/dev/null || { echo "cargo not found; run 'make setup' first"; exit 1; }
	@cargo install dioxus-cli

certs: ## Generate self-signed SSL certs at $(CERTFILE)/$(KEYFILE) (use FORCE=1 to overwrite)
	@if [ -z "$(FORCE)" ] && { [ -f "$(CERTFILE)" ] || [ -f "$(KEYFILE)" ]; }; then \
		echo "refusing to overwrite existing $(CERTFILE) / $(KEYFILE); pass FORCE=1 to regenerate"; \
		exit 1; \
	fi
	@command -v openssl >/dev/null || { echo "openssl not found in PATH"; exit 1; }
	@openssl req -x509 -newkey rsa:4096 \
		-keyout "$(KEYFILE)" -out "$(CERTFILE)" \
		-sha256 -days $(CERT_DAYS) -nodes \
		-subj "$(CERT_SUBJECT)"
	@chmod 600 "$(KEYFILE)"
	@echo "Generated $(CERTFILE) and $(KEYFILE) (valid $(CERT_DAYS) days, subject $(CERT_SUBJECT))"

run: ## Run the backend with gunicorn (loads $(ENV_FILE) if present)
	@uv sync
	@if [ ! -f "$(ENV_FILE)" ]; then \
		echo "warning: $(ENV_FILE) not found; continuing with current environment only"; \
	fi
	@set -a; \
	[ -f "$(ENV_FILE)" ] && . "./$(ENV_FILE)"; \
	set +a; \
	PYTHONPATH="$(REPO_ROOT):$$PYTHONPATH" \
	uv run gunicorn \
		--workers=$(WORKERS) \
		--bind $(BIND) \
		--log-level debug \
		$(if $(strip $(CERTFILE)),--certfile=$(CERTFILE)) \
		$(if $(strip $(KEYFILE)),--keyfile=$(KEYFILE)) \
		run_app:app

migrate: ## Apply app-managed schema migrations
	@uv sync
	@PYTHONPATH="$(REPO_ROOT):$$PYTHONPATH" uv run python scripts/run_migrations.py

test:
	uv run pytest tests/

unit:
	uv run pytest tests/ -m unit

integration:
	uv run pytest tests/ -m integration

format:
	uv run black .