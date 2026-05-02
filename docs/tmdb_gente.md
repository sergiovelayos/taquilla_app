# Importación de personas desde TMDB (`tmdb_gente`)

Script: `tmdb_gente_importer.py`

Este script importa la ficha completa de directores y actores desde la API de TMDB y la guarda en la tabla `public.tmdb_gente`. Soporta dos fuentes de datos independientes, pero comparten la misma tabla: una persona que aparezca como director **y** como actor acumula ambos roles en el campo `roles[]`.

| `--tipo` | Fuente de nombres | Prioridad de búsqueda en TMDB |
|----------|-------------------|-------------------------------|
| `director` | `icaa_fichas.director` (con subvenciones) | Departamento `Directing` |
| `actor` | `icaa_fichas.ficha_artistica[].nombre` (JSONB) | Departamento `Acting` |

El objetivo principal es disponer de **fotos de alta resolución** y datos biográficos para las secciones de directores y actores de la web.

---

## Tabla `tmdb_gente`

```sql
SELECT * FROM public.tmdb_gente LIMIT 1;
```

| Columna | Tipo | Descripción |
|---------|------|-------------|
| `nombre_icaa` | `TEXT PK` | Nombre exacto tal como aparece en `icaa_fichas.director` |
| `tmdb_id` | `INTEGER` | ID único de la persona en TMDB |
| `imdb_id` | `TEXT` | ID en IMDb (ej. `"nm0000953"`) |
| `wikidata_id` | `TEXT` | ID en Wikidata (ej. `"Q26297"`) |
| `nombre_tmdb` | `TEXT` | Nombre canónico según TMDB |
| `tambien_conocido_como` | `TEXT[]` | Lista de alias y variantes del nombre |
| `foto_url` | `TEXT` | URL de la foto de perfil (500px) — lista para usar en web |
| `foto_url_hd` | `TEXT` | URL de la foto en resolución original |
| `todas_las_fotos` | `TEXT[]` | Array con todas las fotos disponibles, ordenadas por valoración |
| `biografia` | `TEXT` | Biografía en español (si disponible en TMDB) |
| `fecha_nacimiento` | `DATE` | Fecha de nacimiento |
| `lugar_nacimiento` | `TEXT` | Lugar de nacimiento tal como lo tiene TMDB |
| `fecha_fallecimiento` | `DATE` | Fecha de fallecimiento (NULL si vive) |
| `genero` | `CHAR(1)` | `'M'`, `'F'` o NULL si TMDB no lo tiene |
| `departamento` | `TEXT` | Departamento conocido en TMDB (normalmente `"Directing"`) |
| `popularidad` | `NUMERIC` | Score de popularidad de TMDB (valor flotante) |
| `homepage` | `TEXT` | Web oficial si TMDB la tiene |
| `instagram_id` | `TEXT` | Usuario de Instagram |
| `twitter_id` | `TEXT` | Usuario de Twitter/X |
| `num_peliculas_tmdb` | `INTEGER` | Número de películas como director en TMDB |
| `peliculas_dirigidas` | `TEXT[]` | Lista de títulos dirigidos: `"Título (año)"`, orden descendente |
| `roles` | `TEXT[]` | `{director}`, `{actor}` o `{actor,director}` — se acumula en cada importación |
| `num_peliculas_director` | `INTEGER` | Número de películas como director en TMDB |
| `peliculas_dirigidas` | `TEXT[]` | Lista `"Título (año)"`, orden descendente |
| `num_peliculas_actor` | `INTEGER` | Número de películas como actor/actriz en TMDB |
| `peliculas_actuado` | `TEXT[]` | Lista `"Título (año) [Personaje]"`, orden por popularidad |
| `match_score` | `NUMERIC` | Confianza del matching automático (0–1) |
| `revisado_manual` | `BOOLEAN` | `TRUE` si se ha verificado o corregido a mano |
| `notas` | `TEXT` | Campo libre para anotaciones |
| `created_at` / `updated_at` | `TIMESTAMPTZ` | Metadatos de auditoría |

### Consultas de referencia

```sql
-- Todos los directores con foto, ordenados por popularidad
SELECT nombre_icaa, nombre_tmdb, foto_url, popularidad, fecha_nacimiento, lugar_nacimiento
FROM public.tmdb_gente
WHERE 'director' = ANY(roles)
  AND foto_url IS NOT NULL
ORDER BY popularidad DESC NULLS LAST;

-- Todos los actores con foto
SELECT nombre_icaa, nombre_tmdb, foto_url, popularidad
FROM public.tmdb_gente
WHERE 'actor' = ANY(roles)
  AND foto_url IS NOT NULL
ORDER BY popularidad DESC NULLS LAST;

-- Personas que son director Y actor a la vez
SELECT nombre_icaa, nombre_tmdb, foto_url, popularidad
FROM public.tmdb_gente
WHERE roles @> ARRAY['director','actor']
ORDER BY popularidad DESC NULLS LAST;

-- Personas sin foto (para revisar manualmente)
SELECT nombre_icaa, roles, tmdb_id, match_score
FROM public.tmdb_gente
WHERE foto_url IS NULL
ORDER BY nombre_icaa;

-- Todas las fotos disponibles de una persona
SELECT unnest(todas_las_fotos) AS foto
FROM public.tmdb_gente
WHERE nombre_icaa = 'Almodóvar, Pedro';

-- JOIN directores con icaa_fichas (subvenciones + foto)
SELECT f.titulo, f.director, f.subvenciones_total_eur, g.foto_url, g.popularidad
FROM public.icaa_fichas f
LEFT JOIN public.tmdb_gente g ON g.nombre_icaa = f.director
WHERE f.subvenciones_total_eur IS NOT NULL
ORDER BY f.subvenciones_total_eur DESC
LIMIT 20;

-- JOIN actores con icaa_fichas (ficha artística + foto)
SELECT f.titulo, elem->>'nombre' AS actor, elem->>'personaje' AS personaje, g.foto_url
FROM public.icaa_fichas f,
LATERAL jsonb_array_elements(f.ficha_artistica::jsonb) AS elem
LEFT JOIN public.tmdb_gente g ON g.nombre_icaa = elem->>'nombre'
WHERE elem ? 'nombre'
LIMIT 50;
```

---

## Fuentes de datos

### Directores

```sql
SELECT DISTINCT director
FROM public.icaa_fichas
WHERE subvenciones_total_eur IS NOT NULL
  AND director IS NOT NULL
  AND TRIM(director) <> ''
ORDER BY 1;
```

Solo se procesan directores de películas con subvenciones concedidas, lo que excluye proyectos en desarrollo o fichas sin datos económicos.

### Actores

```sql
SELECT DISTINCT elem->>'nombre' AS nombre
FROM public.icaa_fichas,
LATERAL jsonb_array_elements(ficha_artistica::jsonb) AS elem
WHERE elem ? 'nombre';
```

Los actores se extraen del campo JSONB `ficha_artistica`, que contiene un array de objetos con las claves `nombre`, `personaje` y otras. El universo de actores es significativamente mayor que el de directores — puede superar el millar de nombres — por lo que conviene ejecutar la importación con `--skip-existing` para poder reanudarla si se interrumpe.

---

## Proceso de matching

Encontrar la persona correcta en TMDB es el paso más delicado, porque `icaa_fichas` puede almacenar los nombres en formato `"Apellido, Nombre"` (p.ej. `"Almodóvar, Pedro"`) mientras que TMDB siempre usa `"Nombre Apellido"`.

### Normalización del nombre

Antes de buscar, el script convierte automáticamente el formato ICAA al formato TMDB:

| Nombre en `icaa_fichas` | Query enviada a TMDB |
|-------------------------|----------------------|
| `"Almodóvar, Pedro"` | `"Pedro Almodóvar"` |
| `"León de Aranoa, Fernando"` | `"Fernando León de Aranoa"` |
| `"Pedro Almodóvar"` | `"Pedro Almodóvar"` (sin cambio) |

### Score de similitud

Para cada candidato devuelto por TMDB se calcula un score (0–1) comparando el nombre ICAA con el nombre TMDB:

- **1.0** — coincidencia exacta tras normalización
- **≥ 0.8** — mismas palabras, distinto orden
- **< 0.4** — descartado automáticamente (probablemente otra persona)

La búsqueda también prioriza candidatos con `known_for_department = "Directing"` por encima de actores o técnicos con nombre similar.

### Casos problemáticos

El matching automático puede fallar con:

- **Nombres muy cortos** o muy comunes (`"García, Luis"` puede devolver decenas de resultados).
- **Directores poco conocidos** con baja popularidad, que TMDB devuelve por detrás de actores más famosos con nombre similar.
- **Grafías con caracteres especiales** que el ICAA registró sin normalizar.

Para estos casos, usar `--nombre` con corrección manual (ver sección de uso).

---

## Uso

```bash
# ── Directores ────────────────────────────────────────────────
# Prueba sin escribir (recomendado antes de la primera ejecución)
python3 tmdb_gente_importer.py --tipo director --dry-run --limit 10

# Importar todos los directores
python3 tmdb_gente_importer.py --tipo director

# Continuar una importación de directores interrumpida
python3 tmdb_gente_importer.py --tipo director --skip-existing

# ── Actores ───────────────────────────────────────────────────
# Prueba con los primeros 20 actores
python3 tmdb_gente_importer.py --tipo actor --dry-run --limit 20

# Importar todos los actores
python3 tmdb_gente_importer.py --tipo actor

# Continuar una importación de actores interrumpida
python3 tmdb_gente_importer.py --tipo actor --skip-existing

# ── Persona concreta (director o actor) ───────────────────────
python3 tmdb_gente_importer.py --tipo actor    --nombre "Penélope Cruz"
python3 tmdb_gente_importer.py --tipo director --nombre "Almodóvar, Pedro"
```

El flag `--tipo` determina la fuente de nombres y la prioridad de búsqueda en TMDB, pero ambos tipos escriben en la misma tabla. El upsert acumula roles: si "Penélope Cruz" ya existe como actriz y luego se importa como directora (o viceversa), su fila pasa a tener `roles = {actor, director}` sin perder ningún dato.

El script usa upsert (`ON CONFLICT DO UPDATE`), por lo que es seguro ejecutarlo múltiples veces — actualiza los datos sin duplicar filas. Los campos `revisado_manual` y `notas` **nunca se sobreescriben** en el upsert para proteger las correcciones manuales.

---

## Correcciones manuales

Cuando el matching automático devuelve la persona equivocada o no encuentra a nadie, hay dos opciones:

### Opción A — Forzar el TMDB ID directamente en BD

Si sabes el ID correcto (búscalo en `https://www.themoviedb.org/person/<id>`):

```sql
-- 1. Ver qué tiene ahora
SELECT nombre_icaa, tmdb_id, nombre_tmdb, match_score
FROM public.tmdb_gente
WHERE nombre_icaa = 'García, Luis';

-- 2. Actualizar el TMDB ID y marcar como revisado
UPDATE public.tmdb_gente
SET tmdb_id = 12345,
    revisado_manual = TRUE,
    notas = 'ID corregido manualmente — el automático devolvió al actor Luis García Berlanga Jr.'
WHERE nombre_icaa = 'García, Luis';

-- 3. Volver a ejecutar el script para rellenar los datos con el ID correcto
-- (el upsert actualizará todos los campos biográficos manteniendo revisado_manual=TRUE)
```

### Opción B — Marcar como "no existe en TMDB"

Para directores que realmente no tienen ficha en TMDB:

```sql
UPDATE public.tmdb_gente
SET tmdb_id = NULL,
    revisado_manual = TRUE,
    notas = 'Confirmado: sin ficha en TMDB'
WHERE nombre_icaa = 'Nombre Desconocido';
```

Con `--skip-existing` el script no volverá a tocar esta fila.

---

## Fotos: uso en la web

Las URLs de foto apuntan directamente a la CDN de TMDB — no hace falta descargarlas ni servirlas desde nuestra infraestructura.

```
foto_url    → https://image.tmdb.org/t/p/w500/xxxxxxx.jpg    (500px, ideal para cards)
foto_url_hd → https://image.tmdb.org/t/p/original/xxxxxxx.jpg (máxima resolución)
```

Para la sección de directores, `foto_url` (500px) es el tamaño recomendado. Si se necesita una foto alternativa (por ejemplo, si la principal no es adecuada), `todas_las_fotos[]` contiene el array completo ordenado por valoración de los usuarios de TMDB.

```sql
-- Obtener la segunda foto mejor valorada de un director
SELECT todas_las_fotos[2]
FROM public.tmdb_gente
WHERE nombre_icaa = 'Bayona, Juan Antonio';
```

---

## Pipeline de referencia

```bash
# 1. Importar directores
python3 tmdb_gente_importer.py --tipo director

# 2. Importar actores (puede tardar más — son más)
python3 tmdb_gente_importer.py --tipo actor

# 3. Verificar cobertura por tipo
SELECT
    unnest(roles)                                        AS rol,
    COUNT(*)                                             AS total,
    COUNT(*) FILTER (WHERE foto_url IS NOT NULL)         AS con_foto,
    ROUND(100.0 * COUNT(*) FILTER (WHERE foto_url IS NOT NULL) / COUNT(*), 1) AS pct_foto
FROM public.tmdb_gente
GROUP BY 1
ORDER BY 1;

# 4. Revisar los sin foto
SELECT nombre_icaa, roles, tmdb_id, nombre_tmdb, match_score
FROM public.tmdb_gente
WHERE foto_url IS NULL AND revisado_manual = FALSE
ORDER BY match_score DESC;

# 5. Reimportar tras correcciones manuales
python3 tmdb_gente_importer.py --tipo director --skip-existing
python3 tmdb_gente_importer.py --tipo actor    --skip-existing
```
