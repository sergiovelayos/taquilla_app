---
name: icaa-sync
description: Sincroniza IDs ICAA para películas de anual_esp que no están en icaa_fichas. Búsqueda por título (sin año) e inserción por lotes.
---

# Sincronización ICAA (V2)

## Flujo de Trabajo
1. **Identificar**: Ejecuta `python3 scripts/sync.py 10` para obtener el lote de las 10 películas más relevantes que faltan.
2. **Buscar**: Usa Google Search con el patrón: `site:sede.mcu.gob.es/CatalogoICAA "[TITULO]"` (SIN AÑO).
3. **Extraer**: Obtén el ID del parámetro `Pelicula=` de la URL oficial.
4. **Insertar**: Graba el ID y el título en `icaa_fichas` inmediatamente.
5. **Repetir**: Continúa con el siguiente lote hasta completar la lista.

## Reglas Críticas
- **Filtrado previo**: NUNCA busques películas que ya tengan entrada en `icaa_fichas`. El script `sync.py` ya se encarga de este filtrado.
- **Solo Título**: No incluyas el año en la búsqueda de Google a menos que sea un título extremadamente genérico que genere ambigüedad.
