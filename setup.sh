#!/bin/bash
# One-time setup: venv + dependencies + .env template.
set -e
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip -q
pip install -r requirements.txt -q

if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env — fill in your credentials (META_ACCESS_TOKEN, META_AD_ACCOUNT_ID, ...)."
fi

echo "Setup done. Run: ./run.sh account"
