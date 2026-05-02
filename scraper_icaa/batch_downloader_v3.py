import requests
import time
import os
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
HEADERS = {"User-Agent": "Mozilla/5.0"}
def download():
    ids = ["156320", "223116", "179018", "64719", "148420", "146917", "156920", "136122", "128522", "133621", "166923", "146620", "50222", "193423"]
    for fid in ids:
        path = f"scraper_icaa/html_sources/{fid}.html"
        if not os.path.exists(path):
            url = f"https://sede.mcu.gob.es/CatalogoICAA/es-es/Peliculas/Detalle?Pelicula={fid}"
            try:
                r = requests.get(url, headers=HEADERS, verify=False, timeout=20)
                with open(path, "w", encoding="utf-8") as f:
                    f.write(r.text)
            except: pass
            time.sleep(1)
if __name__ == '__main__':
    download()
