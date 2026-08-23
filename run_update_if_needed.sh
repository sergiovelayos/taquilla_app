#!/usr/bin/env bash
# run_update_if_needed.sh — reintento de mediodía.
# Re-run unless today's sentinel exists and the expected weekend is in the DB.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_FILE="$SCRIPT_DIR/logs/cron.log"
SENTINEL="$SCRIPT_DIR/logs/.last_success_date"
TODAY=$(date +%Y-%m-%d)
PYTHON="$SCRIPT_DIR/venv/bin/python3"

mkdir -p "$SCRIPT_DIR/logs"

if [ -f "$SENTINEL" ] && [ "$(cat "$SENTINEL")" = "$TODAY" ]; then
    if "$PYTHON" "$SCRIPT_DIR/update.py" --check-current-week >> "$LOG_FILE" 2>&1; then
        echo "[$(date)] Reintento 12h: semana esperada ya cargada. Nada que hacer." >> "$LOG_FILE"
        exit 0
    fi
    echo "[$(date)] Reintento 12h: sentinel obsoleto; faltan datos semanales." >> "$LOG_FILE"
fi

echo "[$(date)] Reintento 12h: actualización no completada aún. Lanzando ahora..." >> "$LOG_FILE"
exec "$SCRIPT_DIR/run_update.sh"
