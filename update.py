#!/usr/bin/env python3
"""
update.py — Weekly taquilla update.

Scrapes new PDFs from cultura.gob.es, parses them, and inserts rows into
the 'comscore' PostgreSQL database. Idempotent: already-processed PDFs
are skipped based on the processed_pdfs audit table.

Usage:
    python3 update.py          # normal run
    python3 update.py --dry-run  # scrape + parse, no DB writes
"""

import argparse
import logging
import logging.handlers
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

# ── bootstrap ────────────────────────────────────────────────────────────────

ROOT = Path(__file__).parent
load_dotenv(ROOT / '.env')

PDF_DIR = Path(os.getenv('PDF_DIR', ROOT / 'pdfs'))
LOG_DIR = Path(os.getenv('LOG_DIR', ROOT / 'logs'))
DATABASE_URL = os.getenv('DATABASE_URL')

PDF_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

# ── logging ───────────────────────────────────────────────────────────────────

def _setup_logging() -> logging.Logger:
    logger = logging.getLogger('taquilla')
    logger.setLevel(logging.DEBUG)
    fmt = logging.Formatter('%(asctime)s %(levelname)-8s %(message)s',
                            datefmt='%Y-%m-%d %H:%M:%S')

    # Rotating file: 5 MB × 5 files
    fh = logging.handlers.RotatingFileHandler(
        LOG_DIR / 'update.log', maxBytes=5 * 1024 * 1024, backupCount=5,
        encoding='utf-8')
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)

    # Console: INFO and above
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)

    logger.addHandler(fh)
    logger.addHandler(ch)
    return logger

log = _setup_logging()

# ── import parsing library ────────────────────────────────────────────────────

sys.path.insert(0, str(ROOT))
from download_parse import (
    fetch_html, download_pdf, scrape_pdf_links,
    parse_dates, extract_dates_from_pdf, parse_pdf,
    BASE_URL,
    TOP25_COLS, TOPESPANOL_COLS,
)

# ── DB helpers ────────────────────────────────────────────────────────────────

def get_connection():
    if not DATABASE_URL:
        raise RuntimeError('DATABASE_URL not set in .env')
    return psycopg2.connect(DATABASE_URL)


def ensure_schema(conn):
    """Apply schema migrations idempotently (safe to run every time)."""
    with conn.cursor() as cur:
        # Audit table for processed PDFs
        cur.execute("""
            CREATE TABLE IF NOT EXISTS processed_pdfs (
                id            SERIAL PRIMARY KEY,
                filename      TEXT        NOT NULL UNIQUE,
                report_type   TEXT        NOT NULL,
                fecha_inicio  DATE,
                fecha_fin     DATE,
                rows_inserted INTEGER     NOT NULL DEFAULT 0,
                processed_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)

        # inserted_at audit column on top25
        cur.execute("""
            ALTER TABLE top25
            ADD COLUMN IF NOT EXISTS inserted_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        """)

        # inserted_at audit column on topespanol
        cur.execute("""
            ALTER TABLE topespanol
            ADD COLUMN IF NOT EXISTS inserted_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        """)
    conn.commit()
    log.debug('Schema verified/migrated')


def already_processed(conn, filename: str) -> bool:
    with conn.cursor() as cur:
        cur.execute('SELECT 1 FROM processed_pdfs WHERE filename = %s', (filename,))
        return cur.fetchone() is not None


def record_processed(conn, filename, report_type, fecha_inicio, fecha_fin, rows_inserted):
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO processed_pdfs (filename, report_type, fecha_inicio, fecha_fin, rows_inserted)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (filename) DO UPDATE
                SET rows_inserted = EXCLUDED.rows_inserted,
                    processed_at  = NOW()
        """, (filename, report_type, fecha_inicio, fecha_fin, rows_inserted))


# ── insert helpers ────────────────────────────────────────────────────────────

_TOP25_DB_COLS = [
    'fecha_inicio', 'fecha_fin', 'rank', 'titulo', 'titulo_original',
    'distribuidora', 'semana', 'cines', 'pantallas', 'recaudacion',
    'pct_rec', 'total_espectadores', 'pct_esp',
    'recaudacion_acum', 'espectadores_acum', 'inserted_at',
]

_TOPESPANOL_DB_COLS = [
    'fecha_inicio', 'fecha_fin', 'rank', 'titulo', 'distribuidora',
    'semana', 'cines', 'pantallas', 'recaudacion', 'pct_rec',
    'rec_media_cine', 'rec_media_pantalla', 'total_espectadores', 'pct_esp',
    'esp_media_cine', 'esp_media_pantalla',
    'recaudacion_acum', 'espectadores_acum', 'inserted_at',
]

# Mapping: CSV column name → DB column name
_TOP25_CSV_TO_DB = dict(zip(TOP25_COLS[2:], _TOP25_DB_COLS[2:-1]))   # skip fecha_* and inserted_at
_TOPESPANOL_CSV_TO_DB = dict(zip(TOPESPANOL_COLS[2:], _TOPESPANOL_DB_COLS[2:-1]))


def _to_db_value(v: str):
    """Empty string → None so postgres stores NULL."""
    return None if v == '' else v


def insert_rows(conn, table: str, rows: list[dict], inserted_at: datetime):
    if not rows:
        return 0

    if table == 'top25':
        db_cols = _TOP25_DB_COLS
        csv_cols = TOP25_COLS
    else:
        db_cols = _TOPESPANOL_DB_COLS
        csv_cols = TOPESPANOL_COLS

    col_sql  = ', '.join(db_cols)
    val_sql  = ', '.join(['%s'] * len(db_cols))
    query    = f'INSERT INTO {table} ({col_sql}) VALUES ({val_sql})'

    records = []
    for row in rows:
        values = (
            [row['fecha_inicio'], row['fecha_fin']]
            + [_to_db_value(row.get(c, '')) for c in csv_cols[2:]]
            + [inserted_at]
        )
        records.append(values)

    with conn.cursor() as cur:
        psycopg2.extras.execute_batch(cur, query, records, page_size=200)

    return len(records)


# ── main update logic ─────────────────────────────────────────────────────────

def run(dry_run: bool = False):
    log.info('=== taquilla update start (dry_run=%s) ===', dry_run)

    # Fetch page
    try:
        html = fetch_html(BASE_URL + '/cultura/areas/cine/datos/taquilla-espectadores.html')
    except Exception as exc:
        log.error('Failed to fetch HTML: %s', exc)
        sys.exit(1)

    links = scrape_pdf_links(html)
    log.info('Found %d PDF links on page', len(links))

    if not links:
        log.warning('No PDF links found — page structure may have changed')
        sys.exit(1)

    conn = None
    if not dry_run:
        try:
            conn = get_connection()
            ensure_schema(conn)
        except Exception as exc:
            log.error('DB connection failed: %s', exc)
            sys.exit(1)

    stats = {'new': 0, 'skipped': 0, 'errors': 0, 'rows': 0}
    inserted_at = datetime.now(timezone.utc)

    for link in links:
        filename    = link['filename']
        report_type = link['report_type']
        section_year = link['section_year']
        pdf_url     = link['url']

        # Build unique storage name: prefix section_year when filename
        # doesn't already contain a 4-digit year (avoids collisions for
        # same-named PDFs from different years on the Ministry page)
        name_no_ext = filename.replace('.pdf', '')
        has_year_in_name = bool(re.search(r'\d{4}', name_no_ext))
        if has_year_in_name or not section_year:
            stored_name = filename
        else:
            stored_name = f"{section_year}_{filename}"

        # Skip if already processed
        if conn and already_processed(conn, stored_name):
            log.debug('Skip (already processed): %s', stored_name)
            stats['skipped'] += 1
            continue

        # Download PDF first (use stored_name to avoid overwrites between years)
        dest = PDF_DIR / stored_name
        try:
            downloaded = download_pdf(pdf_url, dest)
            action = '↓ downloaded' if downloaded else '· cached'
            log.debug('%s %s', action, stored_name)
        except Exception as exc:
            log.error('Download failed %s: %s', stored_name, exc)
            stats['errors'] += 1
            continue

        # Extract dates from PDF content (most reliable — includes year)
        date_start, date_end = extract_dates_from_pdf(str(dest))
        if date_start:
            log.debug('Dates from PDF content: %s → %s – %s', stored_name, date_start, date_end)
        else:
            # Fallback: parse dates from filename + section_year
            date_start, date_end = parse_dates(filename, section_year)
            if date_start:
                log.debug('Dates from filename: %s → %s – %s', stored_name, date_start, date_end)
            else:
                log.warning('Cannot parse dates: %s', stored_name)
                stats['errors'] += 1
                continue

        # Parse PDF
        try:
            rows = parse_pdf(str(dest), report_type, date_start, date_end)
        except Exception as exc:
            log.error('Parse failed %s: %s', stored_name, exc)
            stats['errors'] += 1
            continue

        if not rows:
            log.warning('No rows parsed: %s', filename)
            stats['errors'] += 1
            continue

        log.info('%-55s %d rows', stored_name, len(rows))

        if dry_run:
            stats['new'] += 1
            stats['rows'] += len(rows)
            continue

        # Insert in a single transaction — all rows or none
        try:
            table = 'top25' if report_type == 'top25' else 'topespanol'
            n = insert_rows(conn, table, rows, inserted_at)
            record_processed(conn, stored_name, report_type,
                             date_start, date_end, n)
            conn.commit()
            stats['new'] += 1
            stats['rows'] += n
        except Exception as exc:
            conn.rollback()
            log.error('DB insert failed %s: %s', filename, exc)
            stats['errors'] += 1

    if conn:
        conn.close()

    log.info(
        '=== done: %d new PDFs (%d rows), %d skipped, %d errors ===',
        stats['new'], stats['rows'], stats['skipped'], stats['errors'],
    )

    return stats['errors'] == 0


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Weekly taquilla update')
    parser.add_argument('--dry-run', action='store_true',
                        help='Parse without writing to DB')
    args = parser.parse_args()

    success = run(dry_run=args.dry_run)
    sys.exit(0 if success else 1)
