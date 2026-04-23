#!/bin/bash
set -euo pipefail

# installs XCode CLI tools that homebrew uses to install/compile packages
# only run this if CLI tools are not already installed, otherwise it would raise an error
if ! xcode-select -p &> /dev/null; then
    xcode-select --install
    echo "Installed XCode CLI tools"
fi

# installs homebrew if it's not yet installed
if ! command -v brew &> /dev/null; then
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    echo "Installed Homebrew"
fi

directory=$(dirname "${BASH_SOURCE[0]}")

brew bundle --file="${directory}/Brewfile"
echo "Installed Homebrew packages"
if command -v cargo >/dev/null 2>&1; then
    echo "Cargo is available: $(cargo --version)"
else
    echo "Cargo installation failed or is not on PATH" >&2
    exit 1
fi

chmod +x "${directory}/setup-python.sh"
"${directory}/setup-python.sh"
