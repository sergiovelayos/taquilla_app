# Métricas de Eficiencia del Gasto Público en Cine

> Documentación de los indicadores utilizados en la calculadora de benchmarks (`/calculadora`).
> Última actualización: mayo 2026.

---

## Contexto

La calculadora permite analizar el impacto de las subvenciones públicas del ICAA comparando películas individuales, directores, actores y géneros. Solo se consideran las películas con
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

## Restricción de Antigüedad

Para garantizar que los benchmarks sean justos, solo se incluyen películas con un recorrido comercial consolidado. Se aplica un filtro de **mínimo 2 meses de antigüedad** con la siguiente lógica de precedencia:

1.  **Si existe fecha de estreno:** Debe haber pasado al menos 2 meses desde el estreno (`fecha_estreno < hoy - 60 días`).
2.  **Si no hay fecha de estreno:** Se utiliza el año de producción, el cual debe ser estrictamente anterior al año actual (`anio_produccion < año_actual`).
3.  **Si ambos son desconocidos:** La película se excluye de los promedios y rankings.

Esta restricción aplica a la media global, los rankings (Top/Bottom 50 y Top 20) y al resumen de impacto de directores y actores.

---

## Búsqueda Normalizada

El buscador de la calculadora utiliza un motor de **normalización de texto al vuelo** para facilitar la localización de registros. Esto permite:
- **Ignorar acentos:** "agora" encontrará "Ágora".
- **Ignorar mayúsculas:** "ALMODOVAR" encontrará "Almodóvar".
- **Ignorar caracteres especiales:** "fernandez armero" encontrará "Fernández-Armero".
- **Búsquedas parciales:** "padre no hay mas" encontrará "Padre no hay más que uno".

La base de datos original no se modifica; la normalización solo se aplica durante el proceso de comparación de búsqueda.

---

## Umbrales de filtrado

Para evitar distorsiones estadísticas por casos atípicos o datos incompletos:

| Ranking | Filtro aplicado |
|---|---|
| Todos los Benchmarks | **Restricción de antigüedad (mín. 2 meses)** |
| Top 50 (mayor alcance) | `subvenciones_total_eur > 5.000` y `espectadores > 0` |
| Bottom 50 (menor alcance) | `subvenciones_total_eur > 50.000` y `espectadores IS NOT NULL` |
| Rankings de directores / actores | Mínimo 2 películas que cumplan la antigüedad |
| Rankings de géneros | Mínimo 5 películas que cumplan la antigüedad |

El umbral más alto en el Bottom 50 (50.000€) es intencional: excluye microsubvenciones
donde una sola ayuda pequeña con cero espectadores dominaría artificialmente el ranking
de peor rendimiento.

---

## Dónde aparecen estas métricas

| Ubicación | Métrica mostrada |
|---|---|
| `/calculadora` — indicador global | Media global en esp/k€ |
| `/calculadora` — búsqueda | Resumen de impacto individual y ratio de eficiencia (Búsqueda normalizada) |
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
