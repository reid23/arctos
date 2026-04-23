#!/bin/bash
set -euo pipefail

directory=$(dirname "${BASH_SOURCE[0]}")
min_cargo_version="1.85.0"

version_ge() {
    # Returns 0 if $1 >= $2 using semantic-ish sort.
    [ "$(printf '%s\n%s\n' "$2" "$1" | sort -V | head -n1)" = "$2" ]
}

install_or_upgrade_rustup() {
    if ! command -v rustup >/dev/null 2>&1; then
        curl --proto "=https" --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --default-toolchain stable
    else
        rustup toolchain install stable
        rustup default stable
    fi
    # shellcheck source=/dev/null
    . "${HOME}/.cargo/env"
}

xargs sudo apt-get install -y < "${directory}/apt-packages.txt"
echo "Installed apt packages"

if command -v cargo >/dev/null 2>&1; then
    current_cargo_version="$(cargo --version | awk '{print $2}')"
    if ! version_ge "${current_cargo_version}" "${min_cargo_version}"; then
        echo "Cargo ${current_cargo_version} is too old; installing rustup stable toolchain"
        install_or_upgrade_rustup
    fi
else
    echo "Cargo not found from apt packages; installing rustup stable toolchain"
    install_or_upgrade_rustup
fi
echo "Cargo is available: $(cargo --version)"
rustup target add wasm32-unknown-unknown >/dev/null 2>&1 || true

bash -c "$(curl -LsSf https://astral.sh/uv/install.sh)"
echo "Installed uv"

chmod +x "${directory}/setup-python.sh"
"${directory}/setup-python.sh"
