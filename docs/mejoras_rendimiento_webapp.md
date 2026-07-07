# Mejoras de rendimiento pendientes — webapp

Identificadas en auditoría de código (mayo 2026). Tres problemas independientes, todos en `webapp/app.py`.

---

## 1. DDL ejecutado en cada request (`ensure_matching_schema`)

**Archivo:** `webapp/app.py` líneas 1158–1185, llamado desde L.1266 y L.1707

**Problema:**
`ensure_matching_schema()` se llama en cada GET a `/subvenciones-historico` y `/admin/matching` (y en cada POST de admin). Ejecuta 5 sentencias DDL en 5 conexiones separadas:

```sql
ALTER TABLE icaa_fichas ADD COLUMN IF NOT EXISTS titulo_anual_esp TEXT;
CREATE INDEX IF NOT EXISTS icaa_titulo_anual_esp_idx ON icaa_fichas (titulo_anual_esp);
CREATE TABLE IF NOT EXISTS subvenciones_icaa_matches (...);
ALTER TABLE subvenciones_icaa_matches DROP CONSTRAINT IF EXISTS ...;
CREATE INDEX IF NOT EXISTS subvenciones_icaa_matches_expediente_idx ON ...;
```

`ALTER TABLE` adquiere `AccessExclusiveLock` sobre `icaa_fichas` aunque sea un no-op. Cualquier visita a `/subvenciones-historico` (página pública) bloquea las lecturas de fichas de película concurrentes.

**Fix propuesto:** bandera de módulo — ejecutar exactamente una vez al arrancar:

```python
_schema_ready = False

def ensure_matching_schema():
    global _schema_ready
    if _schema_ready:
        return
    # los 5 DDL existentes sin cambios...
    _schema_ready = True
```

**Alternativa más robusta:** convertir los DDL en una migración SQL aplicada una sola vez fuera del proceso web (ej. en `run_update.sh` o al hacer deploy).

---

## 2. Sin connection pooling — nueva conexión TCP por query

**Archivo:** `webapp/app.py` líneas 27–51 (`get_db`, `query`, `execute`)

**Problema:**
Cada llamada a `query()` o `execute()` abre una conexión TCP a PostgreSQL, se autentica, ejecuta la query, y cierra la conexión. La ruta `index()` hace ~12 llamadas secuenciales = ~12 conexiones abiertas/cerradas por page load.

Con 10 usuarios concurrentes en `index()` → ~120 conexiones simultáneas → supera `max_connections=100` de PostgreSQL por defecto → `OperationalError: FATAL: remaining connection slots are reserved`.

**Fix propuesto:** `ThreadedConnectionPool` de psycopg2:

```python
from psycopg2 import pool as pg_pool

_pool: pg_pool.ThreadedConnectionPool | None = None

def _get_pool() -> pg_pool.ThreadedConnectionPool:
    global _pool
    if _pool is None:
        _pool = pg_pool.ThreadedConnectionPool(
            minconn=2, maxconn=10,
            dsn=os.environ['DATABASE_URL']
        )
    return _pool

def query(sql, params=None):
    pool = _get_pool()
    conn = pool.getconn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            return [dict(row) for row in cur.fetchall()]
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)

def execute(sql, params=None):
    pool = _get_pool()
    conn = pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)
```

`minconn=2, maxconn=10` es un punto de partida razonable para este tráfico. Ajustar según carga real.

**Nota:** el `conn.rollback()` en el `except` es necesario — sin él, una conexión que falla queda en estado de transacción abortada y no puede reutilizarse.

---

## 3. 9 queries pesadas en cada visita a `/calculadora`

**Archivo:** `webapp/app.py` líneas 631–805 (`get_benchmarks`), llamado en L.812

**Problema:**
`get_benchmarks()` se llama incondicionalmente al cargar `/calculadora`, aunque el usuario no haya buscado nada. Lanza 9 GROUP BY sobre `icaa_fichas` incluyendo dos que hacen `jsonb_array_elements(ficha_artistica)` (unnest del catálogo completo) + `LEFT JOIN tmdb_gente`:

```
global_avg       → 1 query
top_directores   → GROUP BY + JOIN tmdb_gente
bottom_directores→ GROUP BY + JOIN tmdb_gente
top_generos      → GROUP BY
bottom_generos   → GROUP BY
top_actores      → jsonb_array_elements × toda la tabla + JOIN tmdb_gente
bottom_actores   → jsonb_array_elements × toda la tabla + JOIN tmdb_gente
top_peliculas    → ORDER BY LIMIT 50
bottom_peliculas → ORDER BY LIMIT 50
```

Los datos cambian semanalmente (pipeline cron). Recomputar en cada visita es innecesario.

**Fix propuesto:** caché en memoria con TTL de 1 hora:

```python
import time

_benchmarks_cache: dict | None = None
_benchmarks_ts: float = 0.0
_BENCHMARKS_TTL = 3600.0

def get_benchmarks() -> dict:
    global _benchmarks_cache, _benchmarks_ts
    if _benchmarks_cache is not None and (time.monotonic() - _benchmarks_ts) < _BENCHMARKS_TTL:
        return _benchmarks_cache
    result = _compute_benchmarks()
    _benchmarks_cache = result
    _benchmarks_ts = time.monotonic()
    return result

def _compute_benchmarks() -> dict:
    # renombrar el cuerpo actual de get_benchmarks() aquí, sin cambios
    ...
```

**Alternativa si el tráfico crece:** materializar los benchmarks en una tabla PostgreSQL (`benchmarks_cache`) y refrescarla en el pipeline semanal. Elimina completamente las 9 queries del path de la web.

---

## Impacto estimado

| Mejora | Latencia por request | Conexiones PG | Riesgo |
|--------|---------------------|---------------|--------|
| Fix DDL (1) | −30–50 ms (5 DDL × ~8 ms) | −5 conexiones/req admin | Bajo |
| Connection pool (2) | −50–150 ms (11 handshakes × ~10 ms) | Estable, máx. 10 | Bajo |
| Cache benchmarks (3) | −200–800 ms (9 queries pesadas) | −9 conexiones/req calculadora | Bajo |

Los tres fixes son independientes y pueden aplicarse en cualquier orden.
