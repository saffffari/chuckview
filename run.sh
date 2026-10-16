#!/usr/bin/env bash
set -eu
root="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
cd "$root"
# Prefer the checkout's own .venv; keep plain checkouts usable with python3.
if [ -x "$root/.venv/bin/python" ]; then
    exec "$root/.venv/bin/python" -m chuckview "$@"
fi
exec python3 -m chuckview "$@"
