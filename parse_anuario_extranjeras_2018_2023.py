#!/usr/bin/env python3
"""
Parse the 2018-2023 foreign-film anuario top-100 PDFs into a flat CSV.

Each PDF contains two tables: one sorted by spectators and one sorted by
revenue. The output joins both tables by year, normalized title and release
date, keeping the union of both rankings.
"""

import argparse
import csv
import re
import unicodedata
from pathlib import Path

import pdfplumber


DEFAULT_PDF_DIR = Path(
    "/Users/macmini/Library/CloudStorage/OneDrive-Personal/Documentos/"
    "01 - Proyectos/taquilla_app/anuarios"
)
DEFAULT_OUTPUT = Path("csv/anuario_peliculas_extranjeras_2018_2023.csv")

DATE_RE = re.compile(r"^\d{2}/\d{2}/\d{4}$")
INT_RE = re.compile(r"^\d{1,3}(?:\.\d{3})*$|^\d+$")
MONEY_RE = re.compile(r"^\d{1,3}(?:\.\d{3})*,\d{2}$|^\d+,\d{2}$")
COUNTRY_NAMES = {
    "alemania",
    "argentina",
    "australia",
    "bélgica",
    "belgica",
    "canadá",
    "canada",
    "china",
    "dinamarca",
    "españa",
    "estados",
    "francia",
    "irlanda",
    "italia",
    "japón",
    "japon",
    "méxico",
    "mexico",
    "países",
    "paises",
    "reino",
    "sin",
    "suecia",
}


def clean_text(value):
    if value is None:
        return ""
    value = str(value).replace("\n", " ")
    value = re.sub(r"\s+", " ", value).strip()
    return value


def normalize_key(value):
    value = clean_text(value).upper()
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def normalize_title(value):
    value = clean_text(value)
    return re.sub(r"(?<=[A-ZÁÉÍÓÚÑ])- (?=[A-ZÁÉÍÓÚÑ])", "", value)


def normalize_date(value):
    value = clean_text(value)
    if not DATE_RE.match(value):
        return value
    day, month, year = value.split("/")
    return f"{year}-{month}-{day}"


def parse_int(value):
    value = clean_text(value).replace(".", "")
    return value if value.isdigit() else ""


def parse_money(value):
    value = clean_text(value).replace("€", "").strip()
    if not value:
        return ""
    value = value.replace(".", "").replace(",", ".")
    try:
        return f"{float(value):.2f}"
    except ValueError:
        return ""


def is_noise(line):
    line_l = line.lower()
    if not line:
        return True
    markers = (
        "boletín informativo",
        "largometrajes extranjeros exhibidos",
        "en las dos tablas",
        "respectivamente",
        "ridad a la fecha",
        "taquilla con posterioridad",
        "primera tabla",
        "segunda tabla",
        "orden título",
        "orden titulo",
        "fecha estreno",
        "distribuidoras",
        "insertar tabla",
        "7.5 largometrajes",
        "desde su calificación",
        "desde su calificacion",
    )
    return any(marker in line_l for marker in markers)


def group_words_into_lines(page):
    words = page.extract_words(x_tolerance=1, y_tolerance=3, keep_blank_chars=False)
    groups = []
    for word in words:
        top = word["top"]
        if top < 45:
            continue
        placed = False
        for group in groups:
            if abs(group[0] - top) < 3:
                group[1].append(word)
                placed = True
                break
        if not placed:
            groups.append([top, [word]])

    return [
        sorted(line_words, key=lambda item: item["x0"])
        for _, line_words in sorted(groups, key=lambda item: item[0])
    ]


def year_layout(year, metric):
    if year == 2018:
        return {"order_left": 120, "title_left": 165, "title_right": 410, "date_left": 590}
    if year <= 2021:
        return {"order_left": 120, "title_left": 165, "title_right": 590, "date_left": 585}
    return {"order_left": 20, "title_left": 45, "title_right": 430, "date_left": 250}


def line_text(words):
    return clean_text(" ".join(word["text"] for word in words))


def first_date_index(words):
    for index, word in enumerate(words):
        if DATE_RE.match(word["text"]):
            return index
    return None


def first_order(words, layout):
    if not words:
        return None
    word = words[0]
    if word["x0"] < layout["order_left"] or not word["text"].isdigit():
        return None
    order = int(word["text"])
    return order if 1 <= order <= 100 else None


def extract_title(words, layout, date_index):
    title_words = []
    for index, word in enumerate(words):
        if index == 0 and word["text"].isdigit():
            continue
        if date_index is not None and index >= date_index:
            break
        if layout["title_left"] <= word["x0"] < layout["title_right"]:
            title_words.append(word["text"])
    return clean_text(" ".join(title_words))


def extract_metric_value(words, metric, date_index=None):
    relevant = words[date_index + 1 :] if date_index is not None else words
    values = []
    for word in relevant:
        text = word["text"].replace("€", "")
        if metric == "espectadores" and INT_RE.match(text):
            if word["text"].lower() not in COUNTRY_NAMES:
                values.append(text)
        elif metric == "recaudacion" and MONEY_RE.match(text):
            values.append(text)
    return values[-1] if values else ""


def title_fragment_before_value(words, metric, layout):
    fragment = []
    for word in words:
        text = word["text"].replace("€", "")
        if metric == "espectadores" and INT_RE.match(text):
            break
        if metric == "recaudacion" and MONEY_RE.match(text):
            break
        if word["x0"] < layout["title_right"]:
            fragment.append(word["text"])
    return clean_text(" ".join(fragment))


def should_append_title(previous_title, fragment, row_from_pending):
    if row_from_pending:
        return True
    previous = normalize_key(previous_title)
    fragment_key = normalize_key(fragment)
    if previous.endswith((" DE", " DEL", " LA", " EL", " LOS", " LAS", ":", "-")):
        return True
    return fragment_key.startswith(("DE ", "DEL "))


def append_title(row, fragment):
    row["titulo"] = normalize_title(f"{row['titulo']} {fragment}")


def parse_pdf(path, year):
    rows_by_metric = {"espectadores": [], "recaudacion": []}
    current_metric = None
    pending_title = ""
    last_row = None
    last_row_from_pending = False

    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            for words in group_words_into_lines(page):
                text = line_text(words)
                text_upper = text.upper()

                if text_upper.startswith("7.5") or "LARGOMETRAJES ESPAÑOLES Y EXTRANJEROS" in text_upper:
                    current_metric = None
                    pending_title = ""
                    last_row = None
                    last_row_from_pending = False
                    continue
                if "TABLA" in text_upper and "ESPECTADORES" in text_upper and "RECAUDACIÓN" not in text_upper:
                    current_metric = "espectadores"
                    pending_title = ""
                    last_row = None
                    last_row_from_pending = False
                    continue
                if "TABLA" in text_upper and "RECAUDACIÓN" in text_upper:
                    current_metric = "recaudacion"
                    pending_title = ""
                    last_row = None
                    last_row_from_pending = False
                    continue
                if not current_metric or is_noise(text):
                    continue

                layout = year_layout(year, current_metric)
                date_index = first_date_index(words)
                order = first_order(words, layout)
                value = extract_metric_value(words, current_metric, date_index)

                if order and date_index is not None:
                    row_title = extract_title(words, layout, date_index)
                    from_pending = False
                    if pending_title:
                        row_title = normalize_title(f"{pending_title} {row_title}".strip())
                        pending_title = ""
                        from_pending = True

                    row = {
                        "anio_anuario": year,
                        "titulo": normalize_title(row_title),
                        "fecha_estreno": normalize_date(words[date_index]["text"]),
                        current_metric: parse_int(value) if current_metric == "espectadores" else parse_money(value),
                    }
                    rows_by_metric[current_metric].append(row)
                    last_row = row
                    last_row_from_pending = from_pending
                    continue

                fragment = title_fragment_before_value(words, current_metric, layout)
                value = extract_metric_value(words, current_metric)
                if last_row and value and not last_row.get(current_metric):
                    if fragment:
                        append_title(last_row, fragment)
                    last_row[current_metric] = parse_int(value) if current_metric == "espectadores" else parse_money(value)
                    last_row_from_pending = False
                    continue

                if fragment and last_row and should_append_title(last_row["titulo"], fragment, last_row_from_pending):
                    append_title(last_row, fragment)
                    last_row_from_pending = False
                    continue

                if fragment and not DATE_RE.search(fragment):
                    pending_title = normalize_title(f"{pending_title} {fragment}".strip())

    return rows_by_metric


def merge_rows(rows_by_metric):
    merged = {}
    for metric, rows in rows_by_metric.items():
        for row in rows:
            if not row.get("titulo") or not row.get("fecha_estreno"):
                continue
            key = (row["anio_anuario"], normalize_key(row["titulo"]), row["fecha_estreno"])
            target = merged.setdefault(
                key,
                {
                    "anio_anuario": row["anio_anuario"],
                    "titulo": row["titulo"],
                    "fecha_estreno": row["fecha_estreno"],
                    "espectadores": "",
                    "recaudacion": "",
                },
            )
            target[metric] = row.get(metric, "")
    return list(merged.values())


def main():
    parser = argparse.ArgumentParser(description="Extrae top extranjeras 2018-2023 a CSV.")
    parser.add_argument("--pdf-dir", type=Path, default=DEFAULT_PDF_DIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--start-year", type=int, default=2018)
    parser.add_argument("--end-year", type=int, default=2023)
    args = parser.parse_args()

    all_rows = []
    counts = []
    for year in range(args.start_year, args.end_year + 1):
        path = args.pdf_dir / f"anuario-{year}-pelis-extranjeras.pdf"
        if not path.exists():
            raise FileNotFoundError(path)
        rows_by_metric = parse_pdf(path, year)
        merged = merge_rows(rows_by_metric)
        all_rows.extend(merged)
        counts.append((year, len(rows_by_metric["espectadores"]), len(rows_by_metric["recaudacion"]), len(merged)))

    all_rows.sort(key=lambda row: (row["anio_anuario"], normalize_key(row["titulo"]), row["fecha_estreno"]))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["anio_anuario", "titulo", "fecha_estreno", "espectadores", "recaudacion"]
    with args.output.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(all_rows)

    for year, espectadores, recaudacion, merged in counts:
        print(f"{year}: espectadores={espectadores} recaudacion={recaudacion} union={merged}")
    print(f"Total: {len(all_rows)} filas")
    print(f"CSV: {args.output}")


if __name__ == "__main__":
    main()
