#!/usr/bin/env bash
# run_update.sh — cron wrapper for taquilla weekly update.
# Ensures correct PATH and working directory regardless of cron environment.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

cd "$SCRIPT_DIR"

# Use the virtual environment's python
exec "$SCRIPT_DIR/venv/bin/python3" "$SCRIPT_DIR/update.py" "$@"
