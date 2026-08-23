-- Rebuild the gold layer used by /historico-taquilla.
--
-- The 2018-2023 foreign anuario PDFs do not expose the country of each film.
-- They are foreign-film tables, so keep the explicit sentinel "No Spain" in
-- silver and classify those rows as foreign in gold.
--
-- For the historical top, keep one row per film using the highest spectators
-- value available. If several anuarios report the same value, prefer the most
-- recent anuario. This handles foreign re-release rows, where a later anuario
-- can contain a smaller annual re-release figure than the previous cumulative
-- historical figure.

UPDATE anuarios_silver
SET pais = 'No Spain'
WHERE source_table = 'anuarios_extranjeras_18_23_raw'
  AND pais IS NULL;

DROP TABLE IF EXISTS anuarios_gold;

CREATE TABLE anuarios_gold AS
WITH base AS (
    SELECT
        s.*,
        CASE
            WHEN s.source_table = 'anuarios_extranjeras_18_23_raw'
              OR UPPER(COALESCE(s.pais, '')) = 'NO SPAIN'
                THEN 'extranjera'
            WHEN s.source_table = 'anual_esp'
              OR UPPER(COALESCE(s.pais, '')) = 'ESPAÑA'
              OR UPPER(COALESCE(s.pais, '')) = 'ESPANA'
                THEN 'espanola'
            ELSE 'extranjera'
        END AS nacionalidad_grupo,
        regexp_replace(
            regexp_replace(
                public.norm_movie_title(COALESCE(s.titulo, '')),
                '^(.+),\s*(el|la|los|las|un|una|unos|unas)$',
                '\2 \1'
            ),
            '[^a-z0-9]+',
            ' ',
            'g'
        ) AS titulo_gold_key
    FROM anuarios_silver s
    WHERE COALESCE(s.titulo, '') <> ''
),
ranked AS (
    SELECT
        base.*,
        'title:' || trim(titulo_gold_key) || '|nat:' || nacionalidad_grupo AS pelicula_key,
        row_number() OVER (
            PARTITION BY trim(titulo_gold_key), nacionalidad_grupo
            ORDER BY espectadores DESC NULLS LAST,
                     COALESCE(recaudacion_desde_estreno, recaudacion) DESC NULLS LAST,
                     anio_anuario DESC NULLS LAST,
                     COALESCE(fecha_estreno, fecha_autorizacion) DESC NULLS LAST,
                     id DESC
        ) AS rn
    FROM base
    WHERE trim(titulo_gold_key) <> ''
)
SELECT
    id AS silver_id,
    source_table,
    source_row_id,
    pelicula_key,
    anio_anuario,
    titulo,
    titulo_normalizado,
    fecha_estreno,
    fecha_autorizacion,
    distribuidora,
    pais,
    nacionalidad_grupo,
    espectadores,
    recaudacion,
    recaudacion_desde_estreno,
    espectadores AS espectadores_historico,
    COALESCE(recaudacion_desde_estreno, recaudacion) AS recaudacion_historica,
    expediente_icaa,
    tmdb_id,
    match_icaa_source,
    match_tmdb_source,
    now() AS created_at
FROM ranked
WHERE rn = 1;

CREATE INDEX idx_anuarios_gold_nat_espectadores
    ON anuarios_gold (nacionalidad_grupo, espectadores_historico DESC NULLS LAST);
CREATE INDEX idx_anuarios_gold_key ON anuarios_gold (pelicula_key);
CREATE INDEX idx_anuarios_gold_anio ON anuarios_gold (anio_anuario);
CREATE INDEX idx_anuarios_gold_pais ON anuarios_gold (pais);
