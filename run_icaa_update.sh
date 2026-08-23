#!/usr/bin/env bash
# Daily ICAA catalogue update: recent list -> detail HTMLs -> database.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="$SCRIPT_DIR/venv/bin/python3"

cd "$SCRIPT_DIR"

echo "=== ICAA Update Started: $(date) ==="

echo "[$(date)] Phase 1: latest classified films..."
"$PYTHON" "$SCRIPT_DIR/icaa_ultimas_calificadas.py"

echo "[$(date)] Phase 2: detail page downloads..."
"$PYTHON" "$SCRIPT_DIR/icaa_downloader.py" --latest

echo "[$(date)] Phase 3: detail parsing, DB upsert and temporary HTML cleanup..."
"$PYTHON" "$SCRIPT_DIR/icaa_parser.py" --delete-parsed

echo "[$(date)] Phase 4: local subsidy matching (no external search)..."
"$PYTHON" "$SCRIPT_DIR/scripts/subvenciones_matching_local.py" --apply

echo "=== ICAA Update Finished: $(date) ==="
echo ""
