# Guía de matching — `/admin/matching`

> Documentación de la herramienta de resolución manual de matchings.
> Última actualización: julio 2026.

---

## Qué es esto y por qué existe

El proyecto cruza datos de varias fuentes independientes (ICAA, TMDB, Comscore,
subvenciones) que no comparten un identificador común — solo el **título de la
película** o el **nombre de la persona**, escritos de forma ligeramente distinta
en cada sitio (acentos, artículos al final tipo "Tribu, La", mayúsculas,
apodos...). El cruce automático por título normalizado resuelve la mayoría de
los casos, pero deja un resto que necesita ojo humano: títulos ambiguos
(varias películas con el mismo nombre), títulos que cambiaron entre el
proyecto y el estreno, o nombres de actor/director escritos de formas distintas.

`/admin/matching` es la página donde se revisan y corrigen esos casos a mano,
sin tocar terminal ni SQL. Está pensada para que cualquiera (no solo quien
programó esto) pueda sentarse, buscar una película o persona concreta, y
decirle al sistema "esto es el expediente X" o "esto no tiene ficha".

---

## Acceso

```text
GET /admin/matching
```

Si está definida la variable de entorno `MATCHING_ADMIN_TOKEN`, hace falta
añadir `?token=...` a la URL (o al `token` oculto de cada formulario, que ya
viaja solo). Si no está definida, la página es accesible sin token — pero como
permite escribir en la base de datos, en un entorno expuesto a internet
conviene tenerla puesta.

---

## Cómo ayudar con matchings — guía rápida

Si alguien te pide "échame una mano revisando matchings", esto es lo que
necesitas saber, sin entrar en el código:

1. Abre `/admin/matching`. Verás 5 pestañas arriba, cada una con un contador
   de cuántos casos están pendientes de revisar.
2. Entra en la pestaña que te toque revisar (te lo dirán, o empieza por la que
   tenga más pendientes).
3. **Si buscas una película o persona concreta**: usa el buscador que hay
   arriba de cada tabla, escribe parte del título/nombre y pulsa "Buscar". Esto
   te deja revisar o corregir un caso puntual aunque no esté en la lista por
   defecto (por ejemplo, si ya tiene un candidato claro y quieres comprobarlo
   o corregirlo de todos modos).
4. Para cada fila, tienes casi siempre:
   - Un enlace de ayuda para buscar la ficha en el catálogo oficial (ICAA o
     TMDB), que abre en pestaña nueva.
   - Un campo de texto donde escribir el ID correcto (expediente ICAA o TMDB
     ID, según la pestaña).
   - Un botón verde (✓) para guardar.
   - Un botón gris ("sin ficha ICAA" / "sin TMDB") para los casos donde
     confirmas que **no existe** ficha — así no te lo vuelve a preguntar.
5. Rellena el campo y pulsa guardar. La página se recarga con un mensaje de
   confirmación y la fila desaparece de la cola.

**Importante:** si no estás seguro de un caso, mejor dejarlo sin tocar que
adivinar. Un expediente/ID incorrecto es peor que uno pendiente — luego es
más difícil de detectar.

---

## Las 5 pestañas

| Pestaña | Qué resuelve | Tabla de origen | Tabla puente |
|---|---|---|---|
| **Películas ICAA** | Títulos de `anual_esp` (resúmenes anuales de cine español, se actualiza cada año) sin ficha ICAA | `anual_esp` | columna `titulo_anual_esp` en `icaa_fichas` |
| **Personas TMDB** | Actores/directores con match TMDB dudoso, sin ID o sin foto | `tmdb_gente` | la propia `tmdb_gente` |
| **Subvenciones ICAA** | Títulos de `subvenciones` sin expediente ICAA | `subvenciones` | `subvenciones_icaa_matches` (por título) |
| **Subvenciones (raw)** | Filas de `subvenciones_raw` sin expediente ICAA | `subvenciones_raw` | `subvenciones_raw_icaa_matches` (por `id`) |
| **Películas TMDB** | Fichas de `icaa_fichas` sin `tmdb_id` vinculado | `icaa_fichas` | `pelicula_tmdb_match` (por `expediente_icaa`) |

Ninguna de estas pestañas modifica las tablas de origen (`anual_esp`,
`subvenciones`, `subvenciones_raw`, `icaa_fichas`, `scrape_icaa`, `tmdb_gente`
en su rol de catálogo): todo lo que se decide manualmente se guarda en una
tabla puente aparte. Esto es deliberado — las tablas de origen son volcados
reproducibles de cada fuente (scraping, importación de CSV...) y no deben
llevar mezclado el juicio manual de matching.

### Buscador y filtro por dificultad

Todas las pestañas tienen un buscador de título/nombre en la parte superior
de la tabla. Busca solo dentro de esa pestaña — no afecta a las demás.

La pestaña **Subvenciones (raw)** además calcula, para cada fila pendiente,
cuántos candidatos hay en `icaa_fichas` + `scrape_icaa` con el mismo título
normalizado:

- **0 candidatos** → badge rojo "sin candidato". Nadie ha encontrado nada
  parecido; puede que la ficha no esté todavía en el catálogo, o que el
  título cambiara mucho entre el proyecto subvencionado y el estreno (ver
  más abajo).
- **1 candidato** → badge verde "único". Ya está prácticamente resuelto — por
  eso **se oculta por defecto**, para no hacerte revisar cientos de casos que
  el sistema ya tiene claros. El campo del formulario viene precargado con
  ese candidato: solo hace falta pulsar guardar para confirmarlo.
- **2 o más candidatos** → badge amarillo "N ambiguos". Título genérico
  reutilizado por varias películas a lo largo de los años (ej. "MADRE",
  "FUEGO", "EL VIAJE"). Aquí sí hace falta mirar a mano cuál es la correcta
  — el enlace al catálogo ICAA ayuda a comparar por año/director.

Para ver también los de candidato único (por ejemplo, para revisarlos en
bloque o hacer una auditoría), hay un botón "Mostrar también los de
candidato único" encima de la tabla. Al buscar por título (`q`), este filtro
se desactiva automáticamente — la búsqueda siempre te deja ver cualquier fila,
esté resuelta o no.

---

## 1. Películas ICAA

Cola: títulos de `anual_esp` sin ficha ICAA asociada (ni por
`icaa_fichas.titulo_anual_esp` ni por título normalizado).

Al guardar un expediente ICAA, la ruta `POST /admin/matching/icaa` hace un
`INSERT ... ON CONFLICT` sobre `icaa_fichas` guardando
`titulo_anual_esp = título original de anual_esp`, para que el join futuro
funcione aunque el título oficial y el de `anual_esp` sean distintos.

`anual_esp` se sigue actualizando cada año por el pipeline normal (no forma
parte de este flujo de matching); esta pestaña solo consume esa tabla como
fuente de la cola pendiente.

---

## 2. Personas TMDB

Cola: filas de `tmdb_gente` con `revisado_manual = FALSE` y algún síntoma de
match dudoso (`tmdb_id` nulo, sin foto, sin score, o score bajo).

Acciones (`POST /admin/matching/persona`):

| Acción | Efecto |
|---|---|
| Guardar con TMDB ID | Actualiza el ID, marca `revisado_manual = TRUE` |
| Guardar sin ID | Marca la fila como revisada tal cual está |
| "Sin ficha TMDB" | Deja `tmdb_id = NULL` y marca revisado |

### Añadir variante de nombre (alias)

Debajo de la cola hay un formulario aparte: **"Añadir variante de nombre"**.
Es para cuando el mismo actor/director aparece escrito de forma distinta en
ICAA (ej. `"PEDRO ALMODOVAR"` en una ficha y `"Pedro Almodóvar C."` en otra) y
ya sabes a qué persona de TMDB corresponde. En vez de esperar a que esa
variante concreta aparezca en la cola de pendientes, la das de alta
directamente: nombre ICAA nuevo + TMDB ID ya conocido.

Esto funciona porque `tmdb_gente.tmdb_id` ya **no** tiene restricción de
unicidad — varias filas (variantes de nombre) pueden apuntar al mismo
`tmdb_id`. Si en el futuro se separa la ficha "rica" de la persona (biografía,
foto...) en una tabla canónica aparte (`personas_tmdb`) y `tmdb_gente` pasa a
ser solo la tabla de alias, este formulario seguirá funcionando igual.

Después de corregir IDs a mano sigue haciendo falta reejecutar el importador
para refrescar biografía/foto:

```bash
python3 tmdb_gente_importer.py --tipo director --skip-existing
python3 tmdb_gente_importer.py --tipo actor --skip-existing
```

El importador respeta `revisado_manual` y no pisa las correcciones hechas
desde la web.

---

## 3. Subvenciones ICAA

Cola: títulos distintos de `subvenciones` sin expediente ICAA (ni en la
propia tabla ni en `subvenciones_icaa_matches`).

```sql
CREATE TABLE subvenciones_icaa_matches (
    titulo_subvencion TEXT PRIMARY KEY,
    expediente_icaa   TEXT NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

La clave es el título literal (no normalizado), para conservar la variante
histórica exacta que se revisó. `expediente_icaa` no tiene FK contra
`icaa_fichas`: puede ser un ID oficial del catálogo ICAA que todavía no está
importado localmente.

**Ojo:** esta tabla usa el título como clave, así que si dos filas de
`subvenciones` compartieran el mismo título pero fueran películas distintas,
el match se aplicaría a ambas por igual. Para `subvenciones_raw` este
problema se solucionó con una tabla puente por `id` (ver siguiente sección) —
`subvenciones` en sí sigue usando título porque es una tabla más antigua y
más pequeña, sin ese caso detectado todavía.

---

## 4. Subvenciones (raw)

Cola: filas de `subvenciones_raw` (id + título + año + tipo de ayuda +
importe) sin expediente ICAA.

```sql
CREATE TABLE subvenciones_raw_icaa_matches (
    subvenciones_raw_id INTEGER PRIMARY KEY REFERENCES subvenciones_raw(id),
    expediente_icaa     TEXT,
    sin_ficha           BOOLEAN NOT NULL DEFAULT FALSE,
    notas               TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_subv_raw_expediente_o_sin_ficha CHECK (expediente_icaa IS NOT NULL OR sin_ficha)
);
```

Por qué por `id` y no por título: en `subvenciones_raw` un mismo título puede
aparecer en más de una fila — porque la misma película recibió dos ayudas en
años distintos, o porque dos películas distintas comparten título (ej. "UN
AMOR", que existen dos, de 2011 y 2023). Con el título como clave no se puede
distinguir esos casos; con el `id` de cada fila, sí.

`POST /admin/matching/subvenciones_raw` acepta:

- `action=save` + `expediente_icaa` → guarda el match.
- `action=sin_ficha` → marca `sin_ficha = TRUE`, `expediente_icaa = NULL`.
  Sirve para descartar de forma permanente un caso sin ficha conocida (por
  ejemplo, títulos de proyecto que se abandonaron antes de rodarse), sin que
  vuelva a aparecer en la cola.

### El problema de los títulos que cambian en rodaje

Un caso que **este sistema no puede resolver solo con título**: una película
puede figurar en la subvención con su título de proyecto y estrenarse con
otro completamente distinto (ej. `"AJEDREZ PARA TRES"` → `"¿QUÉ TE JUEGAS?"`).
Ningún matching por texto va a encontrar eso — no comparten palabras. Se
comprobó además que las resoluciones oficiales del Ministerio no publican el
expediente ICAA de calificación (solo título de proyecto + empresa + NIF), así
que no hay ID compartido de origen para tirar de ahí automáticamente. Estos
casos solo se resuelven con:

- búsqueda manual (IMDb, prensa, el propio catálogo ICAA buscando por
  director/año/productora), o
- si se sabe la respuesta, escribiéndola directamente vía el buscador de esta
  pestaña (busca el título de proyecto y guarda el expediente aunque el
  candidato sugerido esté vacío).

---

## 5. Películas TMDB

Cola: fichas de `icaa_fichas` (catálogo maestro) sin `tmdb_id` todavía.

```sql
CREATE TABLE pelicula_tmdb_match (
    expediente_icaa TEXT PRIMARY KEY,
    tmdb_id         INTEGER,
    sin_tmdb        BOOLEAN NOT NULL DEFAULT FALSE,
    match_score     NUMERIC(4,2),
    fuente          TEXT,
    verificado      BOOLEAN NOT NULL DEFAULT FALSE,
    notas           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_pelicula_tmdb_o_sin_tmdb CHECK (tmdb_id IS NOT NULL OR sin_tmdb),
    -- + columnas "ricas" (titulo_tmdb, sinopsis, poster_url, generos,
    -- reparto_principal, director_tmdb, puntuacion_tmdb...) — mismos campos
    -- que produce tmdb_enricher.py, añadidas con ALTER TABLE ADD COLUMN IF
    -- NOT EXISTS en ensure_matching_schema().
);
```

Es una tabla nueva — antes no existía ningún vínculo guardado entre
`expediente_icaa` y una ficha de película en TMDB (la tabla `tmdb` existente
está pensada para el ranking semanal de Comscore, se cruza por
`(titulo, distribuidora)`, no por expediente).

`POST /admin/matching/pelicula_tmdb` acepta tres acciones:

| Acción | Efecto |
|---|---|
| `action=buscar` | Busca automáticamente en TMDB por título + año (reutiliza `tmdb_enricher.buscar_pelicula`, mismo matcher que el pipeline semanal) y, si encuentra un candidato razonable, descarga la ficha completa (`tmdb_enricher.obtener_detalle_completo` + `extraer_metadatos`) y la guarda con `fuente='auto_busqueda'`, `verificado=FALSE` — queda para que alguien lo confirme |
| `action=save` + `tmdb_id` | Descarga la ficha completa para ese ID exacto y la guarda con `fuente='manual'`, `verificado=TRUE`. Si la API de TMDB falla, igualmente guarda el vínculo del ID (sin los datos ricos) para no perder la decisión |
| `action=sin_tmdb` | Marca `sin_tmdb=TRUE`, no vuelve a aparecer en la cola |

El botón "🔍 Buscar en TMDB" de cada fila dispara `action=buscar`. El enlace
externo a `themoviedb.org/search` sigue ahí para cuando el automático no
encuentra nada y toca localizar el ID a mano.

`buscar_pelicula()` usa `solo_espanol=True` en esta pestaña (a diferencia del
pipeline de Comscore, aquí todas las fichas vienen de `icaa_fichas`, que por
definición son producciones españolas) — descarta candidatos que no sean de
producción española o que sean cortometrajes de menos de 40 minutos.

---

## Funciones y rutas principales (`webapp/app.py`)

| Función / ruta | Responsabilidad |
|---|---|
| `ensure_matching_schema()` | Crea/asegura todas las tablas puente e índices de esta página, y quita el `UNIQUE(tmdb_id)` de `tmdb_gente` |
| `get_icaa_matching_pending(limit, q)` | Cola de `anual_esp` sin ficha ICAA |
| `get_tmdb_people_pending(limit, q)` | Cola de personas TMDB dudosas |
| `get_subvenciones_matching_pending(limit, q)` | Cola de `subvenciones` sin ficha ICAA |
| `get_subvenciones_raw_matching_pending(limit, q, solo_dificiles)` | Cola de `subvenciones_raw` sin ficha ICAA, con recuento de candidatos y filtro de dificultad |
| `get_pelicula_tmdb_pending(limit, q)` | Cola de `icaa_fichas` sin TMDB |
| `require_matching_admin()` | Aplica el token opcional |
| `admin_matching()` | Renderiza la página, lee `tab`, `q`, `todos` de la URL |
| `admin_matching_icaa_save()` | `POST /admin/matching/icaa` |
| `admin_matching_persona_save()` | `POST /admin/matching/persona` |
| `admin_matching_persona_alias_save()` | `POST /admin/matching/persona/alias` |
| `admin_matching_subvenciones_save()` | `POST /admin/matching/subvenciones` |
| `admin_matching_subvenciones_raw_save()` | `POST /admin/matching/subvenciones_raw` |
| `admin_matching_pelicula_tmdb_save()` | `POST /admin/matching/pelicula_tmdb` |

La normalización de título usada en todas las colas es `TITLE_NORM_SQL`
(definida una sola vez en `webapp/app.py`): minúsculas, sin acentos, sin
paréntesis, y el artículo inicial o final tipo "El/La/Los/Las/Un/Una/Unos/Unas"
eliminado o reordenado solo cuando aparece exactamente como sufijo tras coma
(evita el bug de cortar por la primera coma a ciegas, que mezclaba títulos no
relacionados del tipo "CHARLOT, HÉROE DEL PATÍN").

Tras cualquier cambio en `webapp/app.py` o en
`webapp/templates/admin_matching.html` hace falta reconstruir el contenedor:

```bash
ssh ubuntu "cd /home/sergio/taquilla_app && docker-compose up -d --build"
```

---

## Trabajo futuro (no implementado todavía)

- **Volcado masivo de matches ya identificados**: durante la exploración
  previa a esta herramienta se identificaron ~390 filas de `subvenciones_raw`
  cuyo match ya se puede deducir cruzando contra la tabla `subvenciones`
  (que trae expedientes curados). Insertarlos de una vez en
  `subvenciones_raw_icaa_matches` vaciaría gran parte de esa cola sin
  revisión fila a fila.
- **`personas_tmdb` canónica**: separar los datos "ricos" de cada persona
  (biografía, foto, IMDb/Wikidata ID) en una tabla `personas_tmdb` con
  `tmdb_id` como clave, dejando `tmdb_gente` como tabla pura de alias
  (`nombre_icaa → tmdb_id`). Hoy los datos ricos siguen viviendo repetidos en
  cada fila de `tmdb_gente`; el formulario de alias ya está preparado para
  seguir funcionando igual cuando se haga esta migración.
- **Historial de decisiones**: quién aprobó cada match y cuándo, más allá de
  `created_at`/`updated_at`.
- **Autenticación real** (usuarios/roles), más allá del token compartido.
