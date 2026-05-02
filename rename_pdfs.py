#!/usr/bin/env python3
"""
rename_pdfs.py — Rename PDF files to YYYY-MM-DD_original-name.pdf
so they sort chronologically.

Usage:
    python3 rename_pdfs.py           # dry run (no changes)
    python3 rename_pdfs.py --apply   # apply renames
"""

import argparse
import os
import re
import sys
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

ROOT = Path(__file__).parent
load_dotenv(ROOT / '.env')
PDF_DIR = Path(os.getenv('PDF_DIR', ROOT / 'pdfs'))
DATABASE_URL = os.getenv('DATABASE_URL')

sys.path.insert(0, str(ROOT))
from download_parse import parse_dates


def load_db_dates():
    conn = psycopg2.connect(DATABASE_URL)
    with conn.cursor() as cur:
        cur.execute('SELECT filename, fecha_inicio FROM processed_pdfs')
        rows = cur.fetchall()
    conn.close()
    return {fn: str(d) for fn, d in rows if d}


def get_year(filename):
    m = re.search(r'(\d{4})', filename)
    return int(m.group(1)) if m else None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true', help='Apply renames (default: dry run)')
    args = parser.parse_args()

    db_dates = load_db_dates()
    pdfs = sorted(PDF_DIR.glob('*.pdf'))

    renames = []
    skipped = []

    for pdf in pdfs:
        name = pdf.name

        # Skip already-prefixed files
        if re.match(r'^\d{4}-\d{2}-\d{2}_', name):
            continue

        year = get_year(name)
        d1, _ = parse_dates(name, year)
        date_str = str(d1) if d1 else db_dates.get(name)

        if not date_str:
            skipped.append(name)
            continue

        new_name = f'{date_str}_{name}'
        renames.append((pdf, PDF_DIR / new_name))

    print(f'Renames planned : {len(renames)}')
    print(f'Skipped (no date): {len(skipped)}')
    if skipped:
        for s in skipped:
            print(f'  SKIP: {s}')

    if not args.apply:
        print('\nDry run — sample renames:')
        for old, new in renames[:8]:
            print(f'  {old.name}')
            print(f'  -> {new.name}')
            print()
        print('Run with --apply to execute.')
        return

    done = 0
    for old, new in renames:
        old.rename(new)
        done += 1
    print(f'\nRenamed {done} files.')


if __name__ == '__main__':
    main()
