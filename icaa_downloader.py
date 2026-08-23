#!/usr/bin/env python3
"""
icaa_downloader.py — Descarga temporalmente los HTMLs de las fichas ICAA para
los expedientes pendientes de completar en la base de datos.

FLUJO
-----
  1. Lee de icaa_fichas los expedientes que son stubs (director IS NULL),
     los IDs de ultimas_icaa si se usa --latest, o todos con --all
  2. Para cada ID descarga:
       https://sede.mcu.gob.es/CatalogoICAA/Peliculas/Detalle?Pelicula=<ID>
  3. Deja temporalmente el HTML en scraper_icaa/html_sources/<ID>.html
  4. Salta los ficheros temporales que ya existan, salvo con --force

El flujo diario ejecuta después icaa_parser.py --delete-parsed: intenta el upsert
en icaa_fichas y elimina siempre el HTML temporal al terminar. Una ficha fallida
queda pendiente en la base de datos y se vuelve a descargar en la siguiente ejecución.

USO
---
  python3 icaa_downloader.py --dry-run         # solo lista los IDs a descargar
  python3 icaa_downloader.py --limit 50        # descarga los primeros 50 stubs
  python3 icaa_downloader.py --latest         # nuevas fichas vistas por el cron diario
  python3 icaa_downloader.py                   # descarga todos los stubs
  python3 icaa_downloader.py --all             # todos los IDs (redownload incluido)

.env
----
  DATABASE_URL=postgresql://localhost/taquilla_app
"""

import argparse
import logging
import os
import sys
import time
from pathlib import Path

import psycopg2
import requests
import urllib3
from dotenv import load_dotenv

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

load_dotenv()

HTML_DIR  = Path(__file__).parent / "scraper_icaa" / "html_sources"
ICAA_URL  = "https://sede.mcu.gob.es/CatalogoICAA/Peliculas/Detalle?Pelicula={}"
HEADERS   = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "es-ES,es;q=0.9",
}
TIMEOUT   = 20

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Base de datos
# ---------------------------------------------------------------------------

def fetch_stubs(conn, only_stubs: bool, limit):
    """
    Devuelve lista de expediente_icaa (str) a descargar.
    only_stubs=True  -> solo los que tienen director IS NULL (fichas sin enriquecer)
    only_stubs=False -> todos los IDs de icaa_fichas
    """
    where = "WHERE director IS NULL" if only_stubs else ""
    limit_clause = f"LIMIT {int(limit)}" if limit else ""
    sql = f"""
        SELECT expediente_icaa
        FROM icaa_fichas
        {where}
        ORDER BY expediente_icaa
        {limit_clause}
    """
    with conn.cursor() as cur:
        cur.execute(sql)
        return [str(row[0]) for row in cur.fetchall()]


def fetch_latest(conn, limit):
    """Return recent ICAA IDs which are missing or still incomplete locally."""
    limit_clause = f"LIMIT {int(limit)}" if limit else ""
    sql = f"""
        SELECT u.expediente_icaa
        FROM ultimas_icaa u
        LEFT JOIN icaa_fichas i
          ON i.expediente_icaa = u.expediente_icaa
        WHERE i.expediente_icaa IS NULL
           OR i.director IS NULL
        ORDER BY u.resolucion DESC NULLS LAST, u.expediente_icaa
        {limit_clause}
    """
    with conn.cursor() as cur:
        cur.execute(sql)
        return [str(row[0]) for row in cur.fetchall()]


# ---------------------------------------------------------------------------
# Descarga
# ---------------------------------------------------------------------------

def download_html(exp_id: str, dest: Path) -> bool:
    """
    Descarga la ficha ICAA y la guarda en dest.
    Devuelve True si se descargo correctamente.
    """
    url = ICAA_URL.format(exp_id)
    for attempt in range(3):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT, verify=False)
            resp.raise_for_status()
            dest.write_bytes(resp.content)
            return True
        except requests.HTTPError as e:
            log.warning("HTTP %s para ID %s (intento %d)", e.response.status_code, exp_id, attempt + 1)
            if e.response.status_code == 404:
                return False   # no existe, no reintentar
            time.sleep(2 ** attempt)
        except requests.RequestException as e:
            log.warning("Error de red para ID %s (intento %d): %s", exp_id, attempt + 1, e)
            time.sleep(2 ** attempt)
    return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_args():
    p = argparse.ArgumentParser(
        description="Descarga HTMLs de fichas ICAA desde icaa_fichas (stubs)"
    )
    p.add_argument("--dry-run",  action="store_true",
                   help="Solo lista los IDs a descargar, sin descargar nada")
    p.add_argument("--all",      action="store_true",
                   help="Descarga todos los IDs (no solo stubs sin director)")
    p.add_argument("--latest",   action="store_true",
                   help="Descarga pendientes detectados por ultimas_icaa")
    p.add_argument("--limit",    type=int, default=None, metavar="N",
                   help="Descargar solo los primeros N expedientes")
    p.add_argument("--delay",    type=float, default=0.5, metavar="SEG",
                   help="Segundos entre descargas (default 0.5)")
    p.add_argument("--force",    action="store_true",
                   help="Re-descargar aunque el HTML ya exista en disco")
    return p.parse_args()


def main():
    args = parse_args()

    if args.all and args.latest:
        raise SystemExit("--all y --latest no se pueden combinar")

    HTML_DIR.mkdir(parents=True, exist_ok=True)

    dsn  = os.getenv("DATABASE_URL", "postgresql://localhost/taquilla_app")
    conn = psycopg2.connect(dsn)

    if args.latest:
        ids = fetch_latest(conn, limit=args.limit)
        source = "pendientes de ultimas_icaa"
    else:
        ids = fetch_stubs(conn, only_stubs=not args.all, limit=args.limit)
        source = "todos" if args.all else "solo stubs sin director"
    conn.close()

    total = len(ids)
    log.info(
        "%d expedientes a procesar (%s).",
        total,
        source,
    )

    if args.dry_run:
        for exp_id in ids:
            dest = HTML_DIR / f"{exp_id}.html"
            status = "YA_EN_DISCO" if dest.exists() else "PENDIENTE"
            log.info("  %s  %s", exp_id, status)
        log.info("[dry-run] Nada descargado.")
        return

    downloaded = skipped = errors = 0

    for i, exp_id in enumerate(ids, 1):
        dest = HTML_DIR / f"{exp_id}.html"
        prefix = f"[{i}/{total}] ID {exp_id}"

        if dest.exists() and not args.force:
            log.info("%s -> ya en disco, saltando", prefix)
            skipped += 1
            continue

        ok = download_html(exp_id, dest)
        if ok:
            log.info("%s -> OK (%d bytes)", prefix, dest.stat().st_size)
            downloaded += 1
        else:
            log.warning("%s -> ERROR (no descargado)", prefix)
            errors += 1

        time.sleep(args.delay)

    print(f"\n{'='*55}")
    print(f"  Expedientes procesados : {total}")
    print(f"  Descargados            : {downloaded}")
    print(f"  Ya en disco (skip)     : {skipped}")
    print(f"  Errores                : {errors}")
    print(f"\n  HTMLs temporales en: {HTML_DIR}")
    if downloaded > 0:
        print(
            f"\n  Siguiente paso:"
            f"\n    python3 icaa_parser.py --delete-parsed"
            f"\n  para importar los {downloaded} HTMLs y borrar los temporales al terminar."
        )

    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
