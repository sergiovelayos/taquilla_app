import requests
import urllib3
from bs4 import BeautifulSoup
import json

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

def get_full_data(fid):
    # Esta es la URL que usan para los buscadores y versiones de impresion
    url = f"https://sede.mcu.gob.es/CatalogoICAA/Peliculas/DetallePelicula?codPelicula={fid}"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "es-ES,es;q=0.9",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1"
    }

    print(f"Probando URL: {url}")
    r = requests.get(url, headers=headers, verify=False, timeout=20)
    
    if "FICHA ARTÍSTICA" in r.text.upper() or "REPARTO" in r.text.upper():
        print("¡POR FIN! Tenemos la ficha completa.")
        return r.text
    else:
        print(f"Fallo. Status: {r.status_code}. El contenido sigue sin la ficha artística.")
        return r.text

if __name__ == '__main__':
    html = get_full_data("158720")
    with open("scraper_icaa/LAST_REORT.html", "w") as f:
        f.write(html)
