#!/usr/bin/env python3
"""Uso puntual: baja a scrape_icaa los expedientes que ya estaban en icaa_fichas
pero no se habían detectado en el matching, e inserta el match manual."""
import time
import random

import scrape_icaa as si

PARES = [
    ("AGUR ETXEBESTE", "92317"),
    ("ALCARRAS", "77219"),
    ("ANE PELIKULA", "67318"),
    ("CINCO LOBITOS", "90520"),
    ("CONTANDO OVEJAS", "52018"),
    ("COSTA BRAVA, LÍBANO", "78020"),
    ("DALIA Y EL LIBRO ROJO", "44719"),
    ("DEHESA: EL BOSQUE DEL LINCE IBÉRICO", "128616"),
    ("EL SUEÑO DE LA SULTANA", "92618"),
    ("EN LA ALCOBA DEL SULTÁN", "61419"),
    ("¿ES EL ENEMIGO?", "129622"),
    ("FENIX 11·23", "218210"),
    ("INSPECTOR SUN Y LA MALDICIÓN DE LA VIUDA NEGRA", "175318"),
    ("L´ADOPCIO", "44111"),
    ("LA ESTRELLA AZUL", "51019"),
    ("LA GRAN AVENTURA DE LOS LUNNIS Y EL LIBRO MÁGICO", "42918"),
    ("LA MATERNAL", "103120"),
    ("LA NOVIA DE AMERICA", "91320"),
    ("MANTÍCORA", "81720"),
    ("MATRIA", "104120"),
    ("NO CULPES AL KARMA DE LO QUE TE PASA POR GILIPOLLAS", "8316"),
    ("OREINA - CIERVO", "57117"),
    ("PAN DE LIMÓN CON SEMILLAS DE AMAPOLA", "155119"),
    ("ROTA N' ROLL", "48015"),
    ("SECADEROS", "57418"),
    ("SEVILLANAS DE BROOKLYN", "178718"),
    ("SINJAR", "37519"),
    ("SURO", "97718"),
]


class Args:
    dry_run = False
    no_save_html = False
    delay = 2
    delay_max = 4


def main():
    conn = si.get_db()
    si.crear_tablas(conn)
    session = si.nueva_sesion()
    args = Args()

    ok = ya_en_scrape = errores = 0

    for i, (titulo, expediente) in enumerate(PARES, 1):
        pid = int(expediente)
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM scrape_icaa WHERE expediente_icaa = %s", (expediente,))
            existe = cur.fetchone() is not None

        if not existe:
            html, session, fatal = si.obtener_html_con_reintento(session, pid, args)
            if fatal or html is None:
                print(f"[{i}/{len(PARES)}] {expediente} {titulo} -> ERROR descarga")
                errores += 1
                time.sleep(random.uniform(args.delay, args.delay_max))
                continue
            status, titulo_bd = si.parsear_y_guardar(conn, args, pid, html)
            if status == "ok":
                si.marcar_progreso(conn, pid, "ok")
                print(f"[{i}/{len(PARES)}] {expediente} {titulo} -> 💾 {titulo_bd}")
                ok += 1
            else:
                print(f"[{i}/{len(PARES)}] {expediente} {titulo} -> {status}")
                errores += 1
            time.sleep(random.uniform(args.delay, args.delay_max))
        else:
            print(f"[{i}/{len(PARES)}] {expediente} {titulo} -> ya estaba en scrape_icaa")
            ya_en_scrape += 1

        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO subvenciones_icaa_matches (titulo_subvencion, expediente_icaa) "
                "VALUES (%s, %s) ON CONFLICT (titulo_subvencion) DO UPDATE SET "
                "expediente_icaa = EXCLUDED.expediente_icaa, updated_at = NOW()",
                (titulo, expediente),
            )
        conn.commit()

    conn.close()
    print(f"\nDescargadas: {ok} | Ya en scrape_icaa: {ya_en_scrape} | Errores: {errores}")
    print(f"Matches insertados/actualizados en subvenciones_icaa_matches: {len(PARES)}")


if __name__ == "__main__":
    main()
