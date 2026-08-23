# Calculadora de Subvenciones — Documentación

Página en `/calculadora`. Permite analizar el impacto de las subvenciones públicas al cine español cruzando datos del catálogo ICAA con información visual de TMDB. Tiene dos grandes bloques: la **búsqueda personalizada** y los **benchmarks globales** de referencia.

---

## Fuentes de datos

| Tabla | Rol |
|---|---|
| `icaa_catalogo_cache` / `icaa_catalogo` | Catálogo canónico que combina `icaa_fichas` y `scrape_icaa`, conservando por expediente la fila más completa. |
| `subvenciones_resueltas` | Ayudas con expediente ICAA, estado, confianza y método de matching. |
| `peliculas_calculadora` | Vista final: catálogo canónico más ayudas oficiales agregadas por expediente. |
| `tmdb_gente` | Fotos y biografías de directores y actores. Join por `nombre_icaa = director`. |

Cuando existe al menos una ayuda enlazada, `peliculas_calculadora.subvenciones_total_eur`
usa la suma de las resoluciones oficiales. Si no existe, conserva como fallback el
total publicado en la ficha ICAA. Las dos fuentes nunca se suman entre sí.

---

## Lógica de antigüedad

Todas las métricas de impacto excluyen películas demasiado recientes para tener datos de taquilla consolidados:

- Si la película tiene `fecha_estreno`: debe ser anterior a **hoy − 2 meses**.
- Si no tiene `fecha_estreno` pero sí `anio_produccion`: debe ser anterior al **año actual**.
- Las películas que no cumplen este criterio aparecen en las tablas marcadas con la etiqueta **"Reciente"** pero no suman en los totales ni en los benchmarks.

---

## Bloque 1: Búsqueda personalizada

### Buscador

Formulario con dos parámetros:
- **Tipo**: `director`, `actor`, `genero` o `pelicula`.
- **Query**: texto libre con búsqueda por `ILIKE` normalizado (sin acentos, sin caracteres especiales).

Todos los modos consultan `peliculas_calculadora`, que incluye tanto `icaa_fichas`
como `scrape_icaa` y conserva la procedencia del importe.

| Modo | Columna buscada | Nota |
|---|---|---|
| `director` | `director` | Búsqueda sobre texto |
| `actor` | `ficha_artistica->>'nombre'` | El reparto se almacena como array JSONB. Filtra por función: Intérpretes / Actor / Actriz |
| `genero` | `genero` | |
| `pelicula` | `titulo` | |

### Perfil TMDB de la persona (directores y actores)

Si el tipo es `director` o `actor` y hay resultados, se muestra una tarjeta con foto, bio, popularidad y enlace a IMDb. Fuente: `tmdb_gente`, buscando por `nombre_icaa ILIKE %query%`.

### Tarjetas de resumen (4 KPIs)

Solo contabilizan películas que superan el criterio de antigüedad:

| KPI | Campo fuente |
|---|---|
| Películas Analizadas | COUNT de resultados aptos |
| Recaudación Total | SUM de `peliculas_calculadora.recaudacion_eur` |
| Espectadores Totales | SUM de `peliculas_calculadora.espectadores` |
| Ayudas Públicas | SUM de `peliculas_calculadora.subvenciones_total_eur` |

### Ratio de eficiencia

Se calcula en el propio template:

```
recaudacion_total / subvenciones_totales → euros de taquilla por cada euro de subvención
```

### Listado de películas

Tabla con todas las películas del criterio buscado (aptas y recientes). Cada fila enlaza a `/pelicula/<expediente_icaa>`, que renderiza la ficha desde el catálogo canónico.

---

## Bloque 2: Benchmarks globales

Siempre visible, independientemente de si hay búsqueda activa. Se calcula en `get_benchmarks()` en `app.py`.

### Métricas

Dos ratios complementarios:

| Métrica | Fórmula | Etiqueta |
|---|---|---|
| Eficiencia de audiencia | `(espectadores / subvenciones_total_eur) × 1.000` | `esp/k€` (espectadores por cada 1.000€ de subvención) |
| Retorno de taquilla | `recaudacion_eur / subvenciones_total_eur` | `rec/€` (euros de taquilla por cada euro de subvención) |

### Media global de la industria

```sql
(SUM(espectadores) / SUM(subvenciones_total_eur)) × 1.000
FROM peliculas_calculadora
WHERE subvenciones_total_eur > 0 AND espectadores > 0
  AND [criterio de antigüedad]
```

### Top / Bottom 50 películas

| Ranking | Filtro de subvención mínima | Fuente |
|---|---|---|
| Top 50 (mayor alcance) | `subvenciones_total_eur > 5.000 €` | `peliculas_calculadora` |
| Bottom 50 (menor alcance) | `subvenciones_total_eur > 50.000 €` | `peliculas_calculadora` |

El umbral inferior del Bottom es más alto para evitar que películas con subvenciones muy pequeñas (que estadísticamente no pueden haber sido rentables) distorsionen el ranking.

### Top / Bottom 20 Directores

```sql
FROM peliculas_calculadora f
LEFT JOIN tmdb_gente g ON g.nombre_icaa = f.director
WHERE f.subvenciones_total_eur > 0 AND [criterio de antigüedad]
GROUP BY f.director, g.foto_url, g.popularidad, ...
HAVING COUNT(*) >= 2   -- mínimo 2 películas con datos de subvención
ORDER BY ratio DESC/ASC LIMIT 20
```

Las fotos, popularidad, año de nacimiento y lugar de nacimiento vienen de `tmdb_gente`. Si no hay match, se muestra el inicial del nombre como avatar.

### Top / Bottom 20 Actores / Actrices

El reparto se extrae mediante `jsonb_array_elements(ficha_artistica)` filtrando los elementos cuya función contenga `Intérpretes`, `Actor` o `Actriz`. El JOIN con `tmdb_gente` es igual que en directores. Mínimo 2 películas.

### Top / Bottom 20 Géneros

Fuente: campo `genero` de `peliculas_calculadora`. Sin JOIN adicional (sin foto). Mínimo **5 películas** por género para evitar rankings con muestras demasiado pequeñas.

---

## Diagrama de tablas por sección

```
┌─────────────────────────────────────────────────────────────────┐
│                         /calculadora                            │
├────────────────────┬────────────────────────────────────────────┤
│ Sección            │ Tablas                                      │
├────────────────────┼────────────────────────────────────────────┤
│ Perfil persona     │ tmdb_gente                                  │
│ KPIs resumen       │ peliculas_calculadora                       │
│ Listado películas  │ peliculas_calculadora                       │
│ Media global       │ peliculas_calculadora                       │
│ Top/Bottom pelis   │ peliculas_calculadora                       │
│ Top/Bottom dirs    │ peliculas_calculadora + tmdb_gente          │
│ Top/Bottom actores │ peliculas_calculadora (JSONB) + tmdb_gente  │
│ Top/Bottom géneros │ peliculas_calculadora                       │
└────────────────────┴────────────────────────────────────────────┘
```

---

## Campos clave de `peliculas_calculadora`

| Campo | Tipo | Uso |
|---|---|---|
| `expediente_icaa` | TEXT PK | Enlace a `/pelicula/<id>` |
| `titulo` | TEXT | Búsqueda y display |
| `director` | TEXT | Búsqueda y JOIN con `tmdb_gente` |
| `genero` | TEXT | Búsqueda por género |
| `ficha_artistica` | JSONB | Array de objetos `{nombre, funcion}` para búsqueda de actores |
| `subvenciones_total_eur` | NUMERIC | Denominador de todos los ratios |
| `subvenciones_oficiales_eur` | NUMERIC | Suma de resoluciones enlazadas; tiene prioridad |
| `subvenciones_icaa_eur` | NUMERIC | Total original de la ficha, conservado para auditoría |
| `fuente_subvenciones` | TEXT | `resoluciones_oficiales` o `ficha_icaa` |
| `espectadores` | INTEGER | Numerador del ratio `esp/k€` |
| `recaudacion_eur` | NUMERIC | Numerador del ratio `rec/€` |
| `fecha_estreno` | DATE | Criterio de antigüedad (prioridad 1) |
| `anio_produccion` | INTEGER | Criterio de antigüedad (prioridad 2) |

`recaudacion_eur` y `espectadores` son el snapshot del total mostrado por la
ficha ICAA cuando fue descargada. La calculadora no reconstruye esos totales
sumando `top25` o `topespanol`. Si una película se reestrena, el incremento se
incorpora únicamente después de volver a descargar y parsear su ficha actualizada.

La cifra `popularidad` de TMDB que aparece junto a personas es un índice dinámico
de visibilidad, no una puntuación artística ni una escala de 0 a 10. Para personas,
TMDB indica que intervienen las visitas del día y el valor del día anterior. La web
muestra el último valor guardado por el importador, no una consulta en tiempo real.

## Campos clave de `tmdb_gente`

| Campo | Uso |
|---|---|
| `nombre_icaa` | Clave de join con `icaa_fichas.director` / nombres del reparto |
| `nombre_tmdb` | Nombre oficial en TMDB (para display) |
| `foto_url` | Avatar en los rankings y perfil |
| `popularidad` | Índice dinámico de visibilidad de TMDB, mostrado en benchmarks |
| `biografia` | Texto expandible en el perfil de persona |
| `fecha_nacimiento`, `lugar_nacimiento` | Metadata del perfil |
| `imdb_id` | Enlace externo a IMDb |
