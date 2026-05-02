# Rutas de archivos
PELICULAS_CSV = 'scraper_icaa/peliculas.csv'
PELICULAS_JSON = 'scraper_icaa/peliculas_completas.json'
HTML_SOURCES_DIR = 'scraper_icaa/html_sources/'

# URLs
BASE_URL = "https://sede.mcu.gob.es/CatalogoICAA/Peliculas/Detalle?Pelicula="

# Configuración de red
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
    "Accept-Language": "es-ES,es;q=0.9"
}
TIMEOUT = 10

# Mapeo de campos (normalización)
FIELD_MAPPING = {
    "Dirigido por": "director",
    "Recaudación total": "recaudacion_total",
    "Espectadores total": "espectadores_total",
    "Calificación": "calificacion",
    "Nacionalidad": "nacionalidad",
    "Fecha de estreno": "fecha_estreno",
    "Metraje": "metraje"
}
