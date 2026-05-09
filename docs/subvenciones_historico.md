# Página: Subvenciones Históricas al Cine Español

> Documentación técnica de la ruta `/subvenciones-historico`.
> Última actualización: mayo 2026.

---

## Descripción general

La página **Histórico Subvenciones** visualiza la evolución de las ayudas públicas del ICAA a la producción de largometrajes españoles desde 2003 hasta la actualidad, complementada con la serie histórica de espectadores y recaudación del cine español. Es una página estática en el sentido de que no accede a la base de datos PostgreSQL: toda la información procede de ficheros CSV editables ubicados en `webapp/data/`.

---

## Ruta y controlador

```
GET /subvenciones-historico
```

Función en `webapp/app.py`:

```python
@app.route('/subvenciones-historico')
def subvenciones_historico():
    chart_data, stats, hitos = get_subvenciones_historico()
    return render_template('subvenciones_historico.html',
                           chart_data=chart_data,
                           stats=stats,
                           hitos=hitos)
```

La lógica de carga y fusión de datos está encapsulada en `get_subvenciones_historico()`.

---

## Ficheros de datos

La función lee cuatro CSV en `webapp/data/`. Todos son editables directamente en el servidor sin necesidad de tocar el código Python.

### 1. `subvenciones_historico.csv`

Fuente principal. Contiene el detalle **película a película** de las subvenciones concedidas por el ICAA, correspondiente al periodo **2015–2023**. Se generó a partir del scraper ICAA y fue revisado manualmente para corregir columnas intercambiadas en determinadas filas.

| Columna | Tipo | Descripción |
|---|---|---|
| `titulo` | texto | Título de la película |
| `importe_ayuda` | texto (€) | Importe de la subvención concedida, con separadores de miles y símbolo € |
| `presupuesto_proyecto` | texto (€) | Presupuesto declarado del proyecto |
| `tipo_ayuda` | texto | Categoría: `generales`, `selectivas`, `amortización`, `produccion` |
| `anio_ayuda` | texto | Año de la convocatoria (2015–2023) |

El importe viene formateado (ej.: `1.234.567,89 €`). Se parsea con:
```python
value.replace('€','').replace('.','').replace(',','.').strip()
```

### 2. `subvenciones_agregadas.csv`

Fichero de entrada manual para los años que **no tienen dato por película**: actualmente 2003–2014 y 2024–2025. Permite completar el gráfico histórico sin necesidad de desglose individual.

| Columna | Tipo | Descripción |
|---|---|---|
| `anio` | entero | Año |
| `generales` | número | Total ayudas generales en euros (sin formato) |
| `selectivas` | número | Total ayudas selectivas en euros |
| `amortizacion` | número | Total amortización en euros |
| `produccion` | número | Total producción piloto en euros |

Reglas de carga:
- Si el año ya existe en `subvenciones_historico.csv`, la fila de este fichero se ignora completamente (el dato detallado tiene prioridad).
- Si todos los valores son cero o vacíos, la fila también se ignora (permite dejar filas en blanco para años pendientes).

### 3. `espectadores_nacionalidad.csv`

Serie anual de espectadores de películas de **nacionalidad española** en salas, en millones. Editable para añadir o corregir datos.

| Columna | Tipo | Descripción |
|---|---|---|
| `anio` | entero | Año |
| `espectadores_esp_millones` | decimal | Espectadores en millones |

Fuente: [Estadística de Cinematografía — Espectadores por nacionalidad](https://estadisticas.cultura.gob.es/CulturaJaxiPx/Tabla.htm?path=/t20/p20/a2005/l0/&file=T2001006.px&L=0) (Ministerio de Cultura, tabla T2001006).

Para **2015–2023** los datos proceden del desglose de las Memorias Anuales del ICAA. Para **2003–2014 y 2024–2025** son agregados anuales descargados directamente de la tabla estadística del Ministerio.

### 4. `recaudacion_historico.csv`

Serie anual de recaudación del cine español en salas, en millones de euros.

| Columna | Descripción |
|---|---|
| Primera columna | Año |
| Segunda columna | Recaudación en M€ |

La carga se hace **por posición de columna** (no por nombre), ya que el fichero puede tener un encabezado heredado incorrecto. Esto permite editarlo libremente sin preocuparse del nombre exacto de la cabecera.

Fuente: [Estadística de Cinematografía — Recaudación por nacionalidad](https://estadisticas.cultura.gob.es/CulturaJaxiPx/Tabla.htm?path=/t20/p20/a2005/l0/&file=T2001009.px&L=0) (Ministerio de Cultura, tabla T2001009).

---

## Lógica de fusión de datos

`get_subvenciones_historico()` devuelve una lista `chart_data` donde cada elemento es un diccionario con todos los valores de un año. El conjunto de años cubiertos es la **unión** de los años presentes en los cuatro CSV:

```python
all_years = sorted(
    set(int(y) for y in by_year.keys()) |   # subvenciones (detalle + agregado)
    set(espectadores_esp.keys()) |           # espectadores
    set(recaudacion_esp.keys())              # recaudación
)
```

Esto garantiza que un año aparece en el gráfico aunque solo tenga dato de espectadores (sin subvenciones conocidas) o solo recaudación. Los campos sin dato se emiten como `None` (→ `null` en JSON), lo que Chart.js interpreta como punto vacío en la línea y barra ausente.

Estructura de cada elemento de `chart_data`:

| Campo | Tipo | Descripción |
|---|---|---|
| `anio` | int | Año |
| `generales` | int | Ayudas generales en € (redondeado) |
| `selectivas` | int | Ayudas selectivas en € |
| `amortizacion` | int | Amortización en € |
| `count` | int | Número de películas con subvención (0 si es dato agregado) |
| `espectadores_esp` | float o None | Espectadores en millones |
| `recaudacion_esp` | float o None | Recaudación en M€ |

> **Nota:** el campo `produccion` fue eliminado. Las 27 películas de 2015 que originalmente tenían `tipo_ayuda = 'producción'` (la prueba piloto de ayudas anticipadas) fueron reclasificadas a `generales` directamente en `subvenciones_historico.csv`. `subvenciones_agregadas.csv` tampoco incluye la columna `produccion`.

---

## Variables de plantilla

La función devuelve tres objetos que la ruta pasa al template:

### `chart_data`
Lista de dicts descrita arriba. Se serializa como JSON en el template con el filtro Jinja2 `tojson`:
```html
const chartData = {{ chart_data | tojson }};
```

### `stats`
Dict con KPI globales calculados **solo sobre años con datos de subvenciones** (excluye años que solo tienen espectadores o recaudación):

| Clave | Descripción |
|---|---|
| `total_pelis` | Total de películas con subvención en el periodo detallado |
| `total_importe` | Suma total de subvenciones en € |
| `max_anio` | Año con mayor volumen de ayudas |
| `max_importe` | Importe de ese año máximo en € |
| `cagr_pct` | CAGR de subvenciones 2003–2025, suavizado por ventanas de 3 años (ver más abajo) |

#### Cálculo del CAGR suavizado

En lugar de comparar un único año inicial con un único año final (lo que haría el resultado sensible a anomalías puntuales), se usan las **medias de los tres primeros y tres últimos años** de la serie:

```
media_inicio = media(total_2003, total_2004, total_2005)
media_fin    = media(total_2023, total_2024, total_2025)
n            = 20  # años entre medianas de las dos ventanas (2004 → 2024)

CAGR = (media_fin / media_inicio)^(1/n) − 1
```

El punto medio de la ventana inicial es 2004 y el de la final es 2024, de ahí que `n = 20`. El resultado se redondea a un decimal y se muestra en la tarjeta KPI como "+X,X% · CAGR · media 3 años inicio y fin".

Con los datos actuales (mayo 2026):
- Media 2003–2005 ≈ 44,9 M€ (dominada por la amortización)
- Media 2023–2025 ≈ 92 M€ (generales + selectivas)
- **CAGR ≈ +3,6% anual**

### `hitos`
Lista de dicts `{anio, texto}` con comentarios editoriales año a año (2015–2023), codificados directamente en `app.py`.

---

## Gráfico (Chart.js)

El gráfico es de tipo **mixto**: barras apiladas + línea sobre eje secundario.

### Datasets

| Dataset | Tipo | Eje | Stack | Color |
|---|---|---|---|---|
| Generales | bar | y (izq.) | `importe` | `#1a3a6e` (azul oscuro) |
| Selectivas | bar | y (izq.) | `importe` | `#f5c518` (dorado) |
| Amortización | bar | y (izq.) | `importe` | `#9e9e9e` (gris) |
| Recaudación cine español | bar | y (izq.) | `recaudacion` | `rgba(230,81,0,0.75)` (naranja) |
| Espectadores cine español | line | yEsp (dcha.) | — | `#2e7d32` (verde) |

Los tres tipos de subvención forman un **grupo apilado** (`stack: 'importe'`), mientras que la recaudación usa un stack independiente (`stack: 'recaudacion'`), lo que hace que ambos grupos aparezcan como columnas contiguas para el mismo año. El dataset "Producción piloto" fue eliminado: las filas afectadas en 2015 se reclasificaron a "Generales" en el CSV.

### Ejes

- **y** (izquierdo): subvenciones y recaudación en millones de €. Tick callback: `v + ' M€'`.
- **yEsp** (derecho): espectadores en millones. Máximo dinámico: `Math.ceil(max / 5) * 5 + 5`. Color verde para diferenciar.

### Tooltip

- Entradas del eje `yEsp`: formato `X,X M` (millones de personas).
- Entradas de barras: formato `X,X M€`. Se suprimen (`return null`) cuando el valor es cero.
- Footer: suma solo las barras del stack `importe` (excluye `yEsp` y el stack `recaudacion`) y muestra "Total subvenciones: X,X M€".

---

## Cómo añadir o corregir datos

### Añadir datos de un año sin desglose por película
Editar `webapp/data/subvenciones_agregadas.csv` e introducir los valores anuales en euros. Si el año ya aparece en `subvenciones_historico.csv` (2015–2023), la fila se ignorará automáticamente.

### Actualizar espectadores o recaudación
Editar `webapp/data/espectadores_nacionalidad.csv` o `webapp/data/recaudacion_historico.csv` añadiendo o modificando la fila correspondiente. Los datos se descargan de las tablas del Ministerio de Cultura indicadas en la sección de fuentes.

### Añadir un hito editorial
Modificar la lista `hitos` dentro de `get_subvenciones_historico()` en `app.py`. Cada hito es un dict `{'anio': YYYY, 'texto': '...'}`.

---

## Dependencias de front-end

La página es independiente de las demás y no comparte JavaScript con el resto de la app. Utiliza:

- Bootstrap 5.3.3 (CDN)
- Bootstrap Icons 1.11.3 (CDN)
- Chart.js 4.4.7 (CDN)

No hay llamadas AJAX ni al endpoint `/api/`. Todo el dato llega serializado en el HTML inicial vía `tojson`.
