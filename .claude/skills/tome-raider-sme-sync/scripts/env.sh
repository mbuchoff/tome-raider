#!/usr/bin/env bash
# Prints the Python to run the sync scripts with, making its virtualenv first if needed: the app's own requirements
# (azure-storage-blob, pillow, ...) in ~/.cache/tome-raider-sync/venv, outside the repos. This container's python3 has no
# ensurepip, so pip comes from the official get-pip.py.
# usage: PY=$(bash scripts/env.sh)
set -euo pipefail
cache="${XDG_CACHE_HOME:-$HOME/.cache}/tome-raider-sync"
venv="$cache/venv"
req="$(cd -P "$(dirname "$0")/../../../.." && pwd)/requirements.txt"   # the poc repo's, four levels up (-P: also through a link to this skill)
if [ ! -x "$venv/bin/pip" ]; then
  rm -rf "$venv"
  python3 -m venv "$venv" >&2 2>/dev/null || {
    rm -rf "$venv"; python3 -m venv --without-pip "$venv" >&2
    curl -sSfL https://bootstrap.pypa.io/get-pip.py -o "$cache/get-pip.py"
    "$venv/bin/python" "$cache/get-pip.py" -q >&2
  }
fi
if ! cmp -s "$req" "$venv/.requirements"; then
  "$venv/bin/pip" install -q --disable-pip-version-check -r "$req" >&2
  cp "$req" "$venv/.requirements"
fi
echo "$venv/bin/python"
