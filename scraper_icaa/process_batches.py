import os
import csv
from bs4 import BeautifulSoup

def process_all_htmls(source_dir='scraper_icaa/html_sources', csv_path='scraper_icaa/peliculas.csv'):
    # Cargar IDs existentes para evitar duplicados
    existing_ids = set()
    if os.path.exists(csv_path):
        with open(csv_path, mode='r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                existing_ids.add(row['ID'])

    new_entries = []
    
    # Procesar cada archivo .html en la carpeta de origen
    if not os.path.exists(source_dir):
        os.makedirs(source_dir)
        print(f"Carpeta {source_dir} creada. Guarda tus archivos .html allí.")
        return

    files = [f for f in os.listdir(source_dir) if f.endswith('.html')]
    if not files:
        print("No hay archivos .html en la carpeta html_sources.")
        return

    for filename in files:
        file_path = os.path.join(source_dir, filename)
        with open(file_path, 'r', encoding='utf-8') as f:
            soup = BeautifulSoup(f.read(), 'html.parser')
            # Buscamos los enlaces que contienen el ID de la película
            items = soup.find_all('a', class_='list-pro-title')
            for item in items:
                title = item.get_text().strip()
                href = item.get('href', '')
                if 'Pelicula=' in href:
                    film_id = href.split('=')[-1]
                    if film_id not in existing_ids:
                        new_entries.append({
                            'Titulo': title,
                            'ID': film_id,
                            'URL': f'https://sede.mcu.gob.es/CatalogoICAA/Peliculas/Detalle?Pelicula={film_id}'
                        })
                        existing_ids.add(film_id)

    # Escribir las nuevas entradas en el CSV
    if new_entries:
        file_exists = os.path.isfile(csv_path)
        with open(csv_path, mode='a', encoding='utf-8', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=['Titulo', 'ID', 'URL'])
            if not file_exists:
                writer.writeheader()
            writer.writerows(new_entries)
        print(f"Procesado completado: Se han añadido {len(new_entries)} nuevas películas.")
    else:
        print("No se han detectado nuevas películas en los archivos proporcionados.")

    print(f"Total de películas únicas en peliculas.csv: {len(existing_ids)}")

if __name__ == '__main__':
    process_all_htmls()
