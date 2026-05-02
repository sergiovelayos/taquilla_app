#!/usr/bin/env python3
import os
import requests
import psycopg2
import time
import re
from urllib.parse import quote
from dotenv import load_dotenv
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
load_dotenv()

DATABASE_URL = os.getenv('DATABASE_URL')
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

def clean_title(titulo):
    """Limpia el título de ruidos comunes en la base de datos."""
    # 1. Quitar (re), (19xx), etc.
    t = re.sub(r'\(.*?\)', '', titulo)
    # 2. Quitar coletillas de Comscore/Ministerio
    t = t.split('se realiza siguiendo')[0]
    t = t.split('Phoenix')[0]
    t = t.split('Avalon')[0]
    # 3. Quitar caracteres especiales pero mantener espacios
    t = re.sub(r'[^\w\s]', ' ', t)
    # 4. Colapsar espacios
    return " ".join(t.split()).strip()

def search_icaa_id(titulo):
    """Busca el ID en la sede del MCU."""
    search_term = clean_title(titulo)
    if not search_term: return None
    
    url = f"https://sede.mcu.gob.es/CatalogoICAA/es-es/Peliculas/Buscar?Titulo={quote(search_term)}&Todo=False"
    
    try:
        r = requests.get(url, headers=HEADERS, verify=False, timeout=15)
        if r.status_code == 200:
            match = re.search(r'Detalle\?Pelicula=(\d+)', r.text)
            if match:
                return match.group(1)
            
            # Si no hay match directo, probar con la primera palabra significativa si el título es largo
            words = search_term.split()
            if len(words) > 3:
                short_term = " ".join(words[:2])
                url_short = f"https://sede.mcu.gob.es/CatalogoICAA/es-es/Peliculas/Buscar?Titulo={quote(short_term)}&Todo=False"
                r2 = requests.get(url_short, headers=HEADERS, verify=False, timeout=15)
                match2 = re.search(r'Detalle\?Pelicula=(\d+)', r2.text)
                if match2: return match2.group(1)

    except Exception as e:
        print(f" Error: {e}")
    return None

def main():
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()

    cur.execute("""
        SELECT DISTINCT titulo 
        FROM anual_esp 
        WHERE titulo NOT IN (SELECT titulo FROM icaa_fichas)
        ORDER BY titulo ASC
        LIMIT 50
    """)
    missing = [row[0] for row in cur.fetchall()]
    
    print(f"Encontrados {len(missing)} títulos (limpieza mejorada).")
    
    found_count = 0
    for i, titulo in enumerate(missing, 1):
        print(f"[{i}/{len(missing)}] {titulo} -> ", end="", flush=True)
        
        icaa_id = search_icaa_id(titulo)
        
        if icaa_id:
            try:
                cur.execute("INSERT INTO icaa_fichas (expediente_icaa, titulo) VALUES (%s, %s) ON CONFLICT DO NOTHING", (icaa_id, titulo))
                conn.commit()
                print(f"OK ({icaa_id})")
                found_count += 1
            except:
                conn.rollback()
        else:
            print("—")
        
        time.sleep(0.5)

    conn.close()
    print(f"\nFinalizado: {found_count} IDs nuevos.")

if __name__ == "__main__":
    main()
