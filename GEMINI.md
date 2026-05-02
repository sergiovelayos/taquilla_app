# Taquilla España - Documentación del Proyecto

## 🏗️ Arquitectura de Despliegue
- **Infraestructura:** Docker sobre Ubuntu.
- **Red:** `network_mode: host` para acceso directo a PostgreSQL.
- **Puertos:** App en **5002** (0.0.0.0). Evitar puerto 5001.
- **Acceso:** `taquilla.hookponent.cc` (Cloudflare Tunnel).

## 🔄 Flujo de Datos y Scripts Clave

### 1. Ingesta Semanal (Comscore)
- **`run_update.sh`**: Orquestador principal (ejecutado por Cron los lunes a las 09:00).
- **`update.py`**: Motor de ingesta. Scrapea la web del Ministerio, descarga PDFs y guarda los datos en las tablas `top25` y `topespanol`.
- **`download_parse.py`**: Biblioteca de funciones para scraping y parsing de PDFs (PDFPlumber).

### 2. Integración Catálogo ICAA (Ficha Oficial)
- **`brave_icaa.py`**: Busca IDs de expedientes ICAA usando la API de Brave Search.
- **`icaa_downloader.py`**: Descarga los HTMLs de las fichas oficiales desde la sede del MCU usando los IDs mapeados. Soporta concurrencia y bypass SSL.
- **`icaa_parser.py`**: Extrae metadatos (Director, Reparto, Subvenciones) de los HTMLs y los guarda en `icaa_fichas`.

### 3. Enriquecimiento y Mantenimiento
- **`tmdb_enricher.py`**: Obtiene posters, sinopsis y valoraciones desde la API de TMDB.
- **`reconstruct_csv.py`**: Genera CSVs consolidados desde la base de datos para respaldo.
- **`rename_pdfs.py`**: Normaliza los nombres de los archivos PDF descargados.

## 📊 Lógica de Negocio
- **Fecha de Estreno:** Calculada dinámicamente como la primera aparición registrada de la película en cualquier ranking (`MIN(fecha_inicio)`).
- **Base de Datos:** PostgreSQL con esquema `comscore`. Tablas principales: `top25`, `topespanol`, `icaa_fichas`, `processed_pdfs`.

## 🌐 Interfaz Web
1. **Cabecera:** Resumen estadístico y última actualización.
2. **Cuerpo:** Ranking semanal con posters (TMDB) y análisis de concentración.
3. **Pie:** Rankings acumulados (Top Anual y Top Histórico).

## 🛠️ Comandos de Mantenimiento
- **Actualizar todo:** `./run_update.sh`
- **Descargar fichas ICAA pendientes:** `python3 icaa_downloader.py`
- **Enriquecer con TMDB:** `python3 tmdb_enricher.py`
