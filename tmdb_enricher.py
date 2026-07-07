"""
tmdb_enricher.py — Enriquece la BBDD de taquilla con metadatos de TMDB

Uso:
  python3 tmdb_enricher.py --limit 10          # Prueba: procesa las 10 primeras películas y guarda en BBDD
  python3 tmdb_enricher.py --limit 10 --dry-run # Prueba sin escribir en BBDD
  python3 tmdb_enricher.py                      # Procesa todas las películas (top25 + topespanol)
  python3 tmdb_enricher.py --skip-existing      # Solo procesa las que aún no están en la tabla tmdb

JOIN con comscore:
  SELECT t.*, m.*
  FROM top25 t
  LEFT JOIN tmdb m ON t.titulo = m.titulo AND t.distribuidora = m.distribuidora

  SELECT t.*, m.*
  FROM topespanol t
  LEFT JOIN tmdb m ON t.titulo = m.titulo AND t.distribuidora = m.distribuidora
"""

import os
import sys
import time
import logging
import argparse
import unicodedata
import re
from datetime import datetime

import requests
import psycopg2
from dotenv import load_dotenv

# ─── Config ────────────────────────────────────────────────────────────────────

load_dotenv()

TMDB_TOKEN = os.getenv("TMDB_TOKEN")
TMDB_BASE = "https://api.themoviedb.org/3"
TMDB_IMG  = "https://image.tmdb.org/t/p/w500"

DB_DSN = os.getenv("DATABASE_URL", "postgresql://localhost/taquilla_app")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]
)
log = logging.getLogger(__name__)

# ─── Overrides manuales ─────────────────────────────────────────────────────────
# Para títulos genéricos o ambiguos que el buscador automático no resuelve bien.
# Formato: ("titulo exacto", "distribuidora") -> tmdb_id  (None = no existe en TMDB)
# Busca el ID en: https://www.themoviedb.org/search?query=TITULO
TMDB_OVERRIDES = {
    ("REC", "Filmax"): None,  # TODO: añadir ID cuando se localice en TMDB
    ("Familia Beneton + 2, La", "Beta Fiction"): 1391325,
    ("Diablo viste de Prada 2, El", "Walt Disney"): 1314481,
}

# ─── TMDB client ────────────────────────────────────────────────────────────────

session = requests.Session()
session.headers.update({
    "Authorization": f"Bearer {TMDB_TOKEN}",
    "accept": "application/json"
})

def tmdb_get(path, params=None):
    """Llama a la API de TMDB con reintentos."""
    url = f"{TMDB_BASE}{path}"
    for attempt in range(3):
        try:
            r = session.get(url, params=params, timeout=10)
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:
            log.warning(f"  Intento {attempt+1}/3 fallido para {path}: {e}")
            time.sleep(2 ** attempt)
    return None

# ─── Normalización y matching ───────────────────────────────────────────────────

_SUFIJOS_BUSQUEDA = re.compile(
    r'\s*\(.*?\)'
    r'|\s+\d+[ºth°]?\s*(?:aniversario|anniversary)\b'
    r'|\s+\b4[kK]\b|\s+\b3[dD]\b|\s+\bHD\b'
    r'|\s+sing-?a-?long\b|\s+film\s+fest\b|\s+encore\b'
    r'|\s*[-:]\s*$',
    re.IGNORECASE
)

def limpiar_titulo_busqueda(titulo):
    """Elimina paréntesis y anotaciones técnicas/evento antes de buscar en TMDB."""
    if not titulo:
        return ""
    t, prev = titulo, None
    while t != prev:
        prev = t
        t = _SUFIJOS_BUSQUEDA.sub("", t).strip()
    return t


def normalizar(texto):
    """Minúsculas, sin acentos, sin artículos iniciales, sin puntuación ni anotaciones."""
    if not texto:
        return ""
    texto = limpiar_titulo_busqueda(texto)
    texto = unicodedata.normalize("NFD", texto)
    texto = "".join(c for c in texto if unicodedata.category(c) != "Mn")
    texto = texto.lower().strip()
    for art in ["el ", "la ", "los ", "las ", "the ", "a ", "un ", "una "]:
        if texto.startswith(art):
            texto = texto[len(art):]
            break
    texto = re.sub(r"[^\w\s]", "", texto)
    return re.sub(r"\s+", " ", texto).strip()

def similarity_score(s1, s2):
    n1, n2 = normalizar(s1), normalizar(s2)
    if n1 == n2:
        return 1.0
    if n1 in n2 or n2 in n1:
        return 0.8
    words1, words2 = set(n1.split()), set(n2.split())
    if words1 and words2:
        return len(words1 & words2) / max(len(words1), len(words2))
    return 0.0

def es_candidato_valido(candidato):
    """Descarta resultados sin popularidad ni votos (probable basura o duplicado)."""
    return not (candidato.get("popularity", 0) < 0.2 and candidato.get("vote_count", 0) < 5)

def buscar_pelicula(titulo, year=None, solo_espanol=False):
    """
    Devuelve (tmdb_id, score, candidato) o (None, 0, None).
    - Prueba el título tal cual y también la versión invertida ("Infiltrada, La" → "La Infiltrada")
    - Ordena candidatos por popularidad para priorizar los más conocidos
    - Con solo_espanol=True verifica que la película sea de producción española
    """
    titulo_base = limpiar_titulo_busqueda(titulo)
    queries = [titulo_base]
    if ", " in titulo_base:
        partes = titulo_base.split(", ", 1)
        queries.append(f"{partes[1]} {partes[0]}")
    # Fallback sin caracteres especiales (ej: "Familia Beneton + 2" → "Familia Beneton 2")
    titulo_limpio = re.sub(r"[^\w\s,]", " ", titulo_base).strip()
    titulo_limpio = re.sub(r"\s+", " ", titulo_limpio)
    if titulo_limpio != titulo_base and titulo_limpio not in queries:
        queries.append(titulo_limpio)
        if ", " in titulo_limpio:
            partes = titulo_limpio.split(", ", 1)
            queries.append(f"{partes[1]} {partes[0]}")

    best_score, best_id, best_candidato = 0, None, None

    for q in queries:
        params = {"query": q, "language": "es-ES", "region": "ES"}
        if year:
            params["year"] = year
        data = tmdb_get("/search/movie", params)

        # Reintentar sin año si no hay resultados
        if not data or not data.get("results"):
            params.pop("year", None)
            data = tmdb_get("/search/movie", params)

        if not data or not data.get("results"):
            continue

        candidatos = sorted(data["results"][:10], key=lambda x: x.get("popularity", 0), reverse=True)

        for candidato in candidatos:
            if not es_candidato_valido(candidato):
                continue

            score = max(
                similarity_score(titulo, candidato.get("title", "")),
                similarity_score(titulo, candidato.get("original_title", ""))
            )

            # Bonus por año cercano (±1 año por diferencias de estreno)
            release = candidato.get("release_date", "")
            if year and release:
                try:
                    if abs(int(release[:4]) - year) <= 1:
                        score = min(1.0, score + 0.1)
                except ValueError:
                    pass

            # Pequeño bonus de popularidad para desempatar
            score = min(1.0, score + min(candidato.get("popularity", 0) / 1000, 0.05))

            if score > best_score:
                best_score, best_id, best_candidato = score, candidato["id"], candidato

    # Para películas españolas: verificar idioma/país y descartar cortometrajes
    if best_id and solo_espanol:
        detalle = tmdb_get(f"/movie/{best_id}", params={"language": "es-ES"})
        if detalle:
            lang    = detalle.get("original_language", "")
            paises  = [p["iso_3166_1"] for p in detalle.get("production_countries", [])]
            runtime = detalle.get("runtime") or 0
            titulo_c = best_candidato.get("title", "")
            if lang != "es" and "ES" not in paises:
                log.warning(f"  ⚠️  '{titulo_c}' descartado — no es producción española (lang={lang}, países={paises})")
                return None, 0, None
            if 0 < runtime < 40:
                log.warning(f"  ⚠️  '{titulo_c}' descartado — cortometraje ({runtime} min)")
                return None, 0, None

    return best_id, best_score, best_candidato

# ─── Detalle y extracción ───────────────────────────────────────────────────────

def obtener_detalle_completo(tmdb_id):
    return tmdb_get(
        f"/movie/{tmdb_id}",
        params={"language": "es-ES", "append_to_response": "credits,keywords,videos"}
    )

def extraer_metadatos(detalle):
    if not detalle:
        return {}

    crew = detalle.get("credits", {}).get("crew", [])
    director = ", ".join(p["name"] for p in crew if p.get("job") == "Director")
    reparto   = [p["name"] for p in detalle.get("credits", {}).get("cast", [])[:5]]
    generos   = [g["name"] for g in detalle.get("genres", [])]
    paises    = [p["iso_3166_1"] for p in detalle.get("production_countries", [])]
    productoras = [c["name"] for c in detalle.get("production_companies", [])]
    keywords  = [k["name"] for k in detalle.get("keywords", {}).get("keywords", [])][:10]

    poster_path   = detalle.get("poster_path", "")
    backdrop_path = detalle.get("backdrop_path", "")
    trailer_url   = next(
        (f"https://www.youtube.com/watch?v={v['key']}"
         for v in detalle.get("videos", {}).get("results", [])
         if v.get("type") == "Trailer" and v.get("site") == "YouTube"),
        ""
    )

    return {
        "tmdb_id":                  detalle.get("id"),
        "titulo_tmdb":              detalle.get("title", ""),
        "titulo_original_tmdb":     detalle.get("original_title", ""),
        "tagline":                  detalle.get("tagline", ""),
        "sinopsis":                 detalle.get("overview", ""),
        "duracion_min":             detalle.get("runtime"),
        "fecha_estreno_tmdb":       detalle.get("release_date", ""),
        "generos":                  generos,
        "paises_produccion":        paises,
        "productoras":              productoras,
        "director":                 director,
        "reparto_principal":        reparto,
        "keywords":                 keywords,
        "puntuacion_tmdb":          detalle.get("vote_average"),
        "votos_tmdb":               detalle.get("vote_count"),
        "popularidad_tmdb":         detalle.get("popularity"),
        "presupuesto_usd":          detalle.get("budget") or None,
        "recaudacion_mundial_usd":  detalle.get("revenue") or None,
        "poster_url":               f"{TMDB_IMG}{poster_path}" if poster_path else "",
        "backdrop_url":             f"https://image.tmdb.org/t/p/w1280{backdrop_path}" if backdrop_path else "",
        "trailer_url":              trailer_url,
        "idioma_original":          detalle.get("original_language", ""),
        "estado":                   detalle.get("status", ""),
    }

# ─── Base de datos ──────────────────────────────────────────────────────────────

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS tmdb (
    -- Clave de relación con top25 y topespanol
    titulo                  TEXT NOT NULL,
    distribuidora           TEXT NOT NULL,

    -- Datos TMDB
    tmdb_id                 INTEGER,
    titulo_tmdb             TEXT,
    titulo_original_tmdb    TEXT,
    tagline                 TEXT,
    sinopsis                TEXT,
    duracion_min            INTEGER,
    fecha_estreno_tmdb      DATE,
    generos                 TEXT[],
    paises_produccion       TEXT[],
    productoras             TEXT[],
    director                TEXT,
    reparto_principal       TEXT[],
    keywords                TEXT[],
    puntuacion_tmdb         NUMERIC(4,2),
    votos_tmdb              INTEGER,
    popularidad_tmdb        NUMERIC(10,4),
    presupuesto_usd         BIGINT,
    recaudacion_mundial_usd BIGINT,
    poster_url              TEXT,
    backdrop_url            TEXT,
    trailer_url             TEXT,
    idioma_original         TEXT,
    estado                  TEXT,

    -- Metadatos del proceso
    match_score             NUMERIC(4,2),   -- confianza del match automático (0-1)
    verificado              BOOLEAN DEFAULT FALSE,  -- TRUE si se ha revisado manualmente
    fuentes                 TEXT[],         -- ['top25', 'topespanol'] según en qué tablas aparece
    created_at              TIMESTAMP DEFAULT NOW(),
    updated_at              TIMESTAMP DEFAULT NOW(),

    PRIMARY KEY (titulo, distribuidora)
);

-- Índices para acelerar los JOINs con comscore
CREATE INDEX IF NOT EXISTS tmdb_titulo_idx        ON tmdb (titulo);
CREATE INDEX IF NOT EXISTS tmdb_tmdb_id_idx       ON tmdb (tmdb_id);
CREATE INDEX IF NOT EXISTS tmdb_generos_idx       ON tmdb USING GIN (generos);
CREATE INDEX IF NOT EXISTS tmdb_paises_idx        ON tmdb USING GIN (paises_produccion);
"""

UPSERT_SQL = """
INSERT INTO tmdb (
    titulo, distribuidora, tmdb_id, titulo_tmdb, titulo_original_tmdb, tagline, sinopsis,
    duracion_min, fecha_estreno_tmdb, generos, paises_produccion, productoras, director,
    reparto_principal, keywords, puntuacion_tmdb, votos_tmdb, popularidad_tmdb,
    presupuesto_usd, recaudacion_mundial_usd, poster_url, backdrop_url, trailer_url,
    idioma_original, estado, match_score, fuentes, updated_at
) VALUES (
    %s, %s, %s, %s, %s, %s, %s,
    %s, %s, %s, %s, %s, %s,
    %s, %s, %s, %s, %s,
    %s, %s, %s, %s, %s,
    %s, %s, %s, %s, NOW()
)
ON CONFLICT (titulo, distribuidora) DO UPDATE SET
    tmdb_id                 = EXCLUDED.tmdb_id,
    titulo_tmdb             = EXCLUDED.titulo_tmdb,
    titulo_original_tmdb    = EXCLUDED.titulo_original_tmdb,
    tagline                 = EXCLUDED.tagline,
    sinopsis                = EXCLUDED.sinopsis,
    duracion_min            = EXCLUDED.duracion_min,
    fecha_estreno_tmdb      = EXCLUDED.fecha_estreno_tmdb,
    generos                 = EXCLUDED.generos,
    paises_produccion       = EXCLUDED.paises_produccion,
    productoras             = EXCLUDED.productoras,
    director                = EXCLUDED.director,
    reparto_principal       = EXCLUDED.reparto_principal,
    keywords                = EXCLUDED.keywords,
    puntuacion_tmdb         = EXCLUDED.puntuacion_tmdb,
    votos_tmdb              = EXCLUDED.votos_tmdb,
    popularidad_tmdb        = EXCLUDED.popularidad_tmdb,
    presupuesto_usd         = EXCLUDED.presupuesto_usd,
    recaudacion_mundial_usd = EXCLUDED.recaudacion_mundial_usd,
    poster_url              = EXCLUDED.poster_url,
    backdrop_url            = EXCLUDED.backdrop_url,
    trailer_url             = EXCLUDED.trailer_url,
    idioma_original         = EXCLUDED.idioma_original,
    estado                  = EXCLUDED.estado,
    match_score             = EXCLUDED.match_score,
    fuentes                 = EXCLUDED.fuentes,
    updated_at              = NOW();
"""

def get_db():
    return psycopg2.connect(DB_DSN)

def crear_tabla(conn):
    with conn.cursor() as cur:
        cur.execute(CREATE_TABLE_SQL)
    conn.commit()
    log.info("Tabla 'tmdb' e índices listos.")

def get_ya_procesados(conn):
    """Devuelve un set de (titulo, distribuidora) ya presentes en la tabla tmdb."""
    with conn.cursor() as cur:
        cur.execute("SELECT titulo, distribuidora FROM tmdb")
        return set(cur.fetchall())

def guardar_metadata(conn, titulo, distribuidora, meta, score, fuentes):
    fecha = None
    if meta.get("fecha_estreno_tmdb"):
        try:
            fecha = datetime.strptime(meta["fecha_estreno_tmdb"], "%Y-%m-%d").date()
        except ValueError:
            pass

    with conn.cursor() as cur:
        cur.execute(UPSERT_SQL, (
            titulo, distribuidora,
            meta.get("tmdb_id"), meta.get("titulo_tmdb"), meta.get("titulo_original_tmdb"),
            meta.get("tagline"), meta.get("sinopsis"),
            meta.get("duracion_min"), fecha,
            meta.get("generos", []), meta.get("paises_produccion", []), meta.get("productoras", []),
            meta.get("director"),
            meta.get("reparto_principal", []), meta.get("keywords", []),
            meta.get("puntuacion_tmdb"), meta.get("votos_tmdb"), meta.get("popularidad_tmdb"),
            meta.get("presupuesto_usd"), meta.get("recaudacion_mundial_usd"),
            meta.get("poster_url"), meta.get("backdrop_url"), meta.get("trailer_url"),
            meta.get("idioma_original"), meta.get("estado"),
            round(score, 2), fuentes
        ))
    conn.commit()

# ─── Carga de títulos desde BBDD ───────────────────────────────────────────────

def get_titulos_desde_db():
    """
    Une los títulos únicos de top25 y topespanol.
    Marca cada película con las fuentes en las que aparece y si es producción española.
    """
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute("""
            SELECT titulo, distribuidora,
                   MIN(fecha_inicio)::date AS primera_semana,
                   ARRAY_AGG(DISTINCT fuente) AS fuentes
            FROM (
                SELECT titulo, distribuidora, fecha_inicio, 'top25' AS fuente
                FROM top25
                UNION ALL
                SELECT titulo, distribuidora, fecha_inicio, 'topespanol' AS fuente
                FROM topespanol
            ) combinado
            GROUP BY titulo, distribuidora
            ORDER BY MIN(fecha_inicio)
        """)
        rows = [(t, d, f, fu) for t, d, f, fu in cur.fetchall() if t and d]
    conn.close()

    peliculas = []
    for titulo, distribuidora, primera_semana, fuentes in rows:
        year = primera_semana.year if primera_semana else None
        solo_espanol = "topespanol" in fuentes and "top25" not in fuentes
        peliculas.append({
            "titulo": titulo,
            "distribuidora": distribuidora,
            "year": year,
            "fuentes": fuentes,
            "solo_espanol": solo_espanol,
        })
    return peliculas

# ─── Procesamiento ──────────────────────────────────────────────────────────────

def mostrar_resumen(titulo, year, meta, score):
    print(f"\n{'='*62}")
    print(f"📽️  {titulo} ({year})")
    print(f"{'='*62}")
    print(f"  TMDB ID:       {meta.get('tmdb_id')}")
    print(f"  Título TMDB:   {meta.get('titulo_tmdb')}")
    print(f"  Orig. title:   {meta.get('titulo_original_tmdb')}")
    print(f"  Director:      {meta.get('director')}")
    print(f"  Reparto:       {', '.join(meta.get('reparto_principal', []))}")
    print(f"  Géneros:       {', '.join(meta.get('generos', []))}")
    print(f"  Duración:      {meta.get('duracion_min')} min")
    print(f"  Estreno TMDB:  {meta.get('fecha_estreno_tmdb')}")
    print(f"  Países:        {', '.join(meta.get('paises_produccion', []))}")
    print(f"  Productoras:   {', '.join(meta.get('productoras', []))}")
    print(f"  Puntuación:    {meta.get('puntuacion_tmdb')} ({meta.get('votos_tmdb')} votos)")
    if meta.get("presupuesto_usd"):
        print(f"  Presupuesto:   ${meta['presupuesto_usd']:,}")
    if meta.get("recaudacion_mundial_usd"):
        print(f"  Rec. mundial:  ${meta['recaudacion_mundial_usd']:,}")
    if meta.get("sinopsis"):
        print(f"  Sinopsis:      {meta['sinopsis'][:120]}...")
    print(f"  Poster:        {meta.get('poster_url')}")
    print(f"  Trailer:       {meta.get('trailer_url')}")
    if meta.get("keywords"):
        print(f"  Keywords:      {', '.join(meta['keywords'])}")
    print(f"  Match score:   {score:.2f}")

def procesar_lista(peliculas, dry_run=True, conn=None, skip_existing=False):
    ya_procesados = get_ya_procesados(conn) if (conn and skip_existing) else set()
    resultados = {"ok": 0, "no_match": 0, "saltado": 0, "error": 0}

    for i, peli in enumerate(peliculas, 1):
        titulo       = peli["titulo"]
        distribuidora = peli["distribuidora"]
        year         = peli.get("year")
        fuentes      = peli.get("fuentes", [])
        solo_espanol = peli.get("solo_espanol", False)

        prefix = f"[{i}/{len(peliculas)}]"

        # Saltar si ya está procesada
        if skip_existing and (titulo, distribuidora) in ya_procesados:
            log.info(f"{prefix} ⏭️  Ya existe: '{titulo}' — saltando")
            resultados["saltado"] += 1
            continue

        # Override manual
        if (titulo, distribuidora) in TMDB_OVERRIDES:
            override_id = TMDB_OVERRIDES[(titulo, distribuidora)]
            if override_id is None:
                log.info(f"{prefix} ⏭️  '{titulo}' sin ID en TMDB (override=None) — saltando")
                resultados["saltado"] += 1
                continue
            log.info(f"{prefix} 🔧 Override: '{titulo}' → tmdb_id={override_id}")
            tmdb_id, score, candidato = override_id, 1.0, {"title": titulo}
        else:
            log.info(f"{prefix} 🔍 '{titulo}' ({year}) {'[ES]' if solo_espanol else ''}")
            tmdb_id, score, candidato = buscar_pelicula(titulo, year, solo_espanol=solo_espanol)

        if not tmdb_id:
            log.warning(f"{prefix} ❌ Sin match para '{titulo}'")
            resultados["no_match"] += 1
            continue

        log.info(f"       ✅ '{candidato.get('title')}' (score={score:.2f}, id={tmdb_id})")

        detalle = obtener_detalle_completo(tmdb_id)
        meta = extraer_metadatos(detalle)

        mostrar_resumen(titulo, year, meta, score)

        if not dry_run and conn:
            try:
                guardar_metadata(conn, titulo, distribuidora, meta, score, fuentes)
                log.info(f"       💾 Guardado en BBDD.")
                resultados["ok"] += 1
            except Exception as e:
                log.error(f"       ❌ Error guardando '{titulo}': {e}")
                conn.rollback()
                resultados["error"] += 1
        else:
            resultados["ok"] += 1

        time.sleep(0.3)  # Rate limit: ~3 req/s, muy por debajo del límite de TMDB

    return resultados

# ─── Main ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if not TMDB_TOKEN:
        log.error("TMDB_TOKEN not found in environment. Please check your .env file.")
        sys.exit(1)
    parser = argparse.ArgumentParser(description="Enriquecedor TMDB para Taquilla España")
    parser.add_argument("--limit",          type=int, default=None,
                        help="Procesar solo las N primeras películas (útil para pruebas)")
    parser.add_argument("--dry-run",        action="store_true",
                        help="Muestra resultados pero no escribe en BBDD")
    parser.add_argument("--skip-existing",  action="store_true",
                        help="Salta las películas que ya están en la tabla tmdb")
    args = parser.parse_args()

    log.info("=== Cargando títulos desde la BBDD (top25 + topespanol) ===")
    peliculas = get_titulos_desde_db()
    log.info(f"  {len(peliculas)} películas únicas encontradas")

    if args.limit:
        peliculas = peliculas[:args.limit]
        log.info(f"  Limitando a {args.limit} películas (--limit)")

    conn = None
    if not args.dry_run:
        conn = get_db()
        crear_tabla(conn)

    resultados = procesar_lista(
        peliculas,
        dry_run=args.dry_run,
        conn=conn,
        skip_existing=args.skip_existing
    )

    if conn:
        conn.close()

    total = sum(resultados.values())
    print(f"\n{'='*62}")
    print(f"  ✅ Con match:   {resultados['ok']}")
    print(f"  ❌ Sin match:   {resultados['no_match']}")
    print(f"  ⏭️  Saltadas:    {resultados['saltado']}")
    print(f"  💥 Errores:     {resultados['error']}")
    print(f"  Total:          {total}")
    if args.dry_run:
        print(f"\nℹ️  Modo dry-run — nada guardado en BBDD.")
        print(f"   Para guardar, ejecuta sin --dry-run.")
