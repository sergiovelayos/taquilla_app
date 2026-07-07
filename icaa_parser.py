"""
icaa_parser.py — Parsea las fichas HTML del catálogo ICAA y las guarda en PostgreSQL

Uso:
  python3 icaa_parser.py --dry-run    # Muestra lo extraído sin escribir en BBDD
  python3 icaa_parser.py              # Parsea todos los HTMLs y guarda en tabla 'icaa_fichas'

Los HTMLs deben estar en: scraper_icaa/html_sources/*.html
El nombre del fichero es el ID de expediente ICAA (ej: 144423.html → expediente 144423)

JOIN con tmdb y comscore:
  SELECT i.*, t.tmdb_id, t.director AS director_tmdb
  FROM icaa_fichas i
  JOIN tmdb t ON i.titulo = t.titulo          -- por título (aproximado)

  O más robusto, cruzar por expediente ICAA si se mapea previamente.
"""

import os
import re
import logging
import argparse
from datetime import datetime
from pathlib import Path

import psycopg2
from bs4 import BeautifulSoup
from dotenv import load_dotenv

# ─── Config ────────────────────────────────────────────────────────────────────

load_dotenv()
DB_DSN      = os.getenv("DATABASE_URL", "postgresql://localhost/taquilla_app")
HTML_DIR    = Path(__file__).parent / "scraper_icaa" / "html_sources"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]
)
log = logging.getLogger(__name__)

# ─── Helpers ───────────────────────────────────────────────────────────────────

def parse_euros(texto):
    """'4.094.999,17 €' → 4094999.17"""
    if not texto:
        return None
    texto = texto.replace("€", "").replace(".", "").replace(",", ".").strip()
    try:
        return float(texto)
    except ValueError:
        return None

def parse_entero(texto):
    """'652.829' → 652829"""
    if not texto:
        return None
    texto = re.sub(r"[^\d]", "", texto)
    return int(texto) if texto else None

def parse_fecha(texto):
    """'02/09/2024' → date(2024,9,2)"""
    if not texto:
        return None
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(texto.strip(), fmt).date()
        except ValueError:
            continue
    return None

def parse_duracion(texto):
    """'LARGOMETRAJE  / 110 min.' → 110"""
    if not texto:
        return None
    m = re.search(r"(\d+)\s*min", texto, re.IGNORECASE)
    return int(m.group(1)) if m else None

def get_label_value(soup, label_text):
    """Busca un <label> con ese texto y devuelve el texto del siguiente <label>."""
    labels = soup.find_all("label")
    for i, lbl in enumerate(labels):
        if lbl.get_text(strip=True).rstrip(":") == label_text.rstrip(":"):
            if i + 1 < len(labels):
                return labels[i + 1].get_text(strip=True)
    return None

# ─── Parser principal ───────────────────────────────────────────────────────────

def parsear_html(filepath):
    """
    Extrae todos los campos de una ficha ICAA.
    Devuelve un dict con los datos o None si el fichero no es una ficha válida.
    """
    expediente_id = filepath.stem  # nombre del fichero sin extensión

    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
        soup = BeautifulSoup(f.read(), "lxml")

    # ── Título ──
    titulo_el = soup.find("h2")
    titulo = titulo_el.get_text(strip=True) if titulo_el else None
    if not titulo:
        log.warning(f"  {filepath.name}: sin título, saltando")
        return None

    # ── Datos generales (pares de labels) ──
    raw = {
        "calificacion":    get_label_value(soup, "Calificación:"),
        "anio_produccion": get_label_value(soup, "Año de Producción:"),
        "fecha_estreno":   get_label_value(soup, "Fecha de Estreno:"),
        "duracion_raw":    get_label_value(soup, "Duración:"),
        "tipo":            get_label_value(soup, "Tipo:"),
        "genero":          get_label_value(soup, "Género:"),
        "recaudacion_raw": get_label_value(soup, "Recaudación:"),
        "espectadores_raw":get_label_value(soup, "Espectadores:"),
        "nacionalidad":    get_label_value(soup, "Nacionalidad:"),
    }

    # ── Sinopsis ──
    sinopsis = ""
    for lbl in soup.find_all("label"):
        if "Breve sinopsis" in lbl.get_text():
            next_lbl = lbl.find_next("label")
            if next_lbl:
                sinopsis = next_lbl.get_text(strip=True)
            break

    # ── Etiquetas ──
    etiquetas = []
    etiquetado_lbl = soup.find("label", string=lambda t: t and "Etiquetado" in t)
    if etiquetado_lbl:
        # Las etiquetas son labels consecutivos hasta el siguiente bloque
        nxt = etiquetado_lbl.find_next_sibling("label")
        while nxt and len(nxt.get_text(strip=True)) < 80:
            txt = nxt.get_text(strip=True)
            if txt and txt not in ["Sinopsis en Castellano:", "Sinopsis en Inglés:"]:
                etiquetas.append(txt)
            nxt = nxt.find_next_sibling("label")

    # ── Ficha artística (intérpretes y otras funciones) ──
    ficha_artistica = []
    for table in soup.find_all("table"):
        headers = [th.get_text(strip=True) for th in table.find_all("th", limit=3)]
        if headers and "Función" in headers and "Nombre" in headers and "Papel" in headers:
            for tr in table.find_all("tr")[1:]:  # saltar cabecera
                cells = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
                if len(cells) >= 2:
                    ficha_artistica.append({
                        "funcion": cells[0],
                        "nombre":  cells[1],
                        "papel":   cells[2] if len(cells) > 2 else ""
                    })
            break  # solo la primera tabla de este tipo

    # ── Ficha técnica (director, guión, producción…) ──
    ficha_tecnica = []
    director = ""
    for table in soup.find_all("table"):
        headers = [th.get_text(strip=True) for th in table.find_all("th", limit=3)]
        if headers and "Función" in headers and "Nombre" in headers and "Notas" in headers:
            for tr in table.find_all("tr")[1:]:
                cells = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
                if len(cells) >= 2:
                    entry = {
                        "funcion": cells[0],
                        "nombre":  cells[1],
                        "notas":   cells[2] if len(cells) > 2 else ""
                    }
                    ficha_tecnica.append(entry)
                    if "dirigido" in cells[0].lower() and not director:
                        director = cells[1]
            break

    # ── Empresas productoras ──
    empresas_productoras = []
    for div in soup.find_all("div", class_="hidden-panel-details"):
        # Buscar el panel de empresas productoras por el tab que lo precede
        prev_ul = div.find_previous("ul", class_="pro-details-tablist")
        if prev_ul and "PRODUCTORA" in prev_ul.get_text().upper():
            for lbl in div.find_all("label"):
                txt = lbl.get_text(strip=True)
                if txt and "Empresas Productoras" not in txt:
                    empresas_productoras.append(txt)
            if empresas_productoras:
                break  # solo el primer panel productoras

    # ── Distribuidoras ──
    distribuidoras_icaa = []
    for div in soup.find_all("div", class_="hidden-panel-details"):
        prev_ul = div.find_previous("ul", class_="pro-details-tablist")
        if prev_ul and "DISTRIBUIDORA" in prev_ul.get_text().upper():
            for lbl in div.find_all("label"):
                txt = lbl.get_text(strip=True)
                if txt:
                    distribuidoras_icaa.append(txt)
            if distribuidoras_icaa:
                break

    # ── Subvenciones públicas ──
    subvenciones = []
    subvenciones_total = None
    for table in soup.find_all("table"):
        headers = [th.get_text(strip=True) for th in table.find_all("th", limit=3)]
        if "Subvención" in headers and "Importe" in headers:
            importes = []
            for tr in table.find_all("tr")[1:]:
                cells = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
                if len(cells) >= 2:
                    importe = parse_euros(cells[1])
                    subvenciones.append({
                        "concepto": cells[0],
                        "importe":  importe
                    })
                    if importe:
                        importes.append(importe)
            if importes:
                subvenciones_total = sum(importes)

    # ── Información de rodaje ──
    rodaje = {}
    for table in soup.find_all("table"):
        headers = [th.get_text(strip=True) for th in table.find_all("th", limit=3)]
        if "Leyenda" in headers and "Informacion" in headers:
            for tr in table.find_all("tr")[1:]:
                cells = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
                if len(cells) >= 2:
                    key = cells[0].strip()
                    val = cells[1].strip()
                    if "inicio de rodaje" in key.lower():
                        rodaje["fecha_inicio_rodaje"] = val
                    elif "final de rodaje" in key.lower():
                        rodaje["fecha_fin_rodaje"] = val
                    elif "lugar" in key.lower():
                        rodaje.setdefault("lugares", []).append(val)

    # ── Premios ──
    premios = []
    for table in soup.find_all("table"):
        headers = [th.get_text(strip=True) for th in table.find_all("th", limit=3)]
        if "Premios obtenidos" in headers:
            for tr in table.find_all("tr")[1:]:
                cells = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
                if len(cells) >= 2:
                    premios.append({
                        "premio":   cells[0],
                        "seccion":  cells[1],
                        "persona":  cells[2] if len(cells) > 2 else ""
                    })

    # ── Festivales ──
    festivales = []
    for table in soup.find_all("table"):
        headers = [th.get_text(strip=True) for th in table.find_all("th", limit=2)]
        if "Participación en Festivales" in headers:
            for tr in table.find_all("tr")[1:]:
                cells = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
                if len(cells) >= 1:
                    festivales.append({
                        "festival": cells[0],
                        "seccion":  cells[1] if len(cells) > 1 else ""
                    })

    # ── Construir registro final ──
    return {
        "expediente_icaa":      expediente_id,
        "titulo":               titulo,
        "director":             director,
        "calificacion":         raw["calificacion"],
        "anio_produccion":      int(raw["anio_produccion"]) if raw["anio_produccion"] and raw["anio_produccion"].isdigit() else None,
        "fecha_estreno":        parse_fecha(raw["fecha_estreno"]),
        "duracion_min":         parse_duracion(raw["duracion_raw"]),
        "tipo":                 raw["tipo"],
        "genero":               raw["genero"],
        "recaudacion_eur":      parse_euros(raw["recaudacion_raw"]),
        "espectadores":         parse_entero(raw["espectadores_raw"]),
        "nacionalidad":         raw["nacionalidad"],
        "sinopsis":             sinopsis,
        "etiquetas":            etiquetas,
        "ficha_artistica":      ficha_artistica,
        "ficha_tecnica":        ficha_tecnica,
        "empresas_productoras": empresas_productoras,
        "distribuidoras":       distribuidoras_icaa,
        "subvenciones":         subvenciones,
        "subvenciones_total_eur": subvenciones_total,
        "fecha_inicio_rodaje":  parse_fecha(rodaje.get("fecha_inicio_rodaje")),
        "fecha_fin_rodaje":     parse_fecha(rodaje.get("fecha_fin_rodaje")),
        "lugares_rodaje":       rodaje.get("lugares", []),
        "premios":              premios,
        "festivales":           festivales,
    }

# ─── Base de datos ──────────────────────────────────────────────────────────────

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS icaa_fichas (
    -- Identificación
    expediente_icaa         TEXT PRIMARY KEY,
    titulo                  TEXT NOT NULL,
    director                TEXT,

    -- Datos generales
    calificacion            TEXT,
    anio_produccion         INTEGER,
    fecha_estreno           DATE,
    duracion_min            INTEGER,
    tipo                    TEXT,
    genero                  TEXT,
    nacionalidad            TEXT,

    -- Datos económicos
    recaudacion_eur         NUMERIC(14,2),   -- recaudación total en España
    espectadores            INTEGER,
    subvenciones_total_eur  NUMERIC(14,2),   -- suma de todas las subvenciones públicas

    -- Textos
    sinopsis                TEXT,
    etiquetas               TEXT[],

    -- Fichas (almacenadas como JSONB para flexibilidad)
    ficha_artistica         JSONB,           -- [{funcion, nombre, papel}]
    ficha_tecnica           JSONB,           -- [{funcion, nombre, notas}]
    empresas_productoras    TEXT[],
    distribuidoras          TEXT[],
    subvenciones            JSONB,           -- [{concepto, importe}]

    -- Rodaje
    fecha_inicio_rodaje     DATE,
    fecha_fin_rodaje        DATE,
    lugares_rodaje          TEXT[],

    -- Premios y festivales
    premios                 JSONB,           -- [{premio, seccion, persona}]
    festivales              JSONB,           -- [{festival, seccion}]

    -- Control
    created_at              TIMESTAMP DEFAULT NOW(),
    updated_at              TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS icaa_titulo_idx    ON icaa_fichas (titulo);
CREATE INDEX IF NOT EXISTS icaa_director_idx  ON icaa_fichas (director);
CREATE INDEX IF NOT EXISTS icaa_anio_idx      ON icaa_fichas (anio_produccion);
CREATE INDEX IF NOT EXISTS icaa_genero_idx    ON icaa_fichas (genero);
"""

UPSERT_SQL = """
INSERT INTO icaa_fichas (
    expediente_icaa, titulo, director, calificacion, anio_produccion,
    fecha_estreno, duracion_min, tipo, genero, nacionalidad,
    recaudacion_eur, espectadores, subvenciones_total_eur,
    sinopsis, etiquetas, ficha_artistica, ficha_tecnica,
    empresas_productoras, distribuidoras, subvenciones,
    fecha_inicio_rodaje, fecha_fin_rodaje, lugares_rodaje,
    premios, festivales, updated_at
) VALUES (
    %s, %s, %s, %s, %s,
    %s, %s, %s, %s, %s,
    %s, %s, %s,
    %s, %s, %s, %s,
    %s, %s, %s,
    %s, %s, %s,
    %s, %s, NOW()
)
ON CONFLICT (expediente_icaa) DO UPDATE SET
    titulo                  = EXCLUDED.titulo,
    director                = EXCLUDED.director,
    calificacion            = EXCLUDED.calificacion,
    anio_produccion         = EXCLUDED.anio_produccion,
    fecha_estreno           = EXCLUDED.fecha_estreno,
    duracion_min            = EXCLUDED.duracion_min,
    tipo                    = EXCLUDED.tipo,
    genero                  = EXCLUDED.genero,
    nacionalidad            = EXCLUDED.nacionalidad,
    recaudacion_eur         = EXCLUDED.recaudacion_eur,
    espectadores            = EXCLUDED.espectadores,
    subvenciones_total_eur  = EXCLUDED.subvenciones_total_eur,
    sinopsis                = EXCLUDED.sinopsis,
    etiquetas               = EXCLUDED.etiquetas,
    ficha_artistica         = EXCLUDED.ficha_artistica,
    ficha_tecnica           = EXCLUDED.ficha_tecnica,
    empresas_productoras    = EXCLUDED.empresas_productoras,
    distribuidoras          = EXCLUDED.distribuidoras,
    subvenciones            = EXCLUDED.subvenciones,
    fecha_inicio_rodaje     = EXCLUDED.fecha_inicio_rodaje,
    fecha_fin_rodaje        = EXCLUDED.fecha_fin_rodaje,
    lugares_rodaje          = EXCLUDED.lugares_rodaje,
    premios                 = EXCLUDED.premios,
    festivales              = EXCLUDED.festivales,
    updated_at              = NOW();
"""

def get_db():
    return psycopg2.connect(DB_DSN)

def crear_tabla(conn):
    with conn.cursor() as cur:
        cur.execute(CREATE_TABLE_SQL)
    conn.commit()
    log.info("Tabla 'icaa_fichas' e índices listos.")

def guardar(conn, datos):
    import json
    with conn.cursor() as cur:
        cur.execute(UPSERT_SQL, (
            datos["expediente_icaa"],
            datos["titulo"],
            datos["director"],
            datos["calificacion"],
            datos["anio_produccion"],
            datos["fecha_estreno"],
            datos["duracion_min"],
            datos["tipo"],
            datos["genero"],
            datos["nacionalidad"],
            datos["recaudacion_eur"],
            datos["espectadores"],
            datos["subvenciones_total_eur"],
            datos["sinopsis"],
            datos["etiquetas"],
            json.dumps(datos["ficha_artistica"],  ensure_ascii=False),
            json.dumps(datos["ficha_tecnica"],    ensure_ascii=False),
            datos["empresas_productoras"],
            datos["distribuidoras"],
            json.dumps(datos["subvenciones"],     ensure_ascii=False),
            datos["fecha_inicio_rodaje"],
            datos["fecha_fin_rodaje"],
            datos["lugares_rodaje"],
            json.dumps(datos["premios"],          ensure_ascii=False),
            json.dumps(datos["festivales"],       ensure_ascii=False),
        ))
    conn.commit()

# ─── Mostrar resumen ────────────────────────────────────────────────────────────

def mostrar(datos):
    d = datos
    print(f"\n{'='*64}")
    print(f"📽️  {d['titulo']}  [Expediente: {d['expediente_icaa']}]")
    print(f"{'='*64}")
    print(f"  Director:         {d['director']}")
    print(f"  Año producción:   {d['anio_produccion']}")
    print(f"  Fecha estreno:    {d['fecha_estreno']}")
    print(f"  Duración:         {d['duracion_min']} min")
    print(f"  Tipo / Género:    {d['tipo']} / {d['genero']}")
    print(f"  Calificación:     {d['calificacion']}")
    print(f"  Nacionalidad:     {d['nacionalidad']}")
    rec = f"{d['recaudacion_eur']:,.2f} €" if d["recaudacion_eur"] else "N/A"
    esp = f"{d['espectadores']:,}"        if d["espectadores"]    else "N/A"
    print(f"  Recaudación:      {rec}")
    print(f"  Espectadores:     {esp}")
    if d["subvenciones_total_eur"]:
        print(f"  Subvenciones:     {d['subvenciones_total_eur']:,.2f} € ({len(d['subvenciones'])} líneas)")
        for s in d["subvenciones"]:
            imp = f"{s['importe']:,.2f} €" if s["importe"] else "N/A"
            print(f"    · {s['concepto']}: {imp}")
    else:
        print(f"  Subvenciones:     N/A")
    print(f"  Productoras:      {' | '.join(d['empresas_productoras'][:3])}")
    print(f"  Distribuidoras:   {' | '.join(d['distribuidoras'][:2])}")
    if d["ficha_artistica"]:
        actores = [f"{r['nombre']} ({r['papel']})" for r in d["ficha_artistica"][:3] if r["funcion"] == "Intérpretes"]
        print(f"  Intérpretes:      {', '.join(actores)}")
    tecnica_items = {r["funcion"]: r["nombre"] for r in d["ficha_tecnica"]}
    if tecnica_items.get("Guión"):
        print(f"  Guión:            {tecnica_items['Guión']}")
    if d["premios"]:
        print(f"  Premios:          {len(d['premios'])} premios/nominaciones")
    if d["festivales"]:
        print(f"  Festivales:       {len(d['festivales'])} participaciones")
    if d["etiquetas"]:
        print(f"  Etiquetas:        {', '.join(d['etiquetas'])}")
    if d["sinopsis"]:
        print(f"  Sinopsis:         {d['sinopsis'][:120]}...")

# ─── Main ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Parser de fichas ICAA → PostgreSQL")
    parser.add_argument("--dry-run", action="store_true",
                        help="Muestra los datos extraídos sin guardar en BBDD")
    parser.add_argument("--limit", type=int, default=None,
                        help="Procesar solo los N primeros ficheros")
    parser.add_argument("--delete-parsed", action="store_true",
                        help="Borra el HTML del disco tras guardarlo correctamente en BBDD")
    args = parser.parse_args()

    html_files = sorted(HTML_DIR.glob("*.html"))
    if not html_files:
        log.error(f"No se encontraron ficheros HTML en {HTML_DIR}")
        exit(1)

    if args.limit:
        html_files = html_files[:args.limit]

    log.info(f"Procesando {len(html_files)} fichas HTML desde {HTML_DIR}")

    conn = None
    if not args.dry_run:
        conn = get_db()
        crear_tabla(conn)

    ok = errores = saltados = 0

    for i, filepath in enumerate(html_files, 1):
        log.info(f"[{i}/{len(html_files)}] {filepath.name}")
        try:
            datos = parsear_html(filepath)
            if not datos:
                saltados += 1
                continue

            mostrar(datos)

            if not args.dry_run and conn:
                guardar(conn, datos)
                log.info(f"  💾 Guardado en icaa_fichas.")
                if args.delete_parsed:
                    filepath.unlink()
                    log.info(f"  🗑️  HTML eliminado: {filepath.name}")
            ok += 1

        except Exception as e:
            log.error(f"  ❌ Error en {filepath.name}: {e}", exc_info=True)
            if conn:
                conn.rollback()
            errores += 1

    if conn:
        conn.close()

    print(f"\n{'='*64}")
    print(f"  ✅ Procesadas OK:  {ok}")
    print(f"  ⏭️  Saltadas:       {saltados}")
    print(f"  ❌ Errores:        {errores}")
    if args.dry_run:
        print(f"\nℹ️  Modo dry-run — nada guardado en BBDD.")
        print(f"   Ejecuta sin --dry-run para guardar.")
