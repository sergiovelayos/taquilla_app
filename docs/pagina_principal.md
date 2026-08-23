# Taquilla Semanal + Histórico Taquilla

> Documentación técnica de las rutas `/` y `/historico-taquilla`
> (`webapp/templates/index.html` y `webapp/templates/historico_taquilla.html`).
> Última actualización: julio 2026.

---

## Dos páginas separadas

Lo que empezó como una sola portada larga es ahora **dos páginas distintas**,
enlazadas entre sí desde la navbar:

1. **Taquilla Semanal** (`GET /`, `index.html`) — todo lo relativo al fin de
   semana concreto seleccionado: ranking semanal, concentración de mercado,
   aforo/ocupación de salas, mayores estrenos y el buscador de películas con
   su gráfico de evolución.
2. **Histórico Taquilla** (`GET /historico-taquilla`, `historico_taquilla.html`)
   — rankings acumulados y series que no dependen de una semana concreta: Top
   del año + Top histórico, el Informe Anual Oficial ICAA y la Distribución
   Percentil por Año.

Antes todo vivía en una sola plantilla (`index.html`), con "Histórico
Taquilla" como una sección visual dentro de la misma página. Se separó en una
ruta y una plantilla propias porque son casos de uso distintos: una se
consulta cada semana, la otra rara vez cambia. `historico_taquilla.html` es
una copia del `<head>`/`<style>` de `index.html` (mismo sistema de diseño)
con un `<main>` propio — no hereda de una plantilla base porque `index.html`
tampoco lo hacía.

## Selectores y rankings de Histórico Taquilla

```
GET /?tab=top25                        → Taquilla Semanal, tabla top25
GET /?tab=topespanol                   → Taquilla Semanal, tabla topespanol
GET /historico-taquilla?year=<anio>    → Histórico Taquilla, año seleccionado
```

El selector `Toda la Cartelera / Cine Español` existe solo en **Taquilla
Semanal**. Determina la tabla origen (`top25` o `topespanol`) para el ranking
semanal, concentración de mercado, aforo, gráfico de asistencia anual,
mayores estrenos y buscador de películas.

En **Histórico Taquilla** ya no hay selector de mercado. Hay dos tarjetas
separadas para cada año:

- `Más taquilleras — cine español`
- `Más taquilleras — cine extranjero`

Ambas se ordenan por **espectadores** de forma descendente. La recaudación se
muestra como dato secundario, pero no define el orden.

### Fuentes del bloque "Películas estrenadas en"

Para cine español:

- Hasta 2025 se usa `anual_esp`, ampliada con datos de anuarios anteriores a
  2016 sin cambiar su estructura. Fuente pública de referencia:
  [Histórico de taquilla y espectadores en España](https://www.cultura.gob.es/cultura/areas/cine/datos/taquilla-espectadores.html).
- Para 2026, mientras no exista cierre anual oficial en `anual_esp`, se usa
  `topespanol`. Se agrupa por título normalizado y se ordena por el máximo de
  espectadores acumulados disponible; si no hay acumulado se usa la suma de
  espectadores semanales.

Para cine extranjero:

- Hasta 2023 se usan `anuarios_silver` y las tablas raw de anuarios:
  `anuarios_03_17_raw` y `anuarios_extranjeras_18_23_raw`. Fuente pública:
  [Anuarios de Cine del Ministerio](https://www.cultura.gob.es/cultura/areas/cine/mc/anuario-cine/portada.html).
- Para 2024, 2025 y 2026 se usa `top25`, excluyendo títulos identificados como
  españoles en `anual_esp` o `topespanol`. Se agrupa por título normalizado y
  se ordena por espectadores acumulados.

La fecha mostrada en los rankings semanales de fallback es la primera semana
en la que la película aparece en la tabla semanal, no necesariamente la fecha
oficial de estreno.

### Top histórico por espectadores

El Top histórico se alimenta de `anuarios_gold`, calculada desde
`anuarios_silver`. Se muestra una sola fila por película y nacionalidad:

- La clave de película se basa en título normalizado y grupo de nacionalidad.
- Para el histórico se conserva el mayor dato de espectadores disponible; si
  hay empate, se prefiere el anuario más reciente.
- Las películas extranjeras de los anuarios 2018-2023 no traen país concreto;
  en `anuarios_silver` se marcan como `pais = 'No Spain'` y en gold se
  clasifican como `extranjera`.
- Los anuarios cargados llegan hasta 2023.

## Mayores Estrenos — Top 10

Nueva sección al final de "Taquilla Semanal". Lista las 10 películas con más
espectadores en su **primer** fin de semana en cartelera (`semana = 1`),
dentro de la tabla seleccionada (`top25`/`topespanol`).

Backend: `get_top_estrenos(table, limit=10)` en `webapp/app.py`:

```sql
SELECT titulo, distribuidora, fecha_inicio, fecha_fin,
       recaudacion, total_espectadores, cines, pantallas
FROM {table}
WHERE semana = 1
ORDER BY total_espectadores DESC NULLS LAST
LIMIT %s
```

Se pasa a la plantilla como `top_estrenos` y se renderiza con el mismo
componente visual `.top-list` que ya usaban Top año / Top histórico.

## Buscador de películas + evolución semanal

Bloque al final de "Taquilla Semanal" (`id="pelSearchBlock"`). A diferencia
del buscador de `anual_esp` que hay en Histórico Taquilla (que busca en los
informes anuales oficiales), este busca **solo en `top25` o `topespanol`** —
la tabla semanal seleccionada — y al elegir una película dibuja un gráfico de
líneas con su recorrido semana a semana en cartelera.

### Backend

```
GET /api/pelicula_semanal/buscar?tab=top25|topespanol&q=<texto>
```
Busca por `titulo ILIKE` o `distribuidora ILIKE` en la tabla seleccionada,
agrupado por (`titulo`, `distribuidora`), devolviendo hasta 30 resultados
ordenados por recaudación acumulada. Cada resultado trae un resumen: fecha de
estreno, semanas en cartelera, recaudación y espectadores totales, mejor rank.

```
GET /api/pelicula_semanal/evolucion?tab=top25|topespanol&titulo=<t>&distribuidora=<d>
```
Devuelve la serie semanal completa (`fecha_inicio`, `fecha_fin`, `semana`,
`rank`, `recaudacion`, `espectadores`, `cines`) de esa película concreta,
ordenada por `fecha_inicio`. `titulo` + `distribuidora` identifican la
película de forma unívoca dentro de la tabla (no hay un id de fila estable).

### Frontend

Buscador con debounce de 280ms (mismo patrón que el buscador de `anual_esp`).
Al hacer clic en un resultado, se llama al endpoint de evolución y se dibuja
un `Chart.js` de tipo `line` con doble eje:

- Eje izquierdo (`yRec`, rojo): recaudación en €.
- Eje derecho (`yEsp`, azul, discontinuo): espectadores.

El tooltip muestra semana, rango de fechas, recaudación + rank, y
espectadores + nº de cines. Un botón "Cerrar" destruye la instancia de
Chart.js y oculta el bloque (`pelChart.destroy()`), para poder buscar otra
película sin acumular instancias de gráfico.

---

## Funciones y rutas (`webapp/app.py`)

| Función / ruta | Página | Responsabilidad |
|---|---|---|
| `index()` | `GET /` | Taquilla Semanal completa |
| `historico_taquilla()` | `GET /historico-taquilla` | Rankings Acumulados + Informe Anual ICAA + Distribución Percentil |
| `get_top_estrenos(table, limit)` | Taquilla Semanal | Top N estrenos por espectadores en semana 1 |
| `api_pelicula_semanal_buscar()` | Taquilla Semanal | `GET /api/pelicula_semanal/buscar` |
| `api_pelicula_semanal_evolucion()` | Taquilla Semanal | `GET /api/pelicula_semanal/evolucion` |
| `get_anuarios_years()` | Histórico Taquilla | Años disponibles para el selector anual, combinando anuarios y tablas semanales |
| `get_anuarios_annual_top(anio, nacionalidad_grupo, limit)` | Histórico Taquilla | Top anual por espectadores para cine español/extranjero |
| `get_anuarios_historic_top(nacionalidad_grupo, limit)` | Histórico Taquilla | Top histórico por espectadores desde `anuarios_gold` |
| `get_anual_esp_years()` | Histórico Taquilla | Años disponibles en `anual_esp` |

`index()` ya **no** calcula ni pasa los rankings anuales/históricos. Esos
viven solo en `historico_taquilla()`.

## Cómo desplegar cambios en esta página

```bash
ssh ubuntu "cd /home/sergio/taquilla_app && docker-compose up -d --build"
```

Los scrapers en background (`scrape_icaa.py`, ver `docs/matching_web.md` y
CLAUDE.md) corren en el host vía `nohup`, no dentro del contenedor, así que
un rebuild de `taquilla-webapp` no los afecta.
