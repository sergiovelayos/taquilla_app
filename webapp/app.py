"""
Taquilla España — Web App
Flask application to visualize Spanish box office data from PostgreSQL.
"""

import os
from collections import defaultdict
from datetime import date, datetime
from zoneinfo import ZoneInfo
from decimal import Decimal

from flask import Flask, render_template, request, jsonify
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
    """Format a number as euros: 1.234.567 €"""
    if value is None:
        return '—'
    try:
        n = int(value)
        return f"{n:,.0f} €".replace(',', '.')
    except (ValueError, TypeError):
        return '—'


def fmt_number(value):
    """Format a number with dot separators: 1.234.567"""
    if value is None:
        return '—'
    try:
        n = int(value)
        return f"{n:,}".replace(',', '.')
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
            return f"{n/1_000_000:.1f} M€"
        if n >= 1_000:
            return f"{n/1_000:.0f} k€"
        return f"{n:.0f} €"
    except (ValueError, TypeError):
        return '—'


# Register Jinja2 filters
app.jinja_env.filters['euros'] = fmt_euros
app.jinja_env.filters['euros_short'] = fmt_euros_short
app.jinja_env.filters['number'] = fmt_number
app.jinja_env.filters['pct'] = fmt_pct
app.jinja_env.filters['date'] = fmt_date
app.jinja_env.filters['datetime'] = fmt_datetime


# ---------------------------------------------------------------------------
# Calculator / Statistics
# ---------------------------------------------------------------------------

def get_benchmarks():
    """Calcula los mejores y peores ratios de eficiencia por categoría."""
    # Promedio global para referencia
    global_avg = query("SELECT SUM(recaudacion_eur)/NULLIF(SUM(subvenciones_total_eur), 0) as ratio FROM icaa_fichas WHERE subvenciones_total_eur > 0")[0]['ratio'] or 0
    
    # Directores (min 2 pelis) + foto TMDB
    top_directores = query("""
        SELECT f.director AS nombre,
               SUM(f.recaudacion_eur) / SUM(f.subvenciones_total_eur) AS ratio,
               COUNT(*) AS num_pelis,
               g.foto_url,
               g.popularidad,
               EXTRACT(YEAR FROM g.fecha_nacimiento)::int AS anio_nacimiento,
               g.lugar_nacimiento
        FROM icaa_fichas f
        LEFT JOIN tmdb_gente g ON g.nombre_icaa = f.director
        WHERE f.subvenciones_total_eur > 0
        GROUP BY f.director, g.foto_url, g.popularidad, g.fecha_nacimiento, g.lugar_nacimiento
        HAVING COUNT(*) >= 2
        ORDER BY ratio DESC LIMIT 5
    """)

    # Bottom Directores (min 2 pelis) + foto TMDB
    bottom_directores = query("""
        SELECT f.director AS nombre,
               SUM(f.recaudacion_eur) / SUM(f.subvenciones_total_eur) AS ratio,
               COUNT(*) AS num_pelis,
               g.foto_url,
               g.popularidad,
               EXTRACT(YEAR FROM g.fecha_nacimiento)::int AS anio_nacimiento,
               g.lugar_nacimiento
        FROM icaa_fichas f
        LEFT JOIN tmdb_gente g ON g.nombre_icaa = f.director
        WHERE f.subvenciones_total_eur > 0
        GROUP BY f.director, g.foto_url, g.popularidad, g.fecha_nacimiento, g.lugar_nacimiento
        HAVING COUNT(*) >= 2 AND SUM(f.recaudacion_eur) > 0
        ORDER BY ratio ASC LIMIT 5
    """)

    # Géneros (min 5 pelis) — sin cambios
    top_generos = query("""
        SELECT genero as nombre,
               SUM(recaudacion_eur)/SUM(subvenciones_total_eur) as ratio,
               COUNT(*) as num_pelis
        FROM icaa_fichas WHERE subvenciones_total_eur > 0 AND genero IS NOT NULL
        GROUP BY genero HAVING COUNT(*) >= 5
        ORDER BY ratio DESC LIMIT 5
    """)

    # Bottom Géneros (min 5 pelis) — sin cambios
    bottom_generos = query("""
        SELECT genero as nombre,
               SUM(recaudacion_eur)/SUM(subvenciones_total_eur) as ratio,
               COUNT(*) as num_pelis
        FROM icaa_fichas WHERE subvenciones_total_eur > 0 AND genero IS NOT NULL
        GROUP BY genero HAVING COUNT(*) >= 5 AND SUM(recaudacion_eur) > 0
        ORDER BY ratio ASC LIMIT 5
    """)

    # Actores (min 2 pelis) + foto TMDB
    top_actores = query("""
        WITH actor_stats AS (
            SELECT actor->>'nombre' AS nombre, f.recaudacion_eur, f.subvenciones_total_eur
            FROM icaa_fichas f,
                 jsonb_array_elements(f.ficha_artistica) AS actor
            WHERE f.subvenciones_total_eur > 0
              AND (actor->>'funcion' ILIKE '%%Intérpretes%%'
                OR actor->>'funcion' ILIKE '%%Actor%%'
                OR actor->>'funcion' ILIKE '%%Actriz%%')
        )
        SELECT a.nombre,
               SUM(a.recaudacion_eur) / SUM(a.subvenciones_total_eur) AS ratio,
               COUNT(*) AS num_pelis,
               g.foto_url,
               g.popularidad,
               EXTRACT(YEAR FROM g.fecha_nacimiento)::int AS anio_nacimiento,
               g.lugar_nacimiento
        FROM actor_stats a
        LEFT JOIN tmdb_gente g ON g.nombre_icaa = a.nombre
        GROUP BY a.nombre, g.foto_url, g.popularidad, g.fecha_nacimiento, g.lugar_nacimiento
        HAVING COUNT(*) >= 2
        ORDER BY ratio DESC LIMIT 5
    """)

    # Bottom Actores (min 2 pelis) + foto TMDB
    bottom_actores = query("""
        WITH actor_stats AS (
            SELECT actor->>'nombre' AS nombre, f.recaudacion_eur, f.subvenciones_total_eur
            FROM icaa_fichas f,
                 jsonb_array_elements(f.ficha_artistica) AS actor
            WHERE f.subvenciones_total_eur > 0
              AND (actor->>'funcion' ILIKE '%%Intérpretes%%'
                OR actor->>'funcion' ILIKE '%%Actor%%'
                OR actor->>'funcion' ILIKE '%%Actriz%%')
        )
        SELECT a.nombre,
               SUM(a.recaudacion_eur) / SUM(a.subvenciones_total_eur) AS ratio,
               COUNT(*) AS num_pelis,
               g.foto_url,
               g.popularidad,
               EXTRACT(YEAR FROM g.fecha_nacimiento)::int AS anio_nacimiento,
               g.lugar_nacimiento
        FROM actor_stats a
        LEFT JOIN tmdb_gente g ON g.nombre_icaa = a.nombre
        GROUP BY a.nombre, g.foto_url, g.popularidad, g.fecha_nacimiento, g.lugar_nacimiento
        HAVING COUNT(*) >= 2 AND SUM(a.recaudacion_eur) > 0
        ORDER BY ratio ASC LIMIT 5
    """)

    # Top Películas Individuales (Éxitos de taquilla vs Subvención)
    top_peliculas = query("""
        SELECT expediente_icaa, titulo, 
               recaudacion_eur / NULLIF(subvenciones_total_eur, 0) as ratio,
               recaudacion_eur, 
               subvenciones_total_eur
        FROM icaa_fichas 
        WHERE subvenciones_total_eur > 5000 
          AND recaudacion_eur > 10000 
        ORDER BY ratio DESC LIMIT 50
    """)

    # Bottom Películas Individuales
    bottom_peliculas = query("""
        SELECT expediente_icaa, titulo, 
               recaudacion_eur / NULLIF(subvenciones_total_eur, 0) as ratio,
               recaudacion_eur, 
               subvenciones_total_eur
        FROM icaa_fichas 
        WHERE subvenciones_total_eur > 50000 
          AND recaudacion_eur IS NOT NULL
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
        sql = ""
        params = []
        if tipo == 'director':
            sql = "SELECT * FROM icaa_fichas WHERE subvenciones_total_eur IS NOT NULL AND director ILIKE %s ORDER BY fecha_estreno DESC"
            params = [f"%{query_str}%"]
        elif tipo == 'actor':
            sql = "SELECT * FROM icaa_fichas WHERE subvenciones_total_eur IS NOT NULL AND EXISTS (SELECT 1 FROM jsonb_array_elements(ficha_artistica) AS x WHERE x->>'nombre' ILIKE %s) ORDER BY fecha_estreno DESC"
            params = [f"%{query_str}%"]
        elif tipo == 'genero':
            sql = "SELECT * FROM icaa_fichas WHERE subvenciones_total_eur IS NOT NULL AND genero ILIKE %s ORDER BY fecha_estreno DESC"
            params = [f"%{query_str}%"]

        if sql:
            results = query(sql, params)
            for r in results:
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

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5002)
