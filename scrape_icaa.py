#!/usr/bin/env python3
"""
scrape_icaa.py — Barrido por rango de IDs del catálogo ICAA → tabla scrape_icaa

Recorre IDs numéricos de película en sede.mcu.gob.es, descarga la ficha con
cabeceras de navegador real, la parsea con icaa_parser.parsear_html (mismos
campos que icaa_fichas) y la guarda en la tabla scrape_icaa.

Diseñado para tandas MUY largas sin que corten la conexión:
  - Sesión requests con cookies + cabeceras de navegador reales
  - Delay aleatorio entre peticiones (default 3–6 s)
  - Backoff exponencial en errores; pausa larga (15 min) ante 403/429
  - Sesión renovada cada N peticiones
  - Reanudable: progreso registrado en tabla scrape_icaa_progress
  - Commit por ficha: si se corta, no se pierde nada

USO (en el servidor Ubuntu)
---------------------------
  python3 scrape_icaa.py --start 100000 --end 140000
  python3 scrape_icaa.py --start 1 --end 200000 --delay 4 --delay-max 8
  python3 scrape_icaa.py --start 135400 --end 135410 --dry-run

  # En background para tandas largas:
  nohup python3 scrape_icaa.py --start 1 --end 200000 >> scrape_icaa.log 2>&1 &
"""

import argparse
import json
import logging
import os
import random
import sys
import time
from pathlib import Path

import psycopg2
import requests
import urllib3
from dotenv import load_dotenv

import icaa_parser  # reutilizamos parsear_html para paridad total de campos

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ─── Config ────────────────────────────────────────────────────────────────────

load_dotenv()
DB_DSN   = os.getenv("DATABASE_URL", "postgresql://localhost/taquilla_app")
HTML_DIR = Path(__file__).parent / "scraper_icaa" / "html_scrape"

BASE_URL   = "https://sede.mcu.gob.es/CatalogoICAA/es-es"
DETAIL_URL = BASE_URL + "/Peliculas/Detalle?Pelicula={}"

HEADERS = {
    "Accept": ("text/html,application/xhtml+xml,application/xml;q=0.9,"
               "image/avif,image/webp,image/apng,*/*;q=0.8"),
    "Accept-Language": "es-ES,es;q=0.7",
    "Connection": "keep-alive",
    "Referer": "https://sede.mcu.gob.es/CatalogoICAA/es-es?T_General=alatriste",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-User": "?1",
    "Sec-GPC": "1",
    "Upgrade-Insecure-Requests": "1",
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/150.0.0.0 Safari/537.36"),
    "sec-ch-ua": '"Not;A=Brand";v="8", "Chromium";v="150", "Brave";v="150"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"macOS"',
}

TIMEOUT            = 30
MAX_RETRIES        = 4
LONG_PAUSE_SECONDS = 15 * 60   # ante 403/429 o fallos persistentes
SESSION_REFRESH    = 200       # renovar sesión cada N peticiones

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger(__name__)

# ─── Base de datos ──────────────────────────────────────────────────────────────

# Misma estructura que icaa_fichas, tabla scrape_icaa
CREATE_TABLE_SQL = icaa_parser.CREATE_TABLE_SQL \
    .replace("icaa_fichas", "scrape_icaa") \
    .replace("icaa_titulo_idx",   "scrape_icaa_titulo_idx") \
    .replace("icaa_director_idx", "scrape_icaa_director_idx") \
    .replace("icaa_anio_idx",     "scrape_icaa_anio_idx") \
    .replace("icaa_genero_idx",   "scrape_icaa_genero_idx")

UPSERT_SQL = icaa_parser.UPSERT_SQL.replace("icaa_fichas", "scrape_icaa")

CREATE_PROGRESS_SQL = """
CREATE TABLE IF NOT EXISTS scrape_icaa_progress (
    pelicula_id  INTEGER PRIMARY KEY,
    status       TEXT NOT NULL,          -- ok | empty | error
    checked_at   TIMESTAMP DEFAULT NOW()
);
"""


def get_db():
    return psycopg2.connect(DB_DSN)


def crear_tablas(conn):
    with conn.cursor() as cur:
        cur.execute(CREATE_TABLE_SQL)
        cur.execute(CREATE_PROGRESS_SQL)
    conn.commit()
    log.info("Tablas 'scrape_icaa' y 'scrape_icaa_progress' listas.")


def ids_ya_procesados(conn, start, end):
    """IDs del rango ya marcados ok/empty (reanudación)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT pelicula_id FROM scrape_icaa_progress "
            "WHERE pelicula_id BETWEEN %s AND %s AND status IN ('ok','empty')",
            (start, end),
        )
        return {row[0] for row in cur.fetchall()}


def ids_en_icaa_fichas(conn, start, end):
    """IDs del rango que ya existen en icaa_fichas — no se vuelven a scrapear."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT expediente_icaa FROM icaa_fichas "
            "WHERE expediente_icaa ~ '^[0-9]+$' "
            "AND expediente_icaa::int BETWEEN %s AND %s",
            (start, end),
        )
        return {int(row[0]) for row in cur.fetchall()}


def marcar_progreso(conn, pid, status):
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO scrape_icaa_progress (pelicula_id, status, checked_at) "
            "VALUES (%s, %s, NOW()) "
            "ON CONFLICT (pelicula_id) DO UPDATE SET status = EXCLUDED.status, checked_at = NOW()",
            (pid, status),
        )
    conn.commit()


def guardar_ficha(conn, d):
    with conn.cursor() as cur:
        cur.execute(UPSERT_SQL, (
            d["expediente_icaa"], d["titulo"], d["director"], d["calificacion"],
            d["anio_produccion"], d["fecha_estreno"], d["duracion_min"], d["tipo"],
            d["genero"], d["nacionalidad"], d["recaudacion_eur"], d["espectadores"],
            d["subvenciones_total_eur"], d["sinopsis"], d["etiquetas"],
            json.dumps(d["ficha_artistica"], ensure_ascii=False),
            json.dumps(d["ficha_tecnica"],   ensure_ascii=False),
            d["empresas_productoras"], d["distribuidoras"],
            json.dumps(d["subvenciones"],    ensure_ascii=False),
            d["fecha_inicio_rodaje"], d["fecha_fin_rodaje"], d["lugares_rodaje"],
            json.dumps(d["premios"],         ensure_ascii=False),
            json.dumps(d["festivales"],      ensure_ascii=False),
        ))
    conn.commit()

# ─── HTTP ───────────────────────────────────────────────────────────────────────

def nueva_sesion():
    """Crea una sesión y visita la portada del catálogo para obtener cookies."""
    s = requests.Session()
    s.headers.update(HEADERS)
    try:
        s.get(BASE_URL, timeout=TIMEOUT, verify=False)
        time.sleep(random.uniform(1.0, 2.5))
    except requests.RequestException as e:
        log.warning("Warm-up de sesión falló (continuamos igual): %s", e)
    return s


def descargar_ficha(session, pid):
    """
    Descarga el HTML de la ficha. Devuelve (html:str|None, fatal:bool).
    fatal=True → hay que renovar sesión y pausar largo.
    """
    url = DETAIL_URL.format(pid)
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = session.get(url, timeout=TIMEOUT, verify=False)
            if resp.status_code in (403, 429):
                log.warning("HTTP %s en ID %s — posible bloqueo", resp.status_code, pid)
                return None, True
            if resp.status_code == 404:
                return None, False
            if resp.status_code == 500:
                # El ICAA devuelve 500 de forma fiable para IDs inexistentes.
                # Reintento muy corto por si fuera un 500 transitorio, luego se
                # da por vacío. Mantener esto breve es clave: en la cola dispersa
                # (>230k) el 97% de los IDs son 500 y este es el mayor coste.
                if attempt == 1:
                    time.sleep(random.uniform(0.3, 0.7))
                    continue
                return None, False
            resp.raise_for_status()
            return resp.text, False
        except requests.RequestException as e:
            wait = min(60, 5 * 2 ** (attempt - 1)) + random.uniform(0, 3)
            log.warning("ID %s intento %d/%d falló (%s) — espero %.0fs",
                        pid, attempt, MAX_RETRIES, e, wait)
            time.sleep(wait)
    return None, True  # agotados los reintentos: tratamos como fallo serio

# ─── Main ───────────────────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(description="Barrido de fichas ICAA → tabla scrape_icaa")
    p.add_argument("--start", type=int, required=True, help="Primer ID del rango")
    p.add_argument("--end",   type=int, required=True, help="Último ID del rango (incluido)")
    p.add_argument("--delay",     type=float, default=3.0, help="Delay mínimo entre peticiones (s)")
    p.add_argument("--delay-max", type=float, default=6.0, help="Delay máximo entre peticiones (s)")
    p.add_argument("--limit", type=int, default=None, help="Procesar como máximo N IDs pendientes")
    p.add_argument("--dry-run", action="store_true", help="Descarga y parsea sin escribir en BBDD")
    p.add_argument("--no-save-html", action="store_true", help="No guardar el HTML en disco")
    return p.parse_args()


def main():
    args = parse_args()
    HTML_DIR.mkdir(parents=True, exist_ok=True)

    conn = get_db()
    en_icaa = ids_en_icaa_fichas(conn, args.start, args.end)
    if not args.dry_run:
        crear_tablas(conn)
        hechos = ids_ya_procesados(conn, args.start, args.end)
    else:
        hechos = set()

    excluidos = hechos | en_icaa
    pendientes = [i for i in range(args.start, args.end + 1) if i not in excluidos]
    if args.limit:
        pendientes = pendientes[:args.limit]

    total = len(pendientes)
    log.info("Rango %d–%d: %d ya en icaa_fichas (excluidos), %d ya procesados, %d pendientes.",
             args.start, args.end, len(en_icaa), len(hechos), total)
    if not total:
        return

    session = nueva_sesion()
    peticiones = ok = vacios = errores = 0

    for n, pid in enumerate(pendientes, 1):
        # Renovación periódica de sesión
        if peticiones and peticiones % SESSION_REFRESH == 0:
            log.info("Renovando sesión tras %d peticiones…", peticiones)
            session.close()
            time.sleep(random.uniform(10, 20))
            session = nueva_sesion()

        log.info("[%d/%d] Probando ID %d…", n, total, pid)
        html, fatal = descargar_ficha(session, pid)
        peticiones += 1

        if fatal:
            log.warning("Pausa larga de %d min y sesión nueva…", LONG_PAUSE_SECONDS // 60)
            session.close()
            time.sleep(LONG_PAUSE_SECONDS)
            session = nueva_sesion()
            html, fatal = descargar_ficha(session, pid)
            if fatal or html is None:
                log.error("[%d/%d] ID %d → ERROR persistente", n, total, pid)
                if not args.dry_run:
                    marcar_progreso(conn, pid, "error")
                errores += 1
                time.sleep(random.uniform(args.delay, args.delay_max))
                continue

        if html is None:
            # 404: no existe
            if not args.dry_run:
                marcar_progreso(conn, pid, "empty")
            vacios += 1
            log.info("[%d/%d] ID %d → no existe (404/500)", n, total, pid)
            time.sleep(random.uniform(args.delay, args.delay_max))
            continue

        # Guardar HTML y parsear con el parser existente (mismos campos)
        dest = HTML_DIR / f"{pid}.html"
        dest.write_text(html, encoding="utf-8")
        try:
            datos = icaa_parser.parsear_html(dest)
        except Exception as e:
            log.error("[%d/%d] ID %d → error de parseo: %s", n, total, pid, e)
            datos = None
            if not args.dry_run:
                marcar_progreso(conn, pid, "error")
            errores += 1
        finally:
            if args.no_save_html and dest.exists():
                dest.unlink()

        if datos:
            if args.dry_run:
                log.info("[%d/%d] ID %d → %s (dry-run, no guardado)", n, total, pid, datos["titulo"])
            else:
                try:
                    guardar_ficha(conn, datos)
                    marcar_progreso(conn, pid, "ok")
                    log.info("[%d/%d] ID %d → 💾 %s", n, total, pid, datos["titulo"])
                except Exception as e:
                    conn.rollback()
                    log.error("[%d/%d] ID %d → error BBDD: %s", n, total, pid, e)
                    marcar_progreso(conn, pid, "error")
                    errores += 1
                    time.sleep(random.uniform(args.delay, args.delay_max))
                    continue
            ok += 1
        elif datos is None and not args.dry_run:
            # Página sin ficha válida (sin título): ID vacío
            marcar_progreso(conn, pid, "empty")
            vacios += 1
            log.info("[%d/%d] ID %d → sin ficha válida", n, total, pid)
            if dest.exists() and not args.no_save_html:
                dest.unlink()  # no acumular HTMLs vacíos

        time.sleep(random.uniform(args.delay, args.delay_max))

    conn.close()
    print(f"\n{'='*55}")
    print(f"  Pendientes procesados : {total}")
    print(f"  Fichas guardadas      : {ok}")
    print(f"  IDs vacíos/404        : {vacios}")
    print(f"  Errores               : {errores}")
    if args.dry_run:
        print("\n  [dry-run] Nada escrito en BBDD.")


if __name__ == "__main__":
    main()
