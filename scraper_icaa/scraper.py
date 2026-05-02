import requests
from bs4 import BeautifulSoup
import json
import csv
import time
import logging
import os
import sys

# Ajuste de rutas
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(BASE_DIR)

try:
    from config import BASE_URL, HEADERS, TIMEOUT, FIELD_MAPPING, PELICULAS_CSV, PELICULAS_JSON
except ImportError:
    BASE_URL = "https://sede.mcu.gob.es/CatalogoICAA/es-es/Peliculas/Detalle?Pelicula="
    HEADERS = {"User-Agent": "Mozilla/5.0"}
    TIMEOUT = 10
    FIELD_MAPPING = {
        "Dirigido por": "director",
        "Título Original": "titulo_original",
        "Calificación": "calificacion",
        "Año de Producción": "ano_produccion",
        "Nacionalidad": "nacionalidad",
        "Género": "genero",
        "Duración": "duracion"
    }
    PELICULAS_CSV = os.path.join(BASE_DIR, 'peliculas.csv')
    PELICULAS_JSON = os.path.join(BASE_DIR, 'peliculas_completas.json')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.FileHandler(os.path.join(BASE_DIR, "scraper.log")), logging.StreamHandler()]
)

def clean_text(text):
    if not text: return ""
    return " ".join(text.replace('\n', ' ').replace('\r', '').split()).strip()

def parse_panel_hierarchy(container):
    results = []
    headers = container.find_all('div', class_='header-panel-details')
    for header in headers:
        title = clean_text(header.get_text())
        content_div = header.find_next_sibling('div', class_='hidden-panel-details')
        details = {}
        if content_div:
            nested_headers = content_div.find_all('div', class_='header-panel-details', recursive=False)
            if nested_headers:
                details['items'] = parse_panel_hierarchy(content_div)
            else:
                rows = content_div.find_all('div', class_='row')
                for row in rows:
                    label_div = row.find('div', class_='col-sm-4')
                    value_div = row.find('div', class_='col-sm-8')
                    if label_div and value_div:
                        label = clean_text(label_div.get_text()).replace(':', '')
                        value = clean_text(value_div.get_text())
                        details[label] = value
        results.append({'nombre': title, 'detalles': details})
    return results

def scrape_movie(film_id):
    # Forzamos la URL en español para asegurar que el mapeo de FIELD_MAPPING funcione
    url = f"https://sede.mcu.gob.es/CatalogoICAA/es-es/Peliculas/Detalle?Pelicula={film_id}"
    logging.info(f"Procesando película ID: {film_id}")
    
    try:
        response = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, 'html.parser')
    except Exception as e:
        logging.error(f"Error al acceder a {url}: {e}")
        return None

    movie_data = {'id': film_id, 'url': url}
    ident = {}

    # --- ESTRATEGIA 1: PESTAÑAS (DISEÑO COMPLETO) ---
    ident_container = soup.find('div', id='p_identificacion')
    if ident_container:
        rows = ident_container.find_all('div', class_='row')
        for row in rows:
            label = row.find('label')
            value = row.find('p')
            if label and value:
                key = clean_text(label.get_text()).replace(':', '')
                ident[FIELD_MAPPING.get(key, key)] = clean_text(value.get_text())
    
    # --- ESTRATEGIA 2: SECCIÓN PRINCIPAL (DISEÑO SIMPLIFICADO) ---
    # Si ident está vacío, buscamos en la sección 'principal'
    principal = soup.find('div', class_='principal')
    if principal:
        # Buscamos todos los labels de detalles
        detail_divs = principal.find_all('div', class_='product-details-content')
        for div in detail_divs:
            label_node = div.find('label', class_=lambda x: x and 'mcu-text-details-text' in x)
            if label_node:
                key = clean_text(label_node.get_text()).replace(':', '')
                # El valor puede estar en un <a> o en otro <label>
                value_node = div.find(['a', 'label'], class_=lambda x: x and 'label' in x or 'director' in x)
                if value_node:
                    val = clean_text(value_node.get_text())
                    mapped_key = FIELD_MAPPING.get(key, key)
                    if mapped_key not in ident: # No sobreescribir si ya lo trajo la Estrategia 1
                        ident[mapped_key] = val

    movie_data['identificacion'] = ident

    # Sinopsis
    sinopsis = {}
    sin_es = soup.find('div', id='p_sinopsis_es')
    if sin_es: sinopsis['castellano'] = clean_text(sin_es.get_text())
    movie_data['sinopsis'] = sinopsis

    # Equipos, Producción, etc. (Solo si existen pestañas)
    # ... (resto de lógica igual para mantener compatibilidad con diseño completo)
    for section_id, key in [('p_reparto', 'equipo_artistico'), ('p_equipo', 'equipo_tecnico')]:
        items = []
        container = soup.find('div', id=section_id)
        if container:
            table = container.find('table')
            if table:
                rows = table.find_all('tr')[1:]
                for row in rows:
                    cols = row.find_all('td')
                    if len(cols) >= 2:
                        items.append({'nombre': clean_text(cols[0].get_text()), 'rol': clean_text(cols[1].get_text())})
        movie_data[key] = items

    movie_data['produccion'] = []
    movie_data['distribucion'] = []
    empresa_containers = soup.find_all('div', id='p_empresas')
    for container in empresa_containers:
        tab_list = soup.find('ul', class_='nav-tabs')
        if tab_list:
            active_tab = clean_text(tab_list.find('li', class_='active').get_text())
            if "Productoras" in active_tab: movie_data['produccion'] = parse_panel_hierarchy(container)
            elif "Distribuidoras" in active_tab: movie_data['distribucion'] = parse_panel_hierarchy(container)

    movie_data['otros'] = {
        'etiquetas': [clean_text(t.get_text()) for t in soup.find_all('label', class_='mcu-tags')],
        'observaciones': clean_text(soup.find('div', id='p_observaciones').get_text()) if soup.find('div', id='p_observaciones') else ""
    }

    return movie_data

def main():
    csv_to_read = sys.argv[1] if len(sys.argv) > 1 else PELICULAS_CSV
    output_json = os.path.join(BASE_DIR, 'peliculas_completas.json')

    if not os.path.exists(csv_to_read):
        logging.error(f"No se encuentra el archivo CSV: {csv_to_read}")
        return

    all_movies = []
    with open(csv_to_read, mode='r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            data = scrape_movie(row['ID'])
            if data: all_movies.append(data)
            time.sleep(1)

    with open(output_json, 'w', encoding='utf-8') as f:
        json.dump(all_movies, f, ensure_ascii=False, indent=4)
    
    logging.info(f"Scraping finalizado. {len(all_movies)} fichas en {output_json}")

if __name__ == "__main__":
    main()
