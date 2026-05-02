#!/usr/bin/env python3
"""
brave_icaa.py — Busca IDs de expediente ICAA via Brave Search API
y los guarda en la tabla `icaa_fichas` como stubs minimos.

FLUJO
-----
  1. Carga icaa_fichas -> construye mapa {titulo_normalizado -> expediente}
  2. Obtiene pares unicos (titulo, fecha_estreno) de anual_esp
     o de topespanol para un fin de semana concreto (--topespanol-weekend)
  3. Salta los que ya tienen coincidencia en icaa_fichas (0 queries)
  4. Para el resto: OR-batching de BATCH_SIZE titulos por llamada Brave
  5. Fallback individual: busca por titulo + fecha_estreno (DD/MM/YYYY)
     en lugar de distribuidora — reduce falsos positivos en el matching
  6. Guarda nuevos IDs en icaa_fichas como stub minimo
     (expediente_icaa + titulo; el resto lo rellena icaa_parser.py
      cuando se descargue el HTML correspondiente)

OPTIMIZACION DE QUERIES
------------------------
  Brave Free = 2.000 queries/mes. Con ~4.200 peliculas el enfoque 1:1 supera
  ese limite. Tres tecnicas combinadas:
    - OR batching   -> ("Titulo A" OR "Titulo B" OR "Titulo C") = divide /3
    - count=10      -> mas resultados/llamada -> menos fallbacks
    - Dedup por titulo -> ignora el anio, aplica ID a todos los anos

  Estimacion: ~1.400 batch calls + ~20pct fallbacks aprox 1.700 queries totales
  para el proceso inicial. Re-ejecuciones con --skip-existing gastan muy poco.

USO
---
  python3 brave_icaa.py --dry-run                        # preview sin guardar
  python3 brave_icaa.py --limit 20                       # prueba con 20 titulos
  python3 brave_icaa.py --skip-existing                  # salta titulos ya en icaa_fichas
  python3 brave_icaa.py                                  # proceso completo (anual_esp)
  python3 brave_icaa.py --topespanol-weekend 2026-04-24  # fin de semana topespanol

.env
----
  BRAVE_API_KEY=tu_clave
  DATABASE_URL=postgresql://localhost/comscore
"""

import argparse
import datetime
import logging
import os
import re
import sys
import time
import unicodedata
from typing import Dict, List, Optional, Tuple

import psycopg2
import psycopg2.extras
import requests
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

load_dotenv()

BRAVE_API_URL    = "https://api.search.brave.com/res/v1/web/search"

# La API de Brave NO soporta site: con ruta (site:dominio/ruta).
# Solo funciona a nivel de dominio. Para filtrar al catalogo ICAA
# usamos "CatalogoICAA" como keyword adicional en la query.
ICAA_SITE        = "site:sede.mcu.gob.es CatalogoICAA"
ICAA_ID_RE       = re.compile(r"[?&]Pelicula=(\d+)", re.IGNORECASE)

BATCH_SIZE       = 3    # titulos por llamada OR
RESULTS_PER_CALL = 10   # count= en la API
MIN_WORD_MATCH   = 0.55  # fraccion minima de palabras a coincidir

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers de texto
# ---------------------------------------------------------------------------

def normalize(text: str) -> str:
    """Minusculas, sin tildes, sin puntuacion — para comparar titulos."""
    nfkd = unicodedata.normalize("NFKD", text or "")
    ascii_ = nfkd.encode("ascii", "ignore").decode()
    return re.sub(r"[^\w\s]", " ", ascii_).lower()


def search_safe(titulo: str) -> str:
    """
    Version del titulo apta para la API de Brave sin comillas exactas:
      - Elimina contenido entre parentesis  (1960), (re), (V.O.), etc.
      - Elimina comas, puntos y otros chars que rompen el parser de Brave
      - Colapsa espacios multiples
    Sin comillas el matching es flexible: tolera tildes, plurales, etc.
    """
    t = re.sub(r"\([^)]*\)", " ", titulo)   # quitar (año), (re)…
    t = re.sub(r"[^\w\s]", " ", t)          # quitar , . : ; ' " etc.
    return re.sub(r"\s+", " ", t).strip()


def title_score(titulo: str, haystack: str) -> float:
    """Fraccion de palabras significativas del titulo que aparecen en el texto."""
    words = [w for w in normalize(titulo).split() if len(w) > 2]
    if not words:
        return 0.0
    return sum(1 for w in words if w in haystack) / len(words)


# ---------------------------------------------------------------------------
# Brave Search
# ---------------------------------------------------------------------------

def brave_search(query: str, api_key: str, debug: bool = False) -> List[dict]:
    """Llama a Brave Search con reintentos exponenciales en 429."""
    headers = {
        "Accept": "application/json",
        "Accept-Encoding": "gzip",
        "X-Subscription-Token": api_key,
    }
    params = {"q": query, "count": RESULTS_PER_CALL}

    log.info("  QUERY: %s", query)

    for attempt in range(4):
        try:
            resp = requests.get(BRAVE_API_URL, headers=headers, params=params, timeout=15)
        except requests.RequestException as exc:
            log.warning("Error de red (intento %d): %s", attempt + 1, exc)
            time.sleep(2 ** attempt)
            continue

        if resp.status_code == 200:
            data = resp.json()
            results = data.get("web", {}).get("results", [])

            if debug:
                import json
                # Mostrar claves de primer nivel de la respuesta
                log.debug("  Claves respuesta: %s", list(data.keys()))
                log.debug("  Resultados web: %d", len(results))
                for i, r in enumerate(results[:5]):
                    log.debug("  [%d] url=%s", i, r.get("url", ""))
                    log.debug("       title=%s", r.get("title", "")[:80])
                # Si no hay resultados web, mostrar estructura completa
                if not results:
                    log.debug("  Respuesta completa:\n%s",
                              json.dumps(data, indent=2, ensure_ascii=False)[:2000])

            return results

        if resp.status_code == 429:
            wait = 2 ** attempt
            log.warning("Rate limit (429) — esperando %ds...", wait)
            time.sleep(wait)
            continue

        log.warning("HTTP %d para: %s", resp.status_code, query[:80])
        if debug:
            log.debug("  Respuesta: %s", resp.text[:500])
        return []

    log.error("Reintentos agotados: %s", query[:80])
    return []


def extract_id(results: List[dict]) -> Optional[str]:
    """Devuelve el primer Pelicula=XXXXX encontrado (como string, igual que en DB)."""
    for r in results:
        m = ICAA_ID_RE.search(r.get("url", ""))
        if m:
            return m.group(1)
    return None


def match_results_to_titles(
    results: List[dict],
    titulos: List[str],
) -> Dict[str, str]:
    """
    Empareja cada resultado con el titulo mas probable del batch.
    Devuelve {titulo: expediente_icaa_str}.

    Logica de aceptacion:
      - score >= MIN_WORD_MATCH: match por titulo/descripcion del resultado
      - len(remaining) == 1: si solo queda un titulo sin emparejar y el
        resultado es una URL valida del catalogo ICAA, se acepta directamente
        sin exigir score minimo. Las fichas ICAA a veces devuelven titulos
        genericos ("Datos de Pelicula ICAA") que dan score 0 aunque la URL
        sea correcta; con un unico candidato no hay ambiguedad posible.
    """
    found: Dict[str, str] = {}
    remaining = list(titulos)

    for result in results:
        m = ICAA_ID_RE.search(result.get("url", ""))
        if not m:
            continue
        exp_id = m.group(1)

        haystack = normalize(
            result.get("title", "") + " " + result.get("description", "")
        )

        best_titulo = None
        best_score = 0.0
        for titulo in remaining:
            score = title_score(titulo, haystack)
            if score > best_score:
                best_score = score
                best_titulo = titulo

        if best_titulo and (best_score >= MIN_WORD_MATCH or len(remaining) == 1):
            found[best_titulo] = exp_id
            remaining.remove(best_titulo)

    return found


# ---------------------------------------------------------------------------
# Logica de busqueda
# ---------------------------------------------------------------------------

def search_batch(
    movies: List[Tuple[str, str]],
    api_key: str,
    delay: float,
    debug: bool = False,
) -> Dict[str, str]:
    """
    Una sola llamada OR para un batch de titulos. Devuelve {titulo: exp_id}.
    Usa terminos sin comillas para tolerar tildes y chars especiales en
    los titulos (comas, parentesis) que rompen el parser de Brave con comillas.
    """
    titulos = [m[0] for m in movies]
    terms   = " OR ".join(search_safe(t) for t in titulos)
    query   = f"{ICAA_SITE} {terms}"
    results = brave_search(query, api_key, debug=debug)
    time.sleep(delay)
    return match_results_to_titles(results, titulos)


def search_individual(
    titulo: str, fecha_str: str, api_key: str, delay: float, debug: bool = False,
) -> Optional[str]:
    """
    Fallback individual con dos queries en cascada:
      1. site:sede.mcu.gob.es CatalogoICAA <titulo_safe> <fecha_estreno_DD/MM/YYYY>
         (solo si fecha_str disponible — reduce falsos positivos)
      2. site:sede.mcu.gob.es CatalogoICAA <titulo_safe>
    Sin comillas para tolerar tildes y chars especiales.
    La fecha de estreno en las fichas ICAA aparece como "Fecha de Estreno: DD/MM/YYYY"
    y Brave la indexa, por lo que incluirla en la query mejora la precision.
    """
    safe = search_safe(titulo)
    queries = []
    if fecha_str:
        queries.append(f'{ICAA_SITE} {safe} "Fecha de Estreno: {fecha_str}"')
    queries.append(f"{ICAA_SITE} {safe}")
    for q in queries:
        results = brave_search(q, api_key, debug=debug)
        time.sleep(delay)
        exp_id = extract_id(results)
        if exp_id:
            return exp_id
    return None


# ---------------------------------------------------------------------------
# Base de datos
# ---------------------------------------------------------------------------

def get_db(dsn: str):
    return psycopg2.connect(dsn)


def fecha_to_str(fecha) -> str:
    """
    Convierte un objeto date/datetime de psycopg2 al formato de la web ICAA:
    DD/MM/YYYY (p.ej. 2019-11-15 -> "15/11/2019").
    Devuelve cadena vacia si fecha es None.
    """
    if fecha is None:
        return ""
    if isinstance(fecha, (datetime.date, datetime.datetime)):
        return fecha.strftime("%d/%m/%Y")
    # Por si viene como string "YYYY-MM-DD"
    try:
        d = datetime.date.fromisoformat(str(fecha))
        return d.strftime("%d/%m/%Y")
    except ValueError:
        return ""


def load_fichas_map(conn) -> Dict[str, str]:
    """
    Devuelve {normalize(titulo) -> expediente_icaa} para todos los registros
    ya existentes en icaa_fichas. Usamos esto para saltar sin gastar queries.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT titulo, expediente_icaa FROM icaa_fichas WHERE titulo IS NOT NULL")
        return {normalize(row[0]): str(row[1]) for row in cur.fetchall()}


def fetch_unique_pairs(
    conn,
    skip_existing: bool,
    fichas_map: Dict[str, str],
    limit: Optional[int],
) -> List[Tuple[str, str]]:
    """
    Devuelve pares unicos (titulo, fecha_estreno_str) de anual_esp que aun no
    tienen entrada en icaa_fichas (comparacion por titulo normalizado).
    fecha_estreno_str esta en formato DD/MM/YYYY (el mismo que usa la web ICAA)
    para que pueda usarse directamente como termino de busqueda en Brave.
    Se agrupa por titulo tomando la fecha_estreno mas antigua registrada.
    """
    sql = """
        SELECT titulo, MIN(fecha_estreno) AS fecha_estreno
        FROM anual_esp
        WHERE titulo IS NOT NULL
        GROUP BY titulo
        ORDER BY titulo
    """
    if limit:
        sql += f" LIMIT {int(limit)}"

    with conn.cursor() as cur:
        cur.execute(sql)
        rows = cur.fetchall()

    pairs = [(t, fecha_to_str(fe)) for t, fe in rows]

    if skip_existing:
        pairs = [(t, fe) for t, fe in pairs if normalize(t) not in fichas_map]

    return pairs


def fetch_topespanol_pairs(
    conn,
    fecha_ini: str,
    skip_existing: bool,
    fichas_map: Dict[str, str],
    limit: Optional[int],
) -> List[Tuple[str, str]]:
    """
    Devuelve pares (titulo, fecha_estreno_str) para las peliculas del fin de
    semana indicado en la tabla topespanol.
    Busca la fecha_estreno en anual_esp haciendo join por titulo (ILIKE).
    Si no hay coincidencia, fecha_estreno_str queda vacia (busqueda solo por titulo).
    """
    sql = """
        SELECT DISTINCT
            t.titulo,
            (
                SELECT MIN(a.fecha_estreno)
                FROM anual_esp a
                WHERE LOWER(a.titulo) = LOWER(t.titulo)
            ) AS fecha_estreno
        FROM topespanol t
        WHERE t.fecha_inicio = %(fecha_ini)s
          AND t.titulo IS NOT NULL
        ORDER BY t.titulo
    """
    if limit:
        sql += f" LIMIT {int(limit)}"

    with conn.cursor() as cur:
        cur.execute(sql, {"fecha_ini": fecha_ini})
        rows = cur.fetchall()

    pairs = [(t, fecha_to_str(fe)) for t, fe in rows]

    if skip_existing:
        pairs = [(t, fe) for t, fe in pairs if normalize(t) not in fichas_map]

    return pairs


def fetch_top_missing_pairs(
    conn,
    top_n: int,
) -> List[Tuple[str, str]]:
    """
    Devuelve los top_n titulos de anual_esp con mayor recaudacion acumulada
    que todavia NO tienen entrada en icaa_fichas. Incluye la fecha_estreno
    mas antigua disponible en formato DD/MM/YYYY.

    Equivale a:
        SELECT titulo, SUM(recaudacion) AS recaudacion_total
        FROM anual_esp
        WHERE titulo NOT IN (SELECT titulo FROM icaa_fichas)
        GROUP BY titulo
        ORDER BY recaudacion_total DESC
        LIMIT top_n
    """
    sql = """
        SELECT
            regexp_replace(
                unaccent(LOWER(TRIM(
                    regexp_replace(split_part(a.titulo, ',', 1), '\\([^)]*\\)', '', 'g')
                ))),
                '^(el|la|los|las|un|una|unos|unas)\\s+', ''
            ) AS titulo_normalizado,
            MIN(a.fecha_estreno)  AS fecha_estreno,
            SUM(a.recaudacion)    AS recaudacion_total
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
        GROUP BY regexp_replace(
            unaccent(LOWER(TRIM(
                regexp_replace(split_part(a.titulo, ',', 1), '\\([^)]*\\)', '', 'g')
            ))),
            '^(el|la|los|las|un|una|unos|unas)\\s+', ''
        )
        ORDER BY recaudacion_total DESC
        LIMIT %(top_n)s
    """
    with conn.cursor() as cur:
        cur.execute(sql, {"top_n": top_n})
        rows = cur.fetchall()

    for titulo_norm, fecha, rec in rows:
        log.info("  TOP-MISSING  %s  (%.0f€, estreno: %s)",
                 titulo_norm[:55], rec or 0, fecha_to_str(fecha) or "—")

    # titulo_normalizado ya viene limpio (sin tildes, minúsculas, sin el artículo
    # final tipo ", La") — suficiente para que search_safe() lo use en Brave
    return [(titulo_norm, fecha_to_str(fecha)) for titulo_norm, fecha, _ in rows]


def insert_stub(conn, exp_id: str, titulo: str) -> bool:
    """
    Inserta un registro minimo en icaa_fichas.
    ON CONFLICT DO NOTHING: si el expediente ya existe con datos completos,
    no lo sobreescribimos.
    Devuelve True si se inserto una fila nueva.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO icaa_fichas (expediente_icaa, titulo)
            VALUES (%s, %s)
            ON CONFLICT (expediente_icaa) DO NOTHING
            """,
            (exp_id, titulo),
        )
        inserted = cur.rowcount
    conn.commit()
    return inserted > 0


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_args():
    p = argparse.ArgumentParser(
        description="Brave Search -> expediente ICAA -> icaa_fichas (stubs)"
    )
    p.add_argument("--dry-run",       action="store_true",
                   help="Sin escritura en BBDD")
    p.add_argument("--limit",         type=int, default=None, metavar="N",
                   help="Procesar solo N titulos unicos")
    p.add_argument("--skip-existing", action="store_true",
                   help="Saltar titulos que ya aparecen en icaa_fichas")
    p.add_argument("--delay",         type=float, default=1.0, metavar="SEG",
                   help="Segundos entre llamadas API (default 1.0)")
    p.add_argument("--batch-size",    type=int, default=BATCH_SIZE, metavar="N",
                   help=f"Titulos por llamada OR (default {BATCH_SIZE})")
    p.add_argument("--debug",         action="store_true",
                   help="Muestra la respuesta raw de la API para diagnostico")
    p.add_argument("--topespanol-weekend", metavar="YYYY-MM-DD", default=None,
                   help="Procesar solo las peliculas de topespanol de ese fin de semana "
                        "(fecha_inicio en la tabla). Ej: 2026-04-24")
    p.add_argument("--top-missing",       type=int, default=None, metavar="N",
                   help="Buscar los N titulos con mayor recaudacion que aun no estan "
                        "en icaa_fichas (usa anual_esp GROUP BY titulo, fecha_estreno). "
                        "Ej: --top-missing 15")
    return p.parse_args()


def main():
    args = parse_args()

    api_key = os.getenv("BRAVE_API_KEY", "").strip()
    if not api_key:
        log.error("BRAVE_API_KEY no encontrado en .env")
        sys.exit(1)

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)
        log.debug("Modo debug activo — se mostrara la respuesta raw de cada query.")

    dsn  = os.getenv("DATABASE_URL", "postgresql://localhost/comscore")
    conn = get_db(dsn)

    # -- Cargar mapa de icaa_fichas (gratis, 0 queries) ----------------------
    fichas_map = load_fichas_map(conn)
    log.info("icaa_fichas: %d titulos ya con expediente conocido.", len(fichas_map))

    # -- Pares unicos a procesar ---------------------------------------------
    if args.top_missing:
        log.info("Fuente: top-%d titulos con mayor recaudacion sin ficha ICAA", args.top_missing)
        pairs = fetch_top_missing_pairs(conn, args.top_missing)
    elif args.topespanol_weekend:
        log.info("Fuente: topespanol, fin de semana %s", args.topespanol_weekend)
        pairs = fetch_topespanol_pairs(
            conn, args.topespanol_weekend, args.skip_existing, fichas_map, args.limit
        )
    else:
        log.info("Fuente: anual_esp (proceso completo)")
        pairs = fetch_unique_pairs(conn, args.skip_existing, fichas_map, args.limit)

    total = len(pairs)

    n_batches   = max(1, (total + args.batch_size - 1) // args.batch_size)
    est_queries = int(n_batches * 1.25)   # +25% de margen para fallbacks
    log.info(
        "Titulos a buscar: %d -> ~%d llamadas batch(%d) + fallbacks ~ %d queries.",
        total, n_batches, args.batch_size, est_queries,
    )
    if args.dry_run:
        log.info("Modo dry-run activo: no se escribira en BBDD.")

    found = not_found = already_known = inserted_new = 0
    processed = 0

    for batch_start in range(0, total, args.batch_size):
        batch = pairs[batch_start : batch_start + args.batch_size]

        # Separar los que ya estan en icaa_fichas (puede haber llegado
        # en esta misma sesion tras un insert anterior del mismo loop)
        need_search = []
        for titulo, fecha_str in batch:
            key = normalize(titulo)
            if key in fichas_map:
                processed += 1
                already_known += 1
                log.info(
                    "[%d/%d] %s -> ya en icaa_fichas (%s)",
                    processed, total, titulo[:55], fichas_map[key],
                )
            else:
                need_search.append((titulo, fecha_str))

        if not need_search:
            continue

        # -- Llamada OR batch -------------------------------------------------
        batch_results = search_batch(need_search, api_key, args.delay, debug=args.debug)

        for titulo, fecha_str in need_search:
            processed += 1
            prefix = f"[{processed}/{total}] {titulo[:55]}"

            if titulo in batch_results:
                exp_id = batch_results[titulo]
                found += 1
                log.info("%s -> OK %s (batch)", prefix, exp_id)
                fichas_map[normalize(titulo)] = exp_id   # actualizar cache local
                if not args.dry_run:
                    new = insert_stub(conn, exp_id, titulo)
                    if new:
                        inserted_new += 1
            else:
                # -- Fallback individual con fecha_estreno --------------------
                exp_id = search_individual(titulo, fecha_str, api_key, args.delay, debug=args.debug)
                if exp_id:
                    found += 1
                    log.info("%s -> OK %s (fallback)", prefix, exp_id)
                    fichas_map[normalize(titulo)] = exp_id
                    if not args.dry_run:
                        new = insert_stub(conn, exp_id, titulo)
                        if new:
                            inserted_new += 1
                else:
                    not_found += 1
                    log.info("%s -> no encontrado", prefix)

    conn.close()

    print(f"\n{'='*60}")
    print(f"  Pares procesados          : {total}")
    print(f"  Ya en icaa_fichas (skip)  : {already_known}")
    print(f"  Encontrados via Brave     : {found}")
    print(f"  No encontrados            : {not_found}")
    if not args.dry_run:
        print(f"  Stubs insertados nuevos   : {inserted_new}")
    print(f"  Queries estimadas usadas  : ~{est_queries}")
    if args.dry_run:
        print("\n  [dry-run] Nada guardado en BBDD.")
    else:
        print(
            f"\n  Siguiente paso: descarga los HTMLs de los {inserted_new} nuevos"
            " expedientes y ejecuta icaa_parser.py para completar los datos."
        )
        print(
            "  URL: https://sede.mcu.gob.es/CatalogoICAA/Pelicula/Details?Pelicula=<ID>"
        )


if __name__ == "__main__":
    main()
