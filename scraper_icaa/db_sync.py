import json
import psycopg2
import os
from dotenv import load_dotenv

load_dotenv()
DATABASE_URL = os.getenv('DATABASE_URL')
MAPPER_FILE = 'scraper_icaa/id_mapeo_final.json'

def sync():
    with open(MAPPER_FILE, 'r') as f:
        mapping = json.load(f)
    
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    
    updated = 0
    for title, icaa_id in mapping.items():
        if icaa_id:
            # Actualizar en ambas tablas
            cur.execute("UPDATE top25 SET id_icaa = %s WHERE titulo = %s", (icaa_id, title))
            cur.execute("UPDATE topespanol SET id_icaa = %s WHERE titulo = %s", (icaa_id, title))
            updated += 1
            
    conn.commit()
    cur.close()
    conn.close()
    print(f"Sincronización completada: {updated} películas actualizadas en la DB.")

if __name__ == '__main__':
    sync()
