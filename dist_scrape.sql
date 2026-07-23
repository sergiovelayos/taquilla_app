-- Distribución por tramos de 100k de los IDs REALMENTE capturados (fichas guardadas)
SELECT
    (expediente_icaa::int / 100000) AS tramo_100k,
    COUNT(*)                        AS fichas,
    MIN(expediente_icaa::int)       AS id_min,
    MAX(expediente_icaa::int)       AS id_max
FROM scrape_icaa
WHERE expediente_icaa ~ '^[0-9]+$'
GROUP BY 1
ORDER BY 1;
