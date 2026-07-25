#!/usr/bin/env python3
"""
icaa_ultimas_calificadas.py — Snapshot de "Últimas calificadas" del ICAA → tabla ultimas_icaa

Descarga https://infoicaa.mcu.es/CatalogoICAA/es-es/Peliculas/UltimasCalificadas
(listado de las últimas películas calificadas por el ICAA, ~50 filas fijas) y
lo vuelca en la tabla ultimas_icaa. Pensado para correr a diario vía cron: al
observar cuántas filas nuevas aparecen cada día se puede luego ajustar la
frecuencia real del crawler.

Columnas de origen: Película (expediente), Título, Dirección, Nacionalidad,
Calificación, Resolución (fecha). Se guarda además fecha_insercion (solo en el
primer INSERT) y last_update (se refresca en cada ejecución que vea la fila).

USO
---
  python3 icaa_ultimas_calificadas.py
  python3 icaa_ultimas_calificadas.py --dry-run
"""

import argparse
import logging
import os
import re
import sys
from datetime import datetime

import psycopg2
import requests
import urllib3
from bs4 import BeautifulSoup
from dotenv import load_dotenv

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

load_dotenv()
DB_DSN = os.getenv("DATABASE_URL", "postgresql://localhost/taquilla_app")

URL = "https://infoicaa.mcu.es/CatalogoICAA/es-es/Peliculas/UltimasCalificadas"

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/150.0.0.0 Safari/537.36"),
    "Accept-Language": "es-ES,es;q=0.9",
}
TIMEOUT = 30

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger(__name__)

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS ultimas_icaa (
    expediente_icaa  TEXT PRIMARY KEY,
    titulo           TEXT,
    direccion        TEXT,
    nacionalidad     TEXT,
    calificacion     TEXT,
    resolucion       DATE,
    fecha_insercion  TIMESTAMP NOT NULL DEFAULT NOW(),
    last_update      TIMESTAMP NOT NULL DEFAULT NOW()
);
"""

UPSERT_SQL = """
INSERT INTO ultimas_icaa
    (expediente_icaa, titulo, direccion, nacionalidad, calificacion, resolucion,
     fecha_insercion, last_update)
VALUES (%s, %s, %s, %s, %s, %s, NOW(), NOW())
ON CONFLICT (expediente_icaa) DO UPDATE SET
    titulo       = EXCLUDED.titulo,
    direccion    = EXCLUDED.direccion,
    nacionalidad = EXCLUDED.nacionalidad,
    calificacion = EXCLUDED.calificacion,
    resolucion   = EXCLUDED.resolucion,
    last_update  = NOW();
"""


def get_db():
    return psycopg2.connect(DB_DSN)


def crear_tabla(conn):
    with conn.cursor() as cur:
        cur.execute(CREATE_TABLE_SQL)
    conn.commit()


def limpiar_texto(s):
    return re.sub(r"\s+", " ", s or "").strip() or None


def parsear_fecha(s):
    s = (s or "").strip()
    try:
        return datetime.strptime(s, "%d/%m/%Y").date()
    except ValueError:
        return None


def descargar_filas():
    resp = requests.get(URL, headers=HEADERS, timeout=TIMEOUT, verify=False)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    tabla = soup.find("table")
    if tabla is None:
        raise RuntimeError("No se encontró la tabla en la página")

    filas = []
    for tr in tabla.find("tbody").find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 6:
            continue
        expediente = limpiar_texto(tds[0].get_text())
        titulo = limpiar_texto(tds[1].get_text())
        direccion = limpiar_texto(tds[2].get_text())
        nacionalidad = limpiar_texto(tds[3].get_text())
        calificacion = limpiar_texto(tds[4].get_text())
        resolucion = parsear_fecha(tds[5].get_text())
        if not expediente or not titulo:
            continue
        filas.append((expediente, titulo, direccion, nacionalidad, calificacion, resolucion))
    return filas


def parse_args():
    p = argparse.ArgumentParser(description="Snapshot de últimas calificadas ICAA → ultimas_icaa")
    p.add_argument("--dry-run", action="store_true", help="Descarga y parsea sin escribir en BBDD")
    return p.parse_args()


def main():
    args = parse_args()
    filas = descargar_filas()
    log.info("Descargadas %d filas de 'Últimas calificadas'.", len(filas))

    if args.dry_run:
        for expediente, titulo, direccion, nacionalidad, calificacion, resolucion in filas:
            log.info("  %s | %s | %s | %s | %s | %s",
                      expediente, titulo, direccion, nacionalidad, calificacion, resolucion)
        log.info("[dry-run] Nada escrito en BBDD.")
        return

    conn = get_db()
    crear_tabla(conn)
    with conn.cursor() as cur:
        for fila in filas:
            cur.execute(UPSERT_SQL, fila)
    conn.commit()

    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM ultimas_icaa")
        total = cur.fetchone()[0]
    conn.close()
    log.info("Volcado completo. %d filas en esta ejecución, %d filas totales en ultimas_icaa.",
              len(filas), total)


if __name__ == "__main__":
    main()
