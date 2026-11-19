#!/usr/bin/env bash
set -e

# Resolve script directory
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

# Prefer virtualenv python if available (.venv or venv)
if [ -x ".venv/bin/python3" ]; then
    exec ".venv/bin/python3" run.py "$@"
elif [ -x ".venv/bin/python" ]; then
    exec ".venv/bin/python" run.py "$@"
elif [ -x "venv/bin/python3" ]; then
    exec "venv/bin/python3" run.py "$@"
elif [ -x "venv/bin/python" ]; then
    exec "venv/bin/python" run.py "$@"
else
    exec python3 run.py "$@"
fi
