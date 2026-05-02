import requests
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "es-ES,es;q=0.9",
    "Referer": "https://sede.mcu.gob.es/CatalogoICAA/es-es/Peliculas/Detalle?Pelicula=158720"
}

def test():
    s = requests.Session()
    # 1. Entrar en la ficha para pillar cookies
    s.get("https://sede.mcu.gob.es/CatalogoICAA/es-es/Peliculas/Detalle?Pelicula=158720", headers=HEADERS, verify=False)
    
    # 2. Pedir la sección de Ficha Artística
    url = "https://sede.mcu.gob.es/CatalogoICAA/es-es/Peliculas/PartialDetalle?codPelicula=158720&seccion=P_REPARTO"
    r = s.get(url, headers=HEADERS, verify=False)
    
    print(f"Status: {r.status_code}")
    print(f"Content length: {len(r.text)}")
    if "JULIAN LOPEZ" in r.text.upper():
        print("¡ÉXITO! Encontrado Julián López en la ficha artística.")
    else:
        print("Fallo: El contenido sigue vacío o protegido.")
    
    with open("scraper_icaa/debug_partial.html", "w") as f:
        f.write(r.text)

if __name__ == '__main__':
    test()
