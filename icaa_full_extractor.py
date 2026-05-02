#!/usr/bin/env python3
import requests
from bs4 import BeautifulSoup
import json
import time
import os
import urllib3
import re

# Desactivar advertencias SSL
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Configuración
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
    "Accept-Language": "es-ES,es;q=0.9"
}
OUTPUT_FILE = 'scraper_icaa/peliculas_detalladas.json'

def clean_text(text):
    if not text: return ""
    return " ".join(text.replace('\n', ' ').replace('\r', '').split()).strip()

def extract_detailed_movie(film_id):
    url = f"https://sede.mcu.gob.es/CatalogoICAA/es-es/Peliculas/Detalle?Pelicula={film_id}"
    print(f"  -> Extrayendo ID {film_id}...", end=" ", flush=True)
    
    try:
        response = requests.get(url, headers=HEADERS, timeout=20, verify=False)
        if response.status_code != 200:
            print(f"Error {response.status_code}")
            return None
        soup = BeautifulSoup(response.content, 'html.parser')
    except Exception as e:
        print(f"Error: {e}")
        return None

    movie = {
        'id_icaa': film_id,
        'url_oficial': url,
        'titulo': "Sin título",
        'identificacion': {},
        'sinopsis': "",
        'equipo_artistico': [],
        'equipo_tecnico': [],
        'productoras': [],
        'explotacion': {},
        'subvenciones': []
    }

    # 0. Título
    title_node = soup.find(['h3', 'h2'], class_='mcu-title') or soup.find('div', class_='mcu-title')
    if title_node: movie['titulo'] = clean_text(title_node.get_text())

    # 1. Lógica Híbrida de Identificación (Pestañas o Lista Simple)
    # Formato A: Pestañas con ID
    ident_container = soup.find('div', id='p_identificacion')
    if ident_container:
        for row in ident_container.find_all('div', class_='row'):
            l, v = row.find('label'), row.find('p')
            if l and v: movie['identificacion'][clean_text(l.get_text()).replace(':', '')] = clean_text(v.get_text())
    
    # Formato B: Lista simple (clases product-details-content)
    details = soup.find_all('div', class_='product-details-content')
    for d in details:
        l = d.find('label', class_=re.compile('mcu-text-details-text'))
        v = d.find(['label', 'a'], class_=re.compile('custom-simple-label|director|product-details-text'))
        if l and v:
            key = clean_text(l.get_text()).replace(':', '')
            movie['identificacion'][key] = clean_text(v.get_text())

    # 2. Sinopsis
    sin_node = soup.find('div', id='p_sinopsis_es') or soup.find('div', class_='p_sinopsis_es')
    if sin_node: movie['sinopsis'] = clean_text(sin_node.get_text())

    # 3. Equipo (Tablas)
    for sect, key in [('p_reparto', 'equipo_artistico'), ('p_equipo', 'equipo_tecnico')]:
        container = soup.find('div', id=sect)
        if container and container.find('table'):
            for row in container.find('table').find_all('tr')[1:]:
                cols = row.find_all('td')
                if len(cols) >= 2:
                    movie[key].append({'nombre': clean_text(cols[0].get_text()), 'rol': clean_text(cols[1].get_text())})

    # 4. Productoras y Subvenciones (Paneles colapsables)
    def parse_panels(container):
        items = []
        if not container: return items
        headers = container.find_all('div', class_='header-panel-details')
        for h in headers:
            name = clean_text(h.get_text())
            body = h.find_next_sibling('div', class_='hidden-panel-details')
            data = {}
            if body:
                for row in body.find_all('div', class_='row'):
                    label_div = row.find('div', class_='col-sm-4')
                    value_div = row.find('div', class_='col-sm-8')
                    if label_div and value_div:
                        data[clean_text(label_div.get_text()).replace(':', '')] = clean_text(value_div.get_text())
            items.append({'nombre': name, 'datos': data})
        return items

    movie['productoras'] = parse_panels(soup.find('div', id='p_empresas'))
    movie['subvenciones'] = parse_panels(soup.find('div', id='p_subvenciones'))

    # 5. Explotación (Recaudación)
    expl = soup.find('div', id='p_explotacion')
    if expl:
        for row in expl.find_all('div', class_='row'):
            l_div, v_div = row.find('div', class_='col-sm-4'), row.find('div', class_='col-sm-8')
            if l_div and v_div: movie['explotacion'][clean_text(l_div.get_text()).replace(':', '')] = clean_text(v_div.get_text())

    print("¡Hecho!")
    return movie

def main():
    ids_to_process = [
        "188423", "194123", "144423", "158720", "121114", 
        "118916", "157621", "180018", "110517", "179318", 
        "164322", "156420", "143719", "147217", "172221", 
        "192322", "176322", "181622", "179218", "41519", 
        "85918", "178819", "160220"
    ]
    print(f"--- Iniciando extracción detallada (Lógica Híbrida) ---")
    results = [extract_detailed_movie(fid) for fid in ids_to_process]
    results = [r for r in results if r]
    
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=4)
    print(f"\nFinalizado. {len(results)} películas en {OUTPUT_FILE}")

if __name__ == "__main__":
    main()
