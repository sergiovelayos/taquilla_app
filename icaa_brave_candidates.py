#!/usr/bin/env python3
"""
icaa_brave_candidates.py — busca candidatos de expediente ICAA con Brave.

El script es read-only: no escribe en la base de datos. Sirve para validar y
auditar mapeos de peliculas de anual_esp usando:

  1. variantes de titulo ("Tribu, La" -> "La Tribu")
  2. Brave Search limitado a sede.mcu.gob.es/CatalogoICAA
  3. descarga de la ficha oficial candidata
  4. scoring por titulo + fecha de estreno

Entrada TSV/CSV esperada por defecto, sin cabecera:

    2018-03-16<TAB>Tribu, La
    2018-08-31<TAB>Yucatan

Tambien acepta CSV con cabecera:

    "titulo_normalizado","fecha_estreno"
    "tribu la","2018-03-16"

Uso:

    python3 icaa_brave_candidates.py --input pelis.tsv
    python3 icaa_brave_candidates.py --input pelis.tsv --format json
    cat pelis.tsv | python3 icaa_brave_candidates.py --stdin

.env:
    BRAVE_API_KEY=...
"""

import argparse
import csv
import datetime as dt
import html
import json
import os
import re
import sys
import time
import unicodedata
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple

import requests
from dotenv import load_dotenv

try:
    import urllib3

    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
except Exception:
    urllib3 = None


BRAVE_API_URL = "https://api.search.brave.com/res/v1/web/search"
ICAA_SITE = "site:sede.mcu.gob.es CatalogoICAA"
DETAIL_URL = "https://sede.mcu.gob.es/CatalogoICAA/es-es/Peliculas/Detalle?Pelicula={}"

ID_RE = re.compile(r"[?&]pelicula=(\d+)", re.IGNORECASE)
CARATULA_ID_RE = re.compile(r"/Caratulas/(\d+)/|/P(\d+)\.pdf", re.IGNORECASE)

ARTICLES = {"el", "la", "los", "las", "un", "una", "unos", "unas"}
SOFT_STOPWORDS = ARTICLES | {"y", "e", "de", "del", "en", "el", "la", "los", "las"}

# Ruidos observados en titulos importados desde rankings/feeds.
TRAILING_NOISE = (
    " Entertainment",
    " European",
    " phoenix",
    " Phoenix",
    " avalon",
    " Avalon",
)


@dataclass
class Movie:
    fecha_iso: str
    titulo: str


@dataclass
class Candidate:
    expediente: str
    source_url: str
    query: str
    query_kind: str
    brave_title: str
    brave_description: str


@dataclass
class Detail:
    expediente: str
    url: str
    text: str
    titulo: str
    fecha_estreno: str


def strip_accents(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text or "")
    return nfkd.encode("ascii", "ignore").decode()


def normalize(text: str) -> str:
    text = strip_accents(text).lower()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def clean_title_noise(titulo: str) -> str:
    title = re.sub(r"\([^)]*\)", " ", titulo or "").strip()
    title = re.split(r"\s+se realiza siguiendo\b", title, maxsplit=1, flags=re.IGNORECASE)[0]
    for suffix in TRAILING_NOISE:
        if title.lower().endswith(suffix.lower()):
            title = title[: -len(suffix)].strip()
    return re.sub(r"\s+", " ", title).strip(" ,")


def move_trailing_article(titulo: str) -> str:
    match = re.match(
        r"^(.+),\s*(El|La|Los|Las|Un|Una|Unos|Unas)$",
        titulo.strip(),
        flags=re.IGNORECASE,
    )
    if not match:
        parts = titulo.strip().split()
        if len(parts) > 1 and parts[-1].lower() in ARTICLES:
            return f"{parts[-1]} {' '.join(parts[:-1])}"
        return titulo
    return f"{match.group(2)} {match.group(1)}"


def title_variants(titulo: str) -> List[str]:
    base = clean_title_noise(titulo)
    moved = move_trailing_article(base)

    variants: List[str] = []
    for value in (
        moved,
        base,
        re.sub(r"[^\w\s]", " ", moved),
        re.sub(r"[^\w\s]", " ", base),
        strip_accents(moved),
        strip_accents(base),
        strip_accents(re.sub(r"[^\w\s]", " ", moved)),
        strip_accents(re.sub(r"[^\w\s]", " ", base)),
        moved.upper(),
        base.upper(),
    ):
        value = re.sub(r"\s+", " ", value).strip(" ,")
        if value and value not in variants:
            variants.append(value)

    # Si hay subtitulo, a veces el ICAA indexa solo el titulo principal.
    for value in list(variants):
        head = re.split(r"[:;]", value, maxsplit=1)[0].strip()
        if head and head not in variants:
            variants.append(head)

    return variants


def match_words(titulo: str) -> List[str]:
    canonical = normalize(move_trailing_article(clean_title_noise(titulo)))
    return [
        word
        for word in canonical.split()
        if len(word) > 2 and word not in SOFT_STOPWORDS
    ]


def title_score(titulo: str, haystack: str) -> float:
    words = match_words(titulo)
    if not words:
        return 0.0
    hay = normalize(haystack)
    return sum(1 for word in words if word in hay) / len(words)


def title_score_strict(titulo: str, candidate_title: str) -> float:
    """Score simetrico para titulos oficiales ya parseados.

    Evita que titulos de una sola palabra acepten expansiones distintas:
    "Oro" no debe validar automaticamente "Oro rojo".
    """
    left = set(match_words(titulo))
    right = set(match_words(candidate_title))
    if not left or not right:
        return 0.0
    return len(left & right) / max(len(left), len(right))


def iso_to_ddmmyyyy(fecha_iso: str) -> str:
    return dt.date.fromisoformat(fecha_iso).strftime("%d/%m/%Y")


def ddmmyyyy_to_date(value: str) -> Optional[dt.date]:
    try:
        return dt.datetime.strptime(value, "%d/%m/%Y").date()
    except ValueError:
        return None


def days_apart(fecha_iso: str, fecha_ddmmyyyy: str) -> Optional[int]:
    expected = dt.date.fromisoformat(fecha_iso)
    actual = ddmmyyyy_to_date(fecha_ddmmyyyy)
    if not actual:
        return None
    return abs((actual - expected).days)


def extract_id_from_url(url: str) -> Optional[str]:
    match = ID_RE.search(url or "")
    if match:
        return match.group(1)

    match = CARATULA_ID_RE.search(url or "")
    if match:
        return next(group for group in match.groups() if group)

    return None


def html_to_text(raw_html: str) -> str:
    raw_html = re.sub(r"(?is)<script.*?</script>|<style.*?</style>", " ", raw_html)
    raw_html = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>|</tr>", " ", raw_html)
    text = re.sub(r"(?s)<[^>]+>", " ", raw_html)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def extract_detail_title(text: str) -> str:
    patterns = (
        r"VÍNCULOS Y REDES SOCIALES\s+(.{1,140}?)\s+catálogo",
        r"VINCULOS Y REDES SOCIALES\s+(.{1,140}?)\s+catalogo",
        r"Download movie Pdf\s+.{0,80}?\s+([A-ZÁÉÍÓÚÜÑ¿¡0-9][^|]{1,120}?)\s+catálogo",
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return re.sub(r"\s+", " ", match.group(1)).strip(" -|")

    # Fallback para snippets indexados o PDFs.
    match = re.search(r"(?:Titulo Principal|Título Original):\s*(.{1,100}?)(?:\s+Dirigido|\s+Título|\s+Calificación|$)", text, flags=re.IGNORECASE)
    if match:
        return re.sub(r"\s+", " ", match.group(1)).strip(" -|")

    return ""


def extract_release_date(text: str) -> str:
    match = re.search(r"Fecha de Estreno:\s*(\d{2}/\d{2}/\d{4})", text, flags=re.IGNORECASE)
    if match:
        return match.group(1)

    # En algunos PDFs/carátulas aparece en bilingüe.
    match = re.search(r"(?:Estreno en España|Spain Release):\s*(\d{2}/\d{2}/\d{4})", text, flags=re.IGNORECASE)
    if match:
        return match.group(1)

    return ""


class ICAAResolver:
    def __init__(
        self,
        api_key: str,
        delay: float,
        timeout: float,
        max_queries: int,
        max_candidates: int,
        verbose: bool = False,
    ):
        self.api_key = api_key
        self.delay = delay
        self.timeout = timeout
        self.max_queries = max_queries
        self.max_candidates = max_candidates
        self.verbose = verbose
        self.session = requests.Session()
        self.detail_cache: Dict[str, Detail] = {}
        self.query_count = 0
        self.detail_count = 0

    def brave_search(self, query: str) -> List[dict]:
        headers = {
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
            "X-Subscription-Token": self.api_key,
        }
        params = {"q": query, "count": 10}
        response = self.session.get(
            BRAVE_API_URL,
            headers=headers,
            params=params,
            timeout=self.timeout,
        )
        self.query_count += 1
        if self.delay:
            time.sleep(self.delay)
        if response.status_code != 200:
            if self.verbose:
                print(f"# Brave HTTP {response.status_code}: {query}", file=sys.stderr)
            return []
        return response.json().get("web", {}).get("results", [])

    def build_queries(self, movie: Movie) -> List[Tuple[str, str]]:
        fecha = iso_to_ddmmyyyy(movie.fecha_iso)
        year = movie.fecha_iso[:4]
        queries: List[Tuple[str, str]] = []

        variants = title_variants(movie.titulo)

        # Primero: alta precision. No siempre devuelve resultados, pero cuando
        # devuelve uno suele venir con fecha en snippet/PDF.
        for variant in variants[:3]:
            queries.append((f'{ICAA_SITE} "{variant}" "{fecha}"', "titulo_fecha"))

        # Segundo: titulo + año. Suele encontrar PDFs/fichas recientes.
        for variant in variants[:3]:
            queries.append((f'{ICAA_SITE} "{variant}" "{year}"', "titulo_anio"))

        # Tercero: titulo solo, para casos como Yucatan.
        for variant in variants[:3]:
            queries.append((f'{ICAA_SITE} "{variant}"', "titulo"))

        # Ultimo recurso: sin comillas, porque Brave a veces recupera mas.
        for variant in variants[:2]:
            queries.append((f"{ICAA_SITE} {variant}", "titulo_flexible"))

        seen = set()
        unique: List[Tuple[str, str]] = []
        for query, kind in queries:
            if query not in seen:
                seen.add(query)
                unique.append((query, kind))
        return unique[: self.max_queries]

    def collect_candidates(self, movie: Movie) -> List[Candidate]:
        candidates: List[Candidate] = []
        seen_ids = set()
        for query, kind in self.build_queries(movie):
            for result in self.brave_search(query):
                url = result.get("url", "")
                exp_id = extract_id_from_url(url)
                if not exp_id or exp_id in seen_ids:
                    continue
                seen_ids.add(exp_id)
                candidates.append(
                    Candidate(
                        expediente=exp_id,
                        source_url=url,
                        query=query,
                        query_kind=kind,
                        brave_title=result.get("title", ""),
                        brave_description=result.get("description", ""),
                    )
                )
                if len(candidates) >= self.max_candidates:
                    return candidates
        return candidates

    def fetch_detail(self, expediente: str) -> Detail:
        if expediente in self.detail_cache:
            return self.detail_cache[expediente]

        url = DETAIL_URL.format(expediente)
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
            ),
            "Accept-Language": "es-ES,es;q=0.9",
        }
        response = self.session.get(url, headers=headers, timeout=self.timeout, verify=False)
        self.detail_count += 1
        text = html_to_text(response.text if response.status_code == 200 else "")
        detail = Detail(
            expediente=expediente,
            url=url,
            text=text,
            titulo=extract_detail_title(text),
            fecha_estreno=extract_release_date(text),
        )
        self.detail_cache[expediente] = detail
        return detail

    def evaluate(self, movie: Movie, candidate: Candidate) -> dict:
        detail = self.fetch_detail(candidate.expediente)
        expected_date = iso_to_ddmmyyyy(movie.fecha_iso)
        if detail.titulo:
            candidate_title_text = detail.titulo
            score = title_score_strict(movie.titulo, candidate_title_text)
        else:
            candidate_title_text = " ".join(
                part
                for part in (
                    candidate.brave_title,
                    candidate.brave_description,
                )
                if part
            )
            score = title_score(movie.titulo, candidate_title_text)
        exact_date = detail.fecha_estreno == expected_date
        date_distance = (
            days_apart(movie.fecha_iso, detail.fecha_estreno)
            if detail.fecha_estreno
            else None
        )
        same_year = detail.fecha_estreno.endswith(movie.fecha_iso[:4]) if detail.fecha_estreno else False

        confidence = "reject"
        reason = "titulo/fecha insuficientes"

        if not detail.titulo and not detail.fecha_estreno:
            confidence = "reject"
            reason = "ficha sin titulo ni fecha parseables"
        elif not detail.titulo and not exact_date and (date_distance is None or date_distance > 45):
            confidence = "reject"
            reason = "ficha sin titulo y fecha no compatible"
        elif score >= 0.75 and exact_date:
            confidence = "alta"
            reason = "titulo y fecha exactos"
        elif score >= 0.75 and date_distance is not None and date_distance <= 45:
            confidence = "media"
            reason = f"titulo fuerte; fecha cercana ({date_distance} dias)"
        elif score >= 0.75 and same_year:
            confidence = "media-baja"
            reason = "titulo fuerte; mismo anio"
        elif score >= 0.90:
            confidence = "revisar"
            reason = "titulo fuerte; fecha distinta o ausente"

        return {
            "fecha_anual_esp": movie.fecha_iso,
            "titulo_anual_esp": movie.titulo,
            "expediente_icaa": candidate.expediente,
            "confidence": confidence,
            "reason": reason,
            "title_score": round(score, 3),
            "titulo_icaa": detail.titulo,
            "fecha_estreno_icaa": detail.fecha_estreno,
            "url": detail.url,
            "query_kind": candidate.query_kind,
            "query": candidate.query,
            "source_url": candidate.source_url,
        }

    def resolve(self, movie: Movie) -> dict:
        candidates_seen = 0
        evaluations = []
        seen_ids = set()

        for query, kind in self.build_queries(movie):
            for result in self.brave_search(query):
                url = result.get("url", "")
                exp_id = extract_id_from_url(url)
                if not exp_id or exp_id in seen_ids:
                    continue

                seen_ids.add(exp_id)
                candidates_seen += 1
                candidate = Candidate(
                    expediente=exp_id,
                    source_url=url,
                    query=query,
                    query_kind=kind,
                    brave_title=result.get("title", ""),
                    brave_description=result.get("description", ""),
                )
                evaluation = self.evaluate(movie, candidate)
                evaluations.append(evaluation)

                if evaluation["confidence"] == "alta":
                    evaluation["candidates_seen"] = candidates_seen
                    return evaluation

                if candidates_seen >= self.max_candidates:
                    break

            if candidates_seen >= self.max_candidates:
                break

        rank = {"alta": 4, "media": 3, "media-baja": 2, "revisar": 1, "reject": 0}
        evaluations.sort(
            key=lambda item: (
                rank.get(item["confidence"], 0),
                item["title_score"],
                item["fecha_estreno_icaa"] == iso_to_ddmmyyyy(movie.fecha_iso),
            ),
            reverse=True,
        )

        accepted = [item for item in evaluations if item["confidence"] != "reject"]
        if accepted:
            best = accepted[0]
        else:
            best = {
                "fecha_anual_esp": movie.fecha_iso,
                "titulo_anual_esp": movie.titulo,
                "expediente_icaa": "",
                "confidence": "no encontrado",
                "reason": "sin candidato aceptable",
                "title_score": 0,
                "titulo_icaa": "",
                "fecha_estreno_icaa": "",
                "url": "",
                "query_kind": "",
                "query": "",
                "source_url": "",
            }

        best["candidates_seen"] = candidates_seen
        return best


def read_movies_from_rows(rows: Iterable[List[str]]) -> List[Movie]:
    movies: List[Movie] = []
    iterator = iter(rows)
    first_row = next(iterator, None)
    if first_row is None:
        return movies

    def clean_cell(value: str) -> str:
        return (value or "").strip().strip('"')

    def is_iso_date(value: str) -> bool:
        try:
            dt.date.fromisoformat(value)
            return True
        except ValueError:
            return False

    header = [normalize(clean_cell(cell)).replace(" ", "_") for cell in first_row]
    has_header = any(name in header for name in ("fecha_estreno", "titulo", "titulo_normalizado"))

    if has_header:
        def find_col(names: Tuple[str, ...]) -> int:
            for name in names:
                if name in header:
                    return header.index(name)
            raise SystemExit(f"No encuentro columna {names} en cabecera: {first_row}")

        titulo_idx = find_col(("titulo_normalizado", "titulo", "titulo_anual_esp"))
        fecha_idx = find_col(("fecha_estreno", "fecha"))
        data_rows = iterator
    else:
        # Sin cabecera: soporta tanto fecha,titulo como titulo,fecha.
        first = [clean_cell(cell) for cell in first_row]
        if len(first) < 2:
            return movies
        fecha_idx, titulo_idx = (0, 1) if is_iso_date(first[0]) else (1, 0)
        data_rows = [first_row, *iterator]

    for row in data_rows:
        if not row or len(row) < 2:
            continue
        fecha = clean_cell(row[fecha_idx])
        titulo = clean_cell(row[titulo_idx])
        if not fecha or not titulo:
            continue
        # Valida formato pronto para fallar de forma clara.
        dt.date.fromisoformat(fecha)
        movies.append(Movie(fecha_iso=fecha, titulo=titulo))
    return movies


def read_movies(path: Optional[str], use_stdin: bool) -> List[Movie]:
    if use_stdin:
        sample = sys.stdin.read()
        delimiter = "\t" if "\t" in sample else ","
        return read_movies_from_rows(csv.reader(sample.splitlines(), delimiter=delimiter))

    if not path:
        raise SystemExit("Indica --input archivo.tsv o --stdin")

    with open(path, newline="", encoding="utf-8") as fh:
        sample = fh.read(4096)
        fh.seek(0)
        delimiter = "\t" if "\t" in sample else ","
        return read_movies_from_rows(csv.reader(fh, delimiter=delimiter))


TSV_FIELDS = [
    "fecha_anual_esp",
    "titulo_anual_esp",
    "expediente_icaa",
    "confidence",
    "reason",
    "title_score",
    "titulo_icaa",
    "fecha_estreno_icaa",
    "url",
]


def tsv_row(result: dict) -> str:
    return "\t".join(str(result.get(field, "") or "") for field in TSV_FIELDS)


def print_tsv(results: List[dict]) -> None:
    print("\t".join(TSV_FIELDS))
    for result in results:
        print(tsv_row(result))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Busca candidatos de expediente ICAA con Brave, sin escribir en BBDD."
    )
    parser.add_argument("--input", "-i", help="Archivo TSV/CSV con columnas fecha_iso,titulo")
    parser.add_argument("--stdin", action="store_true", help="Leer TSV/CSV desde stdin")
    parser.add_argument("--format", choices=("tsv", "json"), default="tsv")
    parser.add_argument("--output", "-o", help="Archivo de salida. En TSV se escribe incrementalmente.")
    parser.add_argument("--delay", type=float, default=0.2, help="Pausa entre queries Brave")
    parser.add_argument("--timeout", type=float, default=15.0, help="Timeout HTTP")
    parser.add_argument("--max-queries", type=int, default=8, help="Queries Brave maximas por pelicula")
    parser.add_argument("--max-candidates", type=int, default=5, help="Candidatos maximos a validar por pelicula")
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args()


def main() -> None:
    load_dotenv()
    args = parse_args()

    api_key = os.getenv("BRAVE_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("BRAVE_API_KEY no encontrado en .env")

    movies = read_movies(args.input, args.stdin)
    resolver = ICAAResolver(
        api_key=api_key,
        delay=args.delay,
        timeout=args.timeout,
        max_queries=args.max_queries,
        max_candidates=args.max_candidates,
        verbose=args.verbose,
    )

    if args.format == "json":
        results = []
        for index, movie in enumerate(movies, 1):
            if args.verbose:
                print(f"# [{index}/{len(movies)}] {movie.fecha_iso} {movie.titulo}", file=sys.stderr)
            results.append(resolver.resolve(movie))

        payload = {
            "queries_used": resolver.query_count,
            "details_fetched": resolver.detail_count,
            "results": results,
        }
        if args.output:
            with open(args.output, "w", encoding="utf-8") as out:
                json.dump(payload, out, ensure_ascii=False, indent=2)
                out.write("\n")
        else:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        output_handle = open(args.output, "w", encoding="utf-8") if args.output else sys.stdout
        try:
            print("\t".join(TSV_FIELDS), file=output_handle, flush=True)
            for index, movie in enumerate(movies, 1):
                if args.verbose:
                    print(f"# [{index}/{len(movies)}] {movie.fecha_iso} {movie.titulo}", file=sys.stderr)
                result = resolver.resolve(movie)
                print(tsv_row(result), file=output_handle, flush=True)
        except KeyboardInterrupt:
            print(
                f"# cancelado: queries_used={resolver.query_count} "
                f"details_fetched={resolver.detail_count}",
                file=sys.stderr,
            )
            raise
        finally:
            if args.output:
                output_handle.close()
        print(
            f"# completado: queries_used={resolver.query_count} "
            f"details_fetched={resolver.detail_count}",
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
