# shellcheck shell=sh
# Usage: . scripts/env.sh
EIM_ROOT="${EIM_ROOT:-$HOME/.espressif}"
IDF_VERSION="${IDF_VERSION:-v5.5.3}"
ACTIVATE="$EIM_ROOT/tools/activate_idf_${IDF_VERSION}.sh"

if [ ! -f "$ACTIVATE" ]; then
    echo "error: $ACTIVATE not found. Run the repository's scripts/setup.sh first." >&2
    # Return when sourced, exit when invoked as a script.
    # shellcheck disable=SC2317
    return 1 2>/dev/null || exit 1
fi

# The activation script is selected by the configured EIM root and version.
# shellcheck disable=SC1090
. "$ACTIVATE"
# EIM exposes idf.py as a shell function. Put the executable on PATH too so
# subprocess callers such as `rtk proxy idf.py` use the activated interpreter.
PATH="$IDF_PATH/tools:$PATH"
export PATH
echo "ESP-IDF $IDF_VERSION activated (target: esp32s3)"
