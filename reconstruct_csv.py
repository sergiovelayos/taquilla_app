import os
import re
import json
import csv
from html import unescape

base_dir = '/home/sergio/taquilla_app/scraper_icaa'
output_file = os.path.join(base_dir, 'peliculas.csv')
base_url = 'https://sede.mcu.gob.es/CatalogoICAA/Peliculas/Detalle?Pelicula='

movies = {} # ID -> Title

# Patterns
# 1. list-pro-title (from search results)
# <a class="list-pro-title" href="...Pelicula=ID">TITLE</a>
html_list_pattern = re.compile(r'class="list-pro-title"[^>]*href="[^"]*Pelicula=(\d+)"[^>]*>(.*?)</a>', re.DOTALL)

# 2. sin-pro-title (from related movies in detail pages)
# <a href="...Pelicula=ID" ...> ... </a> ... <label class="sin-pro-title" >TITLE</label>
# This is trickier because they are not in the same tag.
# We'll use a more generic approach: find ID, then find the next title label.
sin_pro_block_pattern = re.compile(r'href="[^"]*Pelicula=(\d+)"[^>]*>.*?class="sin-pro-title"[^>]*>(.*?)</label>', re.DOTALL)

# 3. custom-detail-title (the main movie in a detail page)
html_detail_title_pattern = re.compile(r'<h2[^>]*class="custom-detail-title"[^>]*>(.*?)</h2>', re.DOTALL)

any_id_pattern = re.compile(r'Pelicula=(\d+)')

def clean(t):
    t = unescape(t)
    return re.sub(r'\s+', ' ', t).strip()

for filename in os.listdir(base_dir):
    filepath = os.path.join(base_dir, filename)
    if filename.endswith('.html'):
        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
            
            # 1. List pattern
            for mid, title in html_list_pattern.findall(content):
                movies[mid] = clean(title)
            
            # 2. Sin-pro pattern
            for mid, title in sin_pro_block_pattern.findall(content):
                movies[mid] = clean(title)
            
            # 3. Detail title pattern
            detail_match = html_detail_title_pattern.search(content)
            if detail_match:
                title = clean(detail_match.group(1))
                # For detail pages, the main ID is often in many places.
                # Let's look for the one in "GetPdf?Pelicula=ID" or similar
                main_id_match = re.search(r'Pelicula=(\d+)', content)
                if main_id_match:
                    movies[main_id_match.group(1)] = title

    elif filename.endswith('.json'):
        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
            try:
                data = json.load(f)
                if isinstance(data, dict):
                    ident = data.get('identificacion', {})
                    movie_id = ident.get('expediente_icaa')
                    title = ident.get('titulo')
                    if movie_id and title:
                        movies[movie_id] = clean(title)
            except:
                pass

# Final check for any IDs that might still be missing titles
all_found_ids = set()
for filename in os.listdir(base_dir):
    if filename.endswith('.html'):
        filepath = os.path.join(base_dir, filename)
        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
            all_found_ids.update(any_id_pattern.findall(f.read()))

missing_titles = all_found_ids - set(movies.keys())
for mid in missing_titles:
    movies[mid] = "Unknown Title"

# Sort and write
sorted_ids = sorted(movies.keys())
with open(output_file, 'w', encoding='utf-8', newline='') as f:
    writer = csv.writer(f)
    writer.writerow(['Titulo', 'ID', 'URL'])
    for movie_id in sorted_ids:
        title = movies[movie_id]
        url = f"{base_url}{movie_id}"
        writer.writerow([title, movie_id, url])

print(f"Total unique movies found: {len(movies)}")
