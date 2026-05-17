# Incidencia: Permission denied en run_update.sh (Mayo 2026)

## Resumen

El cron del jueves 14 de mayo de 2026 (07:30 CEST) no ejecutó la actualización semanal. Los datos del fin de semana del 8-9 de mayo no se cargaron en la aplicación.

---

## Causa raíz

### 1. Permission denied en el script principal

El script `run_update.sh` fue editado el 13 de mayo desde el macmini (vía SMB). Al guardar el fichero desde macOS sobre la montura SMB del servidor Ubuntu, se perdió el bit de ejecución (`+x`) en el sistema de ficheros del servidor. El cron intentó ejecutarlo y recibió:

```
/bin/sh: 1: /home/sergio/taquilla_app/run_update.sh: Permission denied
```

**Patrón a recordar:** editar scripts del servidor desde macOS vía SMB puede quitar el bit de ejecución. Tras cualquier edición desde el mac, verificar con:

```bash
ls -la /home/sergio/taquilla_app/run_update.sh
# Debe mostrar -rwx... (no -rw-)
```

Y restaurar si es necesario:

```bash
chmod +x /home/sergio/taquilla_app/run_update.sh
chmod +x /home/sergio/taquilla_app/run_update_if_needed.sh
```

### 2. Bug secundario: update.py sale con código 1 cuando no hay PDFs nuevos

`run_update.sh` usa `set -euo pipefail`, por lo que cualquier código de salida distinto de 0 aborta el script. `update.py` devuelve código 1 cuando simplemente no encuentra PDFs nuevos (situación normal si el Ministerio aún no ha publicado). Esto hacía que el script reportara "Phase 1 failed" aunque no hubiera ningún error real.

Este bug ya afectó al cron del 7 de mayo a las 05:30: `update.py` encontró 0 PDFs nuevos, el script abortó, y el reintento del mediodía (`run_update_if_needed.sh`) fue el que finalmente cargó los datos correctamente a las 10:48 UTC.

---

## Solución aplicada

Se modificó `run_update.sh` para distinguir entre "no hay PDFs nuevos" (código 1, situación normal) y un error real (cualquier otro código):

```bash
"$SCRIPT_DIR/venv/bin/python3" "$SCRIPT_DIR/update.py" "$@" || {
    EXIT_CODE=$?
    if [ "$EXIT_CODE" -eq 1 ]; then
        echo "[$(date)] Phase 1: no new PDFs found. Continuing." >> "$LOG_FILE"
    else
        echo "[$(date)] Phase 1 failed (exit $EXIT_CODE). Check logs/update.log for details." >> "$LOG_FILE"
        exit "$EXIT_CODE"
    fi
}
```

---

## Recuperación manual

Tras restaurar los permisos, la actualización pendiente se lanza manualmente:

```bash
chmod +x /home/sergio/taquilla_app/run_update.sh
cd /home/sergio/taquilla_app && ./run_update.sh
```

Si el Ministerio aún no ha publicado los PDFs, el script terminará limpiamente y bastará con reintentarlo al día siguiente.

---

## Logs relevantes

| Fichero | Contenido |
|---|---|
| `logs/cron.log` | Historial de ejecuciones del orquestador y errores de cron |
| `logs/update.log` | Detalle del scraping y parsing de PDFs |
| `logs/.last_success_date` | Sentinel: fecha de la última ejecución completa correcta |

Para verificar que la actualización fue bien tras la recuperación:

```sql
SELECT filename, report_type, fecha_inicio, fecha_fin, rows_inserted, processed_at
FROM processed_pdfs
ORDER BY processed_at DESC
LIMIT 5;
```
