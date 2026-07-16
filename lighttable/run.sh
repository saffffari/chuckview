#!/usr/bin/env bash
set -eu
root="$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)"
cd "$root"
exec python3 -m apps.lighttable.app "$@"
