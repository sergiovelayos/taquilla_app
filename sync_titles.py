#!/usr/bin/env python3
import psycopg2
import os
import unicodedata
import re
from dotenv import load_dotenv

load_dotenv()

def normalize(text):
    if not text: return ""
    # Quitar acentos y poner en minúsculas
    text = "".join(c for c in unicodedata.normalize('NFD', text) if unicodedata.category(c) != 'Mn')
    text = text.lower()
    # Quitar todo lo que no sea alfanumérico
    return re.sub(r'[^a-z0-9]', '', text)

def sync():
    conn = psycopg2.connect(os.getenv('DATABASE_URL'))
    cur = conn.cursor()

    print("Cargando fichas ICAA existentes...")
    cur.execute("SELECT expediente_icaa, titulo FROM icaa_fichas")
    fichas = cur.fetchall()
    icaa_map = {normalize(t): exp for exp, t in fichas if t}
    print(f"Total fichas cargadas: {len(icaa_map)}")

    print("Buscando títulos en anual_esp sin match exacto...")
    cur.execute("SELECT DISTINCT titulo FROM anual_esp WHERE titulo NOT IN (SELECT titulo FROM icaa_fichas)")
    missing_titles = [row[0] for row in cur.fetchall()]
    
    matched = 0
    for titulo in missing_titles:
        norm = normalize(titulo)
        if norm in icaa_map:
            exp_id = icaa_map[norm]
            # Insertamos el alias en icaa_fichas para que el match sea exacto en el futuro
            try:
                cur.execute("""
                    INSERT INTO icaa_fichas (expediente_icaa, titulo) 
                    VALUES (%s, %s) 
                    ON CONFLICT DO NOTHING
                """, (exp_id, titulo))
                matched += 1
            except:
                conn.rollback()
    
    conn.commit()
    conn.close()
    print(f"Sincronización finalizada. Se han vinculado {matched} títulos por normalización.")

if __name__ == "__main__":
    sync()
