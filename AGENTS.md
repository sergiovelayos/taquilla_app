# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

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
python3 scripts/subvenciones_matching_local.py --dry-run # Preview local subsidy matches
python3 scripts/subvenciones_matching_local.py --apply   # Apply safe local matches; no Brave
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
        ↓ subvenciones_matching_local.py --apply
   icaa_catalogo + subvenciones_resueltas + peliculas_calculadora

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
| `subvenciones_icaa_matches` | Manual/auto matches between subvenciones and icaa_fichas |
| `subvenciones_icaa_matches_detalle` | Per-row subsidy matches with state, confidence, method, and notes |
| `subvenciones_icaa_candidates` | Exact/fuzzy local candidates; fuzzy candidates require review |
| `icaa_catalogo_cache` / `icaa_catalogo` | Canonical union of `icaa_fichas` and `scrape_icaa` |
| `peliculas_calculadora` | Calculator view with official-resolution amounts preferred over ficha totals |
| `processed_pdfs` | Audit log — prevents double-processing PDFs |

### Web app (`webapp/app.py`)

Single-file Flask app (~1700 lines). No ORM — raw psycopg2 with `RealDictCursor`. Every request opens and closes its own DB connection via `get_db()` / `query()` / `execute()` helpers.

Routes:
- `/` — weekly ranking dashboard with concentration analysis and capacity insights
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
