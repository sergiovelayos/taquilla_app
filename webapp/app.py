"""
Taquilla España — Web App
Flask application to visualize Spanish box office data from PostgreSQL.
"""

import os
import csv as csv_module
from collections import defaultdict
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from decimal import Decimal

from flask import Flask, render_template, request, jsonify, redirect, url_for, abort
from dotenv import load_dotenv
import psycopg2
import psycopg2.extras

# Load .env from parent directory
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

app = Flask(__name__)

# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def get_db():
    """Return a new database connection."""
    return psycopg2.connect(os.environ['DATABASE_URL'])


def query(sql, params=None):
    """Execute a read query and return a list of dicts."""
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def execute(sql, params=None):
    """Execute a write query and commit it."""
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


def to_float(v):
    """Safely convert Decimal/int/str to float."""
    if v is None:
        return 0.0
    return float(v)


# ---------------------------------------------------------------------------
# Data access
# ---------------------------------------------------------------------------

def get_available_weeks(table='top25'):
    """Return list of available (fecha_inicio, fecha_fin) pairs, most recent first."""
    rows = query(f"""
        SELECT DISTINCT fecha_inicio, fecha_fin
        FROM {table}
        ORDER BY fecha_inicio DESC
    """)
    return [(r['fecha_inicio'], r['fecha_fin']) for r in rows]


def get_weekly_ranking(table, fecha_inicio, fecha_fin):
    """Return the ranking for a given week, enriched with TMDB data if available."""
    # We use a LEFT JOIN with a robust normalization that handles "Title, The" format.
    return query(f"""
        WITH normalized_ranking AS (
            SELECT *,
                CASE 
                    WHEN titulo ~ ', (El|La|Los|Las|Un|Una|Els|Les)$' THEN
                        regexp_replace(titulo, '^(.*), (.*)$', '\\2 \\1')
                    ELSE titulo
                END as titulo_limpio
            FROM {table}
            WHERE fecha_inicio = %s AND fecha_fin = %s
        )
        SELECT r.*, 
               m.poster_url, 
               m.puntuacion_tmdb, 
               m.votos_tmdb,
               m.tmdb_id
        FROM normalized_ranking r
        LEFT JOIN tmdb m ON (
            regexp_replace(LOWER(r.titulo_limpio), '[^a-z0-9]', '', 'g') = 
            regexp_replace(LOWER(m.titulo), '[^a-z0-9]', '', 'g')
        )
        ORDER BY r.rank ASC
    """, (fecha_inicio, fecha_fin))


def get_top_historico(table='top25', limit=10):
    """Return all-time top movies by accumulated box office."""
    if table == 'top25':
        return query("""
            SELECT titulo, titulo_original, distribuidora,
                   MAX(recaudacion_acum) AS recaudacion_total,
                   MAX(espectadores_acum) AS espectadores_total,
                   MAX(semana) AS semanas_en_cartelera,
                   MIN(fecha_estreno_global) AS fecha_estreno
            FROM (
                SELECT *,
                       MIN(fecha_inicio) OVER (PARTITION BY titulo, distribuidora) as fecha_estreno_global
                FROM top25
            ) t
            GROUP BY titulo, titulo_original, distribuidora
            ORDER BY recaudacion_total DESC NULLS LAST
            LIMIT %s
        """, (limit,))
    else:
        return query("""
            SELECT titulo, distribuidora,
                   MAX(recaudacion_acum) AS recaudacion_total,
                   MAX(espectadores_acum) AS espectadores_total,
                   MAX(semana) AS semanas_en_cartelera,
                   MIN(fecha_estreno_global) AS fecha_estreno
            FROM (
                SELECT *,
                       MIN(fecha_inicio) OVER (PARTITION BY titulo, distribuidora) as fecha_estreno_global
                FROM topespanol
            ) t
            GROUP BY titulo, distribuidora
            ORDER BY recaudacion_total DESC NULLS LAST
            LIMIT %s
        """, (limit,))


def get_top_year(table='top25', year=None, limit=10):
    """Return top movies of the given year by accumulated box office."""
    if year is None:
        year = date.today().year
    if table == 'top25':
        return query("""
            SELECT titulo, titulo_original, distribuidora,
                   MAX(recaudacion_acum) AS recaudacion_total,
                   MAX(espectadores_acum) AS espectadores_total,
                   MAX(semana) AS semanas_en_cartelera,
                   MIN(fecha_estreno_global) AS fecha_estreno
            FROM (
                SELECT *,
                       MIN(fecha_inicio) OVER (PARTITION BY titulo, distribuidora) as fecha_estreno_global
                FROM top25
            ) t
            WHERE EXTRACT(YEAR FROM fecha_inicio) = %s
            GROUP BY titulo, titulo_original, distribuidora
            ORDER BY recaudacion_total DESC NULLS LAST
            LIMIT %s
        """, (year, limit))
    else:
        return query("""
            SELECT titulo, distribuidora,
                   MAX(recaudacion_acum) AS recaudacion_total,
                   MAX(espectadores_acum) AS espectadores_total,
                   MAX(semana) AS semanas_en_cartelera,
                   MIN(fecha_estreno_global) AS fecha_estreno
            FROM (
                SELECT *,
                       MIN(fecha_inicio) OVER (PARTITION BY titulo, distribuidora) as fecha_estreno_global
                FROM topespanol
            ) t
            WHERE EXTRACT(YEAR FROM fecha_inicio) = %s
            GROUP BY titulo, distribuidora
            ORDER BY recaudacion_total DESC NULLS LAST
            LIMIT %s
        """, (year, limit))


def get_available_years(table='top25'):
    """Return list of years with data, most recent first."""
    rows = query(f"""
        SELECT DISTINCT EXTRACT(YEAR FROM fecha_inicio)::int AS year
        FROM {table}
        ORDER BY year DESC
    """)
    return [r['year'] for r in rows]


def get_stats_resumen(table='top25'):
    """Return summary stats for the dashboard header, scoped to the active table."""
    rows = query(f"""
        SELECT
            COUNT(DISTINCT titulo) AS total_peliculas,
            COUNT(DISTINCT (fecha_inicio, fecha_fin)) AS total_semanas,
            MIN(fecha_inicio) AS desde,
            MAX(fecha_fin) AS hasta
        FROM {table}
    """)
    return rows[0] if rows else {}


def _percentile(sorted_list, pct):
    """Calculate percentile from a sorted list. Uses linear interpolation."""
    if not sorted_list:
        return 0
    k = (len(sorted_list) - 1) * (pct / 100)
    f = int(k)
    c = f + 1
    if c >= len(sorted_list):
        return sorted_list[-1]
    d = k - f
    return sorted_list[f] + d * (sorted_list[c] - sorted_list[f])


def get_historical_concentration(table='top25', limit=52):
    """
    Calculate the top1 share for each of the last N weeks.
    Returns the raw array plus computed stats (avg, std, p25, p50, p75).
    """
    rows = query(f"""
        SELECT fecha_inicio, fecha_fin, rank, titulo, recaudacion
        FROM {table}
        ORDER BY fecha_inicio DESC, rank ASC
    """)

    if not rows:
        return None

    # Group by week
    weeks = defaultdict(list)
    for r in rows:
        key = (r['fecha_inicio'], r['fecha_fin'])
        weeks[key].append(r)

    # Calculate top1 share per week, sorted most-recent first
    week_data = []
    for (fi, ff) in sorted(weeks.keys(), reverse=True)[:limit]:
        movies = weeks[(fi, ff)]
        total_rec = sum(to_float(m['recaudacion']) for m in movies)
        if total_rec == 0:
            continue
        top1 = next((m for m in movies if m['rank'] == 1), None)
        if not top1:
            continue
        top1_rec = to_float(top1['recaudacion'])
        week_data.append({
            'date': fi.isoformat(),
            'top1_share': round(top1_rec / total_rec * 100, 1),
            'top1_title': top1['titulo'],
            'total_rec': total_rec,
        })

    if len(week_data) < 4:
        return None

    shares = [w['top1_share'] for w in week_data]
    shares_sorted = sorted(shares)

    import statistics
    avg = round(statistics.mean(shares), 1)
    std = round(statistics.stdev(shares), 1) if len(shares) > 1 else 0
    p25 = round(_percentile(shares_sorted, 25), 1)
    p50 = round(_percentile(shares_sorted, 50), 1)
    p75 = round(_percentile(shares_sorted, 75), 1)

    return {
        'weeks': week_data,
        'num_weeks': len(week_data),
        'avg': avg,
        'std': std,
        'p25': p25,
        'p50': p50,
        'p75': p75,
        'min': round(min(shares), 1),
        'max': round(max(shares), 1),
        'low_data': len(week_data) < 8,
    }


def get_concentration(table, fecha_inicio, fecha_fin, hist_stats=None):
    """
    Calculate box office concentration for a given week.
    Returns segment data plus anomaly classification based on historical stats.
    """
    rows = query(f"""
        SELECT rank, titulo, recaudacion, total_espectadores
        FROM {table}
        WHERE fecha_inicio = %s AND fecha_fin = %s
        ORDER BY rank ASC
    """, (fecha_inicio, fecha_fin))

    if not rows:
        return None

    total_rec = sum(to_float(r['recaudacion']) for r in rows)
    total_esp = sum(int(r['total_espectadores'] or 0) for r in rows)

    if total_rec == 0:
        return None

    top1_rec = sum(to_float(r['recaudacion']) for r in rows if r['rank'] == 1)
    top3_rec = sum(to_float(r['recaudacion']) for r in rows if r['rank'] <= 3)
    top5_rec = sum(to_float(r['recaudacion']) for r in rows if r['rank'] <= 5)
    rest_rec = total_rec - top5_rec

    top1_title = next((r['titulo'] for r in rows if r['rank'] == 1), '—')

    seg_1 = round(top1_rec / total_rec * 100, 1)
    seg_2_3 = round((top3_rec - top1_rec) / total_rec * 100, 1)
    seg_4_5 = round((top5_rec - top3_rec) / total_rec * 100, 1)
    seg_rest = round(rest_rec / total_rec * 100, 1)

    # --- Anomaly classification ---
    status = 'normal'
    status_label = 'Normal'
    status_color = '#388E3C'
    status_message = 'Fin de semana normal: la cuota de la nº1 está dentro de lo habitual'

    if hist_stats and not hist_stats['low_data']:
        avg = hist_stats['avg']
        std = hist_stats['std']
        p25 = hist_stats['p25']
        p75 = hist_stats['p75']
        threshold_high = avg + 1.5 * std
        threshold_low = max(0, avg - 1.5 * std)

        if seg_1 > threshold_high:
            status = 'anomaly_high'
            status_label = 'Muy concentrado'
            status_color = '#D32F2F'
            status_message = f'Anomalía alta: {top1_title} acaparó mucho más de lo habitual'
        elif seg_1 < threshold_low:
            status = 'anomaly_low'
            status_label = 'Muy repartido'
            status_color = '#1976D2'
            status_message = 'Anomalía baja: ninguna película dominó claramente'
        else:
            # Refine "normal" — within p25-p75 is typical, outside is borderline
            if seg_1 > p75:
                status_label = 'Concentrado'
                status_color = '#F57C00'
                status_message = f'Algo concentrado: {top1_title} se llevó más que la mayoría de fines de semana'
            elif seg_1 < p25:
                status_label = 'Repartido'
                status_color = '#0288D1'
                status_message = 'Algo repartido: la taquilla se distribuyó más que de costumbre'

    return {
        'total_rec': total_rec,
        'total_esp': total_esp,
        'top1_pct': seg_1,
        'top3_pct': round(top3_rec / total_rec * 100, 1),
        'top5_pct': round(top5_rec / total_rec * 100, 1),
        'rest_pct': seg_rest,
        'top1_title': top1_title,
        'top1_rec': top1_rec,
        'top3_rec': top3_rec,
        'top5_rec': top5_rec,
        'rest_rec': rest_rec,
        'seg_1': seg_1,
        'seg_2_3': seg_2_3,
        'seg_4_5': seg_4_5,
        'seg_rest': seg_rest,
        # Anomaly
        'status': status,
        'status_label': status_label,
        'status_color': status_color,
        'status_message': status_message,
    }


def get_weekly_totals(table='top25'):
    """
    Return aggregated totals per week (pantallas, espectadores, recaudacion)
    for ALL available weeks. Used for the capacity/occupancy insight.
    """
    return query(f"""
        SELECT fecha_inicio, fecha_fin,
               SUM(pantallas) AS total_pantallas,
               SUM(total_espectadores) AS total_espectadores,
               SUM(recaudacion) AS total_recaudacion,
               COUNT(*) AS num_peliculas
        FROM {table}
        GROUP BY fecha_inicio, fecha_fin
        ORDER BY fecha_inicio DESC
    """)


def build_capacity_insight(weekly_totals, current_fi):
    """
    Build the capacity/occupancy insight data.
    Compares the selected week against ALL historical weeks.
    """
    if not weekly_totals:
        return None

    # Find current week in the data
    current = next((w for w in weekly_totals if w['fecha_inicio'] == current_fi), None)
    if not current:
        return None

    # Stats across ALL weeks (full historical benchmark)
    all_esp = [int(w['total_espectadores'] or 0) for w in weekly_totals]
    all_pant = [int(w['total_pantallas'] or 0) for w in weekly_totals]

    cur_esp = int(current['total_espectadores'] or 0)
    cur_pant = int(current['total_pantallas'] or 0)
    cur_rec = to_float(current['total_recaudacion'])

    min_esp = min(all_esp)
    max_esp = max(all_esp)
    avg_esp = sum(all_esp) // len(all_esp)
    min_pant = min(all_pant)
    max_pant = max(all_pant)
    avg_pant = sum(all_pant) // len(all_pant)

    # Percentile position of current week (0-100) against full history
    sorted_esp = sorted(all_esp)
    percentile_esp = sum(1 for x in sorted_esp if x <= cur_esp) / len(sorted_esp) * 100

    sorted_pant = sorted(all_pant)
    percentile_pant = sum(1 for x in sorted_pant if x <= cur_pant) / len(sorted_pant) * 100

    return {
        'cur_esp': cur_esp,
        'cur_pant': cur_pant,
        'cur_rec': cur_rec,
        'min_esp': min_esp,
        'max_esp': max_esp,
        'avg_esp': avg_esp,
        'min_pant': min_pant,
        'max_pant': max_pant,
        'avg_pant': avg_pant,
        'percentile_esp': round(percentile_esp),
        'percentile_pant': round(percentile_pant),
        'num_weeks': len(weekly_totals),
    }


def get_anual_esp_years():
    """Return list of years available in anual_esp, most recent first."""
    try:
        rows = query("SELECT DISTINCT anio FROM anual_esp ORDER BY anio DESC")
        return [r['anio'] for r in rows]
    except Exception:
        return []


def get_anual_esp(anio, orden='rank'):
    """Return all movies of anual_esp for a given year with the requested sort."""
    VALID_ORDER = {
        'rank':          'rank ASC',
        'titulo':        'titulo ASC',
        'fecha_estreno': 'fecha_estreno ASC NULLS LAST',
        'recaudacion':   'recaudacion DESC NULLS LAST',
        'espectadores':  'espectadores DESC NULLS LAST',
    }
    order_clause = VALID_ORDER.get(orden, 'rank ASC')
    return query(f"""
        SELECT rank, titulo, distribuidora, fecha_estreno, recaudacion, espectadores
        FROM anual_esp
        WHERE anio = %s
        ORDER BY {order_clause}
    """, (anio,))


def get_attendance_by_year(table='top25'):
    """
    Return attendance data grouped by calendar week and year for the multi-line chart.
    Uses day-of-year based week number (1-52) for consistent alignment across years.
    Returns dict with:
      - years: {year: [{week, fi, ff, espectadores}, ...]}  (sorted by week)
      - average: [{week, espectadores}, ...]  (historical avg per week)
      - current_year: int
      - previous_years: [year1, year2]  (2 most recent complete years)
    """
    rows = query(f"""
        SELECT EXTRACT(YEAR FROM fecha_inicio)::int AS year,
               LEAST(CEIL(EXTRACT(DOY FROM fecha_inicio) / 7.0), 52)::int AS week,
               SUM(total_espectadores) AS total_espectadores
        FROM {table}
        GROUP BY 1, 2
        ORDER BY 1, 2
    """)

    # Group by year
    by_year = defaultdict(list)
    for r in rows:
        by_year[r['year']].append({
            'week': r['week'],
            'espectadores': int(r['total_espectadores'] or 0),
        })

    current_year = date.today().year
    all_years = sorted(by_year.keys())

    # Previous complete years (excluding current): pick last 2
    complete_years = [y for y in all_years if y < current_year]
    previous_years = complete_years[-2:] if len(complete_years) >= 2 else complete_years

    # Historical average per week (across all complete years)
    week_totals = defaultdict(list)
    for y in complete_years:
        for entry in by_year[y]:
            week_totals[entry['week']].append(entry['espectadores'])

    average = []
    for w in sorted(week_totals.keys()):
        vals = week_totals[w]
        average.append({
            'week': w,
            'espectadores': sum(vals) // len(vals),
        })

    return {
        'years': {y: by_year[y] for y in ([current_year] + previous_years) if y in by_year},
        'average': average,
        'current_year': current_year,
        'previous_years': previous_years,
    }


# ---------------------------------------------------------------------------
# Formatting helpers (passed to templates)
# ---------------------------------------------------------------------------

def fmt_euros(value):
    """Format a number as euros: 1.234.567,89 €"""
    if value is None:
        return '—'
    try:
        n = float(value)
        # Formateamos con coma para decimales y punto para miles
        return f"{n:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.') + " €"
    except (ValueError, TypeError):
        return '—'


def fmt_number(value):
    """Format a number with dot separators: 1.234.567"""
    if value is None:
        return '—'
    try:
        n = float(value)
        if n == int(n):
            n = int(n)
            return f"{n:,}".replace(',', '.')
        # Si tiene decimales, usamos el formato español
        return f"{n:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.')
    except (ValueError, TypeError):
        return '—'


def fmt_decimal(value, decimals=2):
    """Format a number with custom decimals and es-ES style."""
    if value is None:
        return '—'
    try:
        n = float(value)
        fmt = "{:,.%df}" % decimals
        return fmt.format(n).replace(',', 'X').replace('.', ',').replace('X', '.')
    except (ValueError, TypeError):
        return '—'


def fmt_pct(value):
    """Format a percentage: +12% or -5%"""
    if value is None:
        return '—'
    try:
        n = float(value)
        sign = '+' if n > 0 else ''
        return f"{sign}{n:.0f}%"
    except (ValueError, TypeError):
        return '—'


def fmt_date(value):
    """Format a date as dd/mm/yyyy."""
    if value is None:
        return '—'
    if isinstance(value, date):
        return value.strftime('%d/%m/%Y')
    return str(value)


def fmt_datetime(value):
    """Format a datetime as dd/mm/yyyy HH:MM, adjusting to Madrid timezone."""
    if value is None:
        return '—'
    # Si el valor no tiene zona horaria, asumimos UTC para la conversión
    if value.tzinfo is None:
        value = value.replace(tzinfo=ZoneInfo("UTC"))
    
    # Convertimos a hora local de Madrid
    madrid_time = value.astimezone(ZoneInfo("Europe/Madrid"))
    return madrid_time.strftime('%d/%m/%Y %H:%M')


def fmt_euros_short(value):
    """Format euros in compact form: 1,2 M€ or 345 k€."""
    if value is None:
        return '—'
    try:
        n = float(value)
        if n >= 1_000_000:
            val = n / 1_000_000
            return f"{val:.1f}".replace('.', ',') + " M€"
        if n >= 1_000:
            val = n / 1_000
            return f"{val:.0f}" + " k€"
        return f"{n:.0f} €"
    except (ValueError, TypeError):
        return '—'


# Register Jinja2 filters
app.jinja_env.filters['euros'] = fmt_euros
app.jinja_env.filters['euros_short'] = fmt_euros_short
app.jinja_env.filters['number'] = fmt_number
app.jinja_env.filters['decimal'] = fmt_decimal
app.jinja_env.filters['pct'] = fmt_pct
app.jinja_env.filters['date'] = fmt_date
app.jinja_env.filters['datetime'] = fmt_datetime


# ---------------------------------------------------------------------------
# Calculator / Statistics
# ---------------------------------------------------------------------------

def get_benchmarks():
    """Calcula los mejores y peores ratios de eficiencia por categoría.
    Métricas:
      - ratio      : espectadores por cada 1.000€ de subvención  (esp/k€, entero)
      - ratio_rec  : euros de recaudación por cada euro de subvención  (rec/€)
    """
    # Promedio global: espectadores por cada 1.000€ de subvención
    global_avg = query("""
        SELECT (SUM(espectadores)::float / NULLIF(SUM(subvenciones_total_eur), 0)) * 1000 AS ratio
        FROM icaa_fichas
        WHERE subvenciones_total_eur > 0 AND espectadores > 0
          AND COALESCE(fecha_estreno < CURRENT_DATE - INTERVAL '2 months', anio_produccion < EXTRACT(YEAR FROM CURRENT_DATE), FALSE)
    """)[0]['ratio'] or 0

    # Directores (min 2 pelis) + foto TMDB
    top_directores = query("""
        SELECT f.director AS nombre,
               (SUM(f.espectadores)::float / NULLIF(SUM(f.subvenciones_total_eur), 0)) * 1000 AS ratio,
               SUM(f.recaudacion_eur) / NULLIF(SUM(f.subvenciones_total_eur), 0)              AS ratio_rec,
               COUNT(*) AS num_pelis,
               g.foto_url,
               g.popularidad,
               EXTRACT(YEAR FROM g.fecha_nacimiento)::int AS anio_nacimiento,
               g.lugar_nacimiento
        FROM icaa_fichas f
        LEFT JOIN tmdb_gente g ON g.nombre_icaa = f.director
        WHERE f.subvenciones_total_eur > 0 AND f.espectadores > 0
          AND COALESCE(f.fecha_estreno < CURRENT_DATE - INTERVAL '2 months', f.anio_produccion < EXTRACT(YEAR FROM CURRENT_DATE), FALSE)
        GROUP BY f.director, g.foto_url, g.popularidad, g.fecha_nacimiento, g.lugar_nacimiento
        HAVING COUNT(*) >= 2
        ORDER BY ratio DESC LIMIT 20
    """)

    # Bottom Directores (min 2 pelis) + foto TMDB
    bottom_directores = query("""
        SELECT f.director AS nombre,
               (SUM(f.espectadores)::float / NULLIF(SUM(f.subvenciones_total_eur), 0)) * 1000 AS ratio,
               SUM(f.recaudacion_eur) / NULLIF(SUM(f.subvenciones_total_eur), 0)              AS ratio_rec,
               COUNT(*) AS num_pelis,
               g.foto_url,
               g.popularidad,
               EXTRACT(YEAR FROM g.fecha_nacimiento)::int AS anio_nacimiento,
               g.lugar_nacimiento
        FROM icaa_fichas f
        LEFT JOIN tmdb_gente g ON g.nombre_icaa = f.director
        WHERE f.subvenciones_total_eur > 0 AND f.espectadores IS NOT NULL
          AND COALESCE(f.fecha_estreno < CURRENT_DATE - INTERVAL '2 months', f.anio_produccion < EXTRACT(YEAR FROM CURRENT_DATE), FALSE)
        GROUP BY f.director, g.foto_url, g.popularidad, g.fecha_nacimiento, g.lugar_nacimiento
        HAVING COUNT(*) >= 2 AND SUM(f.espectadores) >= 0
        ORDER BY ratio ASC LIMIT 20
    """)

    # Géneros (min 5 pelis)
    top_generos = query("""
        SELECT genero AS nombre,
               (SUM(espectadores)::float / NULLIF(SUM(subvenciones_total_eur), 0)) * 1000 AS ratio,
               SUM(recaudacion_eur) / NULLIF(SUM(subvenciones_total_eur), 0)               AS ratio_rec,
               COUNT(*) AS num_pelis
        FROM icaa_fichas
        WHERE subvenciones_total_eur > 0 AND espectadores > 0 AND genero IS NOT NULL
          AND COALESCE(fecha_estreno < CURRENT_DATE - INTERVAL '2 months', anio_produccion < EXTRACT(YEAR FROM CURRENT_DATE), FALSE)
        GROUP BY genero HAVING COUNT(*) >= 5
        ORDER BY ratio DESC LIMIT 20
    """)

    # Bottom Géneros (min 5 pelis)
    bottom_generos = query("""
        SELECT genero AS nombre,
               (SUM(espectadores)::float / NULLIF(SUM(subvenciones_total_eur), 0)) * 1000 AS ratio,
               SUM(recaudacion_eur) / NULLIF(SUM(subvenciones_total_eur), 0)               AS ratio_rec,
               COUNT(*) AS num_pelis
        FROM icaa_fichas
        WHERE subvenciones_total_eur > 0 AND espectadores IS NOT NULL AND genero IS NOT NULL
          AND COALESCE(fecha_estreno < CURRENT_DATE - INTERVAL '2 months', anio_produccion < EXTRACT(YEAR FROM CURRENT_DATE), FALSE)
        GROUP BY genero HAVING COUNT(*) >= 5 AND SUM(espectadores) >= 0
        ORDER BY ratio ASC LIMIT 20
    """)

    # Actores (min 2 pelis) + foto TMDB
    top_actores = query("""
        WITH actor_stats AS (
            SELECT actor->>'nombre' AS nombre,
                   f.espectadores, f.subvenciones_total_eur, f.recaudacion_eur
            FROM icaa_fichas f,
                 jsonb_array_elements(f.ficha_artistica) AS actor
            WHERE f.subvenciones_total_eur > 0 AND f.espectadores > 0
              AND COALESCE(f.fecha_estreno < CURRENT_DATE - INTERVAL '2 months', f.anio_produccion < EXTRACT(YEAR FROM CURRENT_DATE), FALSE)
              AND (actor->>'funcion' ILIKE '%%Intérpretes%%'
                OR actor->>'funcion' ILIKE '%%Actor%%'
                OR actor->>'funcion' ILIKE '%%Actriz%%')
        )
        SELECT a.nombre,
               (SUM(a.espectadores)::float / NULLIF(SUM(a.subvenciones_total_eur), 0)) * 1000 AS ratio,
               SUM(a.recaudacion_eur) / NULLIF(SUM(a.subvenciones_total_eur), 0)               AS ratio_rec,
               COUNT(*) AS num_pelis,
               g.foto_url,
               g.popularidad,
               EXTRACT(YEAR FROM g.fecha_nacimiento)::int AS anio_nacimiento,
               g.lugar_nacimiento
        FROM actor_stats a
        LEFT JOIN tmdb_gente g ON g.nombre_icaa = a.nombre
        GROUP BY a.nombre, g.foto_url, g.popularidad, g.fecha_nacimiento, g.lugar_nacimiento
        HAVING COUNT(*) >= 2
        ORDER BY ratio DESC LIMIT 20
    """)

    # Bottom Actores (min 2 pelis) + foto TMDB
    bottom_actores = query("""
        WITH actor_stats AS (
            SELECT actor->>'nombre' AS nombre,
                   f.espectadores, f.subvenciones_total_eur, f.recaudacion_eur
            FROM icaa_fichas f,
                 jsonb_array_elements(f.ficha_artistica) AS actor
            WHERE f.subvenciones_total_eur > 0 AND f.espectadores IS NOT NULL
              AND COALESCE(f.fecha_estreno < CURRENT_DATE - INTERVAL '2 months', f.anio_produccion < EXTRACT(YEAR FROM CURRENT_DATE), FALSE)
              AND (actor->>'funcion' ILIKE '%%Intérpretes%%'
                OR actor->>'funcion' ILIKE '%%Actor%%'
                OR actor->>'funcion' ILIKE '%%Actriz%%')
        )
        SELECT a.nombre,
               (SUM(a.espectadores)::float / NULLIF(SUM(a.subvenciones_total_eur), 0)) * 1000 AS ratio,
               SUM(a.recaudacion_eur) / NULLIF(SUM(a.subvenciones_total_eur), 0)               AS ratio_rec,
               COUNT(*) AS num_pelis,
               g.foto_url,
               g.popularidad,
               EXTRACT(YEAR FROM g.fecha_nacimiento)::int AS anio_nacimiento,
               g.lugar_nacimiento
        FROM actor_stats a
        LEFT JOIN tmdb_gente g ON g.nombre_icaa = a.nombre
        GROUP BY a.nombre, g.foto_url, g.popularidad, g.fecha_nacimiento, g.lugar_nacimiento
        HAVING COUNT(*) >= 2 AND SUM(a.espectadores) >= 0
        ORDER BY ratio ASC LIMIT 20
    """)

    # Top 50 Películas: mayor alcance de público por subvención
    top_peliculas = query("""
        SELECT expediente_icaa, titulo,
               (espectadores::float / NULLIF(subvenciones_total_eur, 0)) * 1000 AS ratio,
               recaudacion_eur / NULLIF(subvenciones_total_eur, 0)        AS ratio_rec,
               espectadores,
               recaudacion_eur,
               subvenciones_total_eur
        FROM icaa_fichas
        WHERE subvenciones_total_eur > 5000
          AND espectadores > 0
          AND COALESCE(fecha_estreno < CURRENT_DATE - INTERVAL '2 months', anio_produccion < EXTRACT(YEAR FROM CURRENT_DATE), FALSE)
        ORDER BY ratio DESC LIMIT 50
    """)

    # Bottom 50 Películas: menor alcance de público por subvención
    bottom_peliculas = query("""
        SELECT expediente_icaa, titulo,
               (espectadores::float / NULLIF(subvenciones_total_eur, 0)) * 1000 AS ratio,
               recaudacion_eur / NULLIF(subvenciones_total_eur, 0)        AS ratio_rec,
               espectadores,
               recaudacion_eur,
               subvenciones_total_eur
        FROM icaa_fichas
        WHERE subvenciones_total_eur > 50000
          AND espectadores IS NOT NULL
          AND COALESCE(fecha_estreno < CURRENT_DATE - INTERVAL '2 months', anio_produccion < EXTRACT(YEAR FROM CURRENT_DATE), FALSE)
        ORDER BY ratio ASC LIMIT 50
    """)

    return {
        'global_avg': global_avg,
        'directores': top_directores,
        'generos': top_generos,
        'actores': top_actores,
        'bottom_directores': bottom_directores,
        'bottom_generos': bottom_generos,
        'bottom_actores': bottom_actores,
        'top_peliculas': top_peliculas,
        'bottom_peliculas': bottom_peliculas
    }

@app.route('/calculadora')
def calculadora():
    tipo = request.args.get('tipo', 'director')
    query_str = request.args.get('query', '').strip()
    
    benchmarks = get_benchmarks()
    results = []
    summary = {
        'total_peliculas': 0,
        'recaudacion_total': 0.0,
        'espectadores_totales': 0,
        'subvenciones_totales': 0.0
    }
    
    persona_tmdb = None  # foto + bio de la persona buscada (solo director/actor)

    if query_str:
        # Normalización: minúsculas, sin acentos y solo caracteres alfanuméricos
        # Usamos una cadena base y reemplazamos el marcador de posición manualmente para evitar conflictos en f-strings
        base_norm = "regexp_replace(unaccent(LOWER({})), '[^a-z0-9]', '', 'g')"
        
        sql = ""
        params = [query_str]
        if tipo == 'director':
            col_norm = base_norm.format("director")
            val_norm = base_norm.format("%s")
            sql = f"SELECT * FROM icaa_fichas WHERE subvenciones_total_eur IS NOT NULL AND {col_norm} ILIKE '%%' || {val_norm} || '%%' ORDER BY fecha_estreno DESC"
        elif tipo == 'actor':
            col_norm = base_norm.format("x->>'nombre'")
            val_norm = base_norm.format("%s")
            sql = f"""
                SELECT * FROM icaa_fichas 
                WHERE subvenciones_total_eur IS NOT NULL 
                  AND EXISTS (
                    SELECT 1 FROM jsonb_array_elements(ficha_artistica) AS x 
                    WHERE {col_norm} ILIKE '%%' || {val_norm} || '%%'
                  ) 
                ORDER BY fecha_estreno DESC
            """
        elif tipo == 'genero':
            col_norm = base_norm.format("genero")
            val_norm = base_norm.format("%s")
            sql = f"SELECT * FROM icaa_fichas WHERE subvenciones_total_eur IS NOT NULL AND {col_norm} ILIKE '%%' || {val_norm} || '%%' ORDER BY fecha_estreno DESC"
        elif tipo == 'pelicula':
            col_norm = base_norm.format("titulo")
            val_norm = base_norm.format("%s")
            sql = f"SELECT * FROM icaa_fichas WHERE subvenciones_total_eur IS NOT NULL AND {col_norm} ILIKE '%%' || {val_norm} || '%%' ORDER BY fecha_estreno DESC"

        if sql:
            results = query(sql, params)
            today = date.today()
            limit_date = today - timedelta(days=60)
            
            for r in results:
                # Lógica de antigüedad: precedencia fecha_estreno > anio_produccion
                is_apto = False
                if r.get('fecha_estreno'):
                    is_apto = r['fecha_estreno'] < limit_date
                elif r.get('anio_produccion'):
                    is_apto = r['anio_produccion'] < today.year
                
                r['is_apto'] = is_apto  # Para usar en el template
                
                if is_apto:
                    summary['total_peliculas'] += 1
                    summary['recaudacion_total'] += to_float(r['recaudacion_eur'])
                    summary['espectadores_totales'] += (r['espectadores'] or 0)
                    summary['subvenciones_totales'] += to_float(r['subvenciones_total_eur'])

        # Buscar ficha TMDB de la persona (director o actor)
        if tipo in ('director', 'actor') and results:
            tmdb_rows = query(
                "SELECT * FROM tmdb_gente WHERE nombre_icaa ILIKE %s ORDER BY popularidad DESC NULLS LAST LIMIT 1",
                [f"%{query_str}%"]
            )
            if tmdb_rows:
                persona_tmdb = tmdb_rows[0]

    return render_template('calculadora.html', tipo=tipo, query=query_str, results=results,
                           summary=summary, benchmarks=benchmarks, persona_tmdb=persona_tmdb)

@app.route('/api/anual/percentiles')
def api_anual_percentiles():
    """
    Returns percentile thresholds and aggregated stats for every year in anual_esp.
    Used by the percentile distribution chart + stat cards.
    """
    try:
        rows = query("""
            WITH thresholds AS (
                SELECT anio,
                    COUNT(*) FILTER (WHERE recaudacion IS NOT NULL) AS total,
                    PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY recaudacion) AS p25_rec,
                    PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY recaudacion) AS p50_rec,
                    PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY recaudacion) AS p75_rec,
                    PERCENTILE_CONT(0.90) WITHIN GROUP (ORDER BY recaudacion) AS p90_rec,
                    PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY espectadores) AS p25_esp,
                    PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY espectadores) AS p50_esp,
                    PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY espectadores) AS p75_esp,
                    PERCENTILE_CONT(0.90) WITHIN GROUP (ORDER BY espectadores) AS p90_esp
                FROM anual_esp
                GROUP BY anio
            )
            SELECT
                t.anio,
                t.total,
                ROUND(t.p25_rec::numeric, 0) AS thr_p25_rec,
                ROUND(t.p50_rec::numeric, 0) AS thr_p50_rec,
                ROUND(t.p75_rec::numeric, 0) AS thr_p75_rec,
                ROUND(t.p90_rec::numeric, 0) AS thr_p90_rec,
                ROUND(t.p25_esp::numeric, 0) AS thr_p25_esp,
                ROUND(t.p50_esp::numeric, 0) AS thr_p50_esp,
                ROUND(t.p75_esp::numeric, 0) AS thr_p75_esp,
                ROUND(t.p90_esp::numeric, 0) AS thr_p90_esp,
                -- P90+ (above 90th percentile of rec)
                COUNT(a.rank) FILTER (WHERE a.recaudacion >= t.p90_rec) AS count_p90,
                ROUND(AVG(a.recaudacion) FILTER (WHERE a.recaudacion >= t.p90_rec)::numeric, 0) AS avg_rec_p90,
                ROUND(AVG(a.espectadores) FILTER (WHERE a.recaudacion >= t.p90_rec)::numeric, 0) AS avg_esp_p90,
                -- P75+
                COUNT(a.rank) FILTER (WHERE a.recaudacion >= t.p75_rec) AS count_p75,
                ROUND(AVG(a.recaudacion) FILTER (WHERE a.recaudacion >= t.p75_rec)::numeric, 0) AS avg_rec_p75,
                ROUND(AVG(a.espectadores) FILTER (WHERE a.recaudacion >= t.p75_rec)::numeric, 0) AS avg_esp_p75,
                -- P50+
                COUNT(a.rank) FILTER (WHERE a.recaudacion >= t.p50_rec) AS count_p50,
                ROUND(AVG(a.recaudacion) FILTER (WHERE a.recaudacion >= t.p50_rec)::numeric, 0) AS avg_rec_p50,
                ROUND(AVG(a.espectadores) FILTER (WHERE a.recaudacion >= t.p50_rec)::numeric, 0) AS avg_esp_p50,
                -- P25+
                COUNT(a.rank) FILTER (WHERE a.recaudacion >= t.p25_rec) AS count_p25,
                ROUND(AVG(a.recaudacion) FILTER (WHERE a.recaudacion >= t.p25_rec)::numeric, 0) AS avg_rec_p25,
                ROUND(AVG(a.espectadores) FILTER (WHERE a.recaudacion >= t.p25_rec)::numeric, 0) AS avg_esp_p25,
                -- Totales anuales (para la línea de recaudación)
                ROUND(SUM(a.recaudacion)::numeric, 0) AS total_rec_anio,
                COALESCE(SUM(a.espectadores::bigint), 0) AS total_esp_anio
            FROM thresholds t
            LEFT JOIN anual_esp a ON a.anio = t.anio
            GROUP BY t.anio, t.total,
                     t.p25_rec, t.p50_rec, t.p75_rec, t.p90_rec,
                     t.p25_esp, t.p50_esp, t.p75_esp, t.p90_esp
            ORDER BY t.anio
        """)
    except Exception:
        return jsonify([])

    serialised = []
    for r in rows:
        item = {'anio': r['anio'], 'total': r['total']}
        for k, v in r.items():
            if k in ('anio', 'total'):
                continue
            item[k] = float(v) if v is not None else None
        serialised.append(item)

    return jsonify(serialised)


@app.route('/api/anual/search')
def api_anual_search():
    """Búsqueda de texto libre en anual_esp. Devuelve hasta 50 resultados."""
    q = request.args.get('q', '').strip()
    if len(q) < 2:
        return jsonify([])

    rows = query("""
        SELECT anio, rank, titulo, distribuidora, fecha_estreno, recaudacion, espectadores
        FROM anual_esp
        WHERE titulo ILIKE %s
           OR distribuidora ILIKE %s
        ORDER BY recaudacion DESC NULLS LAST
        LIMIT 50
    """, (f'%{q}%', f'%{q}%'))

    return jsonify([{
        'anio':          r['anio'],
        'rank':          r['rank'],
        'titulo':        r['titulo'],
        'distribuidora': r['distribuidora'] or '',
        'fecha_estreno': r['fecha_estreno'].isoformat() if r['fecha_estreno'] else None,
        'recaudacion':   float(r['recaudacion']) if r['recaudacion'] is not None else None,
        'espectadores':  r['espectadores'],
    } for r in rows])


@app.route('/api/anual')
def api_anual():
    """JSON endpoint for anual_esp data — used by the front-end filter block."""
    years = get_anual_esp_years()
    if not years:
        return jsonify({'rows': [], 'years': []})

    try:
        anio = int(request.args.get('anio', years[0]))
    except (ValueError, TypeError):
        anio = years[0]

    orden = request.args.get('orden', 'rank')

    rows = get_anual_esp(anio, orden)

    # Serialise dates and Decimals for JSON
    serialised = []
    for r in rows:
        serialised.append({
            'rank':          r['rank'],
            'titulo':        r['titulo'],
            'distribuidora': r['distribuidora'] or '',
            'fecha_estreno': r['fecha_estreno'].isoformat() if r['fecha_estreno'] else None,
            'recaudacion':   float(r['recaudacion']) if r['recaudacion'] is not None else None,
            'espectadores':  r['espectadores'],
        })

    return jsonify({'rows': serialised, 'years': years, 'anio': anio, 'orden': orden})


@app.route('/api/decay_curve')
def api_decay_curve():
    """
    Curva de decaimiento promedio para películas que fueron nº1 algún fin de semana.
    """
    tab = request.args.get('tab', 'top25')
    table = 'top25' if tab == 'top25' else 'topespanol'

    try:
        rows = query(f"""
            WITH weekend_totals AS (
                SELECT fecha_inicio,
                       SUM(recaudacion) AS total_rec
                FROM {table}
                GROUP BY fecha_inicio
            ),
            ever_number_one AS (
                SELECT DISTINCT titulo, distribuidora
                FROM {table}
                WHERE rank = 1
            ),
            film_run AS (
                SELECT titulo, distribuidora,
                       SUM(recaudacion) AS total_10sem
                FROM {table}
                WHERE semana BETWEEN 1 AND 10
                  AND recaudacion IS NOT NULL
                GROUP BY titulo, distribuidora
            ),
            film_weeks AS (
                SELECT t.titulo,
                       t.distribuidora,
                       t.semana,
                       CASE WHEN wt.total_rec > 0
                            THEN t.recaudacion / wt.total_rec * 100.0
                            ELSE NULL END AS pct_weekend,
                       SUM(t.recaudacion) OVER (
                           PARTITION BY t.titulo, t.distribuidora
                           ORDER BY t.semana
                           ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                       ) / NULLIF(fr.total_10sem, 0) * 100.0 AS cum_pct_run
                FROM {table} t
                JOIN weekend_totals wt ON wt.fecha_inicio = t.fecha_inicio
                JOIN ever_number_one e
                  ON e.titulo = t.titulo AND e.distribuidora = t.distribuidora
                JOIN film_run fr
                  ON fr.titulo = t.titulo AND fr.distribuidora = t.distribuidora
                WHERE t.semana IS NOT NULL
                  AND t.semana BETWEEN 1 AND 10
                  AND t.recaudacion IS NOT NULL
            )
            SELECT semana,
                   ROUND(AVG(pct_weekend)::numeric, 2)  AS avg_pct,
                   ROUND(AVG(cum_pct_run)::numeric, 2)  AS avg_cum_pct,
                   COUNT(*)                              AS num_obs
            FROM film_weeks
            GROUP BY semana
            ORDER BY semana
        """)
    except Exception:
        return jsonify([])

    return jsonify([{
        'semana':       r['semana'],
        'avg_pct':      float(r['avg_pct'])     if r['avg_pct']     is not None else None,
        'avg_cum_pct':  float(r['avg_cum_pct']) if r['avg_cum_pct'] is not None else None,
        'num_obs':      r['num_obs'],
    } for r in rows])


@app.route('/api/ranking')
def api_ranking():
    """JSON API endpoint for ranking data."""
    tab = request.args.get('tab', 'top25')
    table = 'top25' if tab == 'top25' else 'topespanol'

    weeks = get_available_weeks(table)
    if not weeks:
        return jsonify([])

    week_param = request.args.get('semana')
    if week_param:
        try:
            fi, ff = week_param.split('_')
            semana = (date.fromisoformat(fi), date.fromisoformat(ff))
        except (ValueError, AttributeError):
            semana = weeks[0]
    else:
        semana = weeks[0]

    ranking = get_weekly_ranking(table, semana[0], semana[1])

    # Convert dates to strings for JSON
    for r in ranking:
        for k, v in r.items():
            if isinstance(v, date):
                r[k] = v.isoformat()
            elif isinstance(v, Decimal):
                r[k] = float(v)

    return jsonify(ranking)


@app.route('/pelicula/<expediente_icaa>')
def detalle_pelicula(expediente_icaa):
    res = query("SELECT * FROM icaa_fichas WHERE expediente_icaa = %s", [expediente_icaa])
    if not res:
        return "Película no encontrada", 404

    pelicula = res[0]

    # Enriquecer con datos TMDB del director
    director_tmdb = None
    if pelicula.get('director'):
        tmdb_res = query(
            "SELECT * FROM tmdb_gente WHERE nombre_icaa = %s",
            [pelicula['director']]
        )
        if tmdb_res:
            director_tmdb = tmdb_res[0]

    return render_template('pelicula_detalle.html', p=pelicula, director_tmdb=director_tmdb)


# ---------------------------------------------------------------------------
# Matching review
# ---------------------------------------------------------------------------

TITLE_NORM_SQL = """
regexp_replace(
    unaccent(LOWER(TRIM(
        regexp_replace(split_part({field}, ',', 1), '\\([^)]*\\)', '', 'g')
    ))),
    '^(el|la|los|las|un|una|unos|unas)\\s+', ''
)
"""


def ensure_matching_schema():
    """Create lightweight fields needed by the manual matching UI."""
    execute("""
        ALTER TABLE icaa_fichas
        ADD COLUMN IF NOT EXISTS titulo_anual_esp TEXT;
    """)
    execute("""
        CREATE INDEX IF NOT EXISTS icaa_titulo_anual_esp_idx
        ON icaa_fichas (titulo_anual_esp);
    """)
    execute("""
        CREATE TABLE IF NOT EXISTS subvenciones_icaa_matches (
            titulo_subvencion TEXT PRIMARY KEY,
            expediente_icaa   TEXT NOT NULL,
            created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
    """)
    # Los expedientes de subvenciones pueden existir en el catálogo ICAA aunque
    # todavía no estén importados en el subset local de icaa_fichas.
    execute("""
        ALTER TABLE subvenciones_icaa_matches
        DROP CONSTRAINT IF EXISTS subvenciones_icaa_matches_expediente_icaa_fkey;
    """)
    execute("""
        CREATE INDEX IF NOT EXISTS subvenciones_icaa_matches_expediente_idx
        ON subvenciones_icaa_matches (expediente_icaa);
    """)


def get_icaa_matching_pending(limit=25):
    """Titles from anual_esp that still do not map to an ICAA ficha."""
    norm_a = TITLE_NORM_SQL.format(field='a.titulo')
    norm_i = TITLE_NORM_SQL.format(field='i.titulo')
    return query(f"""
        SELECT
            a.titulo,
            {norm_a} AS titulo_normalizado,
            MIN(a.fecha_estreno) AS fecha_estreno,
            SUM(a.recaudacion) AS recaudacion_total,
            SUM(a.espectadores) AS espectadores_total,
            COUNT(*) AS filas
        FROM anual_esp a
        WHERE NOT EXISTS (
            SELECT 1
            FROM icaa_fichas i
            WHERE i.titulo_anual_esp = a.titulo
               OR {norm_i} = {norm_a}
        )
        GROUP BY a.titulo, titulo_normalizado
        ORDER BY recaudacion_total DESC NULLS LAST, fecha_estreno DESC NULLS LAST
        LIMIT %s
    """, [limit])


def get_tmdb_people_pending(limit=50):
    """People rows whose TMDB match likely needs human review."""
    return query("""
        SELECT nombre_icaa, nombre_tmdb, tmdb_id, roles, match_score,
               popularidad, foto_url, notas
        FROM tmdb_gente
        WHERE COALESCE(revisado_manual, FALSE) = FALSE
          AND (
              tmdb_id IS NULL
              OR foto_url IS NULL
              OR match_score IS NULL
              OR match_score < 0.75
          )
        ORDER BY
            CASE WHEN tmdb_id IS NULL THEN 0 ELSE 1 END,
            match_score ASC NULLS FIRST,
            popularidad DESC NULLS LAST,
            nombre_icaa ASC
        LIMIT %s
    """, [limit])


def get_subvenciones_matching_pending(limit=50):
    """Titles in subvenciones that still have no ICAA ficha linked."""
    return query("""
        SELECT
            s.titulo,
            MIN(s.anio_ayuda) AS primer_anio,
            MAX(s.anio_ayuda) AS ultimo_anio,
            SUM(s.importe_ayuda) AS importe_total,
            COUNT(*) AS filas
        FROM subvenciones s
        LEFT JOIN subvenciones_icaa_matches m
          ON m.titulo_subvencion = s.titulo
        WHERE COALESCE(m.expediente_icaa, s.expediente_icaa) IS NULL
        GROUP BY s.titulo
        ORDER BY importe_total DESC NULLS LAST, ultimo_anio DESC NULLS LAST, s.titulo
        LIMIT %s
    """, [limit])


def require_matching_admin():
    """Require a shared token when MATCHING_ADMIN_TOKEN is configured."""
    expected = os.getenv('MATCHING_ADMIN_TOKEN')
    provided = request.values.get('token')
    if expected and provided != expected:
        abort(403)
    return provided or ''


@app.route('/admin/matching')
def admin_matching():
    token = require_matching_admin()
    ensure_matching_schema()
    tab = request.args.get('tab', 'icaa')
    msg = request.args.get('msg')
    err = request.args.get('err')

    icaa_pending = get_icaa_matching_pending(25)
    people_pending = get_tmdb_people_pending(50)
    subvenciones_pending = get_subvenciones_matching_pending(50)

    stats = {
        'icaa_pending': len(icaa_pending),
        'people_pending': len(people_pending),
        'subvenciones_pending': len(subvenciones_pending),
    }
    return render_template(
        'admin_matching.html',
        tab=tab,
        msg=msg,
        err=err,
        token=token,
        stats=stats,
        icaa_pending=icaa_pending,
        people_pending=people_pending,
        subvenciones_pending=subvenciones_pending,
    )


@app.route('/admin/matching/icaa', methods=['POST'])
def admin_matching_icaa_save():
    token = require_matching_admin()
    ensure_matching_schema()
    titulo = request.form.get('titulo', '').strip()
    expediente_icaa = request.form.get('expediente_icaa', '').strip()

    if not titulo or not expediente_icaa:
        return redirect(url_for('admin_matching', tab='icaa', token=token, err='Falta el titulo o el expediente ICAA.'))

    execute("""
        INSERT INTO icaa_fichas (expediente_icaa, titulo, titulo_anual_esp)
        VALUES (%s, %s, %s)
        ON CONFLICT (expediente_icaa) DO UPDATE
        SET titulo_anual_esp = EXCLUDED.titulo_anual_esp,
            titulo = COALESCE(NULLIF(icaa_fichas.titulo, ''), EXCLUDED.titulo);
    """, [expediente_icaa, titulo, titulo])

    return redirect(url_for('admin_matching', tab='icaa', token=token, msg=f'Mapeo guardado: {titulo} -> ICAA {expediente_icaa}.'))


@app.route('/admin/matching/persona', methods=['POST'])
def admin_matching_persona_save():
    token = require_matching_admin()
    nombre_icaa = request.form.get('nombre_icaa', '').strip()
    tmdb_id = request.form.get('tmdb_id', '').strip()
    action = request.form.get('action', 'reviewed')
    notas = request.form.get('notas', '').strip()

    if not nombre_icaa:
        return redirect(url_for('admin_matching', tab='personas', token=token, err='Falta el nombre ICAA.'))

    if action == 'no_tmdb':
        execute("""
            UPDATE tmdb_gente
            SET tmdb_id = NULL,
                revisado_manual = TRUE,
                notas = COALESCE(NULLIF(%s, ''), 'Confirmado: sin ficha TMDB'),
                updated_at = NOW()
            WHERE nombre_icaa = %s;
        """, [notas, nombre_icaa])
        msg = f'Marcado sin TMDB: {nombre_icaa}.'
    elif tmdb_id:
        try:
            tmdb_id_int = int(tmdb_id)
        except ValueError:
            return redirect(url_for('admin_matching', tab='personas', token=token, err='El TMDB ID debe ser numerico.'))

        execute("""
            UPDATE tmdb_gente
            SET tmdb_id = %s,
                revisado_manual = TRUE,
                notas = COALESCE(NULLIF(%s, ''), notas),
                updated_at = NOW()
            WHERE nombre_icaa = %s;
        """, [tmdb_id_int, notas, nombre_icaa])
        msg = f'TMDB ID guardado para {nombre_icaa}.'
    else:
        execute("""
            UPDATE tmdb_gente
            SET revisado_manual = TRUE,
                notas = COALESCE(NULLIF(%s, ''), notas),
                updated_at = NOW()
            WHERE nombre_icaa = %s;
        """, [notas, nombre_icaa])
        msg = f'Persona marcada como revisada: {nombre_icaa}.'

    return redirect(url_for('admin_matching', tab='personas', token=token, msg=msg))


@app.route('/admin/matching/subvenciones', methods=['POST'])
def admin_matching_subvenciones_save():
    token = require_matching_admin()
    ensure_matching_schema()
    titulo = request.form.get('titulo', '').strip()
    expediente_icaa = request.form.get('expediente_icaa', '').strip()

    if not titulo or not expediente_icaa:
        return redirect(url_for('admin_matching', tab='subvenciones', token=token, err='Falta el titulo de subvencion o el expediente ICAA.'))

    execute("""
        INSERT INTO subvenciones_icaa_matches (titulo_subvencion, expediente_icaa)
        VALUES (%s, %s)
        ON CONFLICT (titulo_subvencion) DO UPDATE
        SET expediente_icaa = EXCLUDED.expediente_icaa,
            updated_at = NOW();
    """, [titulo, expediente_icaa])

    return redirect(url_for('admin_matching', tab='subvenciones', token=token, msg=f'Mapeo guardado: {titulo} -> ICAA {expediente_icaa}.'))


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route('/')
def index():
    """Main page: weekly ranking selector + current ranking table."""
    tab = request.args.get('tab', 'top25')
    table = 'top25' if tab == 'top25' else 'topespanol'

    weeks = get_available_weeks(table)
    if not weeks:
        return render_template('index.html', tab=tab, weeks=[], weeks_by_year={}, 
                               ranking=[], semana_actual=None, stats={}, 
                               concentration=None, hist_stats=None, capacity=None,
                               attendance_chart=None)

    weeks_by_year = defaultdict(list)
    for fi, ff in weeks:
        weeks_by_year[fi.year].append((fi, ff))

    week_param = request.args.get('semana')
    if week_param:
        try:
            fi, ff = week_param.split('_')
            semana_actual = (date.fromisoformat(fi), date.fromisoformat(ff))
        except (ValueError, AttributeError):
            semana_actual = weeks[0]
    else:
        semana_actual = weeks[0]

    ranking = get_weekly_ranking(table, semana_actual[0], semana_actual[1])
    stats = get_stats_resumen(table)
    
    current_year = date.today().year
    available_years = get_available_years(table)
    year_param = request.args.get('year')
    selected_year = int(year_param) if year_param and year_param.isdigit() else current_year
    if selected_year not in available_years:
        selected_year = current_year
    top_year = get_top_year(table, selected_year, 10)
    top_historico = get_top_historico(table, 10)

    hist_stats = get_historical_concentration(table, 52)
    concentration = get_concentration(table, semana_actual[0], semana_actual[1], hist_stats)
    weekly_totals = get_weekly_totals(table)
    capacity = build_capacity_insight(weekly_totals, semana_actual[0])

    last_update_row = query("SELECT MAX(processed_at) as last_run FROM processed_pdfs")
    last_update = last_update_row[0]['last_run'] if last_update_row else None
    attendance_chart = get_attendance_by_year(table)
    anual_years = get_anual_esp_years()

    return render_template('index.html', 
                           tab=tab, 
                           weeks=weeks, 
                           weeks_by_year=dict(weeks_by_year),
                           ranking=ranking, 
                           semana_actual=semana_actual, 
                           stats=stats,
                           top_year=top_year, 
                           top_historico=top_historico,
                           current_year=current_year,
                           selected_year=selected_year,
                           available_years=available_years,
                           concentration=concentration,
                           hist_stats=hist_stats,
                           capacity=capacity,
                           attendance_chart=attendance_chart,
                           last_update=last_update,
                           anual_years=anual_years)

# ---------------------------------------------------------------------------
# Subvenciones Histórico
# ---------------------------------------------------------------------------

def _parse_euro(value):
    """Convert '1.400.000,00€' → float."""
    try:
        return float(value.replace('€', '').replace('.', '').replace(',', '.').strip())
    except (ValueError, AttributeError):
        return 0.0


def get_subvenciones_historico():
    """
    Parse subsidy data from two sources and merge with spectator/revenue data:
    - webapp/data/subvenciones_historico.csv  → per-film detail (2015-2023)
    - webapp/data/subvenciones_agregadas.csv  → manual annual totals (other years)
    - webapp/data/espectadores_nacionalidad.csv → spectators by year (editable)
    - webapp/data/recaudacion_historico.csv   → box office revenue of Spanish films (M€)
    Values in subvenciones_agregadas.csv must be in euros (plain numbers, no formatting).
    """
    csv_path     = os.path.join(os.path.dirname(__file__), 'data', 'subvenciones_historico.csv')
    agr_path     = os.path.join(os.path.dirname(__file__), 'data', 'subvenciones_agregadas.csv')
    esp_csv_path = os.path.join(os.path.dirname(__file__), 'data', 'espectadores_nacionalidad.csv')
    rec_csv_path = os.path.join(os.path.dirname(__file__), 'data', 'recaudacion_historico.csv')

    # --- Espectadores de películas ESPAÑOLAS por año (en millones) ---
    espectadores_esp = {}
    with open(esp_csv_path, newline='', encoding='utf-8') as f:
        for row in csv_module.DictReader(f):
            espectadores_esp[int(row['anio'])] = float(row['espectadores_esp_millones'])

    # --- Recaudación de cine español por año (en millones de €) ---
    recaudacion_esp = {}
    if os.path.exists(rec_csv_path):
        with open(rec_csv_path, newline='', encoding='utf-8') as f:
            for row in csv_module.DictReader(f):
                # Leer primera columna de valor independientemente del nombre
                vals = list(row.values())
                try:
                    recaudacion_esp[int(vals[0])] = float(vals[1])
                except (ValueError, IndexError):
                    pass

    # --- Subvenciones por película (2015-2023) ---
    by_year = defaultdict(lambda: {
        'generales': 0.0, 'selectivas': 0.0,
        'amortizacion': 0.0, 'produccion': 0.0,
        'count': 0,
    })

    with open(csv_path, newline='', encoding='utf-8') as f:
        for row in csv_module.DictReader(f):
            anio = row['anio_ayuda'].strip()
            tipo = row['tipo_ayuda'].strip().lower()
            amt  = _parse_euro(row['importe_ayuda'])
            d    = by_year[anio]
            d['count'] += 1
            if tipo == 'generales':
                d['generales'] += amt
            elif tipo == 'selectivas':
                d['selectivas'] += amt
            elif tipo in ('amortización', 'amortizacion'):
                d['amortizacion'] += amt
            else:
                d['produccion'] += amt

    # --- Subvenciones agregadas (otros años, rellenadas manualmente) ---
    def _safe_float(v):
        try:
            return float(v.strip()) if v and v.strip() else 0.0
        except (ValueError, AttributeError):
            return 0.0

    with open(agr_path, newline='', encoding='utf-8') as f:
        for row in csv_module.DictReader(f):
            anio = row['anio'].strip()
            # Solo incorporar si hay al menos un valor no vacío y el año no está ya en el detalle
            vals = {
                'generales':   _safe_float(row.get('generales',   '')),
                'selectivas':  _safe_float(row.get('selectivas',  '')),
                'amortizacion':_safe_float(row.get('amortizacion','')),
                'produccion':  _safe_float(row.get('produccion',  '')),
            }
            if any(v > 0 for v in vals.values()) and anio not in by_year:
                by_year[anio]['generales']    = vals['generales']
                by_year[anio]['selectivas']   = vals['selectivas']
                by_year[anio]['amortizacion'] = vals['amortizacion']
                by_year[anio]['produccion']   = vals['produccion']
                by_year[anio]['count']        = 0  # sin dato por película

    # --- Unificar años: subvenciones + espectadores + recaudación ---
    all_years = sorted(
        set(int(y) for y in by_year.keys()) |
        set(espectadores_esp.keys()) |
        set(recaudacion_esp.keys())
    )

    chart_data = []
    for anio_int in all_years:
        anio_str = str(anio_int)
        d = by_year.get(anio_str, {})
        chart_data.append({
            'anio':             anio_int,
            'generales':        round(d.get('generales',    0)),
            'selectivas':       round(d.get('selectivas',   0)),
            'amortizacion':     round(d.get('amortizacion', 0)),
            'produccion':       round(d.get('produccion',   0)),
            'count':            d.get('count', 0),
            'espectadores_esp': espectadores_esp.get(anio_int),
            'recaudacion_esp':  recaudacion_esp.get(anio_int),
        })

    # --- KPI stats (calculados solo sobre años con datos de subvenciones) ---
    total_pelis = sum(d.get('count', 0) for d in by_year.values())
    total_importe = sum(
        d.get('generales', 0) + d.get('selectivas', 0) +
        d.get('amortizacion', 0) + d.get('produccion', 0)
        for d in by_year.values()
    )
    year_totals = {
        anio: (d.get('generales', 0) + d.get('selectivas', 0) +
               d.get('amortizacion', 0) + d.get('produccion', 0))
        for anio, d in by_year.items()
    }
    max_anio = max(year_totals, key=year_totals.get) if year_totals else '2023'

    # CAGR suavizado: media de los 3 primeros años (2003-2005) vs. media de los 3 últimos (2023-2025)
    # El punto medio de cada ventana es 2004 y 2024 → n = 20 años
    def _yt(y): return year_totals.get(str(y), 0)
    start_avg = sum(_yt(y) for y in [2003, 2004, 2005]) / 3
    end_avg   = sum(_yt(y) for y in [2023, 2024, 2025]) / 3
    n_cagr    = 20  # distancia entre medianas de ambas ventanas (2004 → 2024)
    if start_avg > 0 and end_avg > 0:
        cagr_pct = round(((end_avg / start_avg) ** (1 / n_cagr) - 1) * 100, 1)
    else:
        cagr_pct = None

    stats = {
        'total_pelis': total_pelis,
        'total_importe': total_importe,
        'max_anio': max_anio,
        'max_importe': year_totals[max_anio],
        'cagr_pct': cagr_pct,
    }

    hitos = [
        {'anio': 2015, 'texto': 'Predominio de la amortización (82% del presupuesto). Primera prueba piloto de ayudas anticipadas sobre proyecto: 36 resoluciones positivas de 424 solicitudes, con 4,75 M€.'},
        {'anio': 2016, 'texto': 'Consolidación del nuevo modelo de ayudas anticipadas en dos modalidades: Generales (30 M€) y Selectivas (7 M€). La amortización sigue vigente con 27,1 M€ en pagos pendientes.'},
        {'anio': 2017, 'texto': 'Estabilización con 30 M€ para Generales y 5,3 M€ para Selectivas. Las ayudas anticipadas se consolidan como el eje del sistema.'},
        {'anio': 2018, 'texto': 'Incremento en producción: 35,5 M€ en Generales y 8,6 M€ en Selectivas. La amortización registra su pico por liquidaciones acumuladas de años anteriores.'},
        {'anio': 2019, 'texto': 'Hito: desaparece definitivamente la línea de amortización. El sistema se centra en 35 M€ de Generales y 8,1 M€ de Selectivas (43,1 M€ totales).'},
        {'anio': 2020, 'texto': 'A pesar del COVID-19, el presupuesto crece a 48,8 M€ (40 M€ Generales + 8,8 M€ Selectivas). Se añaden ayudas directas de emergencia para salas de exhibición.'},
        {'anio': 2021, 'texto': 'Los fondos europeos MRR elevan el presupuesto a 62 M€ (47 M€ Generales + 15 M€ Selectivas), con 10 M€ procedentes del Mecanismo de Recuperación y Resiliencia de la UE.'},
        {'anio': 2022, 'texto': 'Crecimiento continuo: 76 M€ totales. Las Selectivas suben de 15 a 20 M€, reflejando mayor apuesta por el cine cultural y de autor.'},
        {'anio': 2023, 'texto': 'Máximo histórico: 92 M€ (62 M€ Generales + 30 M€ Selectivas). Se reciben 544 solicitudes, la cifra más alta del periodo analizado.'},
    ]

    return chart_data, stats, hitos


def get_subvenciones_db_stats():
    """KPI stats computed directly from the subvenciones table."""
    try:
        totals = query("""
            SELECT COUNT(DISTINCT titulo) AS peliculas,
                   SUM(importe_ayuda)     AS total_importe,
                   MIN(anio_ayuda)        AS desde,
                   MAX(anio_ayuda)        AS hasta
            FROM subvenciones
        """)
        year_max = query("""
            SELECT anio_ayuda, SUM(importe_ayuda) AS total
            FROM subvenciones
            GROUP BY anio_ayuda
            ORDER BY total DESC
            LIMIT 1
        """)
        by_year = query("""
            SELECT anio_ayuda, SUM(importe_ayuda) AS total
            FROM subvenciones
            GROUP BY anio_ayuda
            ORDER BY anio_ayuda
        """)

        peliculas   = totals[0]['peliculas']     if totals    else 0
        total_imp   = to_float(totals[0]['total_importe']) if totals else 0
        desde       = totals[0]['desde']         if totals    else 2006
        hasta       = totals[0]['hasta']         if totals    else 2025
        max_anio    = year_max[0]['anio_ayuda']  if year_max  else None
        max_importe = to_float(year_max[0]['total']) if year_max else 0

        yt = {r['anio_ayuda']: to_float(r['total']) for r in by_year}
        def _yt(y): return yt.get(y, 0)
        # CAGR: 3-year window 2006-2008 vs 2023-2025 (midpoints 2007→2024, n=17)
        start_avg = sum(_yt(y) for y in [2006, 2007, 2008]) / 3
        end_avg   = sum(_yt(y) for y in [2023, 2024, 2025]) / 3
        if start_avg > 0 and end_avg > 0:
            cagr_pct = round(((end_avg / start_avg) ** (1 / 17) - 1) * 100, 1)
        else:
            cagr_pct = None

        return {
            'peliculas':    peliculas,
            'total_importe': total_imp,
            'max_anio':     max_anio,
            'max_importe':  max_importe,
            'desde':        desde,
            'hasta':        hasta,
            'cagr_pct':     cagr_pct,
        }
    except Exception:
        return {}


def get_subvenciones_db_table():
    """All rows from the aggregated subvenciones table for the interactive detail table."""
    try:
        rows = query("""
            SELECT s.titulo,
                   s.importe_ayuda,
                   s.presupuesto_proyecto,
                   s.anio_ayuda,
                   COALESCE(m.expediente_icaa, s.expediente_icaa) AS expediente_icaa,
                   f.expediente_icaa IS NOT NULL AS tiene_ficha_local,
                   s.tmdb_id
            FROM subvenciones s
            LEFT JOIN subvenciones_icaa_matches m
              ON m.titulo_subvencion = s.titulo
            LEFT JOIN icaa_fichas f
              ON f.expediente_icaa = COALESCE(m.expediente_icaa, s.expediente_icaa)
            ORDER BY s.anio_ayuda DESC, s.titulo ASC
        """)
        return [
            {
                'titulo':               r['titulo'],
                'importe_ayuda':        float(r['importe_ayuda'] or 0),
                'presupuesto_proyecto': float(r['presupuesto_proyecto']) if r['presupuesto_proyecto'] is not None else None,
                'anio_ayuda':           r['anio_ayuda'],
                'expediente_icaa':      r['expediente_icaa'],
                'tiene_ficha_local':    r['tiene_ficha_local'],
                'tmdb_id':              r['tmdb_id'],
            }
            for r in rows
        ]
    except Exception:
        return []


@app.route('/subvenciones-historico')
def subvenciones_historico():
    ensure_matching_schema()
    chart_data, stats, hitos = get_subvenciones_historico()
    db_stats         = get_subvenciones_db_stats()
    subvenciones_table = get_subvenciones_db_table()
    return render_template('subvenciones_historico.html',
                           chart_data=chart_data,
                           stats=stats,
                           hitos=hitos,
                           db_stats=db_stats,
                           subvenciones_table=subvenciones_table)


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5002)
