#!/usr/bin/env python3
import requests
from bs4 import BeautifulSoup
import json
import time
import os
import urllib3
import re

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
}
OUTPUT_FILE = 'scraper_icaa/peliculas_completas_v2.json'

def clean(text):
    if not text: return ""
    return " ".join(text.replace('\n', ' ').replace('\r', '').split()).strip()

def extract_all_info(film_id):
    url = f"https://sede.mcu.gob.es/CatalogoICAA/es-es/Peliculas/Detalle?Pelicula={film_id}"
    print(f"  -> Procesando ID {film_id}...", end=" ", flush=True)
    
    try:
        response = requests.get(url, headers=HEADERS, timeout=20, verify=False)
        soup = BeautifulSoup(response.content, 'html.parser')
    except Exception as e:
        print(f"Error: {e}")
        return None

    movie = {
        'id_icaa': film_id,
        'url': url,
        'titulo': clean(soup.find('h3', class_='mcu-title').get_text()) if soup.find('h3', class_='mcu-title') else "Sin Título",
        'secciones': {}
    }

    # Estrategia: Buscar todos los paneles de detalles y sus contenidos
    # Buscamos tanto los de diseño antiguo (id con p_) como los de diseño nuevo
    
    # 1. Bloques de texto plano (Identificación, Sinopsis, Explotación)
    detail_blocks = soup.find_all('div', class_='product-details-content')
    if detail_blocks:
        info_basica = {}
        for block in detail_blocks:
            label = block.find('label', class_=re.compile('mcu-text-details-text'))
            value = block.find(['label', 'a', 'p'], class_=re.compile('custom-simple-label|director|product-details-text'))
            if label and value:
                key = clean(label.get_text()).replace(':', '')
                info_basica[key] = clean(value.get_text())
        movie['secciones']['IDENTIFICACION_Y_EXPLOTACION'] = info_basica

    # 2. Tablas (Ficha Técnica, Artística)
    # Buscamos las cabeceras que contienen "FICHA"
    headers = soup.find_all(['h3', 'h4', 'div'], class_=re.compile('header-panel-details|mcu-title-details'))
    for h in headers:
        title = clean(h.get_text()).upper()
        if not title: continue
        
        # Intentamos encontrar la tabla o el panel que sigue a esta cabecera
        content = h.find_next_sibling(['div', 'table'])
        if not content: continue
        
        # Si es una tabla (Reparto / Equipo)
        table = content.find('table') if content.name == 'div' else (content if content.name == 'table' else None)
        if table:
            rows_data = []
            for row in table.find_all('tr')[1:]:
                cols = row.find_all('td')
                if len(cols) >= 2:
                    rows_data.append({'nombre': clean(cols[0].get_text()), 'rol_o_cargo': clean(cols[1].get_text())})
            movie['secciones'][title] = rows_data
            continue

        # Si son paneles colapsables (Productoras, Subvenciones)
        sub_panels = content.find_all('div', class_='header-panel-details')
        if sub_panels:
            panel_results = []
            for sp in sub_panels:
                p_name = clean(sp.get_text())
                p_body = sp.find_next_sibling('div', class_='hidden-panel-details')
                p_data = {}
                if p_body:
                    for r in p_body.find_all('div', class_='row'):
                        l_div = r.find('div', class_='col-sm-4')
                        v_div = r.find('div', class_='col-sm-8')
                        if l_div and v_div:
                            p_data[clean(l_div.get_text()).replace(':', '')] = clean(v_div.get_text())
                panel_results.append({'entidad': p_name, 'datos': p_data})
            movie['secciones'][title] = panel_results

    # 3. Caso especial: Pestañas clásicas (si el método anterior no pilló algo)
    for p_id in ['p_identificacion', 'p_sinopsis_es', 'p_reparto', 'p_equipo', 'p_empresas', 'p_explotacion', 'p_subvenciones']:
        sect_div = soup.find('div', id=p_id)
        if sect_div and p_id.upper() not in movie['secciones']:
            # Solo si no lo hemos capturado ya por nombre
            movie['secciones'][p_id.upper()] = clean(sect_div.get_text())

    print("¡Hecho!")
    return movie

def main():
    ids = ["188423", "194123", "144423", "158720", "121114", "118916", "157621", "180018", "110517", "179318", "164322", "156420", "143719", "147217", "172221", "192322", "176322", "179218", "41519", "85918", "178819", "160220"]
    print(f"--- Iniciando Extracción Ultra-Detallada de {len(ids)} películas ---")
    results = [extract_all_info(fid) for fid in ids]
    
    os.makedirs('scraper_icaa', exist_ok=True)
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump([r for r in results if r], f, ensure_ascii=False, indent=4)
    print(f"\nFinalizado. Datos guardados en {OUTPUT_FILE}")

if __name__ == "__main__":
    main()
