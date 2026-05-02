# ICAA Film Scraper (Catálogo de Cine)

Este proyecto es un scraper robusto desarrollado en Python diseñado para extraer y estructurar toda la información disponible en el [Catálogo del Cine Español (ICAA)](https://sede.mcu.gob.es/CatalogoICAA/).

El objetivo final es generar un archivo JSON jerárquico y limpio, listo para ser importado en una base de datos relacional o NoSQL.

## 🛠️ Arquitectura del Scraper

El sistema está dividido en dos fases para optimizar el rendimiento y evitar el bloqueo por parte del servidor:

### Fase 1: Recolección de IDs (`process_batches.py`)
Dado que los resultados de búsqueda del ICAA son dinámicos, esta fase procesa fragmentos de HTML guardados localmente:
- **Entrada:** Archivos `.html` en `html_sources/` (volcados manuales de los resultados de búsqueda).
- **Lógica:** Extrae el título y el ID único de cada película.
- **Salida:** `peliculas.csv`, una lista consolidada que actúa como cola de trabajo para la fase 2.

### Fase 2: Extracción de Fichas Completas (`scraper.py`)
Es el motor principal del proyecto. Utiliza `BeautifulSoup4` para realizar una "cirugía" sobre el DOM de la página de detalle de cada película.

#### Desafíos Técnicos Superados:
1. **El Trap de `p_empresas`**: El sitio web reutiliza el mismo ID de contenedor (`id="p_empresas"`) para las pestañas de "Productoras" y "Distribuidoras". El scraper soluciona esto validando el texto del elemento `li.active` antes de procesar.
2. **Paneles Anidados**: Las secciones de Producción y Distribución no usan tablas estándar, sino una jerarquía de `header-panel-details` y `hidden-panel-details`. El scraper implementa un **parser jerárquico** que reconstruye la relación: `País > Empresa > Detalles (Contrato, Porcentaje, etc.)`.
3. **Mapeo de Etiquetas**: Se utiliza un diccionario de mapeo para normalizar etiquetas como "Dirigido por" a `director`, "Recaudación total" a `recaudacion_total`, etc.
4. **Normalización de Texto**: Limpieza agresiva de espacios en blanco, saltos de línea y entidades HTML (ej. `&#243;` -> `ó`).

## 📋 Estructura de Datos (JSON)
El scraper genera un objeto JSON por película con la siguiente estructura:
- `identificacion`: Datos básicos (título, año, metraje, nacionalidad, etc.).
- `sinopsis`: En castellano e inglés.
- `equipo_artistico`: Lista de actores y sus personajes.
- `equipo_tecnico`: Lista de técnicos y sus roles (Dirección, Guion, Música, etc.).
- `produccion`: Estructura anidada por países y empresas.
- `distribucion`: Estructura anidada por empresas y tipos de contrato.
- `otros`: Festivales, Premios, Trailers, Etiquetas (`mcu-tags`) y Observaciones.

## 🚀 Instrucciones de Uso

### 1. Preparar la lista de películas
Coloca tus bloques de HTML con resultados de búsqueda en `scraper_icaa/html_sources/` y ejecuta:
```bash
python3 scraper_icaa/process_batches.py
```

### 2. Ejecutar el scraping de detalles
Una vez tengas el `peliculas.csv` listo, ejecuta el scraper principal:
```bash
python3 scraper_icaa/scraper.py
```

## 📦 Requisitos
- Python 3.x
- BeautifulSoup4
- Requests

---
**Nota:** Este scraper está configurado para forzar siempre la versión en castellano (`/es-es/`) de las URLs para garantizar que las etiquetas de campo coincidan con el motor de mapeo.
