#!/usr/bin/env python3
"""
Parse Ministerio de Cultura annual film-index PDFs into a flat CSV.

Input PDFs are expected to be named:
    anuario-YYYY-pelis.pdf

Output columns:
    anio_anuario,titulo,fecha_autorizacion,distribuidora,
    espectadores_anio,recaudacion_anio,recaudacion_desde_estreno,pais
"""

import argparse
import csv
import re
from decimal import Decimal
from pathlib import Path

import pdfplumber
from pypdf import PdfReader


DEFAULT_PDF_DIR = Path(
    "/Users/macmini/Library/CloudStorage/OneDrive-Personal/Documentos/"
    "01 - Proyectos/taquilla_app/anuarios"
)
DEFAULT_OUTPUT = Path("csv/anuario_peliculas_2003_2017.csv")

DATE_RE = re.compile(r"^\d{2}[/-]\d{2}[/-]\d{2,4}$")
INT_RE = re.compile(r"^\d{1,3}(?:\.\d{3})*$|^\d+$")
MONEY_RE = re.compile(r"^\d{1,3}(?:\.\d{3})*(?:,\d{2})?$|^\d+(?:,\d{2})?$")
COUNTRIES = [
    "COREA (REPUBLICA DE)",
    "COREA (REPÚBLICA DE)",
    "ESTADOS UNIDOS",
    "REINO UNIDO",
    "REPÚBLICA CHECA",
    "REPUBLICA CHECA",
    "SIN DETERMINAR",
    "NUEVA ZELANDA",
    "UNIÓN EUROPEA",
    "UNION EUROPEA",
    "ALEMANIA",
    "ARGENTINA",
    "AUSTRALIA",
    "AUSTRIA",
    "BÉLGICA",
    "BELGICA",
    "BOLIVIA",
    "BRASIL",
    "BULGARIA",
    "CANADÁ",
    "CANADA",
    "CHILE",
    "CHINA",
    "COLOMBIA",
    "CUBA",
    "DINAMARCA",
    "ESPAÑA",
    "EUROPEA",
    "FINLANDIA",
    "FRANCIA",
    "HOLANDA",
    "HONG KONG",
    "HUNGRÍA",
    "HUNGRIA",
    "INDIA",
    "IRÁN",
    "IRAN",
    "IRLANDA",
    "ISLANDIA",
    "ISRAEL",
    "ITALIA",
    "JAPÓN",
    "JAPON",
    "MÉXICO",
    "MEXICO",
    "NORUEGA",
    "PERÚ",
    "PERU",
    "POLONIA",
    "PORTUGAL",
    "RUMANÍA",
    "RUMANIA",
    "RUSIA",
    "SUECIA",
    "SUIZA",
    "URSS",
    "URUGUAY",
    "VENEZUELA",
    "YEMEN",
]
COUNTRY_RE = "|".join(re.escape(country) for country in sorted(COUNTRIES, key=len, reverse=True))
MONEY_TEXT_RE = r"\d[\d.\s]*(?:,\d{2})?"
TEXT_ROW_RE = re.compile(
    rf"(?P<title>.+?)\s+"
    rf"(?P<date>\d{{2}}[/-]\d{{2}}[/-]\d{{2,4}})\s+"
    rf"(?P<dist>.+?)\s*"
    rf"(?P<spect>\d[\d.\s]*?)\s*"
    rf"(?P<rec_year>{MONEY_TEXT_RE})\s*€\s+"
    rf"(?P<rec_total>{MONEY_TEXT_RE})\s*€\s*"
    rf"(?P<country>{COUNTRY_RE})",
    re.IGNORECASE,
)
TEXT_ROW_2007_RE = re.compile(
    rf"^(?P<title>.+?)\s+"
    rf"(?P<date>\d{{2}}[/-]\d{{2}}[/-]\d{{2,4}})\s+"
    rf"(?P<spect>\d[\d.\s]*)\s+"
    rf"(?P<rec_year>{MONEY_TEXT_RE})\s*€\s+"
    rf"(?P<rec_total>{MONEY_TEXT_RE})\s*€\s*"
    rf"(?P<country>{COUNTRY_RE})\s*"
    rf"(?P<dist>.*)$",
    re.IGNORECASE,
)
RIGHT_TEXT_ROW_RE = re.compile(
    rf"^(?P<title>.+?)\s+"
    rf"(?P<date>\d{{2}}[/-]\d{{2}}[/-]\d{{2,4}})\s+"
    rf"(?P<dist>.+)\s+"
    rf"(?P<spect>\d{{1,3}}(?:\.\d{{3}})*|\d+)\s+"
    rf"(?P<rec_year>{MONEY_TEXT_RE})\s*€\s+"
    rf"(?P<rec_total>{MONEY_TEXT_RE})\s*€\s*"
    rf"(?P<country>{COUNTRY_RE})$",
    re.IGNORECASE,
)
HEADER_MARKERS = (
    "boletín informativo",
    "instituto de la cinematografía",
    "índice alfabético",
    "indice alfabetico",
    "en la siguiente tabla",
    "la recaudación obtenida",
    "la recaudacion obtenida",
    "de la primera calificación",
    "autorización es la fecha",
    "junto a la distribuidora",
    "que haya efectuado",
    "boletin ",
    ".indd",
    "título fecha",
    "titulo fecha",
    "autorización distribuidora",
    "autorizacion distribuidora",
    "desde su estreno",
    "nacionalidad",
    "recaudación",
    "recaudacion",
    "espectadores",
    "dores en",
    "el año",
    "en el año",
)


def clean_text(value):
    if value is None:
        return ""
    value = str(value).replace("\n", " ")
    value = re.sub(r"\s+", " ", value).strip()
    return value


def clean_pdf_line(value):
    value = clean_text(value)
    value = value.replace("\u00a0", " ")
    value = re.sub(r"(?<=\d)\.\s+(?=\d{3})", ".", value)
    value = re.sub(r"(?<=\d)\s+(?=\d{3}(?:\D|$))", "", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def compact_date(value):
    return re.sub(r"\s+", "", clean_text(value))


def compact_number(value):
    return clean_text(value).replace("€", "").replace(" ", "")


def normalize_title(value):
    value = clean_text(value)
    return re.sub(r"(?<=[A-ZÁÉÍÓÚÑ])- (?=[A-ZÁÉÍÓÚÑ])", "", value)


def parse_int(value):
    value = clean_text(value)
    if not value:
        return None
    value = value.replace(".", "").replace(" ", "")
    return int(value) if value.isdigit() else None


def parse_money(value):
    value = clean_text(value).replace("€", "").strip()
    if not value:
        return None
    value = value.replace(".", "").replace(" ", "").replace(",", ".")
    try:
        return Decimal(value)
    except Exception:
        return None


def normalize_date(value, anio_anuario):
    value = compact_date(value)
    if not value:
        return ""
    match = re.match(r"^(\d{2})[/-](\d{2})[/-](\d{2}|\d{4})$", value)
    if not match:
        return value

    day, month, year = match.groups()
    if len(year) == 2:
        yy = int(year)
        cutoff = anio_anuario % 100
        year = 2000 + yy if yy <= cutoff else 1900 + yy
    else:
        year = int(year)
    return f"{year:04d}-{int(month):02d}-{int(day):02d}"


def normalize_money_for_csv(value):
    if value is None:
        return ""
    return f"{value:.2f}"


def is_data_row(row):
    date = compact_date(row.get("fecha_autorizacion"))
    espectadores = compact_number(row.get("espectadores_anio"))
    rec_anyo = compact_number(row.get("recaudacion_anio"))
    rec_total = compact_number(row.get("recaudacion_desde_estreno"))
    return (
        bool(clean_text(row.get("titulo")))
        and (not date or DATE_RE.match(date))
        and INT_RE.match(espectadores or "")
        and MONEY_RE.match(rec_anyo.replace("€", "").strip())
        and MONEY_RE.match(rec_total.replace("€", "").strip())
    )


def row_to_output(row, anio_anuario):
    return {
        "anio_anuario": anio_anuario,
        "titulo": normalize_title(row.get("titulo")),
        "fecha_autorizacion": normalize_date(row.get("fecha_autorizacion"), anio_anuario),
        "distribuidora": clean_text(row.get("distribuidora")),
        "espectadores_anio": parse_int(row.get("espectadores_anio")),
        "recaudacion_anio": normalize_money_for_csv(parse_money(row.get("recaudacion_anio"))),
        "recaudacion_desde_estreno": normalize_money_for_csv(parse_money(row.get("recaudacion_desde_estreno"))),
        "pais": clean_text(row.get("pais")),
    }


def is_header_or_noise(line):
    line_l = line.lower()
    if not line or line in {"-", "1", "2", "3", "4", "5"}:
        return True
    return any(marker in line_l for marker in HEADER_MARKERS)


def match_text_row(line, year):
    line = clean_pdf_line(line)
    match = (TEXT_ROW_2007_RE if year == 2007 else TEXT_ROW_RE).match(line)
    if not match:
        return None
    row = {
        "titulo": match.group("title"),
        "fecha_autorizacion": match.group("date") or "",
        "distribuidora": match.group("dist"),
        "espectadores_anio": match.group("spect"),
        "recaudacion_anio": match.group("rec_year"),
        "recaudacion_desde_estreno": match.group("rec_total"),
        "pais": match.group("country"),
    }
    if not INT_RE.match(clean_text(row["espectadores_anio"]).replace(" ", "")):
        return None
    return row


def match_right_text_row(line):
    line = clean_pdf_line(line)
    line = re.sub(
        r"(\d{1,3}(?:\.\d{3})+)(\d{1,3}\.\d{3},\d{2}\s*€)",
        r"\1 \2",
        line,
    )
    line = re.sub(r"(?<=\s)(\d{1,3})(\d{3},\d{2}\s*€)", r"\1 \2", line)
    match = RIGHT_TEXT_ROW_RE.match(line)
    if not match:
        return None
    return {
        "titulo": match.group("title"),
        "fecha_autorizacion": match.group("date") or "",
        "distribuidora": match.group("dist"),
        "espectadores_anio": match.group("spect"),
        "recaudacion_anio": match.group("rec_year"),
        "recaudacion_desde_estreno": match.group("rec_total"),
        "pais": match.group("country"),
    }


def parse_2007_by_pdfplumber_text(path, year):
    rows = []
    previous_row = None
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            for raw_line in (page.extract_text() or "").splitlines():
                line = clean_pdf_line(raw_line)
                if is_header_or_noise(line):
                    continue
                row = match_right_text_row(line)
                if row:
                    if previous_row:
                        rows.append(row_to_output(previous_row, year))
                    previous_row = row
                elif previous_row and not DATE_RE.search(line) and "€" not in line:
                    append_field(previous_row, "distribuidora", line)
    if previous_row:
        rows.append(row_to_output(previous_row, year))
    return rows


def parse_by_text(path, year):
    reader = PdfReader(str(path))
    rows = []

    if year == 2007:
        previous_row = None
        for page in reader.pages:
            text = page.extract_text() or ""
            for raw_line in text.splitlines():
                line = clean_pdf_line(raw_line)
                if is_header_or_noise(line):
                    continue
                row = match_text_row(line, year)
                if row:
                    if previous_row:
                        rows.append(row_to_output(previous_row, year))
                    previous_row = row
                elif previous_row and not DATE_RE.search(line) and not "€" in line:
                    append_field(previous_row, "distribuidora", line)
        if previous_row:
            rows.append(row_to_output(previous_row, year))
        return rows

    for page in reader.pages:
        text = clean_pdf_line(page.extract_text() or "")
        text = re.sub(r"\s+", " ", text)
        last_end = 0
        for match in TEXT_ROW_RE.finditer(text):
            title = clean_pdf_line(match.group("title"))
            # Remove leftover page headers that may precede the first row on a page.
            for marker in ("Recaudación ", "Recaudacion ", "Nacionalidad ", "País "):
                if marker in title:
                    title = title.split(marker)[-1].strip()
            row = {
                "titulo": title,
                "fecha_autorizacion": match.group("date"),
                "distribuidora": match.group("dist"),
                "espectadores_anio": match.group("spect"),
                "recaudacion_anio": match.group("rec_year"),
                "recaudacion_desde_estreno": match.group("rec_total"),
                "pais": match.group("country"),
            }
            rows.append(row_to_output(row, year))
            last_end = match.end()
    return rows


def append_field(row, field, value):
    value = clean_text(value)
    if not value:
        return
    if row.get(field):
        row[field] = f"{row[field]} {value}"
    else:
        row[field] = value


def column_bounds(year):
    if year <= 2007:
        return {
            "titulo": (0, 255),
            "fecha_autorizacion": (255, 310),
            "distribuidora": (310, 500),
            "espectadores_anio": (500, 545),
            "recaudacion_anio": (545, 610),
            "recaudacion_desde_estreno": (610, 685),
            "pais": (685, 842),
        }
    if year == 2008:
        return {
            "titulo": (0, 260),
            "fecha_autorizacion": (260, 315),
            "distribuidora": (315, 485),
            "espectadores_anio": (485, 550),
            "recaudacion_anio": (550, 610),
            "recaudacion_desde_estreno": (610, 670),
            "pais": (670, 842),
        }
    if year == 2009:
        return {
            "titulo": (0, 265),
            "fecha_autorizacion": (265, 310),
            "distribuidora": (310, 493),
            "espectadores_anio": (493, 535),
            "recaudacion_anio": (535, 620),
            "recaudacion_desde_estreno": (620, 698),
            "pais": (698, 842),
        }
    if year == 2010:
        return {
            "titulo": (0, 285),
            "fecha_autorizacion": (285, 340),
            "distribuidora": (340, 529),
            "espectadores_anio": (529, 570),
            "recaudacion_anio": (570, 640),
            "recaudacion_desde_estreno": (640, 715),
            "pais": (715, 842),
        }
    if year <= 2012:
        return {
            "titulo": (0, 305),
            "fecha_autorizacion": (305, 350),
            "distribuidora": (350, 525),
            "espectadores_anio": (525, 570),
            "recaudacion_anio": (570, 640),
            "recaudacion_desde_estreno": (640, 720),
            "pais": (720, 842),
        }
    if year <= 2014:
        return {
            "titulo": (0, 290),
            "fecha_autorizacion": (290, 340),
            "distribuidora": (340, 515),
            "espectadores_anio": (515, 565),
            "recaudacion_anio": (565, 625),
            "recaudacion_desde_estreno": (625, 700),
            "pais": (700, 842),
        }
    return {
        "titulo": (0, 290),
        "fecha_autorizacion": (290, 340),
        "distribuidora": (340, 515),
        "espectadores_anio": (515, 565),
        "recaudacion_anio": (565, 630),
        "recaudacion_desde_estreno": (630, 700),
        "pais": (700, 842),
    }


def words_to_lines(page):
    words = page.extract_words(x_tolerance=1, y_tolerance=3, keep_blank_chars=False)
    groups = []
    for word in words:
        top = word["top"]
        if top < 60:
            continue
        placed = False
        for group in groups:
            if abs(group[0] - top) < 3:
                group[1].append(word)
                placed = True
                break
        if not placed:
            groups.append([top, [word]])

    lines = []
    for top, line_words in sorted(groups, key=lambda item: item[0]):
        lines.append(sorted(line_words, key=lambda item: item["x0"]))
    return lines


def split_line_by_columns(words, bounds):
    row = {field: "" for field in bounds}
    for word in words:
        x = word["x0"]
        for field, (left, right) in bounds.items():
            if left <= x < right:
                append_field(row, field, word["text"])
                break
    return row


def looks_like_start(row):
    date = compact_date(row.get("fecha_autorizacion"))
    return bool(clean_text(row.get("titulo"))) and looks_like_data_values(row, date)


def looks_like_data_values(row, date=None):
    if date is None:
        date = compact_date(row.get("fecha_autorizacion"))
    has_date = bool(DATE_RE.match(date))
    has_numbers = (
        INT_RE.match(compact_number(row.get("espectadores_anio")) or "")
        and clean_text(row.get("recaudacion_anio"))
        and clean_text(row.get("recaudacion_desde_estreno"))
    )
    return has_date and has_numbers


def parse_by_coordinates(pdf, year):
    bounds = column_bounds(year)
    rows = []
    current = None
    pending_title = ""
    current_from_pending_title = False

    for page in pdf.pages:
        for words in words_to_lines(page):
            line = split_line_by_columns(words, bounds)
            full_line_text = clean_text(" ".join(clean_text(line.get(field)) for field in bounds))
            if is_header_or_noise(full_line_text):
                continue

            line_title = clean_text(line.get("titulo"))
            title_only = (
                bool(line_title)
                and not compact_date(line.get("fecha_autorizacion"))
                and not clean_text(line.get("distribuidora"))
                and not clean_text(line.get("espectadores_anio"))
                and not clean_text(line.get("recaudacion_anio"))
                and not clean_text(line.get("recaudacion_desde_estreno"))
                and not clean_text(line.get("pais"))
            )

            if title_only:
                if current and is_data_row(current):
                    if current_from_pending_title:
                        append_field(current, "titulo", line_title)
                        current_from_pending_title = False
                    else:
                        rows.append(row_to_output(current, year))
                        current = None
                        pending_title = line_title
                elif current:
                    append_field(current, "titulo", line_title)
                else:
                    pending_title = f"{pending_title} {line_title}".strip()
                continue

            if looks_like_start(line) or (pending_title and looks_like_data_values(line)):
                if current and is_data_row(current):
                    rows.append(row_to_output(current, year))
                if pending_title:
                    line["titulo"] = f"{pending_title} {line_title}".strip()
                    pending_title = ""
                    current_from_pending_title = True
                else:
                    current_from_pending_title = False
                current = line
            elif current:
                for field in ("titulo", "distribuidora", "pais"):
                    append_field(current, field, line.get(field))
                current_from_pending_title = False

    if current and is_data_row(current):
        rows.append(row_to_output(current, year))
    return rows


def parse_by_tables(pdf, year):
    rows = []
    for page in pdf.pages:
        for table in page.extract_tables() or []:
            for raw in table:
                if not raw or len(raw) < 7:
                    continue
                row = {
                    "titulo": raw[0],
                    "fecha_autorizacion": raw[1],
                    "distribuidora": raw[2],
                    "espectadores_anio": raw[3],
                    "recaudacion_anio": raw[4],
                    "recaudacion_desde_estreno": raw[5],
                    "pais": raw[6],
                }
                if is_data_row(row):
                    rows.append(row_to_output(row, year))
    return rows


def parse_pdf(path, year):
    if year == 2007:
        return parse_2007_by_pdfplumber_text(path, year)
    with pdfplumber.open(path) as pdf:
        return parse_by_coordinates(pdf, year)


def parse_year_from_name(path):
    match = re.search(r"anuario-(\d{4})-pelis\.pdf$", path.name)
    if not match:
        raise ValueError(f"No puedo extraer el año de {path.name}")
    return int(match.group(1))


def main():
    parser = argparse.ArgumentParser(description="Extrae películas de anuarios del Ministerio a CSV.")
    parser.add_argument("--pdf-dir", type=Path, default=DEFAULT_PDF_DIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--start-year", type=int, default=2003)
    parser.add_argument("--end-year", type=int, default=2017)
    args = parser.parse_args()

    all_rows = []
    counts = []
    for year in range(args.start_year, args.end_year + 1):
        path = args.pdf_dir / f"anuario-{year}-pelis.pdf"
        if not path.exists():
            raise FileNotFoundError(path)
        rows = parse_pdf(path, parse_year_from_name(path))
        all_rows.extend(rows)
        counts.append((year, len(rows)))

    args.output.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "anio_anuario",
        "titulo",
        "fecha_autorizacion",
        "distribuidora",
        "espectadores_anio",
        "recaudacion_anio",
        "recaudacion_desde_estreno",
        "pais",
    ]
    with args.output.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(all_rows)

    for year, count in counts:
        print(f"{year}: {count} filas")
    print(f"Total: {len(all_rows)} filas")
    print(f"CSV: {args.output}")


if __name__ == "__main__":
    main()
