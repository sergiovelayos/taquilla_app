# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Infraestructura — CRÍTICO

El proyecto corre en un **servidor Ubuntu remoto**, NO en el macmini local.
- El macmini tiene el directorio del proyecto montado como volumen (edición de código)
- Todos los comandos bash que afecten a la app, Docker, o la DB deben ejecutarse via **`ssh ubuntu`**
- `docker exec`, `docker-compose`, `psql` y scripts Python del pipeline → siempre `ssh ubuntu "<comando>"`
- `DATABASE_URL` usa `localhost` porque dentro del contenedor en Linux con `network_mode: host` el localhost SÍ llega a la DB
- La DB es un contenedor Docker llamado `postgres_db` en el servidor Ubuntu
- **Nunca ejecutar comandos de infraestructura directamente en el macmini** — tiene su propio PostgreSQL (Homebrew) y Docker que son irrelevantes para la app

```bash
# Ejemplo correcto
ssh ubuntu "docker exec taquilla-webapp python3 update.py"

# Ejemplo incorrecto — afecta solo al macmini, no a producción
docker exec taquilla-webapp python3 update.py
```

## Commands

```bash
# Run webapp locally (dev)
python3 webapp/app.py

# Deploy / restart via Docker
docker-compose up -d --build
docker-compose restart webapp

# Weekly ingestion pipeline (full run)
./run_update.sh

# Daily recent ICAA catalogue pipeline
./run_icaa_update.sh

# Individual pipeline steps
python3 update.py                        # Download + parse Comscore PDFs → DB
python3 icaa_downloader.py --latest      # Mirror recent pending ICAA fichas
python3 icaa_parser.py                   # Parse HTMLs → icaa_fichas table
python3 tmdb_enricher.py --skip-existing # Enrich with TMDB posters/metadata
python3 tmdb_gente_importer.py           # Import TMDB people data
python3 reconstruct_csv.py               # Export consolidated CSVs from DB

# Dry-run mode (most scripts support it)
python3 update.py --dry-run
python3 tmdb_enricher.py --limit 5 --dry-run
```

## Architecture

This is a single-container Flask app backed by a PostgreSQL 16 database (`comscore`) running on the host. The container uses `network_mode: host` so it connects to PostgreSQL directly at `localhost:5432`. A Cloudflare Tunnel exposes it externally at `taquilla.hookponent.cc`.

### Automated data pipelines

```
Weekly (Thursday):

Ministerio de Cultura PDFs
        ↓ update.py (pdfplumber — coordinate-based table reconstruction)
   top25 / topespanol tables

Daily (06:00 UTC):

ICAA "Últimas calificadas" → ultimas_icaa
        ↓ icaa_downloader.py --latest
   scraper_icaa/html_sources/<id>.html (temporal)
        ↓ icaa_parser.py --delete-parsed
   icaa_fichas (master catalogue: director, cast, subvenciones, etc.)

Enrichment:

   top25 / topespanol
        ↓ tmdb_enricher.py
   tmdb (posters, synopsis, ratings)
        ↓ tmdb_gente_importer.py
   tmdb_gente (people: directors, actors)
```

PDF parsing challenge: Comscore PDFs have no table lines. `update.py` and `download_parse.py` reconstruct rows from individual glyphs using X/Y coordinates via pdfplumber.

Title matching challenge: titles differ across Comscore, ICAA, and TMDB (e.g. "Tribu, La" vs "La Tribu"). Scripts normalize unicode/accents before matching.

### Database tables (`comscore` DB)

| Table | Contents |
|---|---|
| `top25` | Weekly general market ranking (from Comscore PDFs) |
| `topespanol` | Weekly Spanish-film ranking |
| `anual_esp` | Annual aggregates by film |
| `icaa_fichas` | Master catalogue: official ICAA metadata + subvenciones (JSONB) |
| `tmdb` | Visual enrichment: posters, trailers, ratings, cast arrays |
| `tmdb_gente` | People enrichment: directors/actors with TMDB/IMDb/Wikidata IDs |
| `subvenciones` | Film subsidies linked to ICAA expediente + TMDB id |
| `subvenciones_raw` | Raw import of the year/type/title/amount subsidy series (2006–2025), sourced from ICAA annual reports (2006–2017) and official Ministerio de Cultura resolutions (2018–present) — see `docs/subvenciones_historico.md` |
| `subvenciones_icaa_matches` | Manual/auto matches between subvenciones (by title) and icaa_fichas |
| `subvenciones_raw_icaa_matches` | Manual matches between subvenciones_raw (by row id, not title) and icaa_fichas — see `docs/matching_web.md` |
| `pelicula_tmdb_match` | Manual/auto matches between icaa_fichas.expediente_icaa and a TMDB movie id — see `docs/matching_web.md` |
| `processed_pdfs` | Audit log — prevents double-processing PDFs |
| `scrape_icaa` | Fichas discovered by ID sweep of the ICAA catalogue (same schema as `icaa_fichas`) |
| `scrape_icaa_progress` | Per-ID log of the sweep (`ok`/`empty`/`error`) — enables resuming |

### Web app (`webapp/app.py`)

Single-file Flask app (~1700 lines). No ORM — raw psycopg2 with `RealDictCursor`. Every request opens and closes its own DB connection via `get_db()` / `query()` / `execute()` helpers.

Routes:
- `/` — weekly ranking dashboard (Taquilla Semanal): concentration analysis, capacity insights, top openings, per-film search + evolution chart — see `docs/pagina_principal.md`
- `/historico-taquilla` — Histórico Taquilla: cumulative rankings, official annual ICAA report, percentile distribution — see `docs/pagina_principal.md`
- `/pelicula/<expediente_icaa>` — film detail page (ICAA + TMDB data)
- `/calculadora` — box office calculator / benchmarking tool with percentile analysis
- `/subvenciones-historico` — historical subsidies browser
- `/admin/matching` — manual matching UI for ICAA ↔ Comscore ↔ TMDB links
- `/api/ranking`, `/api/anual`, `/api/decay_curve`, etc. — JSON APIs for frontend charts

### Environment variables (`.env`)

```
DATABASE_URL=postgresql://user:pass@localhost:5432/comscore
TMDB_TOKEN=<bearer token>
BRAVE_API_KEY=<key for ICAA ID discovery>
CLOUDFLARE_TUNNEL_TOKEN=<token>
FLASK_DEBUG=0
FLASK_ENV=production
```

### ICAA scraping scripts

`scraper_icaa/` contains iterative downloaders (`batch_downloader*.py`, `final_downloader.py`) used during bulk historical imports. Normal operation uses `icaa_downloader.py` from the root. HTMLs are staged temporarily in `scraper_icaa/html_sources/` and always deleted after the parsing attempt; failed fichas remain pending for a future download.
`scrape_icaa.py` also uses temporary files by default; `--save-html` is reserved for explicit debugging.

### Admin matching workflow

`/admin/matching` has five panels: ICAA films, TMDB people, subsidies,
raw subsidies, and ICAA films ↔ TMDB. Requires `MATCHING_ADMIN_TOKEN` when
configured (checked via `require_matching_admin()`). Schema is created on first
access via `ensure_matching_schema()`.
