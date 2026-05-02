# Auditoría técnica — Taquilla España

**Fecha:** 30 abril 2026
**Alcance:** repositorio completo en `/Volumes/sergio_home/taquilla_app`
**Criterio:** revisión full-stack tipo *due-diligence*: organización del código, redundancias, mantenibilidad, seguridad, despliegue y propuesta de mejoras priorizadas.

---

## 1. Resumen ejecutivo

El proyecto cumple su función de negocio: ingiere PDFs semanales del Ministerio, los enriquece con metadatos del ICAA y de TMDB, y los publica en una webapp Flask con visualizaciones bastante cuidadas. La capa de parsing de PDFs (`download_parse.py`) es, de hecho, la pieza más sólida del repo: resuelve un problema difícil (tablas sin líneas reconstruidas por coordenadas) con heurísticas razonables.

Sin embargo, todo lo que rodea a ese núcleo presenta el patrón típico de un proyecto que ha crecido por iteración rápida sin una pasada de consolidación: **scripts duplicados con sufijos `_v2`, `_v3`, `_final`, `_ultra`**, **ficheros de debug y JSON intermedios olvidados**, **un secreto de API hardcodeado**, **`venv/` y `__pycache__/` versionados**, **un único `app.py` de 916 líneas** y **una plantilla Jinja monolítica de 1.645 líneas**. Hay deuda real, pero es deuda *manejable*: el dominio está bien acotado, no hay frameworks exóticos y el modelo de datos es simple.

Estimación: **2 semanas de trabajo enfocado** bastan para dejarlo en un estado mantenible (limpieza + refactor de la webapp + tests básicos + CI). Los riesgos críticos (token expuesto, SSL deshabilitado, FLASK_DEBUG en compose) se cierran en **un día**.

---

## 2. Inventario y métricas

| Métrica | Valor |
| :--- | :--- |
| Scripts Python en raíz | ~17 |
| Scripts en `scraper_icaa/` | 14 (varios duplicados) |
| LOC Python (excluyendo venv) | ~5.400 |
| `webapp/app.py` | 916 LOC |
| `webapp/templates/index.html` | 1.645 LOC |
| PDFs almacenados | 1.240 (~206 MB) |
| HTMLs scraping ICAA | 0 en `scraper_icaa/html_sources/` (vacío) + 10 en una carpeta anidada `scraper_icaa/scraper_icaa/html_sources/` |
| Logs activos | `errors.log` 1,1 MB · `icaa_pipeline.log` 830 KB · `logs/update.log` 1,1 MB |
| Tests automatizados | **0** |
| `.gitignore` | **No existe** |
| `venv/` versionado | Sí |

---

## 3. Organización de ficheros

### 3.1 Estructura actual (raíz plana)

Todo convive en la raíz: scripts ETL, webapp, datos, logs, artefactos del entorno virtual y subdirectorios cuyo papel se solapa. Conviven al mismo nivel:

- el **pipeline semanal** (`update.py`, `download_parse.py`, `run_update.sh`),
- el **enriquecimiento ICAA** (`brave_icaa.py`, `icaa_downloader.py`, `icaa_parser.py`, `icaa_mapper.py`, `icaa_full_extractor.py`, `icaa_ultra_extractor.py`, `find_icaa_ids.py`, `sync_titles.py`),
- el **enriquecimiento TMDB** (`tmdb_enricher.py`),
- los **informes anuales** (`parse_anuales_icaa.py`),
- **scripts puntuales** que ya cumplieron su misión (`import_topespanol_mar2026.py`, `rename_pdfs.py`, `reconstruct_csv.py`),
- una **subcarpeta `scraper_icaa/`** que es prácticamente otro proyecto (con su propio `requirements.txt` y `README.md`),
- un **plugin/skill** (`icaa-sync.skill` zip + carpeta `icaa-sync-skill/`),
- y los datos: `pdfs/`, `csv/`, `logs/`, además de tres `*.log` sueltos en raíz.

### 3.2 Subcarpeta anidada huérfana

`scraper_icaa/scraper_icaa/` es una **copia parcial** de su carpeta padre: contiene `html_sources/` (con 10 HTMLs), `peliculas.csv`, `peliculas_completas.json` y `scraper.log`. Tiene toda la pinta de haberse creado al ejecutar un script desde el directorio equivocado y nunca borrarse.

### 3.3 Ficheros de scratch y debug

Solo en `scraper_icaa/` hay: `158720.pdf`, `LAST_REORT.html` (con typo), `debug_20220.html`, `debug_20220_es.html`, `debug_asbestas.html`, `debug_partial.html`, `debug_print.html`, `debug_session.py`, `partial_158720.html`, `test_3.csv`, `test_output.json`, `test_page.html`, `test_full_158720.html`, `top50_titles.txt`, `downloaded_ids.txt`, `id_mapeo_final.json`, `mapper_taquilla_icaa.json`, `peliculas_completas.json`, `peliculas_completas_v2.json`, `peliculas_detalladas.json`, `resultado_final_absoluto.json`. Una carpeta `.archive/` añade cuatro JSON más (`resultado_completo`, `resultado_test`, `resultado_final_test`, `resultado_final_con_distribucion`).

### 3.4 Metadatos macOS y restos SMB

Hay un `.DS_Store` por carpeta y, sobre todo, decenas de ficheros `._*` (AppleDouble) y `.smbdeleteAAA*` (basura del montaje SMB) que aparecen en raíz y en `webapp/`. Son inocuos para macOS pero se replican en cada copia/clon y, sin `.gitignore`, contaminan el repositorio.

### 3.5 Estructura propuesta

Un esqueleto razonable sería:

```
taquilla/
├── pyproject.toml          # deps centralizadas (sustituye los 3 requirements.txt)
├── README.md
├── .env.example
├── .gitignore
├── Dockerfile
├── docker-compose.yml      # dev
├── docker-compose.prod.yml
├── data/                   # NO versionado
│   ├── pdfs/
│   ├── csv/
│   └── html/
├── logs/                   # NO versionado
├── src/taquilla/
│   ├── config.py           # un único load_dotenv
│   ├── db.py               # pool de conexiones psycopg
│   ├── logging_setup.py    # un único dictConfig
│   ├── ingest/             # update + download_parse
│   ├── icaa/               # search + downloader + parser
│   ├── tmdb/
│   ├── annual/
│   └── webapp/
│       ├── app.py          # factory create_app()
│       ├── routes/         # blueprints (index, api)
│       ├── repository.py   # SQL en un solo módulo
│       ├── services/       # concentration, capacity, decay
│       ├── filters.py
│       ├── static/
│       └── templates/
│           ├── base.html
│           ├── index.html
│           └── partials/   # ranking, concentration, capacity, ...
├── scripts/                # CLI thin wrappers (entrypoints)
├── migrations/             # SQL versionado (yoyo / alembic)
└── tests/
    ├── unit/
    ├── integration/
    └── fixtures/pdfs/
```

---

## 4. Redundancia y código muerto

### 4.1 Versiones múltiples del mismo concepto

En `scraper_icaa/` hay **cinco descargadores** que hacen lo mismo con variaciones mínimas:

```
batch_downloader.py  (31 LOC)
batch_downloader_v2.py  (25 LOC)
batch_downloader_v3.py  (20 LOC)
downloader.py  (32 LOC)
final_downloader.py  (26 LOC)
final_chance.py  (33 LOC)
```

En la raíz hay **siete scripts** que tocan ICAA con distintas estrategias y en distinto grado de obsolescencia:

```
brave_icaa.py            ← buscador vía Brave (vigente)
icaa_downloader.py       ← descargador masivo (vigente)
icaa_parser.py           ← parser HTML → BD (vigente)
icaa_mapper.py           ← prototipo de mapper anterior a brave_icaa
icaa_full_extractor.py   ← scraper hardcoded de 23 IDs
icaa_ultra_extractor.py  ← variante del anterior con clases CSS distintas
find_icaa_ids.py         ← otra iteración de búsqueda
```

Solo `brave_icaa.py + icaa_downloader.py + icaa_parser.py` se mencionan en `GEMINI.md` y `README.md`. Los otros cuatro son desechables tras una verificación rápida.

### 4.2 Scripts puntuales que ya cumplieron

`import_topespanol_mar2026.py` es un *one-shot* que descarga un PDF concreto e incluye una función `fix_feb_mar_dates()` que **se ejecuta cada vez que se llama al script**. Es deuda viva: una migración manual que se ha quedado en el árbol como si fuera código de producción.

`rename_pdfs.py` parece una utilidad que ya hizo su trabajo (todos los PDFs ya tienen el prefijo `YYYY-MM-DD_`).

`reconstruct_csv.py` es una utilidad de *backup* que se documenta pero no se llama desde `run_update.sh`.

### 4.3 Datos intermedios versionados

Los `peliculas_*.json`, `resultado_*.json`, `mapper_taquilla_icaa.json`, `id_mapeo_final.json` y la carpeta `.archive/` son outputs de exploración que pertenecen a `data/` (o a `.gitignore`), no al árbol del repo.

### 4.4 Plantilla y CSS

`webapp/templates/index.html` (1.645 líneas) embebe CSS, JavaScript y HTML en un solo fichero. Hay 6+ secciones lógicas (cabecera, ranking, concentración, capacidad, top año/histórico, anual_esp) que serían `partials/` independientes y un bundle JS estático.

### 4.5 Configuración duplicada

- **Tres `requirements.txt`** (raíz, `webapp/`, `scraper_icaa/`) que se solapan parcialmente y son incoherentes: `webapp/requirements.txt` no incluye `pdfplumber` ni `gunicorn`, pero el `Dockerfile` instala desde el de raíz que sí los incluye.
- **`load_dotenv()` repetido** en 11 scripts. Cada uno mantiene su propio diccionario de configuración.
- **`logging.basicConfig(...)` repetido** en 6 scripts, lo que provoca un bug que describo en la siguiente sección.
- **Cadenas DSN de fallback** repetidas: `os.getenv("DATABASE_URL", "postgresql://localhost/comscore")` aparece en 6 ficheros.

---

## 5. Hallazgos críticos (P1 — atender en 24h)

### 5.1 Token TMDB hardcodeado en código fuente

`tmdb_enricher.py` línea 36:

```python
TMDB_TOKEN = os.getenv("TMDB_TOKEN", "eyJhbGciOiJIUzI1NiJ9.eyJhdWQi...fDQ0ozx...")
```

El JWT real de TMDB está como **valor por defecto** del `getenv`, lo que significa que cualquiera con acceso al repo (o al historial de Git si está publicado) tiene la credencial. Hay que:

1. Rotar el token en TMDB.
2. Quitar el default y forzar `RuntimeError` si la variable no está.
3. Reescribir el historial de Git (`git filter-repo` o BFG) para borrar el token de los commits previos. Si el repo es privado y no se ha clonado fuera, el riesgo es bajo, pero el token sigue siendo accesible vía cualquier copia local.

### 5.2 SSL deshabilitado sin justificar

Hay **17 puntos** del código con `verify=False` o `ssl.CERT_NONE` repartidos por casi todos los scripts de red. Algunos están justificados (la sede del MCU sirve un certificado problemático para `urllib`), pero el patrón se ha contagiado a todos los descargadores incluido el de los PDFs del Ministerio (que sí servirían bien con CA pública). Conviene:

- Cargar la CA del MCU como bundle local y usarlo solo donde haga falta.
- Quitar el `verify=False` del cliente PDF.
- Documentar cualquier excepción restante.

### 5.3 FLASK_DEBUG=1 en `docker-compose.yml`

```yaml
environment:
  - FLASK_ENV=development
  - FLASK_DEBUG=1
```

Combinado con `command: python webapp/app.py` y `app.run(debug=True, host='0.0.0.0', port=5002)`, la aplicación pública en `taquilla.hookponent.cc` está sirviendo Flask en modo desarrollo con el *debugger* PIN expuesto. Esto es una vulnerabilidad RCE clásica si un atacante consigue una traza con error. Hay que:

- Compose: poner `FLASK_DEBUG=0` y arrancar con `gunicorn webapp.app:app --bind 0.0.0.0:5002`.
- El `Dockerfile` ya tiene esa intención (`CMD ["gunicorn", "--bind", "0.0.0.0:5001", ...]`) pero el compose la sobrescribe.

### 5.4 Inconsistencia de puertos

`Dockerfile` expone 5001, `docker-compose` lo sustituye por `python webapp/app.py` que escucha en 5002, `GEMINI.md` dice "evitar puerto 5001". El `EXPOSE` del Dockerfile y el comando del compose deben converger.

### 5.5 Ausencia de `.gitignore`

No existe. Se han versionado:

- `venv/` completo (~ varios MB de binarios).
- `__pycache__/` en raíz y en `webapp/`.
- `.DS_Store` en cada carpeta y `._*` AppleDouble por todas partes.
- `.smbdelete*` (residuos de operaciones SMB).
- `errors.log` (1,1 MB), `icaa_pipeline.log` (830 KB), `logs/update.log` (1,1 MB).
- `.env` con `BRAVE_API_KEY` y `DATABASE_URL`.

Hay que crear un `.gitignore` y, después, ejecutar `git rm --cached -r` sobre todos esos ficheros. El `.env`, además, hay que asumirlo comprometido y rotar las claves que contiene si el repo se ha compartido.

---

## 6. Hallazgos altos (P2 — atender en 1-2 semanas)

### 6.1 Bug en el routing de logs

`download_parse.py` ejecuta a nivel de módulo:

```python
logging.basicConfig(filename="errors.log", level=logging.ERROR, ...)
```

`update.py` lo importa, lo que adjunta un `FileHandler` al logger raíz **sin nivel propio** (hereda NOTSET). Cuando el logger `taquilla` (level=DEBUG) emite mensajes, propagan al raíz y se escriben a `errors.log` aunque sean DEBUG/INFO. Resultado: `errors.log` no contiene errores, contiene 13.100 líneas de DEBUG y crece sin rotación. La carpeta `logs/update.log` también recibe lo mismo y sí rota (5×5 MB).

Solución: un único `logging_setup.py` con `dictConfig`, nada de `basicConfig` esparcido por scripts importables.

### 6.2 Cron sin entorno

`logs/cron.log` muestra el 2026-04-27:

```
ModuleNotFoundError: No module named 'psycopg2'
```

Es decir, **antes** del run que sí funcionó luego. Esto indica que el cron no está usando `run_update.sh` (que sí selecciona el `venv/bin/python3`), sino una invocación directa a `update.py` con el Python del sistema. Aunque la línea siguiente del log sí muestra una ejecución correcta, el síntoma sugiere que la `crontab` tiene dos entradas o se ha cambiado a mitad. Hay que revisar `crontab -l`, dejar una sola entrada apuntando a `run_update.sh` y migrar a un `systemd timer` con `journald` (más visibilidad y reintentos nativos).

### 6.3 SQL con interpolación de identificadores

La webapp acepta `tab=top25|topespanol` por query string y construye SQL así:

```python
return query(f"""
    SELECT ... FROM {table}
    WHERE fecha_inicio = %s ...
""", (fi, ff))
```

Hoy es seguro porque hay validación arriba (`'top25' if tab == 'top25' else 'topespanol'`), pero el patrón se repite en **9 funciones** y nada lo centraliza. Un futuro descuido (añadir un tercer valor sin validarlo) abre SQLi. La mitigación: enum `TableName` único y un helper `query_for(table, sql, params)` que valida y formatea.

### 6.4 Webapp monolítica

`app.py` mezcla cuatro responsabilidades: acceso a datos (queries SQL en strings), lógica de negocio (concentración, percentiles, anomalías), formato (filtros Jinja) y rutas. Para 900 LOC ya empieza a costar localizar dónde se carga cada widget de la home. Splits naturales:

- `repository.py` (todas las funciones `get_*`/`query`).
- `services/concentration.py`, `services/capacity.py`, `services/decay.py`.
- `routes/index.py` y `routes/api.py` como blueprints.
- `filters.py` para los filtros Jinja.

### 6.5 Plantilla monolítica

`index.html` 1.645 LOC con CSS y JS embebidos. Igual que el backend, partir en `partials/` (uno por sección visual) y servir el JS y CSS desde `static/`.

### 6.6 Migraciones SQL desperdigadas

Cada script crea sus tablas con `CREATE TABLE IF NOT EXISTS ...` (`update.py`, `icaa_parser.py`, `parse_anuales_icaa.py`...). El esquema vive en SQL embebido en Python. Si alguien crea una columna en producción y luego cambia el SQL, no hay manera de saber el orden. Una herramienta de migraciones (yoyo-migrations es ligera; alembic si se migra a SQLAlchemy) cierra esto.

### 6.7 Sin tests

5.400+ LOC y cero tests. Lo más sensible es `download_parse.py`: las heurísticas de parsing son frágiles y solo se validan en producción. Con guardar ~6 PDFs representativos como *fixtures* y un par de docenas de tests de regresión ya se cubre el 80% del riesgo.

### 6.8 Conexiones a BD sin pool

Cada `query()` abre y cierra una conexión. En la home se ejecutan ~10 queries → 10 conexiones por petición. En tráfico bajo es asumible, pero Postgres se asfixia rápido. `psycopg2.pool.ThreadedConnectionPool` ya hace el trabajo. Si se va a SQLAlchemy/psycopg3, el pool es nativo.

---

## 7. Hallazgos medios (P3 — refactor planificado)

### 7.1 Modelo de datos

`top25` y `topespanol` repiten el esquema con la única diferencia de un par de columnas (TÍTULO ORIGINAL vs medias por cine/pantalla). Mantener dos tablas obliga a ramificar el código en cada query. Un esquema `weekly_ranking(report_type, fecha_inicio, fecha_fin, ...)` con columnas opcionales nullables simplificaría el repository.

El *match* TMDB ↔ comscore se hace por título normalizado en el momento del JOIN dentro de la query. Es trabajoso para Postgres y ruidoso (`regexp_replace(LOWER(...), '[^a-z0-9]', '', 'g')` en LEFT JOIN). Una tabla `movies(id, titulo_canonico, distribuidora_canonica)` con FK desde `top25/topespanol/tmdb/icaa_fichas/anual_esp` resuelve definitivamente el problema de matching y permite cachear.

### 7.2 Subvenciones en JSONB

Las subvenciones se guardan como JSONB en `icaa_fichas`. Para la webapp actual basta, pero limita queries futuras (top empresas subvencionadas, evolución temporal de ayudas). Una tabla `subvenciones(expediente_icaa, concepto, importe_eur, fecha)` desbloquea ese análisis.

### 7.3 Fechas y zonas horarias

`update.py` mezcla `datetime.now(timezone.utc)` con dates de PostgreSQL y la webapp importa `ZoneInfo` que no se usa de manera consistente. Convendría normalizar: BD en UTC, presentación con `Europe/Madrid` en un solo punto.

### 7.4 Convenciones

- Nombres mezclan inglés y español sin patrón (`get_db` vs `crear_tabla`, `download_pdf` vs `parsear_html`).
- Docstrings en idiomas distintos.
- Emojis en logs y prints (`✅`, `🎬`, `💾`, `🔧`). Inocuo, pero rompe en consolas que no soportan UTF-8 emoji y mete ruido en `grep`.
- `print()` y `log.info()` conviven en los mismos scripts.

### 7.5 Caching y rendimiento de la webapp

El home ejecuta `get_historical_concentration` (escanea 52 semanas), `get_weekly_totals` (todas las semanas), `get_top_year`, `get_top_historico`, `get_attendance_by_year`, etc. Con la BD vacía es instantáneo; con varios años acumulados, la home empieza a notarse. Tres caminos:

- Vistas materializadas que se refrescan con el cron del lunes (`mv_top_historico`, `mv_top_year_<n>`, `mv_capacity`).
- `flask-caching` con TTL de 1h en endpoints que dependen solo de la BD.
- Cabeceras `Cache-Control` y `ETag` para que Cloudflare cachee la home.

---

## 8. Pequeños detalles que delatan deuda

- `LAST_REORT.html` (typo de "REPORT") en `scraper_icaa/`.
- `pdfs/2021-12-03_top-25-3-5-diciembre-2021.pdf` está corrupto (errors.log: *"No /Root object! Is this really a PDF?"*) y produce un error en cada ejecución desde hace meses.
- `errors.log` no se rota, sigue creciendo.
- `scraper_icaa/scraper.log` también versionado.
- `webapp/__pycache__/` con `.pyc` de Python 3.10 **y** 3.12, evidencia de despliegues con versiones inconsistentes.
- `icaa-sync.skill` es un ZIP binario y `icaa-sync-skill/` es el mismo contenido descomprimido. Solo uno debería existir.
- Comentario en `download_parse.py`: `# Multiple tokens (column bleed): take last valid number` → la heurística "last token gana" es frágil, conviene tests con PDFs reales que la han disparado.

---

## 9. Plan de acción priorizado

### Hoy (P1 — seguridad)

1. Rotar el token TMDB y eliminar el default hardcodeado.
2. `FLASK_DEBUG=0` en compose y arrancar con gunicorn.
3. Crear `.gitignore` y purgar `venv/`, `__pycache__/`, `.DS_Store`, `._*`, `.smbdelete*`, `*.log`, `.env`, `pdfs/`, `csv/`.
4. Decidir si rotar `BRAVE_API_KEY` y `DATABASE_URL` (depende de si el repo se ha clonado fuera de tu equipo).
5. Reescribir historial de Git para borrar el token TMDB de los commits previos.

### Sprint 1 (1 semana — limpieza)

6. Borrar duplicados:
   - `batch_downloader_v{2,3}.py`, `final_downloader.py`, `final_chance.py`, `downloader.py`, `debug_session.py`, `post_extractor.py` en `scraper_icaa/`.
   - `icaa_full_extractor.py`, `icaa_ultra_extractor.py`, `icaa_mapper.py`, `find_icaa_ids.py` en raíz (validar primero que ninguno se referencia desde cron o documentación interna).
   - Carpeta anidada `scraper_icaa/scraper_icaa/` y todos los `debug_*.html`, `test_*.json`, `test_*.csv`, `test_*.html`, `peliculas_*.json`, `resultado_*.json`, `mapper_*.json`, `partial_*.html`, `158720.pdf`, `LAST_REORT.html`.
7. Mover `import_topespanol_mar2026.py`, `rename_pdfs.py`, `reconstruct_csv.py` a `scripts/oneshots/` o eliminar tras verificar que no se necesitan.
8. Borrar el PDF corrupto de diciembre 2021 (o marcarlo en `processed_pdfs` como ignorado para que pare de generar errores).
9. Limpiar `errors.log` y `icaa_pipeline.log`; añadir rotación.
10. Centralizar `requirements.txt` en `pyproject.toml` con `[project.optional-dependencies]` para webapp y scraper.

### Sprint 2 (1 semana — refactor)

11. Crear el árbol `src/taquilla/` y mover los módulos. Un único `config.py` con `pydantic-settings` o equivalente. Un único `logging_setup.py`.
12. Partir `webapp/app.py` en `routes/`, `repository.py`, `services/`, `filters.py`. Convertir a `create_app()` factory.
13. Partir `index.html` en `partials/` y mover CSS/JS a `static/`.
14. Introducir `psycopg_pool` (o SQLAlchemy si se quiere ORM).
15. Añadir 8-10 tests de regresión sobre `download_parse.py` con PDFs *fixture*.
16. CI mínimo: GitHub Actions con `ruff`, `mypy --strict-optional` y `pytest`.

### Backlog (P3 — cuando haya tiempo)

17. Migraciones SQL versionadas (yoyo-migrations).
18. Vistas materializadas para Top año/histórico, refrescadas en el cron del lunes.
19. Tabla `movies` canónica para sustituir el matching por título.
20. Pasar a `systemd timer` el cron semanal.
21. Autenticación HTTP básica si la URL pública es solo para uso interno.
22. Observabilidad: estructurar logs en JSON, `request_id` por petición, métricas de Prometheus para tiempos de query.

---

## 10. Lo que está bien hecho

Para no quedarme solo en la crítica, conviene reconocer las decisiones acertadas que hay que **preservar** en cualquier refactor:

- **`download_parse.py`** es el corazón del proyecto y está pensado: parsing por coordenadas, detección de anclas en cabeceras, fallback de fechas (PDF → filename → section_year). La abstracción `_build_bounds`/`_anchored_bounds` es elegante.
- **`processed_pdfs`** como tabla de auditoría e idempotencia: la decisión correcta para un cron semanal.
- **Migración de esquema en `update.py`** vía `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` para `inserted_at`. Pequeña pero hace bien las cosas.
- **`brave_icaa.py`** documenta su economía de queries (OR-batching, dedup por título+distribuidora) y eso ahorra esfuerzo a quien venga detrás.
- **La webapp** tiene buenas decisiones en la lógica de negocio: percentiles para clasificar anomalías, *capacity insight* contra todo el histórico, curva de decaimiento por película. Hay producto detrás del código.
- **README.md** y **GEMINI.md** son razonablemente completos como documentación de alto nivel.
- **El `LEFT JOIN` con normalización de "Tribu, La" → "La Tribu"** es justo el tipo de detalle que delata haber peleado con datos reales.

---

## 11. Conclusión

El proyecto funciona y el dominio está controlado. La deuda es predominantemente **estructural** (organización del código) y **operativa** (secretos, configuración, ausencia de tests), no algorítmica: lo difícil ya está resuelto. Una pasada de limpieza + refactor de la webapp + tests sobre el parser + cierre de los hallazgos de seguridad transforma el repo de "proyecto personal en evolución" a "servicio mantenible por más de una persona" en aproximadamente dos semanas de trabajo enfocado.
