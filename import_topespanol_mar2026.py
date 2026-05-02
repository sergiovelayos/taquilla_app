#!/usr/bin/env python3
"""
Script puntual: descarga el PDF de topespanol 27-29 marzo 2026
desde la URL del Ministerio y lo importa en la base de datos.

Uso:
    cd /path/to/taquilla_app
    python3 import_topespanol_mar2026.py
"""

import os
import sys
import ssl
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / '.env')

import psycopg2

from download_parse import extract_dates_from_pdf, parse_dates, parse_pdf
from update import insert_rows, get_connection

PDF_URL  = "https://www.cultura.gob.es/dam/jcr:2e99c0f9-d18a-492d-acb4-0f51854cbbfb/top-25-27-29-marzo-2026.pdf"
PDF_DIR  = Path(os.getenv('PDF_DIR', ROOT / 'pdfs'))
FILENAME = "topespanol-27-29-marzo-2026.pdf"
DEST     = PDF_DIR / FILENAME

SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode    = ssl.CERT_NONE


def download():
    if DEST.exists():
        print(f"PDF ya en disco: {DEST}")
        return
    print(f"Descargando {PDF_URL} ...")
    req = urllib.request.Request(PDF_URL, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, context=SSL_CTX, timeout=30) as r:
        DEST.write_bytes(r.read())
    print(f"Guardado en {DEST} ({DEST.stat().st_size:,} bytes)")


def process():
    # 1. Fechas
    d_ini, d_fin = extract_dates_from_pdf(str(DEST))
    if not d_ini:
        d_ini, d_fin = parse_dates(FILENAME, 2026)
    if not d_ini:
        sys.exit("ERROR: no se pudieron extraer las fechas del PDF")
    print(f"Fechas: {d_ini} – {d_fin}")

    # 2. Parsear filas
    rows = parse_pdf(str(DEST), 'topespanol', d_ini, d_fin)
    if not rows:
        sys.exit("ERROR: no se extrajeron filas del PDF")
    print(f"Filas extraídas: {len(rows)}")

    # 3. Insertar usando la misma función que update.py (mapea columnas CSV→BD)
    conn = get_connection()
    try:
        with conn:
            # Comprobar si ya está procesado
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM processed_pdfs WHERE filename = %s", (FILENAME,))
                if cur.fetchone():
                    print("El PDF ya está en processed_pdfs — abortando para no duplicar.")
                    return

            inserted = insert_rows(conn, 'topespanol', rows, datetime.now(timezone.utc))

            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO processed_pdfs (filename, report_type, fecha_inicio, fecha_fin, rows_inserted)
                    VALUES (%s, 'topespanol', %s, %s, %s)
                    ON CONFLICT DO NOTHING
                """, (FILENAME, d_ini, d_fin, inserted))

        print(f"✅  {inserted} filas insertadas en topespanol para {d_ini} – {d_fin}")
    finally:
        conn.close()


def fix_feb_mar_dates():
    """
    Corrige las filas de la semana 27-feb – 01-mar que se importaron
    con fecha_inicio=2026-03-27 (bug del parser, ya corregido en download_parse.py).
    """
    conn = get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE top25
                    SET fecha_inicio = '2026-02-27'
                    WHERE fecha_inicio = '2026-03-27' AND fecha_fin = '2026-03-01'
                """)
                n_top25 = cur.rowcount

                cur.execute("""
                    UPDATE topespanol
                    SET fecha_inicio = '2026-02-27'
                    WHERE fecha_inicio = '2026-03-27' AND fecha_fin = '2026-03-01'
                """)
                n_esp = cur.rowcount

                cur.execute("""
                    UPDATE processed_pdfs
                    SET fecha_inicio = '2026-02-27'
                    WHERE fecha_inicio = '2026-03-27' AND fecha_fin = '2026-03-01'
                      AND filename ILIKE '%27-febrero%'
                """)
                n_proc = cur.rowcount

        if n_top25 or n_esp or n_proc:
            print(f"🔧  Fechas corregidas — top25: {n_top25} filas, topespanol: {n_esp}, processed_pdfs: {n_proc}")
        else:
            print("ℹ️  No había filas con fecha errónea (ya estaban correctas).")
    finally:
        conn.close()


if __name__ == '__main__':
    print("=== Importando topespanol 27-29 marzo 2026 ===\n")
    download()
    process()
    print("\n=== Corrigiendo fechas 27-feb – 01-mar ===\n")
    fix_feb_mar_dates()
    print("\nHecho. Reinicia la webapp si es necesario.")
