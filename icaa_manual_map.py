#!/usr/bin/env python3
"""
icaa_manual_map.py — Asocia manualmente un titulo de anual_esp con un ID del
catálogo ICAA cuando el matching automático de brave_icaa.py no funciona.

El titulo de anual_esp se guarda en la columna `titulo_anual_esp` de
icaa_fichas, lo que permite hacer JOIN entre ambas tablas aunque los titulos
difieran (acentos, artículos, subtítulos, etc.).

FLUJO
-----
  1. Crea la columna titulo_anual_esp en icaa_fichas si no existe
  2. Verifica que el titulo existe en anual_esp
  3. Verifica que el icaa_id es válido (existe en icaa_fichas o se puede descargar)
  4. Actualiza/inserta el mapeo en icaa_fichas

USO
---
  # Caso básico: el expediente ya está en icaa_fichas
  python3 icaa_manual_map.py --titulo "Cuaderno de Sara, El" --icaa-id 98765

  # Si el expediente aún no está en icaa_fichas, descarga y parsea el HTML
  python3 icaa_manual_map.py --titulo "Cuaderno de Sara, El" --icaa-id 98765 --fetch

  # Ver qué titulos de anual_esp no tienen mapeo en icaa_fichas (diagnóstico)
  python3 icaa_manual_map.py --list-missing --limit 20

  # Modo interactivo: pide titulo e ID uno a uno hasta que escribas 'q'
  python3 icaa_manual_map.py --interactive

.env
----
  DATABASE_URL=postgresql://localhost/comscore
"""

import argparse
import logging
import os
import subprocess
import sys
from pathlib import Path

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger(__name__)

HTML_DIR = Path(__file__).parent / "scraper_icaa" / "html_sources"
ICAA_URL = "https://sede.mcu.gob.es/CatalogoICAA/Peliculas/Detalle?Pelicula={}"


# ---------------------------------------------------------------------------
# Base de datos
# ---------------------------------------------------------------------------

def get_db():
    dsn = os.getenv("DATABASE_URL", "postgresql://localhost/comscore")
    return psycopg2.connect(dsn)


def ensure_column(conn):
    """Añade titulo_anual_esp a icaa_fichas si no existe todavía."""
    with conn.cursor() as cur:
        cur.execute("""
            ALTER TABLE icaa_fichas
            ADD COLUMN IF NOT EXISTS titulo_anual_esp TEXT;
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS icaa_titulo_anual_esp_idx
            ON icaa_fichas (titulo_anual_esp);
        """)
    conn.commit()
    log.info("Columna titulo_anual_esp lista.")


def check_titulo_anual_esp(conn, titulo: str):
    """Devuelve las filas de anual_esp que coinciden con ese titulo."""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""
            SELECT titulo, fecha_estreno, SUM(recaudacion) AS recaudacion_total
            FROM anual_esp
            WHERE titulo = %s
            GROUP BY titulo, fecha_estreno
            ORDER BY fecha_estreno
        """, (titulo,))
        return cur.fetchall()


def check_icaa_ficha(conn, icaa_id: str):
    """Devuelve la fila de icaa_fichas para ese expediente, o None."""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT expediente_icaa, titulo, director, fecha_estreno, titulo_anual_esp "
            "FROM icaa_fichas WHERE expediente_icaa = %s",
            (icaa_id,)
        )
        return cur.fetchone()


def save_mapping(conn, icaa_id: str, titulo_anual_esp: str):
    """
    Si el expediente ya existe en icaa_fichas, actualiza titulo_anual_esp.
    Si no existe, inserta un stub mínimo con ambos titulos.
    """
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO icaa_fichas (expediente_icaa, titulo, titulo_anual_esp)
            VALUES (%s, %s, %s)
            ON CONFLICT (expediente_icaa) DO UPDATE
                SET titulo_anual_esp = EXCLUDED.titulo_anual_esp,
                    updated_at       = NOW()
        """, (icaa_id, titulo_anual_esp, titulo_anual_esp))
    conn.commit()


def list_missing(conn, limit):
    """Muestra titulos de anual_esp sin mapeo en icaa_fichas."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT
                regexp_replace(
                    unaccent(LOWER(TRIM(
                        regexp_replace(split_part(a.titulo, ',', 1), '\\([^)]*\\)', '', 'g')
                    ))),
                    '^(el|la|los|las|un|una|unos|unas)\\s+', ''
                ) AS titulo_normalizado,
                MIN(a.titulo)        AS titulo_original,
                MIN(a.fecha_estreno) AS fecha_estreno,
                SUM(a.recaudacion)   AS recaudacion_total
            FROM anual_esp a
            LEFT JOIN icaa_fichas i
                ON regexp_replace(
                    unaccent(LOWER(TRIM(
                        regexp_replace(split_part(a.titulo, ',', 1), '\\([^)]*\\)', '', 'g')
                    ))),
                    '^(el|la|los|las|un|una|unos|unas)\\s+', ''
                )
                 = regexp_replace(
                    unaccent(LOWER(TRIM(
                        regexp_replace(split_part(i.titulo, ',', 1), '\\([^)]*\\)', '', 'g')
                    ))),
                    '^(el|la|los|las|un|una|unos|unas)\\s+', ''
                )
            WHERE i.titulo IS NULL
            GROUP BY 1
            ORDER BY recaudacion_total DESC
            LIMIT %s
        """, (limit,))
        rows = cur.fetchall()

    print(f"\n{'='*70}")
    print(f"  {'TÍTULO (anual_esp)':<45}  {'ESTRENO':<12}  {'RECAUDACIÓN':>12}")
    print(f"  {'-'*45}  {'-'*12}  {'-'*12}")
    for norm, orig, fecha, rec in rows:
        rec_str = f"{rec:>12,.0f} €" if rec else "           N/A"
        fecha_str = str(fecha) if fecha else "          N/A"
        print(f"  {orig:<45}  {fecha_str:<12}  {rec_str}")
    print(f"{'='*70}")
    print(f"  {len(rows)} títulos sin mapeo ICAA\n")
    print(f"  Para asociar uno manualmente:")
    print(f"    python3 icaa_manual_map.py --titulo \"<titulo>\" --icaa-id <ID> --fetch")
    print(f"  URL de búsqueda manual en el catálogo ICAA:")
    print(f"    https://sede.mcu.gob.es/CatalogoICAA/Peliculas/Buscar?Titulo=<titulo>\n")


# ---------------------------------------------------------------------------
# Fetch + parse opcional
# ---------------------------------------------------------------------------

def fetch_and_parse(icaa_id: str):
    """Descarga el HTML y lo parsea invocando los scripts existentes."""
    dest = HTML_DIR / f"{icaa_id}.html"
    HTML_DIR.mkdir(parents=True, exist_ok=True)

    if not dest.exists():
        import requests, urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        log.info("Descargando HTML para expediente %s...", icaa_id)
        url = ICAA_URL.format(icaa_id)
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "es-ES,es;q=0.9",
        }
        try:
            resp = requests.get(url, headers=headers, timeout=20, verify=False)
            resp.raise_for_status()
            dest.write_bytes(resp.content)
            log.info("HTML guardado en %s (%d bytes)", dest, dest.stat().st_size)
        except Exception as e:
            log.error("Error descargando HTML: %s", e)
            return False
    else:
        log.info("HTML ya existe en disco: %s", dest)

    # Parsear con icaa_parser.py
    parser_script = Path(__file__).parent / "icaa_parser.py"
    log.info("Ejecutando icaa_parser.py para expediente %s...", icaa_id)
    result = subprocess.run(
        [sys.executable, str(parser_script), "--limit", "1"],
        capture_output=False,
    )
    return result.returncode == 0


# ---------------------------------------------------------------------------
# Corrección de ID incorrecto
# ---------------------------------------------------------------------------

def do_fix_id(conn, old_id: str, new_id: str, fetch: bool):
    """
    Corrige un expediente_icaa incorrecto en icaa_fichas.
    Muestra ambas fichas (vieja y nueva) y pide confirmación antes de actuar.
    Pasos:
      1. Verifica que old_id existe en icaa_fichas
      2. Muestra la ficha actual (incorrecta) y la nueva si ya existe
      3. Pide confirmación
      4. UPDATE expediente_icaa = new_id (PostgreSQL permite actualizar PKs
         siempre que no haya foreign keys dependientes)
      5. Si --fetch: descarga y parsea el HTML del new_id para sobreescribir
         los datos con los correctos
    """
    ficha_old = check_icaa_ficha(conn, old_id)
    if not ficha_old:
        log.error("El expediente '%s' no existe en icaa_fichas.", old_id)
        return False

    print(f"\n  Ficha ACTUAL (ID incorrecto: {old_id}):")
    print(f"    · titulo ICAA:      {ficha_old['titulo']}")
    print(f"    · director:         {ficha_old['director'] or '—'}")
    print(f"    · fecha_estreno:    {ficha_old['fecha_estreno'] or '—'}")
    print(f"    · titulo_anual_esp: {ficha_old['titulo_anual_esp'] or '—'}")
    print(f"    · URL actual:       {ICAA_URL.format(old_id)}")

    ficha_new = check_icaa_ficha(conn, new_id)
    if ficha_new:
        print(f"\n  ⚠️  El ID nuevo ({new_id}) ya existe en icaa_fichas:")
        print(f"    · titulo ICAA:   {ficha_new['titulo']}")
        print(f"    · director:      {ficha_new['director'] or '—'}")
        print(f"    · fecha_estreno: {ficha_new['fecha_estreno'] or '—'}")
        print(f"\n  Si continúas, la fila con ID {old_id} se eliminará y sus")
        print(f"  datos se perderán (el ID {new_id} ya tiene su propia fila).")
    else:
        print(f"\n  ID nuevo: {new_id}")
        print(f"    · URL correcta: {ICAA_URL.format(new_id)}")
        if fetch:
            print(f"    · Se descargará y parseará el HTML para rellenar los datos.")

    print(f"\n  Cambio a aplicar:")
    print(f"    expediente_icaa  {old_id}  →  {new_id}")
    respuesta = input("\n  ¿Confirmar? [s/N]: ").strip().lower()
    if respuesta != "s":
        log.info("Operación cancelada.")
        return False

    with conn.cursor() as cur:
        if ficha_new:
            # El new_id ya existe: borrar la fila incorrecta (old_id)
            cur.execute(
                "DELETE FROM icaa_fichas WHERE expediente_icaa = %s",
                (old_id,)
            )
            log.info("Fila con ID %s eliminada (el ID %s ya existía).", old_id, new_id)
        else:
            # Renombrar la PK
            cur.execute(
                "UPDATE icaa_fichas SET expediente_icaa = %s WHERE expediente_icaa = %s",
                (new_id, old_id)
            )
            log.info("expediente_icaa actualizado: %s → %s", old_id, new_id)

        # Borrar HTML del ID incorrecto si existe en disco
        old_html = HTML_DIR / f"{old_id}.html"
        if old_html.exists():
            old_html.unlink()
            log.info("HTML antiguo eliminado: %s", old_html.name)

    conn.commit()

    if fetch:
        fetch_and_parse(new_id)

    log.info("✓ ID corregido correctamente: %s → %s", old_id, new_id)
    return True


# ---------------------------------------------------------------------------
# Lógica principal de mapeo
# ---------------------------------------------------------------------------

def do_map(conn, titulo: str, icaa_id: str, fetch: bool):
    """Valida y guarda el mapeo manual titulo_anual_esp → icaa_id."""

    # 1. Verificar que el titulo existe en anual_esp
    rows_anual = check_titulo_anual_esp(conn, titulo)
    if not rows_anual:
        log.error("El titulo '%s' no existe en anual_esp.", titulo)
        log.error("Comprueba la ortografía exacta (distingue mayúsculas y tildes).")
        return False

    print(f"\n  anual_esp → '{titulo}':")
    for r in rows_anual:
        rec = f"{r['recaudacion_total']:,.0f} €" if r["recaudacion_total"] else "N/A"
        print(f"    · estreno: {r['fecha_estreno']}  |  recaudación: {rec}")

    # 2. Descargar y parsear el HTML si se pide
    if fetch:
        fetch_and_parse(icaa_id)

    # 3. Verificar que el expediente existe en icaa_fichas
    ficha = check_icaa_ficha(conn, icaa_id)
    if ficha:
        print(f"\n  icaa_fichas → expediente {icaa_id}:")
        print(f"    · titulo ICAA:      {ficha['titulo']}")
        print(f"    · director:         {ficha['director'] or '—'}")
        print(f"    · fecha_estreno:    {ficha['fecha_estreno'] or '—'}")
        if ficha["titulo_anual_esp"]:
            print(f"    · titulo_anual_esp: {ficha['titulo_anual_esp']}  (ya tenía mapeo)")
    else:
        log.warning(
            "El expediente %s no está en icaa_fichas. "
            "Se insertará un stub. Usa --fetch para descargarlo.",
            icaa_id,
        )

    # 4. Confirmar
    print(f"\n  Mapeo a guardar:")
    print(f"    titulo_anual_esp = '{titulo}'")
    print(f"    expediente_icaa  = '{icaa_id}'")
    respuesta = input("\n  ¿Confirmar? [s/N]: ").strip().lower()
    if respuesta != "s":
        log.info("Operación cancelada.")
        return False

    save_mapping(conn, icaa_id, titulo)
    log.info("✓ Mapeo guardado correctamente.")
    return True


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_args():
    p = argparse.ArgumentParser(
        description="Mapeo manual titulo anual_esp → expediente ICAA"
    )
    p.add_argument("--titulo",       metavar="TITULO",
                   help="Titulo exacto tal como aparece en anual_esp")
    p.add_argument("--icaa-id",      metavar="ID",
                   help="Expediente ICAA (número)")
    p.add_argument("--fetch",        action="store_true",
                   help="Descarga y parsea el HTML del expediente si no está en icaa_fichas")
    p.add_argument("--list-missing", action="store_true",
                   help="Lista los titulos de anual_esp sin mapeo ICAA por recaudación")
    p.add_argument("--limit",        type=int, default=20, metavar="N",
                   help="Límite para --list-missing (default 20)")
    p.add_argument("--interactive",  action="store_true",
                   help="Modo interactivo: pide titulo e ID uno a uno")
    p.add_argument("--fix-id",       metavar="ID_INCORRECTO",
                   help="Expediente ICAA incorrecto a corregir en icaa_fichas")
    p.add_argument("--new-id",       metavar="ID_CORRECTO",
                   help="Nuevo expediente ICAA correcto (usar junto a --fix-id)")
    return p.parse_args()


def main():
    args = parse_args()
    conn = get_db()
    ensure_column(conn)

    if args.fix_id:
        if not args.new_id:
            log.error("--fix-id requiere también --new-id con el ID correcto.")
            sys.exit(1)
        do_fix_id(conn, args.fix_id, args.new_id, args.fetch)
        conn.close()
        return

    if args.list_missing:
        list_missing(conn, args.limit)
        conn.close()
        return

    if args.interactive:
        print("\nModo interactivo — escribe 'q' para salir.\n")
        while True:
            titulo = input("  Titulo (anual_esp): ").strip()
            if titulo.lower() == "q":
                break
            icaa_id = input("  ID ICAA:            ").strip()
            if icaa_id.lower() == "q":
                break
            fetch = input("  ¿Descargar HTML? [s/N]: ").strip().lower() == "s"
            do_map(conn, titulo, icaa_id, fetch)
            print()
        conn.close()
        return

    if not args.titulo or not args.icaa_id:
        log.error("Debes indicar --titulo y --icaa-id, o usar --interactive / --list-missing.")
        sys.exit(1)

    do_map(conn, args.titulo, args.icaa_id, args.fetch)
    conn.close()


if __name__ == "__main__":
    main()
