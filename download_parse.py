#!/usr/bin/env python3
"""
Descarga y parsea PDFs de taquilla española (ICAA).
Genera: csv/top25.csv y csv/topespanol.csv
"""

import os
import re
import csv
import ssl
import tempfile
import time
import urllib.request
import urllib.parse
import logging
from datetime import date
from pathlib import Path
from collections import defaultdict

import pdfplumber

# ── config ──────────────────────────────────────────────────

# Use environment variables if set, otherwise fallback to relative paths
ROOT = Path(__file__).parent.absolute()
BASE_DIR = Path(os.getenv('BASE_DIR', ROOT))
PDF_DIR  = Path(os.getenv('PDF_DIR', BASE_DIR / "pdfs"))
CSV_DIR  = Path(os.getenv('CSV_DIR', BASE_DIR / "csv"))
LOG_FILE = Path(os.getenv('LOG_FILE', BASE_DIR / "errors.log"))

PDF_DIR.mkdir(parents=True, exist_ok=True)
CSV_DIR.mkdir(parents=True, exist_ok=True)

BASE_URL   = "https://www.cultura.gob.es"
SOURCE_URL = f"{BASE_URL}/cultura/areas/cine/datos/taquilla-espectadores.html"

MONTHS_ES = {
    'enero': 1, 'febrero': 2, 'marzo': 3, 'abril': 4,
    'mayo': 5, 'junio': 6, 'julio': 7, 'agosto': 8,
    'septiembre': 9, 'octubre': 10, 'noviembre': 11, 'diciembre': 12
}

SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode    = ssl.CERT_NONE

logging.basicConfig(
    filename=str(LOG_FILE), level=logging.ERROR,
    format='%(asctime)s %(levelname)s %(message)s'
)
log = logging.getLogger(__name__)

# ── HTTP ────────────────────────────────────────────────────

def fetch_html(url):
    """Fetch the index bypassing the Ministry/CDN cached copy."""
    separator = '&' if urllib.parse.urlsplit(url).query else '?'
    cache_busted_url = f'{url}{separator}_taquilla_ts={time.time_ns()}'
    req = urllib.request.Request(cache_busted_url, headers={
        'User-Agent': 'Mozilla/5.0',
        'Cache-Control': 'no-cache',
        'Pragma': 'no-cache',
    })
    with urllib.request.urlopen(req, context=SSL_CTX, timeout=30) as r:
        return r.read().decode('utf-8', errors='replace')


def is_valid_pdf(path: Path) -> bool:
    """A cached download is reusable only when it has a PDF signature."""
    try:
        with path.open('rb') as f:
            return f.read(5) == b'%PDF-'
    except OSError:
        return False


def download_pdf(url, dest: Path) -> bool:
    """Download atomically, replacing cached HTML/JSON error responses."""
    if is_valid_pdf(dest):
        return False

    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    temp_path = None
    try:
        with urllib.request.urlopen(req, context=SSL_CTX, timeout=30) as r:
            with tempfile.NamedTemporaryFile(
                mode='wb', dir=dest.parent, prefix=f'.{dest.name}.',
                suffix='.tmp', delete=False,
            ) as tmp:
                temp_path = Path(tmp.name)
                while True:
                    chunk = r.read(1024 * 1024)
                    if not chunk:
                        break
                    tmp.write(chunk)

        if not is_valid_pdf(temp_path):
            raise ValueError(f'Downloaded response is not a PDF: {url}')
        os.replace(temp_path, dest)
    finally:
        if temp_path and temp_path.exists():
            temp_path.unlink()
    return True

# ── HTML scraping ───────────────────────────────────────────

def scrape_pdf_links(html):
    links     = []
    cur_year  = None
    cur_month = None
    seen      = set()

    hist_idx = html.find('Hist')
    section  = html[hist_idx:] if hist_idx != -1 else html

    for line in section.split('\n'):
        # Year-only header  e.g. <h3 class="subrayado">2020</h3>
        m = re.search(r'<h3[^>]*>\s*(\d{4})\s*(?:<|$)', line)
        if m:
            cur_year = int(m.group(1))

        # Month+year header  e.g. <h3>Enero 2021</h3>
        m = re.search(r'<h3[^>]*>([A-Za-záéíóúÁÉÍÓÚñÑ]+)\s+(\d{4})\s*</h3>', line)
        if m:
            mname = _strip_acc(m.group(1)).lower()
            if mname in MONTHS_ES:
                cur_month = MONTHS_ES[mname]
                cur_year  = int(m.group(2))

        m = re.search(r'href="(/dam/[^"]*\.pdf)"', line)
        if not m:
            continue
        path     = m.group(1)
        filename = path.split('/')[-1]

        # Detect report type — the Ministry has used many naming variants:
        #   top25: top-25-*, top25-*
        #   topespanol: topespanol-*, topespaniol-*, top-espanol-*, top-espaniol-*,
        #               topepaniol-*, cineespaniol-*, cineespanol-*
        fname_lower = filename.lower()
        if re.match(r'^top-?25-', fname_lower):
            rtype = 'top25'
        elif re.match(r'^(top-?espa[nñ]i?o?l-|topepaniol-|cineespan[iío]l-|cineespa[nñ]i?ol-)', fname_lower):
            rtype = 'topespanol'
        else:
            continue

        url = BASE_URL + path
        if url in seen:
            continue
        seen.add(url)
        links.append({'url': url, 'filename': filename,
                      'report_type': rtype,
                      'section_year': cur_year, 'section_month': cur_month})
    return links

# ── Date parsing ────────────────────────────────────────────

def _strip_acc(s):
    return (s.replace('á','a').replace('é','e').replace('í','i')
             .replace('ó','o').replace('ú','u').replace('ü','u')
             .replace('Á','A').replace('É','E').replace('Í','I')
             .replace('Ó','O').replace('Ú','U').replace('Ü','U'))

_MONTH_TYPOS = {'npviembre': 'noviembre', 'npvienbre': 'noviembre'}

def _expand_parts(parts):
    """Expand tokens like '29abril', '31diciembre2021' into separate day/month/year parts."""
    result = []
    for p in parts:
        # Fix known typos first
        pl = p.lower()
        if pl in _MONTH_TYPOS:
            result.append(_MONTH_TYPOS[pl])
            continue
        # Match: (digits)(letters)(optional_digits)
        m = re.match(r'^(\d{1,2})([a-záéíóúü]+)(\d{4})?$', p.lower())
        if m:
            d, mon, yr = m.group(1), _strip_acc(m.group(2)), m.group(3)
            # Fix embedded typos in month part
            mon = _MONTH_TYPOS.get(mon, mon)
            if mon in MONTHS_ES:
                result.append(d)
                result.append(mon)
                if yr:
                    result.append(yr)
                continue
        result.append(p)
    return result

def parse_dates(filename, section_year):
    name = filename
    # Strip all known prefixes (case-insensitive match, preserve original for split)
    pfx_match = re.match(
        r'^(top-?25-|top-?espa[nñ]i?o?l-|topepaniol-|cineespan[iío]l-|cineespa[nñ]i?ol-)',
        name, re.IGNORECASE
    )
    if pfx_match:
        name = name[pfx_match.end():]
    name  = name.replace('.pdf', '')
    parts = _expand_parts(name.split('-'))

    month_pos = [(i, _strip_acc(p).lower())
                 for i, p in enumerate(parts)
                 if _strip_acc(p).lower() in MONTHS_ES]

    year = section_year
    for p in parts:
        if re.match(r'^\d{4}$', p):
            year = int(p); break

    is_day = lambda s: bool(re.match(r'^\d{1,2}$', s))

    try:
        if len(month_pos) == 1:
            mi, mn = month_pos[0]
            mo     = MONTHS_ES[mn]
            days   = [int(p) for p in parts[:mi] if is_day(p)]
            if len(days) >= 2: d1, d2 = days[0], days[-1]
            elif len(days) == 1: d1 = d2 = days[0]
            else: return None, None
            return date(year, mo, d1), date(year, mo, d2)

        elif len(month_pos) == 2:
            i1, m1 = month_pos[0]; i2, m2 = month_pos[1]
            mo1, mo2 = MONTHS_ES[m1], MONTHS_ES[m2]
            before = [int(p) for p in parts[:i1]    if is_day(p)]
            middle = [int(p) for p in parts[i1+1:i2] if is_day(p)]
            if not before or not middle: return None, None
            # Year for each month: look for explicit year immediately after month token
            y1 = y2 = year
            if i1 + 1 < len(parts) and re.match(r'^\d{4}$', parts[i1+1]):
                y1 = int(parts[i1+1])
            if i2 + 1 < len(parts) and re.match(r'^\d{4}$', parts[i2+1]):
                y2 = int(parts[i2+1])
            # Cross-year: december→january
            if y1 == y2 and mo1 == 12 and mo2 == 1:
                y2 = y1 + 1
            return date(y1, mo1, before[-1]), date(y2, mo2, middle[0])
    except (ValueError, TypeError):
        pass
    return None, None


def extract_dates_from_pdf(pdf_path):
    """
    Extract the authoritative date range from inside the PDF.
    Looks for patterns like:
      "Fin de Semana. España: 07 - 09 Agosto 2020"
      "Top 25 Fin de Semana. España: 12 - 14 Abril 2024"
    Returns (date_start, date_end) or (None, None) if not found.
    """
    try:
        with pdfplumber.open(pdf_path) as pdf:
            page = pdf.pages[0]
            text = page.extract_text() or ''
    except Exception:
        return None, None

    # Pattern: day - day Month year  OR  day Month - day Month year
    # e.g. "07 - 09 Agosto 2020" or "29 Diciembre - 01 Enero 2021"
    # Single month: "12 - 14 Abril 2024"
    m = re.search(
        r'(\d{1,2})\s*[-–]\s*(\d{1,2})\s+([A-Za-záéíóúñÁÉÍÓÚÑ]+)\s+(\d{4})',
        text
    )
    if m:
        d1, d2 = int(m.group(1)), int(m.group(2))
        month_name = _strip_acc(m.group(3)).lower()
        year = int(m.group(4))
        mo = MONTHS_ES.get(month_name)
        if mo:
            try:
                # If d1 > d2, the weekend crosses a month boundary:
                # the end day (d2) is in `mo`, the start day (d1) is in the previous month.
                # e.g. "27 - 01 Marzo 2026" → start=27 Feb, end=01 Mar
                if d1 > d2:
                    from calendar import monthrange
                    mo1 = 12 if mo == 1 else mo - 1
                    y1  = year - 1 if mo == 1 else year
                    return date(y1, mo1, d1), date(year, mo, d2)
                return date(year, mo, d1), date(year, mo, d2)
            except ValueError:
                pass

    # Cross-month: "29 Diciembre 2020 - 01 Enero 2021" or "29 Diciembre - 01 Enero 2021"
    m = re.search(
        r'(\d{1,2})\s+([A-Za-záéíóúñ]+)\s*(?:\d{4})?\s*[-–]\s*(\d{1,2})\s+([A-Za-záéíóúñ]+)\s+(\d{4})',
        text
    )
    if m:
        d1 = int(m.group(1))
        m1 = MONTHS_ES.get(_strip_acc(m.group(2)).lower())
        d2 = int(m.group(3))
        m2 = MONTHS_ES.get(_strip_acc(m.group(4)).lower())
        y2 = int(m.group(5))
        if m1 and m2:
            y1 = y2 - 1 if m1 == 12 and m2 == 1 else y2
            try:
                return date(y1, m1, d1), date(y2, m2, d2)
            except ValueError:
                pass

    return None, None


# ── Number cleaning ─────────────────────────────────────────

def clean_number(text):
    if not text: return ''
    t = text.strip().replace('€','').replace('%','').strip()
    if t in ('', '-'): return ''
    # Multiple tokens (column bleed): take last valid number
    if ' ' in t:
        for tok in reversed(t.split()):
            r = clean_number(tok)
            if r: return r
        return ''
    if ',' in t:
        t = t.replace('.','').replace(',','.')
    else:
        parts = t.split('.')
        if len(parts) > 1 and all(len(p) <= 3 for p in parts[1:]):
            t = t.replace('.','')
    try:
        v = float(t)
        return str(int(v)) if v == int(v) else str(v)
    except ValueError:
        return text.strip()

# ── PDF word grouping ───────────────────────────────────────

def group_by_y(words, tol=6):
    rows = defaultdict(list)
    for w in words:
        rows[round(w['top'] / tol) * tol].append(w)
    return dict(sorted(rows.items()))


def merge_continuation_rows(rows, header_threshold):
    """Merge rows where title wraps to next y-line (old PDFs)."""
    merged = {}
    cur_key = None
    for y, ws in sorted(rows.items()):
        if y <= header_threshold:
            continue
        first = sorted(ws, key=lambda w: w['x0'])[0]['text'].strip() if ws else ''
        all_text = ' '.join(w['text'] for w in ws)
        is_footnote = 'Fuente' in all_text or '(*)' in all_text
        is_totals   = bool(re.match(r'^\d{3,}', first))  # 3+ digit = aggregate/totals row
        if re.match(r'^\d{1,2}$', first):
            cur_key = y
            merged[y] = list(ws)
        elif is_footnote or is_totals:
            cur_key = None  # reset: don't merge anything below totals/footnotes
        elif cur_key is not None:
            merged[cur_key].extend(ws)
    return merged

# ── Column boundary helpers ─────────────────────────────────

def _build_bounds(ordered_cols, page_width):
    """
    ordered_cols: list of (name, x_anchor) sorted by x_anchor.
    Returns list of (name, x_lo, x_hi) with NO gaps.
    Boundary between col_i and col_{i+1} = anchor_i + 0.80*(anchor_{i+1}-anchor_i).
    Each col's lo = previous col's hi.
    """
    n      = len(ordered_cols)
    bounds = []
    for i, (name, x0) in enumerate(ordered_cols):
        lo = 0 if i == 0 else (ordered_cols[i-1][1] + 0.80 * (x0 - ordered_cols[i-1][1]))
        hi = (x0 + 0.80 * (ordered_cols[i+1][1] - x0)) if i+1 < n else page_width + 100
        bounds.append((name, lo, hi))
    return bounds


def assign_cols(row_words, col_bounds):
    buckets = defaultdict(list)
    for w in sorted(row_words, key=lambda x: x['x0']):
        if w['text'].strip() in ('€', ''):
            continue
        for name, lo, hi in col_bounds:
            if lo <= w['x0'] < hi:
                buckets[name].append(w['text'])
                break
    return {k: ' '.join(v) for k, v in buckets.items()}

# ── Header anchor detection ─────────────────────────────────

def detect_anchors(header_words):
    """
    Returns dict of anchor positions detected from header words.
    """
    pos       = {}
    titulo_xs = []
    rec_xs    = []
    pct_xs    = []
    esp_xs    = []

    for w in header_words:
        t  = _strip_acc(w['text'].upper().strip())
        x0 = w['x0']

        if re.search(r'RANK', t):
            pos.setdefault('RANK', x0)
        if t == 'TITULO':
            titulo_xs.append(x0)
        if 'ORIGINAL' in t and 'TITULO' not in t:
            pos['ORIG_HINT'] = x0
        if 'DISTRIBUIDORA' in t:
            pos.setdefault('DISTRIB', x0)
        if t in ('SEM.', 'SEM'):
            pos.setdefault('SEM', x0)
        if t == 'CINES':
            pos.setdefault('CINES', x0)
        if 'PANTALLA' in t and 'CINES' not in t:
            pos.setdefault('PANTALLAS', x0)
        if 'RECAUDACI' in t:
            rec_xs.append(x0)
        if '+/-' in t:
            pct_xs.append(x0)
        # Also match partial ESPECTAD... (old PDFs have char-level extraction)
        if 'ESPECTADOR' in t or t.startswith('ESPE'):
            esp_xs.append(x0)

    titulo_xs.sort(); rec_xs.sort(); pct_xs.sort(); esp_xs.sort()

    pos['titulo_xs'] = titulo_xs
    pos['rec_xs']    = rec_xs
    pos['pct_xs']    = pct_xs
    pos['esp_xs']    = esp_xs
    return pos


def build_bounds_top25(header_words, pw, ph):
    a  = detect_anchors(header_words)
    tx = a['titulo_xs']
    rx = a['rec_xs']
    px = a['pct_xs']
    ex = a['esp_xs']

    # TITULO: if only 1 found and it's too far right, treat as TITULO_ORIG
    if len(tx) >= 2:
        titulo      = tx[0]
        titulo_orig = tx[1]
    elif len(tx) == 1 and tx[0] > 0.18 * pw:
        titulo      = pw * 0.06       # proportional fallback
        titulo_orig = tx[0]
    elif len(tx) == 1:
        titulo      = tx[0]
        titulo_orig = a.get('ORIG_HINT', pw * 0.24)
    else:
        titulo      = pw * 0.06
        titulo_orig = pw * 0.24

    cols = [
        ('RANK',                     a.get('RANK',    pw*0.04)),
        ('TÍTULO',                   titulo),
        ('TÍTULO ORIGINAL',          titulo_orig),
        ('DISTRIBUIDORA',            a.get('DISTRIB', pw*0.40)),
        ('SEM.',                     a.get('SEM',     pw*0.505)),
        ('CINES',                    a.get('CINES',   pw*0.530)),
        ('RECAUDACIÓN',              rx[0] if rx else pw*0.620),
        ('+/-% REC',                 px[0] if px else pw*0.690),
        ('TOTAL ESPECTADORES',       ex[0] if ex else pw*0.720),
        ('+/-% ESP',                 px[1] if len(px)>=2 else pw*0.800),
        ('RECAUDACIÓN (ACUMULADO)',   rx[1] if len(rx)>=2 else pw*0.850),
        ('ESPECTADORES (ACUMULADO)', ex[1] if len(ex)>=2 else pw*0.900),
    ]
    if 'PANTALLAS' in a:
        cols.append(('PANTALLAS', a['PANTALLAS']))
    cols.sort(key=lambda c: c[1])
    return _build_bounds(cols, pw)


def _anchored_bounds(anchors, first_lo, page_width):
    """
    Build no-gap bounds from a list of (name, x_anchor).
    first_lo: explicit left edge of the first column.
    Each column's hi = next column's lo = anchor_i + 0.8*(anchor_{i+1}-anchor_i).
    """
    n      = len(anchors)
    bounds = []
    for i, (name, x) in enumerate(anchors):
        lo = first_lo if i == 0 else (anchors[i-1][1] + 0.8 * (x - anchors[i-1][1]))
        hi = (x + 0.8 * (anchors[i+1][1] - x)) if i+1 < n else page_width + 100
        bounds.append((name, lo, hi))
    return bounds


def build_bounds_topespanol(header_words, pw, ph):
    a  = detect_anchors(header_words)
    rx = a['rec_xs']
    px = a['pct_xs']
    ex = a['esp_xs']

    rank_x  = a.get('RANK',      pw * 0.05)
    distrib_x = a.get('DISTRIB', pw * 0.35)   # header x0 (centered over col)
    sem_x   = a.get('SEM',       pw * 0.49)
    cines_x = a.get('CINES',     pw * 0.51)

    # TITULO starts just after rank; DISTRIB boundary uses geometric formula
    # (topespanol headers are centered, not left-aligned to data)
    titulo_start = rank_x + pw * 0.015
    # TITULO/DISTRIB split: reflect distrib_header inside the zone [titulo_start, sem_x]
    titulo_end   = 1.5 * distrib_x - 0.5 * sem_x
    titulo_end   = max(titulo_end, titulo_start + pw * 0.05)
    titulo_end   = min(titulo_end, sem_x - pw * 0.02)

    rec1  = rx[0]            if rx              else pw * 0.41
    rec2  = rx[-1]           if len(rx) >= 2   else pw * 0.86
    pct1  = px[0]            if px              else pw * 0.52
    pct2  = px[-1]           if len(px) >= 2   else pw * 0.69
    esp   = min(ex)          if ex              else pw * 0.63
    esp2  = max(ex)          if len(ex) >= 2   else pw * 0.92

    if rec2 == rec1:  rec2 = pw * 0.86
    if pct2 == pct1:  pct2 = esp + (rec2 - esp) * 0.20
    if esp2 == esp:   esp2 = pw * 0.92

    rec_mc = pct1 + (esp  - pct1) / 3
    rec_mp = pct1 + (esp  - pct1) * 2 / 3
    esp_mc = pct2 + (rec2 - pct2) / 3
    esp_mp = pct2 + (rec2 - pct2) * 2 / 3

    # Numeric columns: anchored bounds, starting at sem_x.
    # PANTALLAS: since the report dropped this column (last seen week of
    # 2026-05-29), most PDFs no longer have a header label for it. Only
    # include it in the layout when actually detected — a page-width
    # fallback here would fabricate a slice of ~nothing between CINES and
    # RECAUDACIÓN and risks landing on the wrong side of RECAUDACIÓN
    # depending on page width (see PANTALLAS-overflow incident). When
    # absent, parse_pdf() below fills PANTALLAS in from CINES instead.
    num_anchors = [
        ('SEM.',                          sem_x),
        ('CINES',                         cines_x),
        ('RECAUDACIÓN',                   rec1),
        ('+/-% REC',                      pct1),
        ('RECAUDACIÓN (MEDIA/CINE)',       rec_mc),
        ('RECAUDACIÓN (MEDIA/PANTALLA)',   rec_mp),
        ('TOTAL ESPECTADORES',            esp),
        ('+/-% ESP',                      pct2),
        ('ESPECTADORES (MEDIA/CINE)',      esp_mc),
        ('ESPECTADORES (MEDIA/PANTALLA)', esp_mp),
        ('RECAUDACIÓN (ACUMULADO)',        rec2),
        ('ESPECTADORES (ACUMULADO)',       esp2),
    ]
    if 'PANTALLAS' in a:
        num_anchors.append(('PANTALLAS', a['PANTALLAS']))
    num_anchors.sort(key=lambda c: c[1])

    num_bounds = _anchored_bounds(num_anchors, first_lo=sem_x, page_width=pw)

    return [
        ('RANK',          0,            titulo_start),
        ('TÍTULO',        titulo_start, titulo_end),
        ('DISTRIBUIDORA', titulo_end,   sem_x),
    ] + num_bounds

# ── PDF parsing ─────────────────────────────────────────────

NUMERIC_COLS = {
    'RANK', 'SEM.', 'CINES', 'PANTALLAS',
    'RECAUDACIÓN', '+/-% REC', 'TOTAL ESPECTADORES', '+/-% ESP',
    'RECAUDACIÓN (ACUMULADO)', 'ESPECTADORES (ACUMULADO)',
    'RECAUDACIÓN (MEDIA/CINE)', 'RECAUDACIÓN (MEDIA/PANTALLA)',
    'ESPECTADORES (MEDIA/CINE)', 'ESPECTADORES (MEDIA/PANTALLA)',
}


def parse_pdf(pdf_path, report_type, date_start, date_end):
    with pdfplumber.open(pdf_path) as pdf:
        page  = pdf.pages[0]
        words = page.extract_words()
        pw, ph = page.width, page.height

        # Header zone: top 15% of page (captures multi-line headers)
        hdr_threshold = ph * 0.15
        hdr_words = [w for w in words if w['top'] < hdr_threshold]

        if report_type == 'top25':
            col_bounds = build_bounds_top25(hdr_words, pw, ph)
        else:
            col_bounds = build_bounds_topespanol(hdr_words, pw, ph)

        rows   = group_by_y(words, tol=6)
        merged = merge_continuation_rows(rows, hdr_threshold)

        results = []
        for y, row_words in sorted(merged.items()):
            texts = [w['text'].upper().strip() for w in row_words]
            if 'TOTAL' in texts:
                continue  # skip totals row

            col_map = assign_cols(row_words, col_bounds)
            rank_raw = col_map.get('RANK', '').strip()
            if not re.match(r'^\d{1,2}$', rank_raw):
                continue

            row = {'fecha_inicio': str(date_start), 'fecha_fin': str(date_end)}
            for name, _, _ in col_bounds:
                raw = col_map.get(name, '')
                row[name] = clean_number(raw) if name in NUMERIC_COLS else raw.strip()

            # PANTALLAS was dropped from the source report after the week of
            # 2026-05-29; when the PDF has no such column, CINES is the
            # closest available figure (historically the two were equal or
            # off by at most 1).
            if not row.get('PANTALLAS'):
                row['PANTALLAS'] = row.get('CINES', '')

            results.append(row)

    return results

# ── CSV output ──────────────────────────────────────────────

TOP25_COLS = [
    'fecha_inicio', 'fecha_fin',
    'RANK', 'TÍTULO', 'TÍTULO ORIGINAL', 'DISTRIBUIDORA',
    'SEM.', 'CINES', 'PANTALLAS', 'RECAUDACIÓN',
    '+/-% REC', 'TOTAL ESPECTADORES', '+/-% ESP',
    'RECAUDACIÓN (ACUMULADO)', 'ESPECTADORES (ACUMULADO)',
]

TOPESPANOL_COLS = [
    'fecha_inicio', 'fecha_fin',
    'RANK', 'TÍTULO', 'DISTRIBUIDORA',
    'SEM.', 'CINES', 'PANTALLAS', 'RECAUDACIÓN',
    '+/-% REC', 'RECAUDACIÓN (MEDIA/CINE)', 'RECAUDACIÓN (MEDIA/PANTALLA)',
    'TOTAL ESPECTADORES', '+/-% ESP',
    'ESPECTADORES (MEDIA/CINE)', 'ESPECTADORES (MEDIA/PANTALLA)',
    'RECAUDACIÓN (ACUMULADO)', 'ESPECTADORES (ACUMULADO)',
]


def write_csv(path, rows, columns):
    with open(path, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=columns, extrasaction='ignore')
        w.writeheader()
        w.writerows(rows)
    print(f"Written: {path}  ({len(rows)} rows)")

# ── Main ────────────────────────────────────────────────────

def main():
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    CSV_DIR.mkdir(parents=True, exist_ok=True)

    print("Fetching index page...")
    html  = fetch_html(SOURCE_URL)
    links = scrape_pdf_links(html)
    n25   = sum(1 for l in links if l['report_type']=='top25')
    nes   = sum(1 for l in links if l['report_type']=='topespanol')
    print(f"Found {len(links)} PDFs ({n25} top25, {nes} topespanol)")

    top25_rows = []
    topes_rows = []
    total      = len(links)

    for i, link in enumerate(links, 1):
        fn   = link['filename']
        rtyp = link['report_type']
        url  = link['url']
        dest = PDF_DIR / fn

        print(f"[{i}/{total}] {fn}", end=' ', flush=True)

        d1, d2 = parse_dates(fn, link['section_year'])
        if not d1:
            msg = f"Cannot parse dates: {fn}"
            print(f"SKIP"); log.error(msg)
            continue

        try:
            dl = download_pdf(url, dest)
            print('↓' if dl else '·', end=' ', flush=True)
        except Exception as e:
            print(f"DL-ERR"); log.error(f"{fn}: download {e}")
            continue

        try:
            rows = parse_pdf(dest, rtyp, d1, d2)
            print(f"{len(rows)} rows")
            if rtyp == 'top25':  top25_rows.extend(rows)
            else:                topes_rows.extend(rows)
        except Exception as e:
            print(f"PARSE-ERR: {e}"); log.error(f"{fn}: parse {e}")

    write_csv(CSV_DIR / 'top25.csv',      top25_rows,  TOP25_COLS)
    write_csv(CSV_DIR / 'topespanol.csv', topes_rows,  TOPESPANOL_COLS)
    print(f"\nErrors logged to: {LOG_FILE}")


if __name__ == '__main__':
    main()
