#!/usr/bin/env bash
set -eu
root="$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)"
cd "$root"
# Prefer the app's user-owned runtime when installed; keep source checkouts usable
# with their existing Python environment on other machines.
runtime="${XDG_DATA_HOME:-$HOME/.local/share}/2photon/lighttable-venv/bin/python"
if [ -x "$runtime" ]; then
    exec "$runtime" -m apps.lighttable.app "$@"
fi
exec python3 -m apps.lighttable.app "$@"
