# 🎬 Taquilla España - Sistema de Inteligencia Cinematográfica

Pipeline integral de ingesta, enriquecimiento y visualización de datos de taquilla cinematográfica en España. Automatiza desde la descarga de informes oficiales de Comscore (vía Ministerio de Cultura) hasta la integración de metadatos oficiales del ICAA y artes visuales de TMDB.

---

## 🚀 Funcionalidades Clave

### 1. Ingesta Automatizada (Comscore)
- **Scraping Semanal**: Monitorización automática de la web del Ministerio para detectar nuevos informes PDF (Top 25 y Top Español).
- **Parsing Inteligente**: Extracción de datos de tablas PDF sin líneas (basado en coordenadas X/Y) mediante `pdfplumber`.
- **Idempotencia**: Garantiza que ningún informe se procese dos veces gracias a la tabla de auditoría `processed_pdfs`.

### 2. Integración Catálogo ICAA (Ficha Oficial)
- **Mapeo de IDs**: Búsqueda automatizada (vía Brave Search API y Google) para vincular cada película con su expediente oficial en la Sede Electrónica del Ministerio de Cultura.
- **Mirroring de Fichas**: Descarga concurrente de los HTMLs de las fichas técnicas oficiales (bypass de SSL y rate-limiting incluido).
- **Extracción de Metadatos**: Parser avanzado para obtener Director, Reparto, Empresas Productoras y **Subvenciones oficiales** recibidas.

### 3. Enriquecimiento Visual (TMDB)
- **Capa Visual**: Integración con la API de TMDB para obtener pósters en alta resolución, sinopsis y valoraciones de usuarios.
- **Normalización**: Sincronización de títulos entre las diferentes fuentes (Comscore, ICAA, TMDB).

### 4. Visualización Web
- **Dashboard Interactivo**: Aplicación Flask que muestra el ranking semanal con artes visuales.
- **Análisis de Mercado**: Gráficos de concentración de taquilla, aforo cines/pantallas y comparativas anuales de asistencia.
- **Rankings Históricos**: Acceso instantáneo a los Tops anuales y acumulados desde 2020.

---

## 🏗️ Arquitectura de Despliegue
- **Infraestructura**: Docker sobre servidor Ubuntu.
- **Modo de Red**: `host` (acceso directo a PostgreSQL 16).
- **Puertos**: Aplicación escuchando en el **5002**.
- **Acceso Externo**: Túnel Cloudflare (`taquilla.hookponent.cc`).
- **Actualización**: Orquestada por `run_update.sh` mediante Cron (Lunes 09:00).

---

## 🔄 Flujo de Trabajo y Scripts

| Script | Propósito |
| :--- | :--- |
| `update.py` | Motor de ingesta: descarga y parsea PDFs de Comscore. |
| `icaa_downloader.py` | Descarga masiva y concurrente de HTMLs desde la sede del MCU. |
| `icaa_parser.py` | Extrae metadatos (Director, Reparto, Ayudas) de los HTMLs a la DB. |
| `tmdb_enricher.py` | Enriquecimiento con pósters y sinopsis vía API TMDB. |
| `run_update.sh` | Script maestro para ejecución semanal completa. |
| `reconstruct_csv.py` | Genera backups consolidados en formato CSV. |

---

## 📊 Modelo de Datos (PostgreSQL)
Base de datos: `comscore`

- **`top25`**: Histórico semanal del mercado general.
- **`topespanol`**: Histórico semanal de cine nacional.
- **`icaa_fichas`**: Repositorio maestro de metadatos oficiales y subvenciones.
- **`anual_esp`**: Agregados anuales de rendimiento por película.
- **`processed_pdfs`**: Auditoría de ingesta.

---

## 🛠️ Instalación y Uso

### Configuración del Entorno
1. Instalar dependencias: `pip install -r requirements.txt`
2. Configurar `.env` con `DATABASE_URL`, `BRAVE_API_KEY` y `TMDB_API_KEY`.
3. Desplegar con Docker: `docker-compose up -d`.

### Comandos Comunes
- **Actualización manual completa**: `./run_update.sh`
- **Sincronizar fichas ICAA pendientes**: `python3 icaa_downloader.py`
- **Enriquecer con artes de TMDB**: `python3 tmdb_enricher.py`
- **Lanzar Web App (Desarrollo)**: `cd webapp && python3 app.py`

---

## 🛠️ Retos Técnicos Resueltos
- **Parsing de PDFs Complejos**: Reconstrucción de tablas a partir de glifos individuales donde no existen líneas divisorias.
- **Normalización de Títulos**: Sistema de match inteligente por normalización de caracteres para vincular fuentes con diferentes formatos de nombre (ej: "Tribu, La" vs "La Tribu").
- **Concurrencia en Red**: Descargador de fichas ICAA optimizado con `ThreadPoolExecutor` y sesiones persistentes para evitar bloqueos del servidor ministerial.
