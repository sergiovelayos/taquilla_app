#!/usr/bin/env python3
"""
import_subvenciones.py — Crea la tabla 'subveciones' e importa datos desde csv/subvenciones.csv.

Uso:
    python3 import_subvenciones.py           # crea tabla e importa
    python3 import_subvenciones.py --dry-run # muestra lo que haría sin escribir en BD
"""

import argparse
import csv
import os
import sys
from pathlib import Path

# Cargar .env igual que el resto de scripts del proyecto
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import psycopg2

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    sys.exit("ERROR: DATABASE_URL no está definida en el entorno ni en .env")

SCRIPT_DIR = Path(__file__).parent
CSV_PATH = SCRIPT_DIR / "csv" / "subvenciones.csv"

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS subveciones (
    titulo               TEXT,
    importe_ayuda        NUMERIC(15,2),
    presupuesto_proyecto NUMERIC(15,2),
    tipo_ayuda           TEXT,
    anio_ayuda           INTEGER
);
"""

INSERT_SQL = """
INSERT INTO subveciones (titulo, importe_ayuda, presupuesto_proyecto, tipo_ayuda, anio_ayuda)
VALUES (%s, %s, %s, %s, %s)
"""


def parse_decimal(value: str):
    """Convierte string con punto o coma decimal a float. Devuelve None si vacío."""
    v = value.strip()
    if not v:
        return None
    return float(v.replace(",", "."))


def main(dry_run: bool = False):
    if not CSV_PATH.exists():
        sys.exit(f"ERROR: No se encuentra el fichero CSV en {CSV_PATH}")

    rows = []
    errors = []

    with open(CSV_PATH, encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter=";")
        for i, row in enumerate(reader, start=2):
            try:
                titulo = row["titulo"].strip()  # conservar comas internas
                importe = parse_decimal(row["importe_ayuda"])
                presupuesto = parse_decimal(row["presupuesto_proyecto"])
                tipo = row["tipo_ayuda"].strip()
                anio = int(row["anio_ayuda"].strip()) if row["anio_ayuda"].strip() else None
                rows.append((titulo, importe, presupuesto, tipo, anio))
            except Exception as e:
                errors.append(f"  Fila {i}: {e} — {dict(row)}")

    print(f"CSV leído: {len(rows)} filas válidas, {len(errors)} errores de parseo.")
    if errors:
        print("Errores de parseo:")
        for e in errors:
            print(e)

    if dry_run:
        print("\n[dry-run] No se ha escrito nada en la base de datos.")
        if rows:
            print("Primeras 3 filas que se insertarían:")
            for r in rows[:3]:
                print(" ", r)
        return

    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()

    # Borrar y recrear la tabla para garantizar idempotencia
    cur.execute("DROP TABLE IF EXISTS subveciones;")
    cur.execute(CREATE_TABLE_SQL)
    conn.commit()
    print("Tabla 'subveciones' creada (o recreada).")

    # Insertar filas en lote
    cur.executemany(INSERT_SQL, rows)
    conn.commit()

    # Verificación final
    cur.execute("SELECT COUNT(*), MIN(anio_ayuda), MAX(anio_ayuda) FROM subveciones;")
    count, min_anio, max_anio = cur.fetchone()
    print(f"Importación completada: {count} filas en BD, años {min_anio}–{max_anio}.")

    cur.close()
    conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Importa subvenciones.csv a la tabla 'subveciones'.")
    parser.add_argument("--dry-run", action="store_true", help="Muestra lo que haría sin escribir en BD.")
    args = parser.parse_args()
    main(dry_run=args.dry_run)
