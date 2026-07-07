"""
icaa_nuevas_parser.py — Parsea fichas ICAA generadas por el navegador automatizado
                        (pares HTML + JSON) y las inserta en la tabla icaa_fichas.

Estructura esperada en inputs/:
  inputs/
    nombre-descriptivo.html    ← ficha completa del catálogo ICAA
    nombre-descriptivo.json    ← manifiesto con detailUrl (contiene el expediente ID)

El JSON es la fuente primaria del expediente ICAA (extraído de detailUrl ?Pelicula=XXXXX).
Si no hay JSON, se extrae del propio HTML (label "Expediente ICAA:").

Uso:
  python3 icaa_nuevas_parser.py --dry-run          # Muestra lo extraído, sin escribir en BBDD
  python3 icaa_nuevas_parser.py                    # Procesa todos y guarda en icaa_fichas
  python3 icaa_nuevas_parser.py --limit 10         # Solo los 10 primeros
  python3 icaa_nuevas_parser.py --delete-parsed    # Borra los ficheros tras insertarlos OK
"""

import os
import re
import json
import logging
import argparse
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import psycopg2
from bs4 import BeautifulSoup
from dotenv import load_dotenv

# ─── Config ────────────────────────────────────────────────────────────────────

load_dotenv(Path(__file__).parent.parent / ".env")   # hereda el .env del proyecto raíz
DB_DSN    = os.getenv("DATABASE_URL", "postgresql://localhost/taquilla_app")
INPUTS    = Path(__file__).parent / "inputs"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]
)
log = logging.getLogger(__name__)

# ─── Helpers de parseo ─────────────────────────────────────────────────────────

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

# ─── Extracción del expediente ICAA ───────────────────────────────────────────

def expediente_desde_url(url):
    """'https://...?Pelicula=130921' → '130921'"""
    try:
        params = parse_qs(urlparse(url).query)
        return params.get("Pelicula", [None])[0]
    except Exception:
        return None

def expediente_desde_html(soup):
    """Extrae el expediente del label 'Expediente ICAA:' dentro del HTML."""
    return get_label_value(soup, "Expediente ICAA:")

# ─── Parser principal ──────────────────────────────────────────────────────────

def parsear_html(filepath, expediente_override=None):
    """
    Parsea una ficha ICAA y devuelve un dict con todos los campos.
    expediente_override: si se pasa, se usa como expediente_icaa (viene del JSON).
    """
    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
        soup = BeautifulSoup(f.read(), "lxml")

    # ── Expediente ICAA ──
    expediente_id = expediente_override or expediente_desde_html(soup)
    if not expediente_id:
        log.warning(f"  {filepath.name}: no se pudo determinar el expediente ICAA, saltando")
        return None

    # ── Título ──
    titulo_el = soup.find("h2")
    titulo = titulo_el.get_text(strip=True) if titulo_el else None
    if not titulo:
        log.warning(f"  {filepath.name}: sin título, saltando")
        return None

    # ── Datos generales ──
    raw = {
        "calificacion":     get_label_value(soup, "Calificación:"),
        "anio_produccion":  get_label_value(soup, "Año de Producción:"),
        "fecha_estreno":    get_label_value(soup, "Fecha de Estreno:"),
        "duracion_raw":     get_label_value(soup, "Duración:"),
        "tipo":             get_label_value(soup, "Tipo:"),
        "genero":           get_label_value(soup, "Género:"),
        "recaudacion_raw":  get_label_value(soup, "Recaudación:"),
        "espectadores_raw": get_label_value(soup, "Espectadores:"),
        "nacionalidad":     get_label_value(soup, "Nacionalidad:"),
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
        nxt = etiquetado_lbl.find_next_sibling("label")
        while nxt and len(nxt.get_text(strip=True)) < 80:
            txt = nxt.get_text(strip=True)
            if txt and txt not in ["Sinopsis en Castellano:", "Sinopsis en Inglés:"]:
                etiquetas.append(txt)
            nxt = nxt.find_next_sibling("label")

    # ── Ficha artística ──
    ficha_artistica = []
    for table in soup.find_all("table"):
        headers = [th.get_text(strip=True) for th in table.find_all("th", limit=3)]
        if "Función" in headers and "Nombre" in headers and "Papel" in headers:
            for tr in table.find_all("tr")[1:]:
                cells = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
                if len(cells) >= 2:
                    ficha_artistica.append({
                        "funcion": cells[0],
                        "nombre":  cells[1],
                        "papel":   cells[2] if len(cells) > 2 else ""
                    })
            break

    # ── Ficha técnica ──
    ficha_tecnica = []
    director = ""
    for table in soup.find_all("table"):
        headers = [th.get_text(strip=True) for th in table.find_all("th", limit=3)]
        if "Función" in headers and "Nombre" in headers and "Notas" in headers:
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
        prev_ul = div.find_previous("ul", class_="pro-details-tablist")
        if prev_ul and "PRODUCTORA" in prev_ul.get_text().upper():
            for lbl in div.find_all("label"):
                txt = lbl.get_text(strip=True)
                if txt and "Empresas Productoras" not in txt:
                    empresas_productoras.append(txt)
            if empresas_productoras:
                break

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

    # ── Subvenciones ──
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
                    subvenciones.append({"concepto": cells[0], "importe": importe})
                    if importe:
                        importes.append(importe)
            if importes:
                subvenciones_total = sum(importes)

    # ── Rodaje ──
    rodaje = {}
    for table in soup.find_all("table"):
        headers = [th.get_text(strip=True) for th in table.find_all("th", limit=3)]
        if "Leyenda" in headers and "Informacion" in headers:
            for tr in table.find_all("tr")[1:]:
                cells = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
                if len(cells) >= 2:
                    key, val = cells[0].strip(), cells[1].strip()
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
                        "premio":  cells[0],
                        "seccion": cells[1],
                        "persona": cells[2] if len(cells) > 2 else ""
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

    return {
        "expediente_icaa":        expediente_id,
        "titulo":                 titulo,
        "director":               director,
        "calificacion":           raw["calificacion"],
        "anio_produccion":        int(raw["anio_produccion"]) if raw["anio_produccion"] and raw["anio_produccion"].isdigit() else None,
        "fecha_estreno":          parse_fecha(raw["fecha_estreno"]),
        "duracion_min":           parse_duracion(raw["duracion_raw"]),
        "tipo":                   raw["tipo"],
        "genero":                 raw["genero"],
        "recaudacion_eur":        parse_euros(raw["recaudacion_raw"]),
        "espectadores":           parse_entero(raw["espectadores_raw"]),
        "nacionalidad":           raw["nacionalidad"],
        "sinopsis":               sinopsis,
        "etiquetas":              etiquetas,
        "ficha_artistica":        ficha_artistica,
        "ficha_tecnica":          ficha_tecnica,
        "empresas_productoras":   empresas_productoras,
        "distribuidoras":         distribuidoras_icaa,
        "subvenciones":           subvenciones,
        "subvenciones_total_eur": subvenciones_total,
        "fecha_inicio_rodaje":    parse_fecha(rodaje.get("fecha_inicio_rodaje")),
        "fecha_fin_rodaje":       parse_fecha(rodaje.get("fecha_fin_rodaje")),
        "lugares_rodaje":         rodaje.get("lugares", []),
        "premios":                premios,
        "festivales":             festivales,
    }

# ─── Base de datos ─────────────────────────────────────────────────────────────

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

def guardar(conn, datos):
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

# ─── Resumen por pantalla ──────────────────────────────────────────────────────

def mostrar(datos):
    d = datos
    print(f"\n{'='*64}")
    print(f"🎬  {d['titulo']}  [Expediente: {d['expediente_icaa']}]")
    print(f"{'='*64}")
    print(f"  Director:         {d['director']}")
    print(f"  Año producción:   {d['anio_produccion']}")
    print(f"  Fecha estreno:    {d['fecha_estreno']}")
    print(f"  Duración:         {d['duracion_min']} min")
    print(f"  Tipo / Género:    {d['tipo']} / {d['genero']}")
    print(f"  Calificación:     {d['calificacion']}")
    print(f"  Nacionalidad:     {d['nacionalidad']}")
    rec = f"{d['recaudacion_eur']:,.2f} €" if d["recaudacion_eur"] else "N/A"
    esp = f"{d['espectadores']:,}"         if d["espectadores"]    else "N/A"
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
    tecnica = {r["funcion"]: r["nombre"] for r in d["ficha_tecnica"]}
    if tecnica.get("Guión"):
        print(f"  Guión:            {tecnica['Guión']}")
    if d["premios"]:
        print(f"  Premios:          {len(d['premios'])} premios/nominaciones")
    if d["festivales"]:
        print(f"  Festivales:       {len(d['festivales'])} participaciones")
    if d["etiquetas"]:
        print(f"  Etiquetas:        {', '.join(d['etiquetas'])}")
    if d["sinopsis"]:
        print(f"  Sinopsis:         {d['sinopsis'][:120]}...")

# ─── Descubrimiento de ficheros ────────────────────────────────────────────────

def descubrir_pares(inputs_dir):
    """
    Devuelve lista de (html_path, expediente_id) iterando los JSON como manifiesto.
    Si no hay JSON para un HTML, intenta extraer el expediente del propio HTML.
    """
    pares = []
    html_sin_json = []

    json_files = sorted(inputs_dir.glob("*.json"))
    html_files = {f.stem: f for f in inputs_dir.glob("*.html")}

    # Primero, los que tienen JSON (fuente de verdad para el expediente)
    for jf in json_files:
        try:
            meta = json.loads(jf.read_text(encoding="utf-8"))
            expediente = expediente_desde_url(meta.get("detailUrl", ""))
            html_path = html_files.get(jf.stem)
            if not html_path:
                log.warning(f"JSON sin HTML correspondiente: {jf.name}")
                continue
            if not expediente:
                log.warning(f"No se pudo extraer expediente de detailUrl en {jf.name}, se intentará desde el HTML")
            pares.append((html_path, expediente))
        except Exception as e:
            log.error(f"Error leyendo JSON {jf.name}: {e}")

    # HTMLs sin JSON asociado
    jsons_con_html = {jf.stem for jf in json_files}
    for stem, html_path in html_files.items():
        if stem not in jsons_con_html:
            html_sin_json.append(html_path)

    if html_sin_json:
        log.info(f"{len(html_sin_json)} HTML(s) sin JSON — expediente se extraerá del HTML")
        for hp in html_sin_json:
            pares.append((hp, None))  # expediente_override=None → lo lee del HTML

    return pares

# ─── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Parser fichas ICAA (browser) → PostgreSQL"
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Muestra los datos extraídos sin guardar en BBDD")
    parser.add_argument("--limit", type=int, default=None,
                        help="Procesar solo los N primeros ficheros")
    parser.add_argument("--delete-parsed", action="store_true",
                        help="Borra HTML y JSON del disco tras insertar correctamente")
    parser.add_argument("--inputs-dir", type=Path, default=INPUTS,
                        help=f"Directorio con los pares HTML/JSON (default: {INPUTS})")
    args = parser.parse_args()

    inputs_dir = args.inputs_dir
    if not inputs_dir.exists():
        log.error(f"El directorio de inputs no existe: {inputs_dir}")
        exit(1)

    pares = descubrir_pares(inputs_dir)
    if not pares:
        log.error(f"No se encontraron ficheros HTML en {inputs_dir}")
        exit(1)

    if args.limit:
        pares = pares[: args.limit]

    log.info(f"Procesando {len(pares)} fichas desde {inputs_dir}")

    conn = None
    if not args.dry_run:
        conn = psycopg2.connect(DB_DSN)

    ok = errores = saltados = 0

    for i, (html_path, expediente_override) in enumerate(pares, 1):
        log.info(f"[{i}/{len(pares)}] {html_path.name}  →  expediente={expediente_override or '(desde HTML)'}")
        try:
            datos = parsear_html(html_path, expediente_override=expediente_override)
            if not datos:
                saltados += 1
                continue

            mostrar(datos)

            if not args.dry_run and conn:
                guardar(conn, datos)
                log.info(f"  💾 Guardado: {datos['titulo']} [{datos['expediente_icaa']}]")
                if args.delete_parsed:
                    html_path.unlink()
                    json_path = html_path.with_suffix(".json")
                    if json_path.exists():
                        json_path.unlink()
                    log.info(f"  🗑️  Ficheros eliminados: {html_path.name}")
            ok += 1

        except Exception as e:
            log.error(f"  ❌ Error en {html_path.name}: {e}", exc_info=True)
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
