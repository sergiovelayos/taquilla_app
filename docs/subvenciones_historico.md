# Página: Subvenciones Históricas al Cine Español

> Documentación técnica de la ruta `/subvenciones-historico`.
> Última actualización: mayo 2026.

---

## Descripción general

La página **Histórico Subvenciones** visualiza la evolución de las ayudas públicas del ICAA a la producción de largometrajes españoles desde 2003 hasta la actualidad, complementada con la serie histórica de espectadores y recaudación del cine español. El gráfico principal sigue leyendo CSV editables desde `webapp/data/`, pero la tabla de detalle inferior se alimenta de PostgreSQL para poder enlazar cada título con su ficha ICAA cuando existe.

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

### Tabla de detalle `subvenciones`

La tabla interactiva de películas situada bajo el gráfico no usa el CSV en memoria, sino la tabla PostgreSQL `subvenciones`. Para mostrar enlaces a `/pelicula/<expediente_icaa>`, la consulta combina:

- `subvenciones.expediente_icaa`, cuando ya existe;
- `subvenciones_icaa_matches.expediente_icaa`, cuando el enlace se resolvió manualmente desde `/admin/matching`.

La relación manual usa `subvenciones_icaa_matches.titulo_subvencion = subvenciones.titulo` y se documenta en `docs/matching_web.md`.

Los IDs de la tabla puente pueden existir en el catálogo oficial del ICAA aunque todavía no estén importados en el subset local `icaa_fichas`. En ese caso la tabla muestra un enlace externo al catálogo ICAA; cuando sí hay ficha local, enlaza a `/pelicula/<id>`.

---

## Fuentes oficiales y tabla `subvenciones_raw`

Además de los CSV editables descritos arriba, la base de datos tiene una tabla auxiliar **`subvenciones_raw`** con el detalle película a película de las ayudas a la producción, cubriendo **2006–2025** (2055 registros). Es la fuente de referencia de la que se derivan (o se corrigen) los datos anuales que consumen `subvenciones_historico.csv` y `subvenciones_agregadas.csv`.

### Esquema

```sql
CREATE TABLE subvenciones_raw (
    id SERIAL PRIMARY KEY,
    anio INTEGER,
    tipo_ayuda TEXT,
    titulo TEXT,
    importe_ayuda FLOAT,
    created_at TIMESTAMP DEFAULT NOW(),
    last_updated TIMESTAMP DEFAULT NOW()
);
```

`tipo_ayuda` toma valores como `generales`, `selectivas` y `amortizacion`, análogos a las categorías usadas en el resto del pipeline de subvenciones.

### Metodología de dos fuentes según el año

Los datos de `subvenciones_raw` combinan **dos fuentes distintas** según el año, priorizando siempre la más detallada disponible:

**2006–2017 → Memorias/Anuarios de ayudas a la cinematografía (fuente procesada).**
El ICAA publica anualmente una "Memoria de ayudas a la cinematografía" en PDF, con doce secciones por año (presentación y presupuesto, ayudas generales y selectivas a largometrajes, cortometrajes, distribución, festivales, salas de exhibición, laboratorios, transferencias corrientes, subvenciones nominativas, resumen por empresa). Para estos años **no existe** una página de resolución oficial navegable con el mismo nivel de detalle que a partir de 2018, así que se usa el desglose ya procesado de estos PDFs.

Enlaces de referencia (estructura de las memorias, común a todos los años del periodo):
- [Presentación y presupuesto](https://www.cultura.gob.es/dam/jcr:a6996342-349d-4c0a-bbd5-220a39447a7c/1-presentacion-presupuesto.pdf)
- [Ayudas generales a la producción de largometrajes sobre proyecto](https://www.cultura.gob.es/dam/jcr:444f9499-aded-4c59-9374-58ba3b23057f/2-prodlargoggenerales.pdf)
- [Ayudas selectivas a la producción de largometrajes sobre proyecto](https://www.cultura.gob.es/dam/jcr:30879fe3-2bb7-426a-817b-ffca909f6325/3-prodlargoselectivas.pdf)
- [Ayudas a la producción de cortometrajes sobre proyecto](https://www.cultura.gob.es/dam/jcr:68b6a65c-8173-42db-beac-837f303f5fa2/4-cortorpoyecto.pdf)
- [Ayudas a cortometrajes realizados](https://www.cultura.gob.es/dam/jcr:df273f20-7e49-4b81-8aca-09af498dc385/5-cortosrealizados.pdf)
- [Ayudas a la distribución de películas españolas, comunitarias e iberoamericanas](https://www.cultura.gob.es/dam/jcr:d28b6eed-4ec0-4138-b5fd-76e3a3cc8479/6-distribucion.pdf)
- [Ayudas a festivales](https://www.cultura.gob.es/dam/jcr:5e4dac6a-939f-4294-b088-dc785ee1c439/7-festivales.pdf)
- [Ayudas de concesión directa para titulares de salas de exhibición cinematográfica](https://www.cultura.gob.es/dam/jcr:cd658ac6-eaa4-44af-9951-d74ac05f1a87/8-salas-de-exhibicion.pdf)
- [Ayudas para laboratorios e incubadoras de creación y desarrollo de proyectos audiovisuales](https://www.cultura.gob.es/dam/jcr:dc50a54e-7c4e-4777-ad99-8a54602214c4/9-laboratorios.pdf)
- [Transferencias corrientes](https://www.cultura.gob.es/dam/jcr:4e414ff2-d2a3-4551-a645-98aa5c86faef/10-transferencias-corrientes.pdf)
- [Subvenciones nominativas y de concesión directa](https://www.cultura.gob.es/dam/jcr:4cfb23e6-084c-4894-bcba-60a0fa11efd6/11-subvenciones-nominativas-concesion-directa.pdf)
- [Resumen de ayudas obtenidas por las empresas](https://www.cultura.gob.es/dam/jcr:69416a2a-a759-432e-90a8-27362601e9cf/12-resumen-ayudas.pdf)

La página que agrupa las memorias cubre en origen 2006–2023, pero en `subvenciones_raw` solo se usa hasta **2017 inclusive**: a partir de 2018 se prioriza la fuente oficial por tener mayor detalle (ver siguiente punto).

**2018 en adelante → Resoluciones oficiales del Ministerio de Cultura (fuente primaria).**
Desde 2018 se usa directamente la página de [ayudas a la producción](https://www.cultura.gob.es/cultura/areas/cine/ayudas/produccion.html) del Ministerio, que publica una ficha de resolución por año y modalidad con el detalle completo de cada expediente:

- [Ayudas generales para la producción de largometrajes sobre proyecto](https://www.cultura.gob.es/cultura/areas/cine/ayudas/produccion/generales.html) — ayudas anticipadas por criterios objetivos. Fichas anuales: [2025](https://www.cultura.gob.es/servicios-a-la-ciudadania/catalogo/general/20/202995/ficha/202995-2025.html), [2024](https://www.cultura.gob.es/servicios-a-la-ciudadania/catalogo/general/20/202995/ficha/202995-2024.html), [2023](https://www.cultura.gob.es/servicios-a-la-ciudadania/catalogo/general/20/202995/ficha/202995-2023.html), [2022](https://www.cultura.gob.es/servicios-a-la-ciudadania/catalogo/general/20/202995/ficha/202995-2022.html), [2021](https://www.cultura.gob.es/servicios-a-la-ciudadania/catalogo/general/20/202995/ficha/202995-2021.html), [2020](https://www.cultura.gob.es/servicios-a-la-ciudadania/catalogo/general/20/202995/ficha/202995-2020.html), [2019](https://www.cultura.gob.es/servicios-a-la-ciudadania/catalogo/general/20/202995/ficha/202995-2019.html) (2026 en curso, plazo de solicitud abierto).
- [Ayudas selectivas para la producción de largometrajes sobre proyecto](https://www.cultura.gob.es/servicios-a-la-ciudadania/catalogo/general/05/051790.html) — ayudas a empresas productoras independientes con valor cinematográfico, cultural o social especial (documental, experimental o nuevos realizadores), previo informe del órgano colegiado. Fichas anuales: [2025](https://www.cultura.gob.es/servicios-a-la-ciudadania/catalogo/general/05/051790/ficha/051790-2025.html), [2024](https://www.cultura.gob.es/servicios-a-la-ciudadania/catalogo/general/05/051790/ficha/051790-2024.html), [2023](https://www.cultura.gob.es/servicios-a-la-ciudadania/catalogo/general/05/051790/ficha/051790-2023.html), [2022](https://www.cultura.gob.es/servicios-a-la-ciudadania/catalogo/general/05/051790/ficha/051790-2022.html), [2021](https://www.cultura.gob.es/servicios-a-la-ciudadania/catalogo/general/05/051790/ficha/051790-2021.html), [2020](https://www.cultura.gob.es/servicios-a-la-ciudadania/catalogo/general/05/051790/ficha/051790-2020.html), [2019](https://www.cultura.gob.es/servicios-a-la-ciudadania/catalogo/general/05/051790/ficha/051790-2019.html), [2018](https://www.cultura.gob.es/servicios-a-la-ciudadania/catalogo/general/05/051790/ficha/051790-2018.html) (2026 pendiente de resolución).

### Relación con el resto del pipeline

`subvenciones_raw` es un volcado de trabajo/staging, no la tabla que consume la web directamente (esa sigue siendo `subvenciones`, ver más abajo). Se usa para: comparar/auditar los totales anuales de `subvenciones_agregadas.csv` y el detalle de `subvenciones_historico.csv`, y como base para futuras cargas hacia `subvenciones` cuando se complete el proceso de matching con `icaa_fichas`.

### Matching `subvenciones_raw` × `scrape_icaa` × `subvenciones`

Para vincular cada título de `subvenciones_raw` con su expediente ICAA se hizo un cruce en dos
pasadas (normalización de título estilo `TITLE_NORM_SQL`, ver `docs/matching_web.md`):

1. Título normalizado contra `scrape_icaa` → único candidato, varios candidatos ambiguos
   (desempatados por año de producción más cercano) o ningún candidato.
2. Para lo que no dio match único, cruce adicional contra `subvenciones` (que ya trae
   `expediente_icaa` curado en ~474 filas) — se comprobó que cuando esta fuente está disponible,
   discrepa siempre del candidato elegido solo por año, así que se prioriza sobre el heurístico.

De los 1988 títulos distintos, esto deja ~612 sin resolver de forma fiable (ambiguos sin
confirmar y sin ningún candidato) que requieren revisión manual.

**Herramienta de revisión: artifact "Matching subvenciones_raw × scrape_icaa × subvenciones"**

Es una página HTML autocontenida publicada en claude.ai (no vive en el repo ni en el servidor),
con una tabla por categoría (match único, ambiguo resuelto por subvenciones, ambiguo resuelto
por año, ambiguo sin resolver, sin match resuelto por subvenciones, sin match definitivo),
buscador y orden por columna. Las tres categorías sin resolución fiable tienen un campo de
texto para teclear el expediente ICAA correcto (o marcarlo como "sin ficha" si se confirma que
no existe), y un botón "Generar SQL" que produce los `INSERT ... ON CONFLICT` listos para
`subvenciones_icaa_matches`.

- Las ediciones se guardan en el `localStorage` del navegador (por dispositivo/perfil, no
  sincronizado) — el SQL generado hay que copiarlo o descargarlo y aplicarlo a mano contra la
  base de datos; el artifact no escribe directo en Postgres.
- URL: (privada, pedir enlace en la conversación donde se creó; se puede seguir actualizando en
  el mismo enlace en conversaciones futuras).

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
