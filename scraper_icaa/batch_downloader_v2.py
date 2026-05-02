import requests
import time
import os
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
HEADERS = {"User-Agent": "Mozilla/5.0"}

def download():
    ids = ["111921", "129124", "56414", "157320", "164816", "151020", "123023", "134517", "31809", "77219", "101618", "156620", "156421", "178918", "159720"]
    os.makedirs("scraper_icaa/html_sources", exist_ok=True)
    for fid in ids:
        path = f"scraper_icaa/html_sources/{fid}.html"
        if not os.path.exists(path):
            print(f"Descargando {fid}...")
            url = f"https://sede.mcu.gob.es/CatalogoICAA/es-es/Peliculas/Detalle?Pelicula={fid}"
            try:
                r = requests.get(url, headers=HEADERS, verify=False, timeout=20)
                with open(path, "w", encoding="utf-8") as f:
                    f.write(r.text)
            except: pass
            time.sleep(1)

if __name__ == '__main__':
    download()
