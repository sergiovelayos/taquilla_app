# Actualización semanal de datos de taquilla

> Cómo incorporar los datos del último fin de semana a Insights Taquilla España.

---

## Fuente de datos

Los datos de taquilla semanales los publica el **Ministerio de Cultura** en formato PDF cada semana (normalmente martes o miércoles de la semana siguiente al fin de semana):

```
https://www.cultura.gob.es/cultura/areas/cine/datos/taquilla-espectadores.html
```

El script `update.py` accede a esa página automáticamente, detecta los PDFs nuevos, los descarga y los parsea. No hace falta descargarlos a mano.

Hay dos tipos de informe:

| Tipo | Tabla destino | Contenido |
|------|--------------|-----------|
| `top25` | `public.top25` | Top 25 películas de la semana (todas las nacionalidades) |
| `topespanol` | `public.topespanol` | Top películas españolas de la semana |

---

## Pipeline completo

```
cultura.gob.es (PDFs)
        │
        ▼
   update.py  ──────────────────────────►  top25 + topespanol
        │
        ▼
tmdb_enricher.py  ────────────────────►  tmdb
        │
        ▼
tmdb_gente_importer.py  ──────────────►  tmdb_gente
```

Cada paso es idempotente: se puede ejecutar varias veces sin duplicar datos.

---

## Paso 1 — Descargar y parsear los PDFs del Ministerio

```bash
cd /path/to/taquilla_app

# Prueba: muestra qué PDFs nuevos hay sin escribir en BBDD
python3 update.py --dry-run

# Ejecución real: descarga y parsea los PDFs nuevos e inserta en top25 y topespanol
python3 update.py
```

El script:
- Accede a la página del Ministerio y busca todos los PDFs publicados.
- Comprueba la tabla `processed_pdfs` para saltarse los que ya se procesaron.
- Descarga solo los nuevos a la carpeta `pdfs/`.
- Parsea el PDF con `pdfplumber` para extraer rango de fechas y filas de la tabla.
- Inserta en `top25` o `topespanol` según el tipo de informe.
- Registra el PDF en `processed_pdfs` para no reprocesarlo en el futuro.

Si el script no encuentra PDFs nuevos, simplemente termina indicando `0 new PDFs`. Eso significa que los datos ya están al día.

**Log de ejecución:** `logs/update.log` (rotación automática 5 MB × 5 ficheros).

---

## Paso 2 — Enriquecer los títulos nuevos con metadatos de TMDB

Tras insertar los datos de taquilla hay que enriquecer los títulos nuevos con su información de TMDB (póster, tráiler, director, reparto, géneros, recaudación mundial…).

```bash
# Solo procesa los títulos que aún no tienen entrada en la tabla tmdb
python3 tmdb_enricher.py --skip-existing

# Para ver qué haría sin escribir nada (prueba)
python3 tmdb_enricher.py --skip-existing --dry-run --limit 10
```

El script busca en TMDB cada par `(titulo, distribuidora)` nuevo de `top25` y `topespanol`, hace el match por título y distribuidora, y guarda el resultado en la tabla `tmdb`. El match es automático con un score de confianza; los títulos con score bajo quedan marcados para revisión manual.

**Si el match falla o es incorrecto** para un título concreto:

```python
# Añadir en tmdb_enricher.py, sección TMDB_OVERRIDES
TMDB_OVERRIDES = {
    ("TITULO EXACTO", "Distribuidora"): 12345,  # ID correcto de TMDB
    ("OTRO TITULO",   "Distribuidora"): None,   # No existe en TMDB
}
```

---

## Paso 3 — Actualizar personas (directores y actores)

Si los nuevos títulos traen directores o actores que no estaban aún en la tabla `tmdb_gente`, hay que importarlos:

```bash
# Importar directores nuevos (los que aún no tienen fila en tmdb_gente)
python3 tmdb_gente_importer.py --tipo director --skip-existing

# Importar actores nuevos
python3 tmdb_gente_importer.py --tipo actor --skip-existing
```

Este paso es el más pesado (hay más actores que directores) y el que más depende de la calidad del nombre tal como viene del ICAA. Ver `docs/tmdb_gente.md` para el proceso detallado de corrección de matches.

> **Nota:** Este paso solo afecta a la **Calculadora de Subvenciones** (sección de directores y actores con foto). Para los datos de taquilla en la home, no es necesario.

---

## Resumen de comandos por orden de ejecución

```bash
cd /path/to/taquilla_app

# 1. Datos de taquilla nuevos
python3 update.py

# 2. Metadatos TMDB para los títulos nuevos
python3 tmdb_enricher.py --skip-existing

# 3. (Opcional) Personas nuevas para la calculadora
python3 tmdb_gente_importer.py --tipo director --skip-existing
python3 tmdb_gente_importer.py --tipo actor    --skip-existing
```

El paso 3 solo hace falta si en esa semana han entrado directores o actores que aún no aparecían en ninguna película anterior de la base de datos.

---

## Automatización con cron

`run_update.sh` ejecuta la descarga/parseo de taquilla, comprueba que estén
presentes los dos informes del fin de semana esperado y después ejecuta el
enriquecimiento TMDB. `run_update_if_needed.sh` hace el reintento de mediodía
si falta alguno de los informes.

Configuración instalada en el servidor (UTC):

```cron
30 5 * * 4 /home/sergio/taquilla_app/run_update.sh >> /home/sergio/taquilla_app/logs/cron.log 2>&1
0 10 * * 4 /home/sergio/taquilla_app/run_update_if_needed.sh >> /home/sergio/taquilla_app/logs/cron.log 2>&1

# Catálogo ICAA reciente: flujo independiente y diario
0 6 * * * cd /home/sergio/taquilla_app && ./run_icaa_update.sh >> logs/ultimas_icaa.log 2>&1
```

El cron diario ICAA ejecuta `icaa_ultimas_calificadas.py`,
`icaa_downloader.py --latest` e `icaa_parser.py`. No forma parte del wrapper
semanal de taquilla.

---

## Tablas de la base de datos

| Tabla | Descripción |
|-------|-------------|
| `top25` | Datos semanales del top 25 general (todas las nacionalidades) |
| `topespanol` | Datos semanales del top películas españolas |
| `processed_pdfs` | Auditoría de PDFs ya procesados (garantiza idempotencia) |
| `tmdb` | Metadatos TMDB por película: póster, tráiler, director, reparto, géneros… |
| `tmdb_gente` | Fichas de personas (directores y actores): foto, biografía, popularidad… |
| `icaa_fichas` | Fichas del catálogo ICAA: subvenciones, expediente, ficha técnica y artística |

---

## Verificar que la actualización fue bien

```sql
-- Últimas semanas cargadas
SELECT DISTINCT fecha_inicio, fecha_fin, COUNT(*) AS peliculas
FROM top25
GROUP BY fecha_inicio, fecha_fin
ORDER BY fecha_inicio DESC
LIMIT 5;

-- PDFs procesados recientemente
SELECT filename, report_type, fecha_inicio, fecha_fin, rows_inserted, processed_at
FROM processed_pdfs
ORDER BY processed_at DESC
LIMIT 10;

-- Títulos nuevos sin enriquecer aún con TMDB
SELECT t.titulo, t.distribuidora, t.fecha_inicio
FROM top25 t
WHERE NOT EXISTS (
    SELECT 1 FROM tmdb m
    WHERE m.titulo = t.titulo AND m.distribuidora = t.distribuidora
)
ORDER BY t.fecha_inicio DESC
LIMIT 20;
```

---

## Solución de problemas habituales

**El script no encuentra PDFs nuevos**
El Ministerio publica los datos con retraso variable (normalmente martes, a veces miércoles o jueves). Volver a intentarlo al día siguiente.

**Error de conexión a la BBDD**
Comprobar que `DATABASE_URL` está definida en el fichero `.env` del proyecto.

**Un título nuevo no aparece en la app tras el update**
Verificar que el paso 2 (`tmdb_enricher.py`) se ejecutó. La home muestra datos de `top25`/`topespanol` directamente, pero la ficha de detalle necesita la entrada en `tmdb`.

**El PDF se descargó pero las fechas quedaron a NULL**
`update.py` intenta extraer las fechas del contenido del PDF antes de recurrir al nombre del fichero. Si ambas estrategias fallan, el registro queda sin fechas y aparece como error en el log. En ese caso, revisar el PDF manualmente y si la estructura cambió, actualizar `parse_dates` en `download_parse.py`.
