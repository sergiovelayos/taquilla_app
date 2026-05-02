import requests
import time
import os
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}

def download():
    ids = [
        "188423", "194123", "144423", "158720", "121114", "118916", "157621", "180018", 
        "110517", "179318", "164322", "156420", "143719", "147217", "172221", "192322", 
        "176322", "181622", "179218", "41519", "85918", "178819", "160220",
        "134122", "50722", "159120", "159020", "156120", "155123", "43322", "53020", "100317"
    ]
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
