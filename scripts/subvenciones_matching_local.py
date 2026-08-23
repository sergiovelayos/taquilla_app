#!/usr/bin/env python3
"""Matching local de subvenciones contra el catalogo ICAA descargado.

No realiza consultas externas. En modo ``--dry-run`` solo analiza. En modo
``--apply`` prepara el esquema, genera candidatos locales y aprueba unicamente
los titulos normalizados que tienen un solo expediente ICAA posible.
"""

from __future__ import annotations

import argparse
import os
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import date
from difflib import SequenceMatcher

import psycopg2
from dotenv import load_dotenv


ARTICLES = "el|la|los|las|un|una|unos|unas"


def canonical_title(value: str) -> str:
    """Normalizacion conservadora: nunca corta un titulo por una coma."""
    title = re.sub(r"\([^)]*\)", " ", value or "")
    match = re.match(rf"^(.+),\s*({ARTICLES})$", title.strip(), re.IGNORECASE)
    if match:
        title = f"{match.group(2)} {match.group(1)}"
    title = unicodedata.normalize("NFKD", title)
    title = title.encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", title)).strip()


def plausible_year(production_year, aid_year, aid_types) -> bool:
    """Filtro conservador para evitar homonimos de decadas distintas."""
    if production_year is None or aid_year is None:
        return False
    if aid_types == {"amortizacion"}:
        return aid_year - 6 <= production_year <= aid_year
    if aid_types:
        return aid_year - 2 <= production_year <= aid_year + 6
    return aid_year - 6 <= production_year <= aid_year + 6


SCHEMA_SQL = r"""
CREATE EXTENSION IF NOT EXISTS unaccent;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE OR REPLACE FUNCTION public.norm_movie_title(value text)
RETURNS text
LANGUAGE sql
IMMUTABLE
AS $function$
    WITH cleaned AS (
        SELECT trim(regexp_replace(coalesce(value, ''), '\([^)]*\)', ' ', 'g')) AS title
    ), moved AS (
        SELECT CASE
            WHEN title ~* '^(.+),\s*(el|la|los|las|un|una|unos|unas)$'
                THEN regexp_replace(title, '^(.+),\s*(el|la|los|las|un|una|unos|unas)$', '\2 \1', 'i')
            ELSE title
        END AS title
        FROM cleaned
    )
    SELECT trim(regexp_replace(regexp_replace(unaccent(lower(title)), '[^a-z0-9]+', ' ', 'g'), '\s+', ' ', 'g'))
    FROM moved;
$function$;

ALTER TABLE subvenciones ADD COLUMN IF NOT EXISTS id BIGSERIAL;
CREATE UNIQUE INDEX IF NOT EXISTS subvenciones_id_unique_idx ON subvenciones (id);

ALTER TABLE subvenciones_raw
    ADD COLUMN IF NOT EXISTS empresa_beneficiaria TEXT,
    ADD COLUMN IF NOT EXISTS nif_beneficiario TEXT,
    ADD COLUMN IF NOT EXISTS expediente_ayuda TEXT,
    ADD COLUMN IF NOT EXISTS titulo_proyecto_original TEXT,
    ADD COLUMN IF NOT EXISTS fuente_url TEXT;

CREATE TABLE IF NOT EXISTS subvenciones_icaa_matches_detalle (
    subvencion_id    BIGINT PRIMARY KEY REFERENCES subvenciones(id) ON DELETE CASCADE,
    expediente_icaa TEXT,
    estado           TEXT NOT NULL DEFAULT 'review',
    confianza        NUMERIC(5,4),
    metodo           TEXT,
    notas            TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT subvenciones_match_estado_chk
        CHECK (estado IN ('matched', 'review', 'pending_ficha', 'sin_ficha')),
    CONSTRAINT subvenciones_match_expediente_chk
        CHECK ((estado = 'matched' AND expediente_icaa IS NOT NULL) OR estado <> 'matched')
);
CREATE INDEX IF NOT EXISTS subvenciones_match_detalle_expediente_idx
    ON subvenciones_icaa_matches_detalle (expediente_icaa);

CREATE TABLE IF NOT EXISTS subvenciones_icaa_candidates (
    subvencion_id     BIGINT NOT NULL REFERENCES subvenciones(id) ON DELETE CASCADE,
    expediente_icaa  TEXT NOT NULL,
    titulo_icaa      TEXT,
    anio_produccion  INTEGER,
    fuente_catalogo  TEXT,
    metodo           TEXT NOT NULL,
    similitud_titulo NUMERIC(6,5) NOT NULL,
    rank_candidato   INTEGER,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (subvencion_id, expediente_icaa)
);

CREATE INDEX IF NOT EXISTS icaa_fichas_titulo_norm_idx
    ON icaa_fichas (public.norm_movie_title(titulo));
CREATE INDEX IF NOT EXISTS scrape_icaa_titulo_norm_idx
    ON scrape_icaa (public.norm_movie_title(titulo));
CREATE INDEX IF NOT EXISTS icaa_fichas_titulo_trgm_idx
    ON icaa_fichas USING gin (public.norm_movie_title(titulo) gin_trgm_ops);
CREATE INDEX IF NOT EXISTS scrape_icaa_titulo_trgm_idx
    ON scrape_icaa USING gin (public.norm_movie_title(titulo) gin_trgm_ops);

CREATE MATERIALIZED VIEW IF NOT EXISTS icaa_catalogo_cache AS
WITH catalogo AS (
    SELECT f.*, 'icaa_fichas'::text AS fuente_catalogo,
           ((f.director IS NOT NULL)::int + (f.sinopsis IS NOT NULL)::int +
            (f.anio_produccion IS NOT NULL)::int + (f.fecha_estreno IS NOT NULL)::int +
            (f.espectadores IS NOT NULL)::int + (f.subvenciones_total_eur IS NOT NULL)::int) AS completitud
    FROM icaa_fichas f
    UNION ALL
    SELECT s.*, NULL::text AS titulo_anual_esp, 'scrape_icaa'::text AS fuente_catalogo,
           ((s.director IS NOT NULL)::int + (s.sinopsis IS NOT NULL)::int +
            (s.anio_produccion IS NOT NULL)::int + (s.fecha_estreno IS NOT NULL)::int +
            (s.espectadores IS NOT NULL)::int + (s.subvenciones_total_eur IS NOT NULL)::int) AS completitud
    FROM scrape_icaa s
)
SELECT DISTINCT ON (expediente_icaa) *
FROM catalogo
ORDER BY expediente_icaa, completitud DESC,
         CASE fuente_catalogo WHEN 'icaa_fichas' THEN 0 ELSE 1 END;

CREATE UNIQUE INDEX IF NOT EXISTS icaa_catalogo_cache_expediente_idx
    ON icaa_catalogo_cache (expediente_icaa);
CREATE INDEX IF NOT EXISTS icaa_catalogo_cache_titulo_norm_idx
    ON icaa_catalogo_cache (public.norm_movie_title(titulo));
CREATE INDEX IF NOT EXISTS icaa_catalogo_cache_subvencion_idx
    ON icaa_catalogo_cache (subvenciones_total_eur) WHERE subvenciones_total_eur > 0;

CREATE OR REPLACE VIEW icaa_catalogo AS
SELECT * FROM icaa_catalogo_cache;

CREATE OR REPLACE VIEW subvenciones_resueltas AS
SELECT s.*,
       CASE WHEN d.subvencion_id IS NOT NULL THEN d.expediente_icaa
            ELSE COALESCE(l.expediente_icaa, s.expediente_icaa) END AS expediente_resuelto,
       CASE
           WHEN d.subvencion_id IS NOT NULL THEN d.estado
           WHEN COALESCE(l.expediente_icaa, s.expediente_icaa) IS NOT NULL THEN 'matched'
           WHEN s.anio_ayuda >= EXTRACT(YEAR FROM CURRENT_DATE)::int - 1 THEN 'pending_ficha'
           ELSE 'review'
       END AS estado_matching,
       COALESCE(d.confianza,
                CASE WHEN COALESCE(l.expediente_icaa, s.expediente_icaa) IS NOT NULL THEN 1.0 END) AS confianza_matching,
       COALESCE(d.metodo,
                CASE WHEN l.expediente_icaa IS NOT NULL THEN 'legacy_manual'
                     WHEN s.expediente_icaa IS NOT NULL THEN 'directo' END) AS metodo_matching
FROM subvenciones s
LEFT JOIN subvenciones_icaa_matches_detalle d ON d.subvencion_id = s.id
LEFT JOIN subvenciones_icaa_matches l ON l.titulo_subvencion = s.titulo;

CREATE OR REPLACE VIEW peliculas_calculadora AS
WITH ayudas AS (
    SELECT expediente_resuelto AS expediente_icaa,
           SUM(importe_ayuda) AS subvenciones_oficiales_eur,
           COUNT(*) AS numero_ayudas,
           MIN(anio_ayuda) AS primera_ayuda,
           MAX(anio_ayuda) AS ultima_ayuda
    FROM subvenciones_resueltas
    WHERE estado_matching = 'matched' AND expediente_resuelto IS NOT NULL
    GROUP BY expediente_resuelto
)
SELECT c.expediente_icaa, c.titulo, c.director, c.calificacion,
       c.anio_produccion, c.fecha_estreno, c.duracion_min, c.tipo, c.genero,
       c.nacionalidad, c.recaudacion_eur, c.espectadores,
       COALESCE(a.subvenciones_oficiales_eur, c.subvenciones_total_eur) AS subvenciones_total_eur,
       c.subvenciones_total_eur AS subvenciones_icaa_eur,
       a.subvenciones_oficiales_eur, a.numero_ayudas, a.primera_ayuda, a.ultima_ayuda,
       CASE WHEN a.subvenciones_oficiales_eur IS NOT NULL THEN 'resoluciones_oficiales'
            WHEN c.subvenciones_total_eur IS NOT NULL THEN 'ficha_icaa' END AS fuente_subvenciones,
       c.sinopsis, c.etiquetas, c.ficha_artistica, c.ficha_tecnica,
       c.empresas_productoras, c.distribuidoras, c.subvenciones,
       c.fecha_inicio_rodaje, c.fecha_fin_rodaje, c.lugares_rodaje,
       c.premios, c.festivales, c.created_at, c.updated_at,
       c.titulo_anual_esp, c.fuente_catalogo
FROM icaa_catalogo c
LEFT JOIN ayudas a ON a.expediente_icaa = c.expediente_icaa;
"""


def connect():
    load_dotenv()
    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        raise SystemExit("DATABASE_URL no esta definida")
    return psycopg2.connect(dsn)


def _trigrams(value: str):
    padded = f"  {value}  "
    return {padded[index:index + 3] for index in range(max(0, len(padded) - 2))}


def load_analysis(cur, include_fuzzy=False):
    cur.execute("""
        SELECT expediente_icaa, titulo, anio_produccion, 'icaa_fichas'
        FROM icaa_fichas
        UNION ALL
        SELECT s.expediente_icaa, s.titulo, s.anio_produccion, 'scrape_icaa'
        FROM scrape_icaa s
        WHERE NOT EXISTS (
            SELECT 1 FROM icaa_fichas f WHERE f.expediente_icaa = s.expediente_icaa
        )
    """)
    catalog = defaultdict(list)
    for expediente, titulo, anio, fuente in cur.fetchall():
        norm = canonical_title(titulo)
        if norm:
            catalog[norm].append((str(expediente), titulo, anio, fuente))

    cur.execute("SELECT titulo, anio, tipo_ayuda FROM subvenciones_raw")
    aid_type_map = defaultdict(set)
    for titulo, anio, aid_type in cur.fetchall():
        if aid_type:
            aid_type_map[(canonical_title(titulo), anio)].add(aid_type.lower())

    cur.execute("""
        SELECT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'subvenciones' AND column_name = 'id'
        )
    """)
    id_expr = "s.id" if cur.fetchone()[0] else "NULL::bigint"
    cur.execute("SELECT to_regclass('public.subvenciones_icaa_matches_detalle') IS NOT NULL")
    has_detail = cur.fetchone()[0] and id_expr == "s.id"
    assigned_expr = (
        "COALESCE(d.expediente_icaa, m.expediente_icaa, s.expediente_icaa)"
        if has_detail else "COALESCE(m.expediente_icaa, s.expediente_icaa)"
    )
    detail_join = (
        "LEFT JOIN subvenciones_icaa_matches_detalle d "
        "ON d.subvencion_id = s.id AND d.estado = 'matched'"
        if has_detail else ""
    )
    cur.execute(f"""
        SELECT {id_expr}, s.titulo, s.anio_ayuda,
               {assigned_expr} AS asignado,
               {('d.metodo' if has_detail else 'NULL::text')} AS metodo_asignado
        FROM subvenciones s
        LEFT JOIN subvenciones_icaa_matches m ON m.titulo_subvencion = s.titulo
        {detail_join}
        ORDER BY s.anio_ayuda, s.titulo
    """)
    rows = cur.fetchall()
    result = []
    for sid, titulo, anio, assigned, assigned_method in rows:
        candidates = catalog.get(canonical_title(titulo), [])
        unique_ids = {candidate[0] for candidate in candidates}
        aid_types = aid_type_map.get((canonical_title(titulo), anio), set())
        unique = len(unique_ids) == 1
        safe_unique = unique and plausible_year(candidates[0][2], anio, aid_types)
        result.append({
            "id": sid,
            "titulo": titulo,
            "anio": anio,
            "assigned": str(assigned) if assigned else None,
            "assigned_method": assigned_method,
            "aid_types": aid_types,
            "candidates": candidates,
            "unique": unique,
            "safe_unique": safe_unique,
        })
    if include_fuzzy:
        gram_index = defaultdict(list)
        for norm in catalog:
            for gram in _trigrams(norm):
                gram_index[gram].append(norm)
        for row in result:
            row["fuzzy"] = []
            if row["assigned"] or row["candidates"]:
                continue
            query_norm = canonical_title(row["titulo"])
            shared = Counter()
            for gram in _trigrams(query_norm):
                # Los trigramas demasiado comunes aportan poco y disparan el coste.
                norms = gram_index.get(gram, ())
                if len(norms) <= 3000:
                    shared.update(norms)
            scored = []
            for candidate_norm, _overlap in shared.most_common(100):
                score = SequenceMatcher(None, query_norm, candidate_norm).ratio()
                if score >= 0.55:
                    scored.append((score, candidate_norm))
            scored.sort(reverse=True)
            rank = 0
            seen_ids = set()
            for score, candidate_norm in scored:
                for expediente, titulo, anio, fuente in catalog[candidate_norm]:
                    if expediente in seen_ids:
                        continue
                    seen_ids.add(expediente)
                    rank += 1
                    row["fuzzy"].append((expediente, titulo, anio, fuente, score, rank))
                    if rank >= 5:
                        break
                if rank >= 5:
                    break
    return result


def print_summary(rows):
    existing = sum(bool(row["assigned"]) for row in rows)
    unique_new = sum(not row["assigned"] and row["safe_unique"] for row in rows)
    year_conflicts = sum(row["unique"] and not row["safe_unique"] for row in rows)
    ambiguous = sum(not row["assigned"] and len(row["candidates"]) > 1 for row in rows)
    no_exact = sum(not row["assigned"] and not row["candidates"] for row in rows)
    print(f"Total subvenciones: {len(rows)}")
    print(f"Ya enlazadas: {existing}")
    print(f"Nuevos matches exactos unicos: {unique_new}")
    print(f"Coincidencias unicas descartadas por año: {year_conflicts}")
    print(f"Ambiguos para revision: {ambiguous}")
    print(f"Sin coincidencia exacta local: {no_exact}")
    by_year = defaultdict(lambda: [0, 0, 0, 0])
    for row in rows:
        bucket = by_year[row["anio"]]
        bucket[0] += 1
        bucket[1] += bool(row["assigned"])
        bucket[2] += not row["assigned"] and row["safe_unique"]
        bucket[3] += not row["assigned"] and not row["candidates"]
    print("\nAño  total  previos  nuevos-seguros  sin-exacto")
    for year in sorted(by_year):
        total, previous, new, missing = by_year[year]
        print(f"{year}  {total:5d}  {previous:7d}  {new:13d}  {missing:10d}")


def apply_matches(conn, rows):
    cur = conn.cursor()
    cur.execute(SCHEMA_SQL)
    conn.commit()
    cur = conn.cursor()
    cur.execute("REFRESH MATERIALIZED VIEW icaa_catalogo_cache")
    conn.commit()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO subvenciones_icaa_matches_detalle
            (subvencion_id, expediente_icaa, estado, confianza, metodo, notas)
        SELECT s.id, COALESCE(m.expediente_icaa, s.expediente_icaa),
               'matched', 1.0,
               CASE WHEN m.expediente_icaa IS NOT NULL THEN 'legacy_manual' ELSE 'directo' END,
               'Migrado desde el matching existente'
        FROM subvenciones s
        LEFT JOIN subvenciones_icaa_matches m ON m.titulo_subvencion = s.titulo
        WHERE COALESCE(m.expediente_icaa, s.expediente_icaa) IS NOT NULL
        ON CONFLICT (subvencion_id) DO NOTHING
    """)

    cur.execute("DELETE FROM subvenciones_icaa_candidates")
    exact_rows = []
    auto_rows = []
    status_rows = []
    review_rows = []
    current_year = date.today().year
    for row in rows:
        if row["id"] is None:
            continue
        target_year = (row["anio"] - 2 if row["aid_types"] == {"amortizacion"}
                       else row["anio"] + 1 if row["aid_types"] else row["anio"])
        ordered_candidates = sorted(
            row["candidates"],
            key=lambda candidate: (
                not plausible_year(candidate[2], row["anio"], row["aid_types"]),
                abs(candidate[2] - target_year) if candidate[2] is not None else 9999,
                candidate[0],
            ),
        )
        for rank, (expediente, titulo, anio, fuente) in enumerate(ordered_candidates, 1):
            exact_rows.append((row["id"], expediente, titulo, anio, fuente,
                               "exact_norm", 1.0, rank))
        if not row["assigned"] and row["safe_unique"]:
            expediente = row["candidates"][0][0]
            auto_rows.append((row["id"], expediente, "matched", 1.0, "exact_norm_unique",
                              "Coincidencia local unica por titulo normalizado"))
        elif not row["assigned"] and not row["candidates"]:
            estado = "pending_ficha" if row["anio"] >= current_year - 1 else "review"
            status_rows.append((row["id"], None, estado, None, "local_no_exact",
                                "Sin coincidencia exacta en el catalogo local"))
        elif row["unique"] and not row["safe_unique"] and (
                not row["assigned"] or row["assigned_method"] == "exact_norm_unique"):
            review_rows.append((row["id"], None, "review", None,
                                "exact_norm_year_conflict",
                                "Titulo unico, pero el año de produccion no es compatible con el de la ayuda"))

    if exact_rows:
        cur.executemany("""
            INSERT INTO subvenciones_icaa_candidates
                (subvencion_id, expediente_icaa, titulo_icaa, anio_produccion,
                 fuente_catalogo, metodo, similitud_titulo, rank_candidato)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (subvencion_id, expediente_icaa) DO UPDATE SET
                titulo_icaa=EXCLUDED.titulo_icaa,
                anio_produccion=EXCLUDED.anio_produccion,
                fuente_catalogo=EXCLUDED.fuente_catalogo,
                metodo=EXCLUDED.metodo,
                similitud_titulo=EXCLUDED.similitud_titulo,
                rank_candidato=EXCLUDED.rank_candidato,
                created_at=NOW()
        """, exact_rows)

    fuzzy_rows = []
    for row in rows:
        for expediente, titulo, anio, fuente, score, rank in row.get("fuzzy", []):
            fuzzy_rows.append((row["id"], expediente, titulo, anio, fuente,
                               "fuzzy_local", score, rank))
    if fuzzy_rows:
        cur.executemany("""
            INSERT INTO subvenciones_icaa_candidates
                (subvencion_id, expediente_icaa, titulo_icaa, anio_produccion,
                 fuente_catalogo, metodo, similitud_titulo, rank_candidato)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (subvencion_id, expediente_icaa) DO NOTHING
        """, fuzzy_rows)
    for payload in auto_rows + status_rows + review_rows:
        cur.execute("""
            INSERT INTO subvenciones_icaa_matches_detalle
                (subvencion_id, expediente_icaa, estado, confianza, metodo, notas)
            VALUES (%s,%s,%s,%s,%s,%s)
            ON CONFLICT (subvencion_id) DO UPDATE SET
                expediente_icaa=EXCLUDED.expediente_icaa,
                estado=EXCLUDED.estado,
                confianza=EXCLUDED.confianza,
                metodo=EXCLUDED.metodo,
                notas=EXCLUDED.notas,
                updated_at=NOW()
            WHERE COALESCE(subvenciones_icaa_matches_detalle.metodo, '') <> 'manual'
              AND subvenciones_icaa_matches_detalle.estado <> 'sin_ficha'
              AND (
                    subvenciones_icaa_matches_detalle.estado <> 'matched'
                    OR (subvenciones_icaa_matches_detalle.metodo = 'exact_norm_unique'
                        AND EXCLUDED.metodo = 'exact_norm_year_conflict')
              )
        """, payload)

    cur.execute("""
        INSERT INTO subvenciones_raw_icaa_matches
            (subvenciones_raw_id, expediente_icaa, sin_ficha, notas)
        SELECT r.id, sr.expediente_resuelto, FALSE,
               'Propagado desde subvenciones por titulo normalizado y año'
        FROM subvenciones_raw r
        JOIN subvenciones_resueltas sr
          ON public.norm_movie_title(sr.titulo) = public.norm_movie_title(r.titulo)
         AND sr.anio_ayuda = r.anio
         AND sr.estado_matching = 'matched'
        ON CONFLICT (subvenciones_raw_id) DO NOTHING
    """)

    conn.commit()
    print(f"Aplicados {len(auto_rows)} matches exactos unicos.")
    print(f"Marcados {len(status_rows)} casos sin coincidencia exacta para seguimiento.")
    print(f"Descartados {len(review_rows)} homonimos o candidatos sin año verificable.")
    print(f"Guardados {len(fuzzy_rows)} candidatos difusos locales para revision.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true", help="Analiza sin modificar la base de datos")
    group.add_argument("--apply", action="store_true", help="Prepara esquema y aplica matches locales seguros")
    args = parser.parse_args()

    conn = connect()
    try:
        if args.apply:
            with conn.cursor() as cur:
                cur.execute("ALTER TABLE subvenciones ADD COLUMN IF NOT EXISTS id BIGSERIAL")
            conn.commit()
        with conn.cursor() as cur:
            rows = load_analysis(cur, include_fuzzy=args.apply)
        print_summary(rows)
        if args.apply:
            apply_matches(conn, rows)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
