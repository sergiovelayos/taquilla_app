# Métricas de Eficiencia del Gasto Público en Cine

> Documentación de los indicadores utilizados en la calculadora de benchmarks (`/calculadora`).
> Última actualización: mayo 2026.

---

## Contexto

La calculadora compara películas, directores, actores y géneros en función de cómo han
aprovechado las subvenciones públicas del ICAA. Solo se consideran las películas con
`subvenciones_total_eur IS NOT NULL` (aproximadamente 721 registros con datos completos).

---

## Métricas principales

### 1. esp/k€ — Espectadores por cada 1.000€ de subvención

**Fórmula:**
```
(espectadores / subvenciones_total_eur) × 1.000
```

**Qué mide:** el alcance de público generado con cada mil euros de dinero público invertido.
A mayor valor, más espectadores ha conseguido la película por cada unidad de subvención recibida.

**Ejemplo:** una película con 200.000 espectadores y 500.000€ de subvención obtiene
`(200.000 / 500.000) × 1.000 = 400 esp/k€`.

**Uso en la app:**
- Indicador global de la industria (media ponderada de todas las películas).
- Criterio de ordenación principal en los rankings Top 50 / Bottom 50.
- Badge principal (verde / rojo) en cada fila del ranking.

**Formato de visualización:** entero redondeado, sin decimales (`400 esp/k€`).

---

### 2. rec/€ — Retorno en taquilla por euro de subvención

**Fórmula:**
```
recaudacion_eur / subvenciones_total_eur
```

**Qué mide:** los euros de taquilla generados por cada euro de subvención recibida.
Es una medida del retorno económico directo de la inversión pública.
Un valor de `2x` significa que la película recaudó el doble de lo que recibió en subvenciones.

**Ejemplo:** una película con 1.000.000€ de recaudación y 500.000€ de subvención
obtiene `1.000.000 / 500.000 = 2x rec/€`.

**Uso en la app:**
- Badge secundario (tono suave) junto al badge principal en los rankings Top 50 / Bottom 50.
- Solo se muestra cuando hay dato de recaudación disponible.

**Formato de visualización:** entero redondeado, sin decimales (`2x rec/€`).

---

## Diferencia entre las dos métricas

| Situación | esp/k€ | rec/€ |
|---|---|---|
| Película de arte y ensayo: mucho público, precio de entrada bajo | Alto | Bajo |
| Blockbuster subvencionado: pocos espectadores, alta taquilla por entrada | Bajo | Puede ser alto |
| Documental sin estreno comercial: pocas entradas vendidas | Bajo | Bajo |
| Comedia popular: mucho público y buena recaudación | Alto | Alto |

Las dos métricas son complementarias. Una película puede ser eficiente en alcance de
público (esp/k€ alto) pero con retorno económico bajo, o viceversa.

---

## Umbrales de filtrado

Para evitar distorsiones estadísticas por casos atípicos o datos incompletos:

| Ranking | Filtro aplicado |
|---|---|
| Top 50 (mayor alcance) | `subvenciones_total_eur > 5.000` y `espectadores > 0` |
| Bottom 50 (menor alcance) | `subvenciones_total_eur > 50.000` y `espectadores IS NOT NULL` |
| Rankings de directores / actores (Top/Bottom 20) | Mínimo 2 películas con datos |
| Rankings de géneros (Top/Bottom 20) | Mínimo 5 películas con datos |

El umbral más alto en el Bottom 50 (50.000€) es intencional: excluye microsubvenciones
donde una sola ayuda pequeña con cero espectadores dominaría artificialmente el ranking
de peor rendimiento.

---

## Dónde aparecen estas métricas

| Ubicación | Métrica mostrada |
|---|---|
| `/calculadora` — indicador global | Media global en esp/k€ |
| `/calculadora` — Top 50 mayor alcance | Badge principal: esp/k€ · Badge secundario: rec/€ |
| `/calculadora` — Top 50 menor alcance | Badge principal: esp/k€ · Badge secundario: rec/€ |
| `/calculadora` — rankings directores/actores/géneros (Top/Bottom 20) | Badge principal: esp/k€ · Badge secundario: rec/€ |
| `/pelicula/<id>` — ficha individual | esp/€ (sin escalar, dato exacto de la película) |

---

## Fuente de datos

- **Subvenciones:** campo `subvenciones_total_eur` de la tabla `icaa_fichas`, suma de todas
  las líneas de ayuda pública registradas en el catálogo del ICAA.
- **Espectadores:** campo `espectadores` de `icaa_fichas`, dato oficial del ICAA.
- **Recaudación:** campo `recaudacion_eur` de `icaa_fichas`, dato oficial del ICAA.
