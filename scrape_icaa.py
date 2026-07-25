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

  # Lista explícita de expedientes (CSV con cabecera, un ID por fila):
  python3 scrape_icaa.py --ids-file lista_expediente_icaa.csv --limit 300
  # Se puede relanzar tantas veces como haga falta: los IDs ya guardados
  # (en scrape_icaa o en icaa_fichas) o ya resueltos en scrape_icaa_lista_progress
  # se saltan automáticamente, así que --limit trocea el trabajo en tandas
  # seguras que se pueden cortar sin perder progreso.
  #
  # Algunos listados externos de expedientes vienen truncados: falta un
  # sufijo "40" que sí lleva el expediente real en sede.mcu.gob.es
  # (ej. listado "31182" → real "3118240"). Cuando el ID tal cual no
  # devuelve ficha, se reintenta automáticamente con "40" al final antes
  # de darlo por vacío.
"""

import argparse
import csv
import json
import logging
import os
import random
import sys
import tempfile
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

# Seguimiento específico de --ids-file: independiente de scrape_icaa_progress
# porque aquí un ID del listado puede resolverse bajo OTRO expediente real
# (sufijo "40"). expediente_lista es lo que traía el CSV de origen.
CREATE_LISTA_PROGRESS_SQL = """
CREATE TABLE IF NOT EXISTS scrape_icaa_lista_progress (
    expediente_lista     INTEGER PRIMARY KEY,
    expediente_resuelto  TEXT,
    encontrado           BOOLEAN NOT NULL,
    checked_at           TIMESTAMP DEFAULT NOW()
);
"""


def get_db():
    return psycopg2.connect(DB_DSN)


def crear_tablas(conn):
    with conn.cursor() as cur:
        cur.execute(CREATE_TABLE_SQL)
        cur.execute(CREATE_PROGRESS_SQL)
        cur.execute(CREATE_LISTA_PROGRESS_SQL)
    conn.commit()
    log.info("Tablas 'scrape_icaa', 'scrape_icaa_progress' y 'scrape_icaa_lista_progress' listas.")


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


def leer_ids_file(path):
    """Lee un CSV con cabecera y un expediente ICAA por fila. Deduplica preservando orden."""
    vistos = set()
    ids = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        next(reader, None)  # cabecera
        for row in reader:
            if not row or not row[0].strip():
                continue
            try:
                pid = int(row[0].strip())
            except ValueError:
                continue
            if pid not in vistos:
                vistos.add(pid)
                ids.append(pid)
    return ids


def ids_en_lista(conn, tabla, ids):
    """IDs de la lista que ya existen en la tabla dada (icaa_fichas o scrape_icaa)."""
    if not ids:
        return set()
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT expediente_icaa FROM {tabla} "
            "WHERE expediente_icaa ~ '^[0-9]+$' "
            "AND expediente_icaa::int = ANY(%s)",
            (ids,),
        )
        return {int(row[0]) for row in cur.fetchall()}


def ids_procesados_en_lista(conn, ids):
    """IDs de la lista ya marcados ok/empty en scrape_icaa_progress."""
    if not ids:
        return set()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT pelicula_id FROM scrape_icaa_progress "
            "WHERE status IN ('ok','empty') AND pelicula_id = ANY(%s)",
            (ids,),
        )
        return {row[0] for row in cur.fetchall()}


def ids_resueltos_en_lista_progress(conn, ids):
    """Expedientes-lista ya resueltos (encontrados o no) por una corrida previa de --ids-file."""
    if not ids:
        return set()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT expediente_lista FROM scrape_icaa_lista_progress WHERE expediente_lista = ANY(%s)",
            (ids,),
        )
        return {row[0] for row in cur.fetchall()}


def estado_progreso_generico(conn, ids):
    """Dict {pelicula_id: status} de scrape_icaa_progress para los IDs literales dados."""
    if not ids:
        return {}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT pelicula_id, status FROM scrape_icaa_progress WHERE pelicula_id = ANY(%s)",
            (ids,),
        )
        return dict(cur.fetchall())


def marcar_progreso(conn, pid, status):
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO scrape_icaa_progress (pelicula_id, status, checked_at) "
            "VALUES (%s, %s, NOW()) "
            "ON CONFLICT (pelicula_id) DO UPDATE SET status = EXCLUDED.status, checked_at = NOW()",
            (pid, status),
        )
    conn.commit()


def marcar_progreso_si_falta(conn, pid, status):
    """Como marcar_progreso, pero sin pisar un status ya existente (para no mentir sobre un 'ok' previo)."""
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO scrape_icaa_progress (pelicula_id, status, checked_at) "
            "VALUES (%s, %s, NOW()) ON CONFLICT (pelicula_id) DO NOTHING",
            (pid, status),
        )
    conn.commit()


def marcar_lista_progreso(conn, expediente_lista, expediente_resuelto, encontrado):
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO scrape_icaa_lista_progress "
            "(expediente_lista, expediente_resuelto, encontrado, checked_at) "
            "VALUES (%s, %s, %s, NOW()) "
            "ON CONFLICT (expediente_lista) DO UPDATE SET "
            "expediente_resuelto = EXCLUDED.expediente_resuelto, "
            "encontrado = EXCLUDED.encontrado, checked_at = NOW()",
            (expediente_lista, expediente_resuelto, encontrado),
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


def obtener_html_con_reintento(session, pid, args):
    """
    Envuelve descargar_ficha con el manejo de bloqueo (403/429): pausa larga,
    sesión nueva y un reintento. Devuelve (html|None, session, error_persistente).
    """
    html, fatal = descargar_ficha(session, pid)
    if not fatal:
        return html, session, False

    log.warning("Pausa larga de %d min y sesión nueva…", LONG_PAUSE_SECONDS // 60)
    session.close()
    time.sleep(LONG_PAUSE_SECONDS)
    session = nueva_sesion()
    html, fatal = descargar_ficha(session, pid)
    if fatal or html is None:
        return None, session, True
    return html, session, False


def parsear_y_guardar(conn, args, pid, html):
    """
    Parsea el HTML de una ficha y la guarda en scrape_icaa (salvo --dry-run).
    Devuelve (status, titulo) con status en {'ok', 'empty', 'error'}.

    Con --no-save-html el fichero temporal se escribe en /tmp (fuera del
    directorio del proyecto, que está montado como volumen compartido con el
    macmini) en vez de en HTML_DIR, para no generar eventos de filesystem en
    esa carpeta sincronizada por cada ficha descargada.
    """
    dest = (Path(tempfile.gettempdir()) if args.no_save_html else HTML_DIR) / f"{pid}.html"
    dest.write_text(html, encoding="utf-8")
    try:
        datos = icaa_parser.parsear_html(dest)
    except Exception as e:
        log.error("ID %s → error de parseo: %s", pid, e)
        if args.no_save_html and dest.exists():
            dest.unlink()
        return "error", None
    finally:
        if args.no_save_html and dest.exists():
            dest.unlink()

    if not datos:
        if dest.exists() and not args.no_save_html:
            dest.unlink()  # no acumular HTMLs vacíos
        return "empty", None

    if args.dry_run:
        return "ok", datos["titulo"]

    try:
        guardar_ficha(conn, datos)
        return "ok", datos["titulo"]
    except Exception as e:
        conn.rollback()
        log.error("ID %s → error BBDD: %s", pid, e)
        return "error", None

# ─── Main ───────────────────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(description="Barrido de fichas ICAA → tabla scrape_icaa")
    p.add_argument("--start", type=int, help="Primer ID del rango")
    p.add_argument("--end",   type=int, help="Último ID del rango (incluido)")
    p.add_argument("--ids-file", type=str, help=(
        "CSV con cabecera y un expediente ICAA por fila. Alternativa a --start/--end "
        "para procesar una lista concreta de IDs en vez de un rango."
    ))
    p.add_argument("--delay",     type=float, default=3.0, help="Delay mínimo entre peticiones (s)")
    p.add_argument("--delay-max", type=float, default=6.0, help="Delay máximo entre peticiones (s)")
    p.add_argument("--limit", type=int, default=None, help="Procesar como máximo N IDs pendientes")
    p.add_argument("--dry-run", action="store_true", help="Descarga y parsea sin escribir en BBDD")
    p.add_argument("--no-save-html", action="store_true", help="No guardar el HTML en disco")
    args = p.parse_args()
    if args.ids_file:
        if args.start is not None or args.end is not None:
            p.error("--ids-file no se combina con --start/--end")
    elif args.start is None or args.end is None:
        p.error("hace falta --start y --end, o bien --ids-file")
    return args


DEFAULT_IDS_FILE_LIMIT = 300  # tanda segura por invocación cuando se usa --ids-file sin --limit
SUFIJO_EXPEDIENTE_TRUNCADO = "40"  # ver docstring del módulo


def main():
    args = parse_args()
    HTML_DIR.mkdir(parents=True, exist_ok=True)

    conn = get_db()

    if args.ids_file:
        ejecutar_modo_lista(conn, args)
    else:
        ejecutar_modo_rango(conn, args)


def ejecutar_modo_rango(conn, args):
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
        if peticiones and peticiones % SESSION_REFRESH == 0:
            log.info("Renovando sesión tras %d peticiones…", peticiones)
            session.close()
            time.sleep(random.uniform(10, 20))
            session = nueva_sesion()

        log.info("[%d/%d] Probando ID %d…", n, total, pid)
        html, session, error_persistente = obtener_html_con_reintento(session, pid, args)
        peticiones += 1

        if error_persistente:
            log.error("[%d/%d] ID %d → ERROR persistente", n, total, pid)
            if not args.dry_run:
                marcar_progreso(conn, pid, "error")
            errores += 1
            time.sleep(random.uniform(args.delay, args.delay_max))
            continue

        if html is None:
            if not args.dry_run:
                marcar_progreso(conn, pid, "empty")
            vacios += 1
            log.info("[%d/%d] ID %d → no existe (404/500)", n, total, pid)
            time.sleep(random.uniform(args.delay, args.delay_max))
            continue

        status, titulo = parsear_y_guardar(conn, args, pid, html)
        if status == "ok":
            ok += 1
            suffix = " (dry-run, no guardado)" if args.dry_run else ""
            log.info("[%d/%d] ID %d → 💾 %s%s", n, total, pid, titulo, suffix)
            if not args.dry_run:
                marcar_progreso(conn, pid, "ok")
        elif status == "empty":
            vacios += 1
            log.info("[%d/%d] ID %d → sin ficha válida", n, total, pid)
            if not args.dry_run:
                marcar_progreso(conn, pid, "empty")
        else:
            errores += 1
            if not args.dry_run:
                marcar_progreso(conn, pid, "error")

        time.sleep(random.uniform(args.delay, args.delay_max))

    conn.close()
    print(f"\n{'='*55}")
    print(f"  Pendientes procesados : {total}")
    print(f"  Fichas guardadas      : {ok}")
    print(f"  IDs vacíos/404        : {vacios}")
    print(f"  Errores               : {errores}")
    if args.dry_run:
        print("\n  [dry-run] Nada escrito en BBDD.")


def ejecutar_modo_lista(conn, args):
    ids_lista = leer_ids_file(args.ids_file)
    en_icaa = ids_en_lista(conn, "icaa_fichas", ids_lista)
    en_scrape = ids_en_lista(conn, "scrape_icaa", ids_lista)
    if not args.dry_run:
        crear_tablas(conn)
        resueltos = ids_resueltos_en_lista_progress(conn, ids_lista)
    else:
        resueltos = set()

    excluidos = resueltos | en_icaa | en_scrape
    pendientes = [i for i in ids_lista if i not in excluidos]
    limit = args.limit or DEFAULT_IDS_FILE_LIMIT
    pendientes = pendientes[:limit]

    # Si el ID literal ya está marcado 'empty' en el barrido genérico por rango,
    # nos ahorramos esa primera petición y vamos directos al candidato +40.
    estado_generico = estado_progreso_generico(conn, pendientes)

    total = len(pendientes)
    log.info(
        "Lista %s: %d IDs totales, %d ya en icaa_fichas, %d ya en scrape_icaa, "
        "%d ya resueltos en corridas previas, %d pendientes (tanda de %d).",
        args.ids_file, len(ids_lista), len(en_icaa), len(en_scrape),
        len(resueltos), total, limit,
    )
    if not total:
        log.info("Nada pendiente en esta tanda.")
        return

    session = nueva_sesion()
    peticiones = ok = derivados = vacios = errores = 0

    for n, pid_lista in enumerate(pendientes, 1):
        if peticiones and peticiones % SESSION_REFRESH == 0:
            log.info("Renovando sesión tras %d peticiones…", peticiones)
            session.close()
            time.sleep(random.uniform(10, 20))
            session = nueva_sesion()

        candidatos = [pid_lista]
        derivado_pid = int(f"{pid_lista}{SUFIJO_EXPEDIENTE_TRUNCADO}")
        if estado_generico.get(pid_lista) == "empty":
            # Ya sabemos que el ID literal no tiene ficha; probamos directo el derivado.
            candidatos = [derivado_pid]
        else:
            candidatos.append(derivado_pid)

        resultado_status, resultado_titulo, resultado_pid = None, None, None
        error_persistente = False

        for i, pid in enumerate(candidatos):
            log.info("[%d/%d] Probando ID %d (expediente lista %d)…", n, total, pid, pid_lista)
            html, session, fatal = obtener_html_con_reintento(session, pid, args)
            peticiones += 1

            if fatal:
                error_persistente = True
                if not args.dry_run:
                    marcar_progreso(conn, pid, "error")
                break

            if html is None:
                if not args.dry_run:
                    marcar_progreso_si_falta(conn, pid, "empty")
                if i < len(candidatos) - 1:
                    time.sleep(random.uniform(args.delay, args.delay_max))
                    continue
                resultado_status = "empty"
                break

            status, titulo = parsear_y_guardar(conn, args, pid, html)
            if status == "ok":
                if not args.dry_run:
                    marcar_progreso(conn, pid, "ok")
                resultado_status, resultado_titulo, resultado_pid = "ok", titulo, pid
                break
            elif status == "empty":
                if not args.dry_run:
                    marcar_progreso_si_falta(conn, pid, "empty")
                if i < len(candidatos) - 1:
                    time.sleep(random.uniform(args.delay, args.delay_max))
                    continue
                resultado_status = "empty"
            else:
                if not args.dry_run:
                    marcar_progreso(conn, pid, "error")
                resultado_status = "error"
                break

        if error_persistente:
            log.error("[%d/%d] expediente lista %d → ERROR persistente", n, total, pid_lista)
            errores += 1
            if not args.dry_run:
                marcar_lista_progreso(conn, pid_lista, None, False)
        elif resultado_status == "ok":
            usado_sufijo = resultado_pid != pid_lista
            etiqueta = f" (vía sufijo {SUFIJO_EXPEDIENTE_TRUNCADO})" if usado_sufijo else ""
            suffix = " (dry-run, no guardado)" if args.dry_run else ""
            log.info("[%d/%d] expediente lista %d → 💾 %s%s%s",
                     n, total, pid_lista, resultado_titulo, etiqueta, suffix)
            if usado_sufijo:
                derivados += 1
            ok += 1
            if not args.dry_run:
                marcar_lista_progreso(conn, pid_lista, str(resultado_pid), True)
        elif resultado_status == "empty":
            vacios += 1
            log.info("[%d/%d] expediente lista %d → sin ficha (ni literal ni +%s)",
                      n, total, pid_lista, SUFIJO_EXPEDIENTE_TRUNCADO)
            if not args.dry_run:
                marcar_lista_progreso(conn, pid_lista, None, False)
        else:
            errores += 1
            if not args.dry_run:
                marcar_lista_progreso(conn, pid_lista, None, False)

        time.sleep(random.uniform(args.delay, args.delay_max))

    conn.close()
    print(f"\n{'='*55}")
    print(f"  Pendientes procesados       : {total}")
    print(f"  Fichas guardadas            : {ok}")
    print(f"    · resueltas vía sufijo +{SUFIJO_EXPEDIENTE_TRUNCADO} : {derivados}")
    print(f"  Sin ficha (ni literal ni +{SUFIJO_EXPEDIENTE_TRUNCADO}) : {vacios}")
    print(f"  Errores                     : {errores}")
    if args.dry_run:
        print("\n  [dry-run] Nada escrito en BBDD.")


if __name__ == "__main__":
    main()
