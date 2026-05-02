#!/usr/bin/env python3
import os
import requests
import json
import time
import re
from urllib.parse import quote
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv
import urllib3

# Desactivar advertencias de SSL inseguro (necesario para sede.mcu.gob.es)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Cargar configuración
load_dotenv()
DATABASE_URL = os.getenv('DATABASE_URL')
MAPPER_FILE = 'scraper_icaa/mapper_taquilla_icaa.json'

def get_unique_movies():
    """Obtiene la lista de películas únicas del Top Español desde la DB."""
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT titulo, MIN(EXTRACT(YEAR FROM fecha_inicio)) as anio 
                FROM topespanol 
                WHERE titulo IS NOT NULL
                GROUP BY titulo 
                ORDER BY anio DESC
            """)
            return cur.fetchall()
    finally:
        conn.close()

def search_icaa_id(titulo, anio):
    """
    Busca el ID de la película en el catálogo ICAA.
    Utiliza el buscador oficial de la sede.
    """
    if not titulo:
        return None
        
    # Limpiar título para la búsqueda
    search_term = titulo.split(':')[0].split('(')[0].strip()
    url = f"https://sede.mcu.gob.es/CatalogoICAA/Peliculas/Buscar?Titulo={quote(search_term)}&Todo=False"
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }

    try:
        # verify=False para ignorar el error de certificado SSL
        response = requests.get(url, headers=headers, timeout=15, verify=False)
        if response.status_code == 200:
            # Buscamos el patrón de la URL de detalle
            match = re.search(r'Detalle\?Pelicula=(\d+)', response.text)
            if match:
                return match.group(1)
    except Exception as e:
        # Loguear error pero no detener el script
        pass
    
    return None

def main():
    os.makedirs('scraper_icaa', exist_ok=True)
    
    mapping = {}
    if os.path.exists(MAPPER_FILE):
        try:
            with open(MAPPER_FILE, 'r', encoding='utf-8') as f:
                mapping = json.load(f)
        except:
            mapping = {}

    print("--- Iniciando Mapeo de IDs ICAA (SSL Bypass Activo) ---")
    movies = get_unique_movies()
    total_total = len(movies)
    print(f"Total de películas únicas a procesar: {total_total}")

    new_found = 0
    for i, movie in enumerate(movies, 1):
        titulo = movie['titulo']
        anio = int(movie['anio'])
        
        # Saltamos si ya está mapeado con éxito
        if titulo in mapping and mapping[titulo] and mapping[titulo].get('id_icaa'):
            continue

        print(f"[{i}/{total_total}] {titulo} ({anio})...", end=" ", flush=True)
        
        icaa_id = search_icaa_id(titulo, anio)
        
        if icaa_id:
            mapping[titulo] = {
                'id_icaa': icaa_id,
                'url': f"https://sede.mcu.gob.es/CatalogoICAA/Peliculas/Detalle?Pelicula={icaa_id}",
                'anio_taquilla': anio,
                'updated_at': time.strftime("%Y-%m-%d %H:%M:%S")
            }
            new_found += 1
            print(f"¡OK! ID: {icaa_id}")
        else:
            # Si no se encuentra, guardamos el intento fallido para no repetir
            if titulo not in mapping:
                mapping[titulo] = None
            print("—")

        # Guardado incremental
        if i % 10 == 0:
            with open(MAPPER_FILE, 'w', encoding='utf-8') as f:
                json.dump(mapping, f, indent=4, ensure_ascii=False)
        
        time.sleep(0.8) # Un poco más rápido pero seguro

    with open(MAPPER_FILE, 'w', encoding='utf-8') as f:
        json.dump(mapping, f, indent=4, ensure_ascii=False)

    print(f"\n--- Proceso finalizado ---")
    print(f"Nuevos IDs encontrados: {new_found}")

if __name__ == "__main__":
    main()
