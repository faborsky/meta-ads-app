#!/bin/bash
# Convenience wrapper: activate the venv and run the CLI.
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -d "$DIR/.venv" ]; then
  source "$DIR/.venv/bin/activate"
elif [ -d "$DIR/venv" ]; then
  source "$DIR/venv/bin/activate"
else
  echo "No venv found — run ./setup.sh first." >&2
  exit 1
fi
exec python "$DIR/meta_ads_cli.py" "$@"
