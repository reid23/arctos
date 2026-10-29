#!/bin/bash

set -e

. $BASHRC_PATH

cd $REPO_ROOT


if [ "${HAS_PULLED:-0}" -ne 1 ]; then
    git fetch origin
    git reset --hard $GIT_BRANCH
    exec env HAS_PULLED=1 $REPO_ROOT/update.sh
    exit 0
fi

. $CARGO_ENV_FILE

# Bootstrap just on the deploy host if it isn't already on PATH.
# Mirrors setup/setup-ubuntu.sh; idempotent so subsequent deploys skip.
if ! command -v just &> /dev/null; then
    mkdir -p "${HOME}/.local/bin"
    curl --proto '=https' --tlsv1.2 -sSf https://just.systems/install.sh \
        | bash -s -- --to "${HOME}/.local/bin"
    export PATH="${HOME}/.local/bin:${PATH}"
fi

cd frontend
dx bundle --web --release
uv run python ../scripts/prepare_public_assets.py --dist dist/public
if [ -n "${ARCTOS_FRONTEND_DIST:-}" ]; then
  uv run python ../scripts/stage_frontend.py dist/public "$ARCTOS_FRONTEND_DIST"
else
  # Preserve the existing deployment destination until the public document
  # proxy and Flask's ARCTOS_FRONTEND_DIST are configured together.
  # Keep copied assets readable by nginx without granting public write access.
  (
    umask 022
    chmod -R u=rwX,go=rX dist/public
    cp -r dist/public "$FRONTEND_SERVER_ROOT"
  )
fi

cd ..

systemctl --user stop arctos
just db-migrate-safe
systemctl --user start arctos

if [ "$BUILD_DOCS" -eq 1 ]; then
  just docs
  chmod -R a+rwX docs/_build
  cp -r docs/_build/html/* $DOCS_SERVER_ROOT
fi

set +e
