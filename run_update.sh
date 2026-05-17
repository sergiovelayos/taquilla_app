#!/usr/bin/env bash
# run_update.sh — cron wrapper for taquilla weekly update.
# Ensures correct PATH and working directory regardless of cron environment.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_FILE="$SCRIPT_DIR/logs/cron.log"

cd "$SCRIPT_DIR"

# Ensure logs directory exists
mkdir -p "$SCRIPT_DIR/logs"

echo "=== Weekly Update Started: $(date) ===" >> "$LOG_FILE"

# 1. Update Box Office Data (PDF Scraping & Parsing)
echo "[$(date)] Phase 1: Taquilla PDF Update..." >> "$LOG_FILE"
"$SCRIPT_DIR/venv/bin/python3" "$SCRIPT_DIR/update.py" "$@" || {
    EXIT_CODE=$?
    if [ "$EXIT_CODE" -eq 1 ]; then
        echo "[$(date)] Phase 1: no new PDFs found. Continuing." >> "$LOG_FILE"
    else
        echo "[$(date)] Phase 1 failed (exit $EXIT_CODE). Check logs/update.log for details." >> "$LOG_FILE"
        exit "$EXIT_CODE"
    fi
}
echo "[$(date)] Phase 1 completed." >> "$LOG_FILE"

# 2. Enrich with TMDB Data
echo "[$(date)] Phase 2: TMDB Enrichment..." >> "$LOG_FILE"
if "$SCRIPT_DIR/venv/bin/python3" "$SCRIPT_DIR/tmdb_enricher.py"; then
    echo "[$(date)] Phase 2 completed successfully." >> "$LOG_FILE"
else
    echo "[$(date)] Phase 2 failed. Check console output or log for details." >> "$LOG_FILE"
    exit 1
fi

echo "=== Weekly Update Finished: $(date) ===" >> "$LOG_FILE"
echo "" >> "$LOG_FILE"

# Marca de éxito para el reintento de mediodía
date +%Y-%m-%d > "$SCRIPT_DIR/logs/.last_success_date"
