#!/bin/bash
set -euo pipefail

SOLUTION_DIR="$(cd "$(dirname "$0")" && pwd)"
cp "$SOLUTION_DIR/reference.py" /app/linkage_ledger.py
chmod 0755 /app/linkage_ledger.py

cd /app
python3 linkage_ledger.py \
  data/current_run.json \
  data/result.json

python3 linkage_ledger.py \
  data/current_run.json \
  data/result.json
