#!/usr/bin/env bash
set -euo pipefail

IDF_VERSION="${IDF_VERSION:-v5.5.3}"
EIM_ROOT="${EIM_ROOT:-$HOME/.espressif}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if ! command -v eim >/dev/null 2>&1; then
    echo "error: eim not found. Install the ESP-IDF Installation Manager CLI first:" >&2
    echo "https://docs.espressif.com/projects/idf-im-ui/en/latest/" >&2
    exit 1
fi

if [ -f "$EIM_ROOT/tools/activate_idf_${IDF_VERSION}.sh" ]; then
    echo "ESP-IDF $IDF_VERSION already installed in $EIM_ROOT"
else
    echo "Installing ESP-IDF $IDF_VERSION into $EIM_ROOT ..."
    eim install --config "$REPO_ROOT/scripts/eim-config.toml" -p "$EIM_ROOT"
fi

echo "Done. Activate with: source \"$REPO_ROOT/scripts/env.sh\""
