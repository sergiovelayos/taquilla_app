# Correcciones manuales de mappings ICAA

Script: `icaa_manual_map.py`

Este script centraliza las correcciones manuales que no puede resolver el proceso automático de `brave_icaa.py`. Hay dos tipos de corrección:

1. **Asociar un título de `anual_esp` a un ID ICAA** — cuando el título en la base de datos difiere del título oficial de la ficha ICAA (artículos, subtítulos, grafías distintas).
2. **Corregir un ID ICAA incorrecto** — cuando el proceso automático asignó un expediente equivocado a una película.

---

## 1. Asociar un título manualmente

### Cuándo ocurre

El matching automático de `brave_icaa.py` compara el título de `anual_esp` con el título y descripción que Brave indexa de las fichas ICAA. Falla cuando:

- El título en `anual_esp` tiene una grafía muy distinta a la ficha ICAA (p.ej. `"Cuaderno de Sara, El"` vs `"EL CUADERNO DE SARA"`).
- La película tiene un subtítulo o título original que confunde el score de matching.
- La ficha ICAA usa un título en otro idioma.
- Brave no tiene indexada la ficha (la página ICAA no aparece en sus resultados).

### Normalización automática de títulos

Antes de recurrir al mapeo manual, el proceso automático intenta emparejar títulos entre `anual_esp` e `icaa_fichas` aplicando una normalización en cuatro pasos:

```sql
regexp_replace(
    unaccent(LOWER(TRIM(
        regexp_replace(split_part(titulo, ',', 1), '\([^)]*\)', '', 'g')
    ))),
    '^(el|la|los|las|un|una|unos|unas)\s+', ''
)
```

| Paso | Función | Ejemplo |
|------|---------|---------|
| `split_part(titulo, ',', 1)` | Elimina el artículo al final si va tras una coma | `"Reina de España, La"` → `"Reina de España"` |
| `regexp_replace(..., '\([^)]*\)', '', 'g')` | Elimina contenido entre paréntesis | `"Verano (2019)"` → `"Verano"` |
| `unaccent(LOWER(TRIM(...)))` | Quita tildes, pasa a minúsculas y elimina espacios extra | `"Reina de España"` → `"reina de espana"` |
| `regexp_replace(..., '^(el\|la\|...)' , '')` | Elimina el artículo inicial | `"la reina de espana"` → `"reina de espana"` |

Esto resuelve la mayoría de diferencias entre tablas. Por ejemplo:

| `anual_esp` | `icaa_fichas` | Normalizado (ambos) |
|-------------|---------------|---------------------|
| `"Reina de España, La"` | `"LA REINA DE ESPAÑA"` | `"reina de espana"` ✓ |
| `"Mejor verano de mi vida, El"` | `"El mejor verano de mi vida"` | `"mejor verano de mi vida"` ✓ |
| `"Familia Benetón (2024), La"` | `"La Familia Benetón"` | `"familia beneton"` ✓ |

El mapeo manual solo es necesario cuando esta normalización no es suficiente (títulos completamente distintos o películas no indexadas por Brave).

La query completa para ver los títulos pendientes es:

```sql
SELECT
    regexp_replace(
        unaccent(LOWER(TRIM(
            regexp_replace(split_part(a.titulo, ',', 1), '\([^)]*\)', '', 'g')
        ))),
        '^(el|la|los|las|un|una|unos|unas)\s+', ''
    ) AS titulo_normalizado,
    MIN(a.fecha_estreno) AS fecha_estreno,
    SUM(a.recaudacion)   AS recaudacion_total
FROM anual_esp a
LEFT JOIN icaa_fichas i
    ON regexp_replace(
        unaccent(LOWER(TRIM(
            regexp_replace(split_part(a.titulo, ',', 1), '\([^)]*\)', '', 'g')
        ))),
        '^(el|la|los|las|un|una|unos|unas)\s+', ''
    )
     = regexp_replace(
        unaccent(LOWER(TRIM(
            regexp_replace(split_part(i.titulo, ',', 1), '\([^)]*\)', '', 'g')
        ))),
        '^(el|la|los|las|un|una|unos|unas)\s+', ''
    )
WHERE i.titulo IS NULL
GROUP BY 1
ORDER BY recaudacion_total DESC
LIMIT 15;
```

### Cómo funciona el mapeo manual

El título de `anual_esp` se guarda en la columna `titulo_anual_esp` de `icaa_fichas`. Esto permite hacer JOIN entre ambas tablas aunque los títulos difieran:

```sql
SELECT i.*, a.*
FROM icaa_fichas i
JOIN anual_esp a
  ON a.titulo = i.titulo_anual_esp   -- mapeo manual
  OR a.titulo = i.titulo             -- matching normal
```

### Flujo del script

1. Verifica que el título existe exactamente en `anual_esp`.
2. Muestra los datos de esa película en `anual_esp` (recaudación, fecha de estreno).
3. Comprueba si el expediente ya existe en `icaa_fichas` y muestra sus datos.
4. Pide confirmación antes de escribir.
5. Guarda el mapeo (`titulo_anual_esp` = título de `anual_esp`).

### Uso

```bash
# Ver qué películas siguen sin mapeo (ordenadas por recaudación)
python3 icaa_manual_map.py --list-missing
python3 icaa_manual_map.py --list-missing --limit 50

# Asociar una película
python3 icaa_manual_map.py --titulo "Cuaderno de Sara, El" --icaa-id 98765

# Si el expediente aún no está en icaa_fichas, descarga y parsea el HTML
python3 icaa_manual_map.py --titulo "Cuaderno de Sara, El" --icaa-id 98765 --fetch

# Modo interactivo para trabajar varias seguidas
python3 icaa_manual_map.py --interactive
```

### Cómo encontrar el ID correcto

Buscar en el catálogo oficial del ICAA:

```
https://sede.mcu.gob.es/CatalogoICAA/Peliculas/Buscar?Titulo=<titulo>
```

El ID aparece en la URL de la ficha de detalle:

```
https://sede.mcu.gob.es/CatalogoICAA/Peliculas/Detalle?Pelicula=98765
                                                                 ^^^^^
```

---

## 2. Corregir un ID incorrecto

### Cuándo ocurre

`brave_icaa.py` a veces asigna un expediente equivocado, especialmente cuando:

- Dos películas comparten palabras clave en el título y el score de matching es similar.
- La ficha ICAA devuelve un título genérico ("Datos de Pelicula ICAA") y el batch OR no puede discriminar entre candidatos.
- Existe una secuela o remake con título casi idéntico.

Para detectarlo, consultar `icaa_fichas` y verificar que el `titulo` ICAA coincide con la película esperada:

```sql
-- Verificar los mappings recientes
SELECT expediente_icaa, titulo, titulo_anual_esp, fecha_estreno
FROM icaa_fichas
WHERE titulo_anual_esp IS NOT NULL
ORDER BY updated_at DESC
LIMIT 20;

-- Ver la URL de la ficha para revisarla en el navegador
-- https://sede.mcu.gob.es/CatalogoICAA/Peliculas/Detalle?Pelicula=<expediente_icaa>
```

### Flujo del script

1. Verifica que el ID incorrecto existe en `icaa_fichas` y muestra su ficha actual.
2. Comprueba si el ID correcto ya existe en `icaa_fichas` (puede ser un duplicado).
3. Pide confirmación antes de actuar.
4. Aplica la corrección según el caso:
   - **ID nuevo no existe** → renombra la fila (`UPDATE expediente_icaa`), conservando todos los datos ya guardados. Con `--fetch` sobreescribe los campos con el HTML correcto.
   - **ID nuevo ya existe** → elimina la fila incorrecta y deja intacta la correcta.
5. Borra el HTML del ID incorrecto de disco si existe, para evitar que `icaa_parser.py` lo reprocese.

### Uso

```bash
# Corregir el ID (solo renombra/elimina la fila)
python3 icaa_manual_map.py --fix-id 11111 --new-id 98765

# Corregir y además descargar el HTML correcto para rellenar los datos
python3 icaa_manual_map.py --fix-id 11111 --new-id 98765 --fetch
```

**Ejemplo 1 — Brave asignó la secuela en lugar del original:**

Brave encontró el ID `182824` para "La Familia Benetón" pero ese expediente corresponde a "La Familia Benetón + 2" (la secuela). El ID correcto es `155123`.

```bash
python3 icaa_manual_map.py --fix-id 182824 --new-id 155123 --fetch
```

El script renombra la fila en `icaa_fichas` y descarga el HTML del expediente `155123` para sobreescribir el título, director, fecha de estreno y el resto de campos con los datos correctos.

**Ejemplo 2 — El ID incorrecto ya tiene su propia fila en `icaa_fichas`:**

Brave asignó el ID `43322` a "Reina de España, La", pero ese expediente ya existía en `icaa_fichas` y corresponde a "La Navidad en sus manos". El ID correcto para "Reina de España" es `6316`.

```bash
python3 icaa_manual_map.py --fix-id 43322 --new-id 6316 --fetch
```

En este caso el script detecta que `6316` ya existe en `icaa_fichas` y en lugar de renombrar elimina la fila incorrecta (`43322`), dejando intacta la fila de "La Navidad en sus manos". Con `--fetch` descarga el HTML de `6316` para completar los datos de "Reina de España".

---

## Pipeline completo de referencia

```bash
# 1. Buscar IDs automáticamente via Brave Search
python3 brave_icaa.py --top-missing 15

# 2. Descargar HTMLs de los stubs nuevos
python3 icaa_downloader.py

# 3. Parsear HTMLs y enriquecer icaa_fichas
python3 icaa_parser.py

# 4. Revisar qué títulos siguen sin mapeo
python3 icaa_manual_map.py --list-missing

# 5. Corregir manualmente los que fallan
python3 icaa_manual_map.py --titulo "Titulo exacto, El" --icaa-id 98765 --fetch
python3 icaa_manual_map.py --fix-id 11111 --new-id 98765 --fetch
```
