import requests
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

def get_section(fid, section):
    url = "https://sede.mcu.gob.es/CatalogoICAA/es-es/Peliculas/PartialDetalle"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": f"https://sede.mcu.gob.es/CatalogoICAA/es-es/Peliculas/Detalle?Pelicula={fid}"
    }
    data = {"codPelicula": fid, "seccion": section}
    
    s = requests.Session()
    # Petición inicial para establecer cookies de sesión
    s.get(f"https://sede.mcu.gob.es/CatalogoICAA/es-es/Peliculas/Detalle?Pelicula={fid}", headers=headers, verify=False)
    
    # El POST que carga la sección
    r = s.post(url, headers=headers, data=data, verify=False)
    return r.text

if __name__ == '__main__':
    fid = "158720"
    sections = ["P_REPARTO", "P_EQUIPO", "P_SUBVENCIONES"]
    for s in sections:
        print(f"Buscando sección {s}...")
        html = get_section(fid, s)
        if "JULIAN LOPEZ" in html.upper() or "SUBVENCIONES" in html.upper() or "ALVARO FERNANDEZ" in html.upper():
            print(f"  -> ¡ÉXITO en {s}!")
            with open(f"scraper_icaa/debug_{s}.html", "w") as f:
                f.write(html)
        else:
            print(f"  -> Fallo en {s} (Longitud: {len(html)})")
