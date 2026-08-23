# Procesos automáticos de actualización

Hay dos flujos independientes: la taquilla semanal y las fichas ICAA diarias.

## Cron instalado

```cron
# Taquilla: jueves 07:30 CEST y reintento a las 12:00 CEST
30 5 * * 4 /home/sergio/taquilla_app/run_update.sh >> /home/sergio/taquilla_app/logs/cron.log 2>&1
0 10 * * 4 /home/sergio/taquilla_app/run_update_if_needed.sh >> /home/sergio/taquilla_app/logs/cron.log 2>&1

# ICAA: todos los días a las 08:00 CEST
0 6 * * * cd /home/sergio/taquilla_app && ./run_icaa_update.sh >> logs/ultimas_icaa.log 2>&1
```

El servidor usa UTC. En invierno esos horarios locales se desplazan una hora si no se
ajusta el cron.

## Taquilla semanal: `run_update.sh`

El script `run_update.sh` centraliza todas las tareas necesarias para mantener la aplicación actualizada:

### Fase 1: Ingesta de Datos de Taquilla (`update.py`)
1. **Scraping:** Escanea la web del Ministerio de Cultura en busca de nuevos archivos PDF de la semana anterior.
2. **Descarga:** Descarga los PDFs de las secciones "Top 25" y "Top Español".
3. **Parsing:** Extrae los datos de las tablas del PDF (Recaudación, Espectadores, Pantallas, etc.) mediante coordenadas.
4. **Inserción:** Guarda los datos en las tablas `top25` y `topespanol` de la base de datos PostgreSQL.
5. **Auditoría:** Registra el archivo como procesado en la tabla `processed_pdfs` para evitar duplicados.

### Fase 2: Enriquecimiento con TMDB (`tmdb_enricher.py`)
Una vez que los nuevos datos están en la base de datos, este script:
1. **Identificación:** Busca películas que acaban de entrar en el ranking y no tienen metadatos asociados.
2. **Búsqueda:** Utiliza la API de The Movie Database (TMDB) para encontrar el ID de la película.
3. **Extracción:** Obtiene posters, sinopsis, valoraciones y votos.
4. **Almacenamiento:** Guarda la información en la tabla `tmdb`.

El wrapper solo escribe `logs/.last_success_date` cuando `top25` y `topespanol`
del último viernes-domingo están cargados. Si el Ministerio todavía no los ha
publicado, el reintento de mediodía permanece activo.

## Catálogo ICAA diario: `run_icaa_update.sh`

1. `icaa_ultimas_calificadas.py` actualiza el historial `ultimas_icaa` desde
   "Últimas calificadas".
2. `icaa_downloader.py --latest` selecciona expedientes ausentes o incompletos
   en `icaa_fichas` y deja sus HTML temporalmente en `scraper_icaa/html_sources/`.
3. `icaa_parser.py --delete-parsed` extrae la ficha completa, intenta el upsert
   en `icaa_fichas` y elimina siempre el HTML temporal al terminar.

Los HTML no forman parte del almacenamiento permanente ni de los backups. Si
una ficha falla al parsearse o guardarse, queda pendiente y se vuelve a descargar
en la siguiente ejecución; una ejecución sin pendientes termina correctamente.
La eliminación es también el comportamiento predeterminado del parser cuando se
ejecuta manualmente; `--keep-html` debe solicitarse expresamente para depuración.

Las colas de `/admin/matching` se calculan en vivo: no hay que reconstruirlas
después del cron. Las fichas nuevas aparecen automáticamente como candidatos para
anual/subvenciones y, mientras no tengan vínculo, en **Películas TMDB**.

## 📊 Monitoreo y Logs

Se pueden revisar los logs para verificar el estado de las actualizaciones:

- **Log General de Cron:** `logs/cron.log` (Contiene el historial de ejecuciones del orquestador).
- **Log de Ingesta:** `logs/update.log` (Detalles específicos del parsing de PDFs).
- **Log de Errores:** `errors.log` (Errores críticos del sistema).
- **Log ICAA diario:** `logs/ultimas_icaa.log`.

## 🛠️ Ejecución Manual

Si se desea forzar la actualización antes del jueves (por ejemplo, si los datos se publican el miércoles por la tarde), se puede ejecutar:

```bash
cd /home/sergio/taquilla_app
./run_update.sh

# Solo el catálogo ICAA diario
./run_icaa_update.sh
```

## 🔑 Requisitos
- **Archivo .env:** Debe contener `DATABASE_URL` y `TMDB_TOKEN`.
- **Entorno Virtual:** Se utiliza el entorno ubicado en `venv/` para asegurar las dependencias.
