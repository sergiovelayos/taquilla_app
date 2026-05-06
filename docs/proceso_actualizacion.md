# Proceso de Actualización Semanal

Este documento detalla el flujo de actualización de datos de la taquilla en España, que se ejecuta de forma automatizada cada semana.

## ⚙️ Configuración del Cronjob

El proceso está programado para ejecutarse automáticamente mediante `cron`:

- **Horario:** Todos los **jueves a las 07:30** (Hora de España / CEST).
- **Configuración en Crontab:** `30 5 * * 4` (Ajustado a UTC para coincidir con las 07:30 CEST).
- **Script Orquestador:** `/home/sergio/taquilla_app/run_update.sh`

## 🚀 Flujo de Ejecución

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

## 📊 Monitoreo y Logs

Se pueden revisar los logs para verificar el estado de las actualizaciones:

- **Log General de Cron:** `logs/cron.log` (Contiene el historial de ejecuciones del orquestador).
- **Log de Ingesta:** `logs/update.log` (Detalles específicos del parsing de PDFs).
- **Log de Errores:** `errors.log` (Errores críticos del sistema).

## 🛠️ Ejecución Manual

Si se desea forzar la actualización antes del jueves (por ejemplo, si los datos se publican el miércoles por la tarde), se puede ejecutar:

```bash
cd /home/sergio/taquilla_app
./run_update.sh
```

## 🔑 Requisitos
- **Archivo .env:** Debe contener `DATABASE_URL` y `TMDB_TOKEN`.
- **Entorno Virtual:** Se utiliza el entorno ubicado en `venv/` para asegurar las dependencias.
