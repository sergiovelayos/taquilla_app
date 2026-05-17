# Página de resolución de matching

> Documentación técnica de la ruta `/admin/matching`.
> Última actualización: mayo 2026.

---

## Objetivo

La página de **Resolución de Matching** permite corregir desde la web algunos de los joins por nombre que antes requerían scripts o SQL manual:

1. Títulos de `anual_esp` que todavía no están vinculados con una ficha de `icaa_fichas`.
2. Personas de `tmdb_gente` cuyo match automático con TMDB necesita revisión.

El MVP no sustituye todavía todo el sistema de matching del proyecto. Su objetivo es concentrar en una interfaz operativa las dos colas de revisión manual más frecuentes, reducir trabajo por terminal y dejar una base sobre la que ampliar aliases y decisiones reutilizables.

---

## Ruta y acceso

```text
GET /admin/matching
```

La ruta está implementada en `webapp/app.py` y renderiza la plantilla:

```text
webapp/templates/admin_matching.html
```

### Protección opcional

Si se define la variable de entorno `MATCHING_ADMIN_TOKEN`, la página y sus formularios requieren un token compartido:

```bash
export MATCHING_ADMIN_TOKEN="un-token-largo"
```

En ese caso se accede con:

```text
/admin/matching?token=un-token-largo
```

Si la variable no está configurada, la página queda accesible sin token. Como la ruta permite escrituras en base de datos, conviene definirla en entornos expuestos.

---

## Estructura de la interfaz

La página tiene dos pestañas:

| Pestaña | Fuente | Finalidad |
|---|---|---|
| `Películas ICAA` | `anual_esp` + `icaa_fichas` | Resolver títulos sin ficha ICAA asociada |
| `Personas TMDB` | `tmdb_gente` | Revisar personas con match dudoso, sin ID o sin foto |

En la parte superior se muestran contadores de elementos visibles en cada cola.

---

## 1. Películas ICAA

### Qué muestra

La cola lista títulos de `anual_esp` que todavía no tienen correspondencia en `icaa_fichas`.

Cada fila muestra:

- título original;
- título normalizado;
- fecha de estreno;
- impacto aproximado por recaudación y espectadores;
- campo para introducir el expediente ICAA correcto;
- enlace directo al buscador del catálogo ICAA.

### Cómo decide si un título está pendiente

La query usa la misma lógica de normalización ya documentada en `docs/correcciones_manuales_icaa.md`:

- elimina artículos finales tras coma;
- elimina texto entre paréntesis;
- pasa a minúsculas;
- elimina tildes;
- quita artículos iniciales.

Un título queda fuera de la cola si:

1. ya existe un `icaa_fichas.titulo_anual_esp` igual al título de `anual_esp`; o
2. el título normalizado coincide con `icaa_fichas.titulo`.

### Qué hace la acción de guardar

Al introducir un ID ICAA y guardar, la ruta:

```text
POST /admin/matching/icaa
```

ejecuta un `INSERT ... ON CONFLICT` sobre `icaa_fichas` y guarda:

```text
titulo_anual_esp = título original de anual_esp
```

Esto permite que los joins posteriores funcionen aunque el título oficial del ICAA y el título de `anual_esp` sean diferentes.

Además, al cargar la página se asegura que exista la columna e índice necesarios:

```sql
ALTER TABLE icaa_fichas
ADD COLUMN IF NOT EXISTS titulo_anual_esp TEXT;

CREATE INDEX IF NOT EXISTS icaa_titulo_anual_esp_idx
ON icaa_fichas (titulo_anual_esp);
```

### Relación con el flujo previo

Esta pestaña cubre desde la web el caso de uso básico que antes se resolvía con:

```bash
python3 icaa_manual_map.py --titulo "Titulo exacto, El" --icaa-id 98765
```

El script sigue siendo útil para operaciones más avanzadas, especialmente `--fetch` y corrección de IDs erróneos con `--fix-id`.

---

## 2. Personas TMDB

### Qué muestra

La cola lista filas de `tmdb_gente` con `revisado_manual = FALSE` y alguno de estos síntomas:

- `tmdb_id IS NULL`;
- `foto_url IS NULL`;
- `match_score IS NULL`;
- `match_score < 0.75`.

Cada fila muestra:

- nombre ICAA;
- match TMDB actual, si existe;
- roles acumulados (`director`, `actor`);
- score;
- formulario de corrección.

### Acciones disponibles

La ruta:

```text
POST /admin/matching/persona
```

permite tres decisiones:

| Acción | Efecto |
|---|---|
| Guardar con `tmdb_id` | Actualiza el ID, marca `revisado_manual = TRUE` y conserva o añade notas |
| Guardar sin `tmdb_id` | Marca la fila como revisada |
| `Sin ficha TMDB` | Deja `tmdb_id = NULL`, marca `revisado_manual = TRUE` y añade la nota por defecto `Confirmado: sin ficha TMDB` si no se escribió otra |

### Importante

Guardar un `tmdb_id` correcto no rellena por sí solo toda la ficha biográfica ni descarga fotos nuevas. Después de corregir IDs manualmente sigue siendo necesario reejecutar el importador para refrescar datos:

```bash
python3 tmdb_gente_importer.py --tipo director --skip-existing
python3 tmdb_gente_importer.py --tipo actor --skip-existing
```

El importador ya protege `revisado_manual` y `notas`, por lo que la revisión hecha desde la web no se pierde.

---

## Funciones principales

| Función | Responsabilidad |
|---|---|
| `execute()` | Helper de escritura con commit |
| `ensure_matching_schema()` | Asegura `titulo_anual_esp` e índice |
| `get_icaa_matching_pending()` | Construye la cola de títulos ICAA pendientes |
| `get_tmdb_people_pending()` | Construye la cola de personas TMDB pendientes |
| `require_matching_admin()` | Aplica el token opcional |
| `admin_matching()` | Renderiza la página |
| `admin_matching_icaa_save()` | Guarda mappings ICAA |
| `admin_matching_persona_save()` | Guarda revisiones de personas TMDB |

---

## Limitaciones actuales del MVP

El MVP resuelve revisión manual, pero todavía no incorpora:

- tabla genérica de aliases;
- historial de decisiones;
- candidatos múltiples con score explicable;
- pestaña de películas `top25/topespanol` frente a `tmdb`;
- corrección de expedientes ICAA erróneos;
- refresco automático de ficha TMDB tras guardar un ID;
- autenticación real de usuarios, roles o auditoría por usuario.

---

## Evolución recomendada

La siguiente iteración natural sería pasar de correcciones puntuales a un modelo explícito de decisiones:

```text
movie_aliases
person_aliases
match_candidates
match_reviews
```

Con eso la interfaz podría:

- mostrar varios candidatos por caso;
- registrar quién aprobó cada unión;
- reutilizar aliases entre lotes;
- separar `aceptado`, `rechazado` y `no existe`;
- cubrir también películas Comscore ↔ TMDB;
- reducir joins por nombre normalizado en tiempo de consulta.

Ese paso encaja con la recomendación ya recogida en la auditoría del proyecto: avanzar hacia entidades canónicas (`movies`, `people`) y dejar los nombres de origen como aliases, no como claves de unión definitivas.
