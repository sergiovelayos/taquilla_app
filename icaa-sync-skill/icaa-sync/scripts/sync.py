#!/usr/bin/env python3
import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()
DATABASE_URL = os.getenv('DATABASE_URL')

def get_missing_movies(limit=10):
    """Obtiene películas de anual_esp que NO están en icaa_fichas, por recaudación."""
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    cur.execute("""
        SELECT titulo, SUM(recaudacion) as total
        FROM anual_esp 
        WHERE titulo NOT IN (SELECT titulo FROM icaa_fichas)
        GROUP BY titulo
        ORDER BY total DESC
        LIMIT %s
    """, (limit,))
    rows = cur.fetchall()
    conn.close()
    return rows

if __name__ == "__main__":
    import sys
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    movies = get_missing_movies(limit)
    for titulo, total in movies:
        print(f"{titulo}")
