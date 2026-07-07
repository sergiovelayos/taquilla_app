"""
tmdb_gente_importer.py — Importa datos de personas desde TMDB a public.tmdb_gente

Soporta dos fuentes:
  --tipo director  Lee icaa_fichas.director (con subvenciones)
  --tipo actor     Lee icaa_fichas.ficha_artistica[].nombre (campo JSONB)

La tabla tmdb_gente es compartida: una persona que aparezca como director
Y como actor acumulará ambos roles en el campo roles[].

Uso:
  python3 tmdb_gente_importer.py --tipo director           # todos los directores
  python3 tmdb_gente_importer.py --tipo actor              # todos los actores
  python3 tmdb_gente_importer.py --tipo actor --limit 50   # prueba con 50
  python3 tmdb_gente_importer.py --tipo actor --dry-run    # sin escribir en BD
  python3 tmdb_gente_importer.py --tipo actor --skip-existing
  python3 tmdb_gente_importer.py --nombre "Penélope Cruz"  # una persona concreta
"""

import os
import re
import time
import logging
import argparse
import unicodedata
from datetime import datetime

import requests
import psycopg2
from dotenv import load_dotenv

# ─── Config ─────────────────────────────────────────────────────────────────────

load_dotenv()

TMDB_TOKEN = os.getenv("TMDB_TOKEN")
if not TMDB_TOKEN:
    raise RuntimeError(
        "La variable de entorno TMDB_TOKEN no está definida. "
        "Añádela a tu fichero .env o expórtala antes de ejecutar el script.\n"
        "  export TMDB_TOKEN=eyJ..."
    )
TMDB_BASE   = "https://api.themoviedb.org/3"
TMDB_IMG_W  = "https://image.tmdb.org/t/p/w500"
TMDB_IMG_OR = "https://image.tmdb.org/t/p/original"

DB_DSN = os.getenv("DATABASE_URL", "postgresql://localhost/taquilla_app")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]
)
log = logging.getLogger(__name__)

# ─── TMDB API ────────────────────────────────────────────────────────────────────

_session = requests.Session()
_session.headers.update({
    "Authorization": f"Bearer {TMDB_TOKEN}",
    "accept": "application/json"
})

def tmdb_get(path, params=None):
    url = f"{TMDB_BASE}{path}"
    for attempt in range(3):
        try:
            r = _session.get(url, params=params, timeout=15)
            if r.status_code == 429:
                wait = int(r.headers.get("Retry-After", 5))
                log.warning(f"  Rate limit: esperando {wait}s…")
                time.sleep(wait)
                continue
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:
            log.warning(f"  Intento {attempt+1}/3 fallido ({path}): {e}")
            time.sleep(2 ** attempt)
    return None

# ─── Normalización ───────────────────────────────────────────────────────────────

def normalizar(texto):
    if not texto:
        return ""
    texto = unicodedata.normalize("NFD", texto)
    texto = "".join(c for c in texto if unicodedata.category(c) != "Mn")
    texto = texto.lower().strip()
    texto = re.sub(r"[^\w\s]", " ", texto)
    return re.sub(r"\s+", " ", texto).strip()

def nombre_icaa_a_query(nombre_icaa):
    """Convierte 'Apellido, Nombre' → 'Nombre Apellido' para buscar en TMDB."""
    nombre = nombre_icaa.strip()
    if "," in nombre:
        partes = nombre.split(",", 1)
        nombre = f"{partes[1].strip()} {partes[0].strip()}"
    return nombre

def score_nombre(nombre_icaa, nombre_tmdb):
    """Similitud entre nombre ICAA y nombre TMDB. Devuelve float 0–1."""
    if not nombre_icaa or not nombre_tmdb:
        return 0.0
    n2 = normalizar(nombre_tmdb)

    # Comparar en formato directo
    n1 = normalizar(nombre_icaa)
    if n1 == n2:
        return 1.0

    # Comparar en formato invertido ("Apellido, Nombre" → "Nombre Apellido")
    n1_inv = normalizar(nombre_icaa_a_query(nombre_icaa))
    if n1_inv == n2:
        return 1.0

    # Score por palabras compartidas (el mejor de los dos formatos)
    def word_score(a, b):
        wa, wb = set(a.split()), set(b.split())
        return len(wa & wb) / max(len(wa), len(wb)) if wa and wb else 0.0

    return max(word_score(n1, n2), word_score(n1_inv, n2))

# ─── Búsqueda y detalle en TMDB ─────────────────────────────────────────────────

def buscar_persona(nombre_icaa, dpto_preferido="Directing"):
    """
    Busca una persona en TMDB.
    dpto_preferido: 'Directing' para directores, 'Acting' para actores.
    Devuelve (tmdb_id, score, candidato_dict) o (None, 0, None).
    """
    query = nombre_icaa_a_query(nombre_icaa)

    data = tmdb_get("/search/person", {"query": query, "language": "es-ES"})
    if not data or not data.get("results"):
        # Segundo intento sin acentos
        data = tmdb_get("/search/person", {"query": normalizar(query).title(), "language": "es-ES"})

    if not data or not data.get("results"):
        return None, 0.0, None

    resultados = data["results"]

    # Ordenar: primero el departamento preferido, luego por popularidad
    def prioridad(r):
        dept = r.get("known_for_department", "")
        return (0 if dept == dpto_preferido else 1, -r.get("popularity", 0))
    resultados.sort(key=prioridad)

    best_score, best_id, best_candidato = 0.0, None, None
    for candidato in resultados[:5]:
        score = score_nombre(nombre_icaa, candidato.get("name", ""))
        score = min(1.0, score + min(candidato.get("popularity", 0) / 500, 0.05))
        if score > best_score:
            best_score, best_id, best_candidato = score, candidato["id"], candidato

    return best_id, best_score, best_candidato

def obtener_detalle_persona(tmdb_id):
    return tmdb_get(
        f"/person/{tmdb_id}",
        {"language": "es-ES", "append_to_response": "external_ids,movie_credits,images"}
    )

def extraer_datos_persona(detalle, tipo="director"):
    """
    Extrae todos los campos de la respuesta TMDB.
    tipo: 'director' → rellena peliculas_dirigidas
          'actor'    → rellena peliculas_actuado
    Ambos tipos rellenan los campos biográficos y de foto.
    """
    if not detalle:
        return {}

    profile_path = detalle.get("profile_path", "")
    foto_url     = f"{TMDB_IMG_W}{profile_path}"    if profile_path else None
    foto_url_hd  = f"{TMDB_IMG_OR}{profile_path}"   if profile_path else None

    imagenes_raw = detalle.get("images", {}).get("profiles", [])
    todas_las_fotos = [
        f"{TMDB_IMG_OR}{img['file_path']}"
        for img in sorted(imagenes_raw, key=lambda x: x.get("vote_average", 0), reverse=True)
        if img.get("file_path")
    ]

    ext = detalle.get("external_ids", {})

    # Créditos como director
    crew = detalle.get("movie_credits", {}).get("crew", [])
    dirs = [
        c for c in crew
        if c.get("job") in ("Director", "Co-Director")
    ]
    dirs.sort(key=lambda x: (x.get("release_date") or ""), reverse=True)
    peliculas_dirigidas = [
        f"{c['title']} ({(c.get('release_date') or '')[:4]})" if c.get("release_date") else c["title"]
        for c in dirs
    ]

    # Créditos como actor/actriz
    cast = detalle.get("movie_credits", {}).get("cast", [])
    # Ordenar por popularidad de la película (campo 'popularity') y tomar los más relevantes
    cast_sorted = sorted(cast, key=lambda x: x.get("popularity", 0), reverse=True)
    peliculas_actuado = []
    for c in cast_sorted[:50]:  # máximo 50 para no inflar el array
        anno = (c.get("release_date") or "")[:4]
        titulo = c.get("title", "")
        personaje = c.get("character", "")
        entrada = f"{titulo} ({anno})" if anno else titulo
        if personaje:
            entrada += f" [{personaje}]"
        peliculas_actuado.append(entrada)

    genero_map = {0: None, 1: "F", 2: "M"}

    return {
        "tmdb_id":               detalle.get("id"),
        "nombre_tmdb":           detalle.get("name", ""),
        "tambien_conocido_como": detalle.get("also_known_as", []),
        "foto_url":              foto_url,
        "foto_url_hd":           foto_url_hd,
        "todas_las_fotos":       todas_las_fotos,
        "biografia":             detalle.get("biography") or None,
        "fecha_nacimiento":      detalle.get("birthday") or None,
        "lugar_nacimiento":      detalle.get("place_of_birth") or None,
        "fecha_fallecimiento":   detalle.get("deathday") or None,
        "popularidad":           detalle.get("popularity"),
        "departamento":          detalle.get("known_for_department"),
        "genero":                genero_map.get(detalle.get("gender", 0)),
        "homepage":              detalle.get("homepage") or None,
        "imdb_id":               ext.get("imdb_id"),
        "wikidata_id":           ext.get("wikidata_id"),
        "instagram_id":          ext.get("instagram_id"),
        "twitter_id":            ext.get("twitter_id"),
        # Director
        "num_peliculas_director": len(dirs),
        "peliculas_dirigidas":    peliculas_dirigidas,
        # Actor
        "num_peliculas_actor":    len(cast),
        "peliculas_actuado":      peliculas_actuado,
    }

# ─── Base de datos ───────────────────────────────────────────────────────────────

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS public.tmdb_gente (
    -- Identificadores
    nombre_icaa              TEXT         NOT NULL PRIMARY KEY,
    tmdb_id                  INTEGER      UNIQUE,
    imdb_id                  TEXT,
    wikidata_id              TEXT,

    -- Roles en el proyecto (acumulativo: director, actor, o ambos)
    roles                    TEXT[]       DEFAULT '{}',

    -- Nombre y alias
    nombre_tmdb              TEXT,
    tambien_conocido_como    TEXT[],

    -- Fotos
    foto_url                 TEXT,        -- 500px, lista para web
    foto_url_hd              TEXT,        -- resolución original
    todas_las_fotos          TEXT[],      -- todas las disponibles, ordenadas por valoración

    -- Biográfico
    biografia                TEXT,
    fecha_nacimiento         DATE,
    lugar_nacimiento         TEXT,
    fecha_fallecimiento      DATE,
    genero                   CHAR(1),     -- 'M', 'F' o NULL

    -- Datos TMDB
    departamento             TEXT,        -- 'Directing', 'Acting', etc.
    popularidad              NUMERIC(10,4),
    homepage                 TEXT,
    instagram_id             TEXT,
    twitter_id               TEXT,

    -- Filmografía como director
    num_peliculas_director   INTEGER,
    peliculas_dirigidas      TEXT[],      -- "Título (año)", orden desc

    -- Filmografía como actor/actriz
    num_peliculas_actor      INTEGER,
    peliculas_actuado        TEXT[],      -- "Título (año) [Personaje]", orden por popularidad

    -- Metadatos del proceso
    match_score              NUMERIC(4,2),
    revisado_manual          BOOLEAN      DEFAULT FALSE,
    notas                    TEXT,
    created_at               TIMESTAMPTZ  DEFAULT NOW(),
    updated_at               TIMESTAMPTZ  DEFAULT NOW()
);

-- Columnas nuevas (idempotente: no falla si ya existen)
DO $$ BEGIN
    ALTER TABLE public.tmdb_gente ADD COLUMN IF NOT EXISTS roles TEXT[] DEFAULT '{}';
    ALTER TABLE public.tmdb_gente ADD COLUMN IF NOT EXISTS num_peliculas_director INTEGER;
    ALTER TABLE public.tmdb_gente ADD COLUMN IF NOT EXISTS num_peliculas_actor INTEGER;
    ALTER TABLE public.tmdb_gente ADD COLUMN IF NOT EXISTS peliculas_actuado TEXT[];
EXCEPTION WHEN duplicate_column THEN NULL;
END $$;

-- Índices
CREATE INDEX IF NOT EXISTS idx_tmdb_gente_tmdb_id
    ON public.tmdb_gente (tmdb_id);
CREATE INDEX IF NOT EXISTS idx_tmdb_gente_nombre_tmdb
    ON public.tmdb_gente (nombre_tmdb);
CREATE INDEX IF NOT EXISTS idx_tmdb_gente_popularidad
    ON public.tmdb_gente (popularidad DESC NULLS LAST);
CREATE INDEX IF NOT EXISTS idx_tmdb_gente_roles
    ON public.tmdb_gente USING GIN (roles);
"""

# El upsert acumula roles sin duplicar (array_cat + DISTINCT)
# y protege revisado_manual y notas de sobreescrituras accidentales.
UPSERT_SQL = """
INSERT INTO public.tmdb_gente (
    nombre_icaa, tmdb_id, imdb_id, wikidata_id,
    roles,
    nombre_tmdb, tambien_conocido_como,
    foto_url, foto_url_hd, todas_las_fotos,
    biografia, fecha_nacimiento, lugar_nacimiento, fecha_fallecimiento, genero,
    departamento, popularidad, homepage, instagram_id, twitter_id,
    num_peliculas_director, peliculas_dirigidas,
    num_peliculas_actor, peliculas_actuado,
    match_score, updated_at
) VALUES (
    %(nombre_icaa)s, %(tmdb_id)s, %(imdb_id)s, %(wikidata_id)s,
    %(roles)s,
    %(nombre_tmdb)s, %(tambien_conocido_como)s,
    %(foto_url)s, %(foto_url_hd)s, %(todas_las_fotos)s,
    %(biografia)s, %(fecha_nacimiento)s, %(lugar_nacimiento)s, %(fecha_fallecimiento)s, %(genero)s,
    %(departamento)s, %(popularidad)s, %(homepage)s, %(instagram_id)s, %(twitter_id)s,
    %(num_peliculas_director)s, %(peliculas_dirigidas)s,
    %(num_peliculas_actor)s, %(peliculas_actuado)s,
    %(match_score)s, NOW()
)
ON CONFLICT (nombre_icaa) DO UPDATE SET
    tmdb_id                 = EXCLUDED.tmdb_id,
    imdb_id                 = EXCLUDED.imdb_id,
    wikidata_id             = EXCLUDED.wikidata_id,
    -- roles: acumula sin duplicar
    roles                   = ARRAY(
                                  SELECT DISTINCT unnest(
                                      array_cat(
                                          COALESCE(tmdb_gente.roles, '{}'),
                                          EXCLUDED.roles
                                      )
                                  ) ORDER BY 1
                              ),
    nombre_tmdb             = EXCLUDED.nombre_tmdb,
    tambien_conocido_como   = EXCLUDED.tambien_conocido_como,
    foto_url                = EXCLUDED.foto_url,
    foto_url_hd             = EXCLUDED.foto_url_hd,
    todas_las_fotos         = EXCLUDED.todas_las_fotos,
    biografia               = EXCLUDED.biografia,
    fecha_nacimiento        = EXCLUDED.fecha_nacimiento,
    lugar_nacimiento        = EXCLUDED.lugar_nacimiento,
    fecha_fallecimiento     = EXCLUDED.fecha_fallecimiento,
    genero                  = EXCLUDED.genero,
    departamento            = EXCLUDED.departamento,
    popularidad             = EXCLUDED.popularidad,
    homepage                = EXCLUDED.homepage,
    instagram_id            = EXCLUDED.instagram_id,
    twitter_id              = EXCLUDED.twitter_id,
    num_peliculas_director  = COALESCE(EXCLUDED.num_peliculas_director, tmdb_gente.num_peliculas_director),
    peliculas_dirigidas     = COALESCE(EXCLUDED.peliculas_dirigidas,    tmdb_gente.peliculas_dirigidas),
    num_peliculas_actor     = COALESCE(EXCLUDED.num_peliculas_actor,    tmdb_gente.num_peliculas_actor),
    peliculas_actuado       = COALESCE(EXCLUDED.peliculas_actuado,      tmdb_gente.peliculas_actuado),
    match_score             = EXCLUDED.match_score,
    updated_at              = NOW()
    -- revisado_manual y notas NO se tocan (protección edición manual)
;
"""

def get_db():
    return psycopg2.connect(DB_DSN)

def crear_tabla_y_migrar(conn):
    with conn.cursor() as cur:
        cur.execute(CREATE_TABLE_SQL)
    conn.commit()
    log.info("✅ Tabla 'tmdb_gente' e índices listos.")

def get_ya_importados(conn, tipo):
    """
    Devuelve el set de nombre_icaa que ya tienen el rol especificado.
    Así --skip-existing solo salta a quien ya fue importado como ese tipo.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT nombre_icaa FROM public.tmdb_gente WHERE %s = ANY(roles)",
            (tipo,)
        )
        return {row[0] for row in cur.fetchall()}

def guardar_persona(conn, nombre_icaa, datos, score, rol):
    def parse_date(d):
        if not d:
            return None
        try:
            return datetime.strptime(d, "%Y-%m-%d").date()
        except ValueError:
            return None

    # Para campos de director/actor: solo enviamos los del tipo actual,
    # el COALESCE del upsert conserva los del otro tipo si ya existían.
    params = {
        "nombre_icaa":           nombre_icaa,
        "tmdb_id":               datos.get("tmdb_id"),
        "imdb_id":               datos.get("imdb_id"),
        "wikidata_id":           datos.get("wikidata_id"),
        "roles":                 [rol],
        "nombre_tmdb":           datos.get("nombre_tmdb"),
        "tambien_conocido_como": datos.get("tambien_conocido_como", []),
        "foto_url":              datos.get("foto_url"),
        "foto_url_hd":           datos.get("foto_url_hd"),
        "todas_las_fotos":       datos.get("todas_las_fotos", []),
        "biografia":             datos.get("biografia"),
        "fecha_nacimiento":      parse_date(datos.get("fecha_nacimiento")),
        "lugar_nacimiento":      datos.get("lugar_nacimiento"),
        "fecha_fallecimiento":   parse_date(datos.get("fecha_fallecimiento")),
        "genero":                datos.get("genero"),
        "departamento":          datos.get("departamento"),
        "popularidad":           datos.get("popularidad"),
        "homepage":              datos.get("homepage"),
        "instagram_id":          datos.get("instagram_id"),
        "twitter_id":            datos.get("twitter_id"),
        "num_peliculas_director": datos.get("num_peliculas_director") if rol == "director" else None,
        "peliculas_dirigidas":    datos.get("peliculas_dirigidas", [])  if rol == "director" else None,
        "num_peliculas_actor":    datos.get("num_peliculas_actor")       if rol == "actor"    else None,
        "peliculas_actuado":      datos.get("peliculas_actuado", [])    if rol == "actor"    else None,
        "match_score":           round(score, 2),
    }
    with conn.cursor() as cur:
        cur.execute(UPSERT_SQL, params)
    conn.commit()

# ─── Fuentes de datos ────────────────────────────────────────────────────────────

def get_directores_desde_db(conn):
    with conn.cursor() as cur:
        cur.execute("""
            SELECT DISTINCT director
            FROM public.icaa_fichas
            WHERE subvenciones_total_eur IS NOT NULL
              AND director IS NOT NULL
              AND TRIM(director) <> ''
            ORDER BY 1
        """)
        return [row[0].strip() for row in cur.fetchall()]

def get_actores_desde_db(conn):
    with conn.cursor() as cur:
        cur.execute("""
            SELECT DISTINCT elem->>'nombre' AS nombre
            FROM public.icaa_fichas,
            LATERAL jsonb_array_elements(ficha_artistica::jsonb) AS elem
            WHERE elem ? 'nombre'
              AND TRIM(elem->>'nombre') <> ''
            ORDER BY 1
        """)
        return [row[0].strip() for row in cur.fetchall() if row[0]]

# ─── Proceso ─────────────────────────────────────────────────────────────────────

DPTO_POR_TIPO = {
    "director": "Directing",
    "actor":    "Acting",
}

def mostrar_resumen(nombre_icaa, datos, score, tipo):
    sep = "─" * 64
    print(f"\n{sep}")
    print(f"  {'🎬' if tipo == 'director' else '🎭'}  {nombre_icaa}  [{tipo}]")
    print(sep)
    print(f"  TMDB ID:      {datos.get('tmdb_id')}  |  IMDB: {datos.get('imdb_id')}")
    print(f"  Nombre TMDB:  {datos.get('nombre_tmdb')}")
    if datos.get("tambien_conocido_como"):
        print(f"  Alias:        {', '.join(datos['tambien_conocido_como'][:3])}")
    print(f"  Nacimiento:   {datos.get('fecha_nacimiento') or '—'}  |  {datos.get('lugar_nacimiento') or '—'}")
    if datos.get("fecha_fallecimiento"):
        print(f"  Fallecimiento:{datos['fecha_fallecimiento']}")
    print(f"  Popularidad:  {datos.get('popularidad')}  |  Dpto: {datos.get('departamento')}")
    print(f"  Foto:         {datos.get('foto_url') or '❌ Sin foto'}")
    print(f"  Fotos total:  {len(datos.get('todas_las_fotos', []))}")
    if datos.get("biografia"):
        print(f"  Bio:          {datos['biografia'][:100]}…")
    if tipo == "director":
        print(f"  Dirigidas:    {datos.get('num_peliculas_director', 0)} películas")
        if datos.get("peliculas_dirigidas"):
            print(f"  Filmografía:  {', '.join(datos['peliculas_dirigidas'][:3])}")
    else:
        print(f"  Actuado en:   {datos.get('num_peliculas_actor', 0)} películas")
        if datos.get("peliculas_actuado"):
            print(f"  Destacadas:   {', '.join(datos['peliculas_actuado'][:3])}")
    print(f"  Match score:  {score:.2f}")

def procesar(personas, conn, dry_run, skip_existing, tipo):
    dpto = DPTO_POR_TIPO[tipo]
    ya_importados = get_ya_importados(conn, tipo) if skip_existing else set()
    stats = {"ok": 0, "no_match": 0, "saltado": 0, "sin_foto": 0, "error": 0}

    for i, nombre_icaa in enumerate(personas, 1):
        prefix = f"[{i:4}/{len(personas)}]"

        if skip_existing and nombre_icaa in ya_importados:
            log.info(f"{prefix} ⏭️  Ya importado como {tipo}: {nombre_icaa}")
            stats["saltado"] += 1
            continue

        log.info(f"{prefix} 🔍 {nombre_icaa}")

        try:
            tmdb_id, score, candidato = buscar_persona(nombre_icaa, dpto_preferido=dpto)
        except Exception as e:
            log.error(f"{prefix} ❌ Error búsqueda: {e}")
            stats["error"] += 1
            continue

        if not tmdb_id:
            log.warning(f"{prefix} ❌ Sin match")
            stats["no_match"] += 1
            continue

        nombre_enc = candidato.get("name", "?") if candidato else "?"
        log.info(f"          ✅ '{nombre_enc}' (score={score:.2f}, id={tmdb_id})")

        if score < 0.4:
            log.warning(f"          ⚠️  Score muy bajo ({score:.2f}) — descartado")
            stats["no_match"] += 1
            continue

        try:
            detalle = obtener_detalle_persona(tmdb_id)
            datos   = extraer_datos_persona(detalle, tipo=tipo)
        except Exception as e:
            log.error(f"{prefix} ❌ Error detalle: {e}")
            stats["error"] += 1
            continue

        if not datos.get("foto_url"):
            stats["sin_foto"] += 1

        mostrar_resumen(nombre_icaa, datos, score, tipo)

        if not dry_run and conn:
            try:
                guardar_persona(conn, nombre_icaa, datos, score, rol=tipo)
                log.info(f"          💾 Guardado.")
                stats["ok"] += 1
            except Exception as e:
                log.error(f"          ❌ Error guardando: {e}")
                conn.rollback()
                stats["error"] += 1
        else:
            stats["ok"] += 1

        time.sleep(0.35)

    return stats

# ─── Main ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Importa directores o actores de ICAA → TMDB (tabla tmdb_gente)"
    )
    parser.add_argument("--tipo",           choices=["director", "actor"], default="director",
                        help="Tipo de personas a importar (default: director)")
    parser.add_argument("--limit",          type=int, default=None,
                        help="Procesar solo los N primeros")
    parser.add_argument("--dry-run",        action="store_true",
                        help="Muestra resultados pero NO escribe en BD")
    parser.add_argument("--skip-existing",  action="store_true",
                        help="Salta los que ya están importados con ese rol")
    parser.add_argument("--nombre",         type=str, default=None,
                        help="Procesar una persona concreta por nombre")
    args = parser.parse_args()

    conn = get_db()
    crear_tabla_y_migrar(conn)

    if args.nombre:
        personas = [args.nombre]
        log.info(f"=== Modo individual: '{args.nombre}' [{args.tipo}] ===")
    elif args.tipo == "director":
        log.info("=== Cargando directores desde icaa_fichas… ===")
        personas = get_directores_desde_db(conn)
        log.info(f"  {len(personas)} directores únicos con subvenciones.")
    else:
        log.info("=== Cargando actores desde icaa_fichas.ficha_artistica… ===")
        personas = get_actores_desde_db(conn)
        log.info(f"  {len(personas)} actores únicos encontrados.")

    if args.limit:
        personas = personas[:args.limit]
        log.info(f"  Limitando a {args.limit} (--limit)")

    if args.dry_run:
        log.info("  ⚠️  DRY-RUN: no se escribirá nada en la BD.\n")

    stats = procesar(
        personas,
        conn=conn,
        dry_run=args.dry_run,
        skip_existing=args.skip_existing,
        tipo=args.tipo,
    )

    conn.close()

    total = sum(v for k, v in stats.items() if k != "sin_foto")
    icono = "🎬" if args.tipo == "director" else "🎭"
    print(f"\n{'═'*64}")
    print(f"  RESUMEN — {icono} {args.tipo.upper()}S")
    print(f"{'═'*64}")
    print(f"  ✅ Importados con éxito:   {stats['ok']}")
    print(f"  ❌ Sin match en TMDB:      {stats['no_match']}")
    print(f"  ⏭️  Saltados (ya existían): {stats['saltado']}")
    print(f"  📷  Sin foto:              {stats['sin_foto']} (de los encontrados)")
    print(f"  💥  Errores:               {stats['error']}")
    print(f"  Total procesados:          {total}")
    if args.dry_run:
        print(f"\n  ℹ️  DRY-RUN activo — ejecuta sin --dry-run para guardar en BD.")
    print(f"{'═'*64}\n")
