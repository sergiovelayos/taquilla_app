#!/usr/bin/env bash
# run_update_if_needed.sh — reintento de mediodía.
# Solo ejecuta la actualización si no hubo éxito previo hoy.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_FILE="$SCRIPT_DIR/logs/cron.log"
SENTINEL="$SCRIPT_DIR/logs/.last_success_date"
TODAY=$(date +%Y-%m-%d)

mkdir -p "$SCRIPT_DIR/logs"

if [ -f "$SENTINEL" ] && [ "$(cat "$SENTINEL")" = "$TODAY" ]; then
    echo "[$(date)] Reintento 12h: actualización ya completada hoy ($TODAY). Nada que hacer." >> "$LOG_FILE"
    exit 0
fi

echo "[$(date)] Reintento 12h: actualización no completada aún. Lanzando ahora..." >> "$LOG_FILE"
exec "$SCRIPT_DIR/run_update.sh"
