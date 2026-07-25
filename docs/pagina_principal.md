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

## El selector Toda la Cartelera / Cine Español

```
GET /?tab=top25                        → Taquilla Semanal, tabla top25
GET /?tab=topespanol                   → Taquilla Semanal, tabla topespanol
GET /historico-taquilla?tab=top25      → Histórico Taquilla, tabla top25
GET /historico-taquilla?tab=topespanol → Histórico Taquilla, tabla topespanol
```

El selector existe en **ambas páginas** de forma independiente (cada una lee
su propio `?tab=` de la URL; no hay estado compartido entre páginas al
navegar entre ellas). Determina la tabla origen (`top25` o `topespanol`):

- En **Taquilla Semanal**: ranking semanal, concentración de mercado, aforo,
  gráfico de asistencia anual, curva de decaimiento, mayores estrenos y el
  buscador de películas.
- En **Histórico Taquilla**: Top año y Top histórico (Rankings Acumulados).

**Excepción**: el *Informe Anual Oficial ICAA* y la *Distribución Percentil
por Año*, dentro de Histórico Taquilla, usan siempre `anual_esp` (los
informes oficiales anuales del ICAA, que solo cubren cine español) — son
independientes de ese selector, aunque estén en la misma página.

Para que esto quede claro sin tener que deducirlo:

- Cada página tiene un aviso o nota junto al selector explicando qué cambia.
- Los encabezados de las secciones que sí dependen del selector llevan un
  badge oscuro con el valor actual (`Toda la Cartelera` / `Cine Español`).
- Los encabezados del Informe Anual y la Distribución Percentil llevan un
  badge claro con el texto "independiente del selector".

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
| `get_top_year(table, year, limit)` | Histórico Taquilla | Top del año seleccionado |
| `get_top_historico(table, limit)` | Histórico Taquilla | Top histórico acumulado |
| `get_anual_esp_years()` | Histórico Taquilla | Años disponibles en `anual_esp` |

`index()` ya **no** calcula ni pasa `top_year`, `top_historico`,
`selected_year`, `available_years` ni `anual_years` — esos viven ahora solo
en `historico_taquilla()`. Al mover el contenido se detectó y corrigió un bug
de paso: el script del buscador de películas semanales había quedado
envuelto sin querer dentro del `{% if anual_years %}` de la sección anual, así
que si esa variable llegaba vacía, el buscador dejaba de cargar su JS aunque
su HTML no dependía de `anual_years` en absoluto. Ahora ese script es
incondicional en `index.html`, como corresponde.

## Cómo desplegar cambios en esta página

```bash
ssh ubuntu "cd /home/sergio/taquilla_app && docker-compose up -d --build"
```

Los scrapers en background (`scrape_icaa.py`, ver `docs/matching_web.md` y
CLAUDE.md) corren en el host vía `nohup`, no dentro del contenedor, así que
un rebuild de `taquilla-webapp` no los afecta.
