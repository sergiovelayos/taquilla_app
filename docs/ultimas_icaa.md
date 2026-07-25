# Snapshot diario: "Últimas calificadas" del ICAA

> Documentación técnica de `icaa_ultimas_calificadas.py` y la tabla `ultimas_icaa`.
> Creado: julio 2026.

---

## Objetivo

`https://infoicaa.mcu.es/CatalogoICAA/es-es/Peliculas/UltimasCalificadas` publica un listado
(fijo, ~50 filas) de las películas calificadas más recientemente por el ICAA. Es una fuente
potencialmente útil para detectar estrenos nuevos y cruzarlos cuanto antes con su ficha ICAA,
sin esperar al barrido masivo por rango de IDs (`scrape_icaa.py`).

De momento **no se conoce la frecuencia real de actualización** de esa página. El propósito
inicial de este proceso es puramente observacional: tomar una foto diaria y, con `fecha_insercion`
frente a `last_update`, medir cada cuánto aparecen filas nuevas o cambia el contenido de las
existentes. Con esos datos se podrá ajustar la frecuencia del cron a lo estrictamente necesario.

## Script

`icaa_ultimas_calificadas.py` (raíz del proyecto):

```bash
python3 icaa_ultimas_calificadas.py            # descarga + upsert en BBDD
python3 icaa_ultimas_calificadas.py --dry-run  # solo imprime lo parseado
```

Descarga la página con `requests`, parsea la única `<table>` con BeautifulSoup y hace upsert
fila a fila en `ultimas_icaa`.

## Tabla `ultimas_icaa`

```sql
CREATE TABLE ultimas_icaa (
    expediente_icaa  TEXT PRIMARY KEY,   -- columna "Película" de la web (nº de expediente)
    titulo           TEXT,
    direccion        TEXT,               -- columna "Dirección" (director/a)
    nacionalidad     TEXT,
    calificacion     TEXT,
    resolucion       DATE,               -- columna "Resolución" (fecha, dd/mm/yyyy en origen)
    fecha_insercion  TIMESTAMP NOT NULL DEFAULT NOW(),  -- solo se fija en el primer INSERT
    last_update      TIMESTAMP NOT NULL DEFAULT NOW()   -- se refresca en cada ejecución
);
```

`expediente_icaa` es el mismo espacio de IDs que `icaa_fichas` / `scrape_icaa`, así que en el
futuro se puede cruzar directamente por esa clave para traer la ficha completa en cuanto
aparezca una película nueva en este listado.

Al ser upsert por `expediente_icaa`, si una fila deja de aparecer en el listado (porque ya
salieron 50 más recientes) **no se borra** — `ultimas_icaa` acumula histórico de todo lo visto,
no es un espejo 1:1 del listado actual. Para saber qué hay ahora mismo en la web, filtrar por
`resolucion` reciente o cruzar con la última ejecución vía `last_update`.

## Cron

Diario a las 8:00 hora España (06:00 UTC en horario de verano; el servidor está en UTC, así
que en invierno pasará a ser 7:00 hora España — ajustar la hora del cron si se quiere mantener
fija las 8:00 todo el año):

```
0 6 * * * cd /home/sergio/taquilla_app && ./venv/bin/python3 icaa_ultimas_calificadas.py >> logs/ultimas_icaa.log 2>&1
```

Log: `logs/ultimas_icaa.log` en el servidor.

## Cómo evaluar la frecuencia real

Con unos días de histórico:

```sql
-- filas nuevas por día de inserción
SELECT date(fecha_insercion), count(*)
FROM ultimas_icaa
GROUP BY 1 ORDER BY 1;

-- distribución de fechas de resolución vistas en el snapshot más reciente
SELECT resolucion, count(*)
FROM ultimas_icaa
WHERE last_update > now() - interval '1 day'
GROUP BY 1 ORDER BY 1 DESC;
```

Si tras varios días se ve que la web solo añade películas nuevas 1-2 veces por semana (o en
días concretos), bajar la frecuencia del cron en consecuencia.
