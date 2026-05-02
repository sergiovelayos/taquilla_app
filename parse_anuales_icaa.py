"""
parse_anuales_icaa.py — Parsea los informes anuales ICAA de cine español
y los guarda en la tabla `anual_esp` de PostgreSQL.

Uso:
  python3 parse_anuales_icaa.py --dry-run    # Muestra lo extraído sin escribir en BBDD
  python3 parse_anuales_icaa.py              # Procesa todos los PDFs y guarda

JOIN con otras tablas:
  SELECT a.*, t.director, t.poster_url
  FROM anual_esp a
  LEFT JOIN tmdb t ON a.titulo = t.titulo AND a.distribuidora = t.distribuidora
  WHERE a.anio = 2025 ORDER BY a.rank;
"""

import os
import re
import logging
import argparse
from pathlib import Path
from collections import defaultdict
from datetime import datetime

import pdfplumber
import psycopg2
from dotenv import load_dotenv

# ─── Config ────────────────────────────────────────────────────────────────────

load_dotenv()
DB_DSN   = os.getenv("DATABASE_URL", "postgresql://localhost/comscore")
PDF_DIR  = Path(__file__).parent / "pdfs" / "informes_anuales_icaa_cine_esp"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]
)
log = logging.getLogger(__name__)

# Mapeo fichero → año (por si el nombre no lo deja claro)
YEAR_MAP = {
    "recaudacion-espectadores-cine-espanol-2016.pdf": 2016,
    "recaudacion-espectadores-cine-espanol-2017.pdf": 2017,
    "recaudaci-n-y-espectadores-cine-espanol-2018-def.pdf": 2018,
    "acumulado-2019-icaa.pdf": 2019,
    "acumulado-2020-icaa-temporal.pdf": 2020,
    "acumulado2021-2-enero-2022.pdf": 2021,
    "recaudacion-y-espectadores-cine-espanol-2022.pdf": 2022,
    "acumulado-2023.pdf": 2023,
    "acumulado-2024.pdf": 2024,
    "acumulado-2025.pdf": 2025,
}

# ─── Helpers ───────────────────────────────────────────────────────────────────

def parse_euros(texto):
    if not texto:
        return None
    texto = re.sub(r"[€\s]", "", texto).replace(".", "").replace(",", ".")
    try:
        return float(texto)
    except ValueError:
        return None

def parse_entero(texto):
    if not texto:
        return None
    texto = re.sub(r"[^\d]", "", texto)
    return int(texto) if texto else None

def parse_fecha(texto):
    if not texto:
        return None
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(texto.strip(), fmt).date()
        except ValueError:
            continue
    return None

def year_from_filename(fname):
    """Extrae el año del nombre del fichero o del YEAR_MAP."""
    if fname in YEAR_MAP:
        return YEAR_MAP[fname]
    m = re.search(r"(20\d{2})", fname)
    return int(m.group(1)) if m else None

# ─── Parser de páginas ─────────────────────────────────────────────────────────

def agrupar_por_linea(words, tolerancia=3):
    """Agrupa palabras en líneas físicas usando tolerancia vertical."""
    lines = defaultdict(list)
    for w in words:
        y = round(w["top"] / tolerancia) * tolerancia
        lines[y].append(w)
    return {y: sorted(ws, key=lambda w: w["x0"]) for y, ws in lines.items()}

def detectar_columnas(lines):
    """
    Detecta las posiciones X de las columnas combinando:
    1. Palabras clave del encabezado para rank/titulo/distrib
    2. Posición real de las fechas (dd/mm/yyyy) en los datos para fecha_x
       (más fiable que el header, que a veces está desplazado)
    """
    import re as _re
    cols = {}
    ys_sorted = sorted(lines.keys())

    # Paso 1: encabezados para rank/distrib
    for y in ys_sorted[:15]:
        for w in lines[y]:
            t = w["text"].upper().strip("*")
            if t == "RANK":               cols["rank_x"]    = w["x0"]
            elif t in ("TÍTULO","TITULO"): cols["titulo_x"] = w["x0"]
            elif t == "DISTRIBUIDORA":    cols["distrib_x"] = w["x0"]

    # Paso 2: fecha_x = x mínima donde aparecen fechas reales en los datos
    fecha_xs = []
    for y in ys_sorted[3:25]:   # saltar cabeceras, mirar primeras filas de datos
        for w in lines[y]:
            if _re.match(r"\d{2}/\d{2}/\d{4}$", w["text"]):
                fecha_xs.append(w["x0"])
    if fecha_xs:
        cols["fecha_x"] = min(fecha_xs) - 2   # pequeño margen de seguridad

    # Fallback fecha_x si no hay fechas en las primeras filas
    if "distrib_x" in cols and "fecha_x" not in cols:
        all_x = [w["x0"] for ws in lines.values() for w in ws]
        cols["fecha_x"] = max(all_x) * 0.73

    # Fallback completo si la cabecera no se detectó
    if "distrib_x" not in cols:
        all_x = [w["x0"] for ws in lines.values() for w in ws]
        page_w = max(all_x) if all_x else 600
        cols = {"rank_x":page_w*.06, "titulo_x":page_w*.10,
                "distrib_x":page_w*.55, "fecha_x":page_w*.73}

    cols.setdefault("rank_x", 0)
    cols.setdefault("titulo_x", cols.get("rank_x", 0) + 10)

    # Detectar rank_limit: es el hueco entre el número de rank y el inicio del título.
    # Buscamos filas donde la 1ª palabra es un dígito (=rank) y hay una 2ª palabra (=título).
    # rank_limit = min(x de esa 2ª palabra) - 1, que es justo antes de que empiece el título.
    title_starts = []
    for y in ys_sorted[3:30]:
        row = lines[y]
        if len(row) >= 2 and row[0]["text"].isdigit() and int(row[0]["text"]) < 300:
            title_starts.append(row[1]["x0"])
    if title_starts:
        cols["rank_limit"] = min(title_starts) - 1
    else:
        cols["rank_limit"] = cols["rank_x"] + 15

    return cols

def clasificar_palabra(w, cols):
    """
    Devuelve la columna a la que pertenece una palabra según su x.
    El límite rank/título usa rank_x + 15 porque el header 'TÍTULO' está centrado
    sobre la columna pero los datos reales empiezan justo después del número de rank.
    """
    x = w["x0"]
    rank_limit = cols.get("rank_limit", cols["rank_x"] + 15)
    if x < rank_limit:
        return "rank"
    elif x < cols["distrib_x"] - 2:
        return "titulo"
    elif x < cols["fecha_x"] - 2:
        return "distrib"
    else:
        return "derecha"  # fecha, recaudación, espectadores

def parsear_pagina(page, cols, anio):
    """
    Extrae registros de una página usando las posiciones de columna detectadas.
    Devuelve lista de dicts con los campos de cada película.
    """
    words = page.extract_words()
    lines = agrupar_por_linea(words)

    # Construir filas clasificadas
    filas = []
    for y in sorted(lines.keys()):
        fila = {"rank": [], "titulo": [], "distrib": [], "derecha": [], "y": y}
        for w in lines[y]:
            col = clasificar_palabra(w, cols)
            fila[col].append(w["text"])
        filas.append(fila)

    # Filtrar filas vacías o de cabecera
    filas = [f for f in filas if any(f["rank"] + f["titulo"] + f["distrib"] + f["derecha"])]

    # ── Agrupar filas en registros ──
    # Un registro puede ocupar 1 o 2 líneas físicas cuando el título o distribuidor
    # tiene palabras en distinto y (caso frecuente en PDFs 2024-2025).
    # Identificador de nuevo registro: columna rank contiene un número.
    #
    # Caso especial 2024: el PDF puede colocar el TÍTULO del registro N+1 y sus
    # espectadores en la misma línea física que los datos finales del registro N,
    # ANTES de que aparezca el número de rank de N+1.  Ej:
    #   y=102: [rank=2] [titulo="Infiltrada, La"] [distrib="Beta Fiction"] [fecha+recaud+€+1.274.000]
    #   y=111: [titulo="Buffalo Kids"] [derecha="837.070"]   ← precede al rank 3
    #   y=114: [rank=3] [distrib="Warner Bros"] [fecha+recaud+€]
    # La heurística para detectarlo: línea de continuación con titulo+derecha pero
    # SIN distrib, cuando el buffer ya tiene distrib y ya tiene '€' en su derecha
    # (es decir, el registro actual está completo). En ese caso guardamos los tokens
    # como "pendientes" para el siguiente registro.
    registros = []
    buffer = None
    pending_titulo = []   # título que precede al próximo número de rank
    pending_derecha = []  # datos de derecha que preceden al próximo número de rank

    def es_rank(tokens):
        return bool(tokens) and tokens[0].isdigit()

    def flush(buf):
        if buf:
            registros.append(buf)

    def es_pre_siguiente(fila, buf):
        """True si esta continuación pertenece en realidad al SIGUIENTE registro."""
        if not buf:
            return False
        return (bool(fila["titulo"]) and bool(fila["derecha"])
                and not fila["distrib"]
                and bool(buf["distrib"])
                and "€" in buf["derecha"])

    for fila in filas:
        if es_rank(fila["rank"]):
            flush(buffer)
            buffer = {k: list(v) for k, v in fila.items() if k != "y"}
            # Inyectar tokens que pertenecen a este registro pero llegaron antes
            if pending_titulo:
                buffer["titulo"] = pending_titulo + buffer["titulo"]
            if pending_derecha:
                buffer["derecha"].extend(pending_derecha)
            pending_titulo = []
            pending_derecha = []
        else:
            if buffer is not None:
                if es_pre_siguiente(fila, buffer):
                    # Guardar para el siguiente registro; no contaminar el buffer actual
                    pending_titulo = list(fila["titulo"])
                    pending_derecha = list(fila["derecha"])
                else:
                    for col in ["titulo", "distrib", "derecha"]:
                        buffer[col].extend(fila[col])
                    pending_titulo = []
                    pending_derecha = []
            # Si no hay buffer activo (cabecera, pie, etc.) ignorar

    flush(buffer)

    # ── Parsear cada registro ──
    peliculas = []
    for rec in registros:
        rank_str = rec["rank"][0] if rec["rank"] else ""
        if not rank_str.isdigit():
            continue
        rank = int(rank_str)

        titulo = " ".join(rec["titulo"]).strip()
        distrib = " ".join(rec["distrib"]).strip()

        # La columna "derecha" tiene: fecha recaudacion € espectadores
        # Ejemplo: ['26/06/2025', '13.406.008', '€', '2.043.434']
        derecha = rec["derecha"]

        fecha_str = next((t for t in derecha if re.match(r"\d{2}/\d{2}/\d{4}", t)), None)
        euro_idx  = next((i for i, t in enumerate(derecha) if t == "€"), None)

        recaudacion_str  = derecha[euro_idx - 1] if euro_idx and euro_idx > 0 else None
        espectadores_str = derecha[euro_idx + 1] if euro_idx and euro_idx + 1 < len(derecha) else None

        # A veces espectadores viene partido en dos tokens (número cortado al final de página)
        if espectadores_str and not re.search(r"\d", espectadores_str):
            espectadores_str = None

        peliculas.append({
            "anio":          anio,
            "rank":          rank,
            "titulo":        titulo,
            "distribuidora": distrib,
            "fecha_estreno": parse_fecha(fecha_str),
            "recaudacion":   parse_euros(recaudacion_str),
            "espectadores":  parse_entero(espectadores_str),
        })

    return peliculas

def parsear_pdf(filepath, anio):
    """Parsea todas las páginas de un PDF y devuelve la lista de películas."""
    resultados = []
    vistos = set()  # evitar duplicados por títulos repetidos entre páginas

    with pdfplumber.open(filepath) as pdf:
        # Detectar columnas en la primera página
        words_p0 = pdf.pages[0].extract_words()
        lines_p0 = agrupar_por_linea(words_p0)
        cols = detectar_columnas(lines_p0)
        log.info(f"  Columnas detectadas: distrib_x={cols['distrib_x']:.0f}, fecha_x={cols['fecha_x']:.0f}")

        for i, page in enumerate(pdf.pages):
            pelis = parsear_pagina(page, cols, anio)
            for p in pelis:
                key = (p["rank"], p["titulo"][:15])
                if key not in vistos and p["titulo"]:
                    vistos.add(key)
                    resultados.append(p)

    return resultados

# ─── Base de datos ──────────────────────────────────────────────────────────────

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS anual_esp (
    id              SERIAL PRIMARY KEY,
    anio            INTEGER NOT NULL,       -- año del informe
    rank            INTEGER NOT NULL,       -- posición en el ranking anual
    titulo          TEXT NOT NULL,
    distribuidora   TEXT,
    fecha_estreno   DATE,
    recaudacion     NUMERIC(14,2),          -- recaudación acumulada en ese año (€)
    espectadores    INTEGER,                -- espectadores acumulados en ese año
    created_at      TIMESTAMP DEFAULT NOW(),
    UNIQUE (anio, rank)
);

CREATE INDEX IF NOT EXISTS anual_esp_anio_idx    ON anual_esp (anio);
CREATE INDEX IF NOT EXISTS anual_esp_titulo_idx  ON anual_esp (titulo);
"""

UPSERT_SQL = """
INSERT INTO anual_esp (anio, rank, titulo, distribuidora, fecha_estreno, recaudacion, espectadores)
VALUES (%s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (anio, rank) DO UPDATE SET
    titulo        = EXCLUDED.titulo,
    distribuidora = EXCLUDED.distribuidora,
    fecha_estreno = EXCLUDED.fecha_estreno,
    recaudacion   = EXCLUDED.recaudacion,
    espectadores  = EXCLUDED.espectadores;
"""

def get_db():
    return psycopg2.connect(DB_DSN)

def crear_tabla(conn):
    with conn.cursor() as cur:
        cur.execute(CREATE_TABLE_SQL)
    conn.commit()
    log.info("Tabla 'anual_esp' e índices listos.")

def guardar(conn, peliculas):
    with conn.cursor() as cur:
        for p in peliculas:
            cur.execute(UPSERT_SQL, (
                p["anio"], p["rank"], p["titulo"], p["distribuidora"],
                p["fecha_estreno"], p["recaudacion"], p["espectadores"]
            ))
    conn.commit()

# ─── Main ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Parser informes anuales ICAA → anual_esp")
    parser.add_argument("--dry-run", action="store_true", help="No escribe en BBDD")
    args = parser.parse_args()

    pdf_files = sorted(f for f in PDF_DIR.glob("*.pdf") if not f.name.startswith("._"))
    if not pdf_files:
        log.error(f"No se encontraron PDFs en {PDF_DIR}")
        exit(1)

    conn = None
    if not args.dry_run:
        conn = get_db()
        crear_tabla(conn)

    total_ok = total_err = 0

    for pdf_path in pdf_files:
        anio = year_from_filename(pdf_path.name)
        if not anio:
            log.warning(f"No se pudo determinar el año de {pdf_path.name} — saltando")
            continue

        log.info(f"\n{'='*60}")
        log.info(f"📄 {pdf_path.name}  →  año {anio}")

        try:
            peliculas = parsear_pdf(pdf_path, anio)
            log.info(f"  {len(peliculas)} películas extraídas")

            # Mostrar muestra (top 5 + última)
            print(f"\n  {'RK':>3}  {'TÍTULO':<45} {'DISTRIBUIDORA':<22} {'ESTRENO':<12} {'RECAUDACIÓN':>14}  {'ESPECT.':>9}")
            print(f"  {'-'*3}  {'-'*45} {'-'*22} {'-'*12} {'-'*14}  {'-'*9}")
            muestra = peliculas[:5] + (peliculas[-1:] if len(peliculas) > 5 else [])
            for p in muestra:
                rec = f"{p['recaudacion']:>12,.0f} €" if p["recaudacion"] else "             N/A"
                esp = f"{p['espectadores']:>9,}"      if p["espectadores"] else "        N/A"
                print(f"  {p['rank']:>3}  {p['titulo']:<45.45} {(p['distribuidora'] or ''):<22.22} {str(p['fecha_estreno'] or ''):<12} {rec}  {esp}")

            if not args.dry_run and conn:
                guardar(conn, peliculas)
                log.info(f"  💾 Guardadas en BBDD.")
            total_ok += len(peliculas)

        except Exception as e:
            log.error(f"  ❌ Error procesando {pdf_path.name}: {e}", exc_info=True)
            if conn:
                conn.rollback()
            total_err += 1

    if conn:
        conn.close()

    print(f"\n{'='*60}")
    print(f"  ✅ Total películas procesadas: {total_ok}")
    if total_err:
        print(f"  ❌ PDFs con error: {total_err}")
    if args.dry_run:
        print(f"\nℹ️  Modo dry-run — nada guardado en BBDD.")
        print(f"   Ejecuta sin --dry-run para guardar.")
