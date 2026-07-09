"""
Script para scrapear los PRÓXIMOS EVENTOS (upcoming events) de la red
Azure Tech Groups en Meetup, y extraer de cada evento: nombre, descripción,
fecha, hora de inicio y hora de fin.

La salida sigue el mismo formato que scraping_agenda_tdsynnex.py.

Uso:
    python scraping_eventos_azuretechgroups.py

Notas:
- Meetup renderiza el contenido con JS, por eso se usa Playwright con
  espera de red inactiva ("networkidle") + un margen extra.
- El marcado HTML de Meetup cambia con frecuencia. Este script intenta
  varias estrategias de extracción (microdata schema.org, atributos
  datetime, y como último recurso regex sobre el texto visible) para
  ser más resistente a esos cambios. Si algún selector deja de
  funcionar, es el primer lugar a revisar.
"""

import logging
import re
from datetime import datetime
from urllib.parse import urljoin, urlparse

from playwright.sync_api import Browser, sync_playwright

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("eventos_azuretechgroups")

DOMINIO = "https://www.meetup.com"
# Los eventos próximos aparecen tanto en la página raíz de la red (con
# ?eventOrigin=network_page) como en /events/, así que escaneamos ambas.
PAGINAS_ORIGEN = [
    "https://www.meetup.com/pro/azuretechgroups/",
    "https://www.meetup.com/pro/azuretechgroups/events/",
]
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# Patrón de las páginas de detalle de un evento en Meetup:
# https://www.meetup.com/<slug-del-grupo>/events/<id-numerico>/
PATRON_EVENTO = re.compile(r"^/[^/]+/events/\d+/?$")

# Fallback: patrón fecha + hora inicio + hora fin en texto visible,
# ej: "Thursday, July 9, 2026, 6:00 PM to 8:00 PM"
PATRON_FECHA_HORA_TEXTO = re.compile(
    r"([A-Za-z]+,\s+[A-Za-z]+\s+\d{1,2},\s+\d{4}),?\s+"
    r"(\d{1,2}:\d{2}\s*[AaPp][Mm])\s+to\s+(\d{1,2}:\d{2}\s*[AaPp][Mm])"
)

# El <title> de una página de evento en Meetup trae la fecha/hora, ej:
# "#26: Azure Cloud Native, Thu, Jul 9, 2026, 6:30 PM | Meetup"
PATRON_FECHA_HORA_TITULO = re.compile(
    r"[A-Za-z]{3},\s*([A-Za-z]{3}\s+\d{1,2},\s+\d{4}),\s*"
    r"(\d{1,2}:\d{2}\s*[AaPp][Mm])\s*\|\s*Meetup"
)


def extraer_links_eventos(browser: Browser, url: str, espera_extra_ms: int = 3000) -> list[str]:
    """Abre la página de próximos eventos de la red, espera a que cargue
    el contenido dinámico, y devuelve las URLs únicas de los eventos
    individuales.
    """
    urls_eventos: set[str] = set()
    pagina = browser.new_page(user_agent=USER_AGENT)

    try:
        log.info(f"Navegando a {url} ...")
        pagina.goto(url, wait_until="networkidle", timeout=30000)
        pagina.wait_for_timeout(espera_extra_ms)

        # 1) Hacer click repetidamente en el botón "Show more" (Meetup lo usa
        #    para paginar los eventos de la red; sin esto solo se ven ~8).
        clicks_show_more = 0
        for _ in range(15):
            boton_encontrado = False
            for texto_boton in ("Show more", "Show more events", "See more", "Ver más"):
                try:
                    boton = pagina.get_by_role("button", name=texto_boton, exact=False).first
                    if boton.is_visible(timeout=1000):
                        boton.scroll_into_view_if_needed(timeout=2000)
                        boton.click(timeout=2000)
                        clicks_show_more += 1
                        boton_encontrado = True
                        pagina.wait_for_timeout(1500)
                        break
                except Exception:
                    continue
            if not boton_encontrado:
                break
        if clicks_show_more:
            log.info(f"Se hizo click en 'Show more' {clicks_show_more} veces.")

        # 2) Además, scroll por si hay carga perezosa adicional sin botón
        altura_anterior = 0
        for _ in range(10):
            pagina.mouse.wheel(0, 3000)
            pagina.wait_for_timeout(800)
            altura_actual = pagina.evaluate("document.body.scrollHeight")
            if altura_actual == altura_anterior:
                break
            altura_anterior = altura_actual

        anchors = pagina.eval_on_selector_all(
            "a[href]", "els => els.map(e => e.getAttribute('href'))"
        )
        log.info(f"Se encontraron {len(anchors)} enlaces en total en la página.")
    except Exception as e:
        log.error(f"No se pudo cargar la página de eventos ({url}): {e}")
        anchors = []
    finally:
        pagina.close()

    for href in anchors:
        if not href:
            continue
        url_absoluta = urljoin(DOMINIO, href)
        parsed = urlparse(url_absoluta)

        if "meetup.com" not in parsed.netloc:
            continue

        if PATRON_EVENTO.match(parsed.path):
            # Normalizamos quitando querystring/fragment
            url_limpia = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
            urls_eventos.add(url_limpia)

    log.info(f"Se identificaron {len(urls_eventos)} eventos próximos únicos.")
    return sorted(urls_eventos)


def _extraer_fecha_hora(pagina) -> tuple[str | None, str | None, str | None]:
    """Intenta obtener fecha, hora_inicio y hora_fin probando varias
    estrategias, de la más confiable a la más frágil.
    """
    fecha, hora_inicio, hora_fin = None, None, None

    # Estrategia 0: parsear el <title> de la página, ej:
    # "#26: Azure Cloud Native, Thu, Jul 9, 2026, 6:30 PM | Meetup"
    # Es texto plano y estable, así que se prueba primero. Solo trae la
    # hora de inicio (Meetup no muestra la hora de fin en el title).
    try:
        titulo = pagina.title()
        match = PATRON_FECHA_HORA_TITULO.search(titulo)
        if match:
            fecha_str, hora_str = match.groups()
            dt_inicio = datetime.strptime(fecha_str, "%b %d, %Y")
            hora_inicio_dt = datetime.strptime(hora_str.upper().replace(" ", ""), "%I:%M%p")
            fecha = dt_inicio.strftime("%d/%m/%Y")
            hora_inicio = hora_inicio_dt.strftime("%H:%M")
    except Exception:
        pass

    # Estrategia 1: microdata schema.org (itemprop="startDate"/"endDate").
    # Se usa también para completar hora_fin si la estrategia 0 ya resolvió
    # fecha/hora_inicio pero no trae hora de fin.
    try:
        inicio_iso = pagina.get_attribute('[itemprop="startDate"]', "content") \
            or pagina.get_attribute('time[itemprop="startDate"]', "datetime")
        fin_iso = pagina.get_attribute('[itemprop="endDate"]', "content") \
            or pagina.get_attribute('time[itemprop="endDate"]', "datetime")

        if fecha is None and inicio_iso:
            dt_inicio = datetime.fromisoformat(inicio_iso.replace("Z", "+00:00"))
            fecha = dt_inicio.strftime("%d/%m/%Y")
            hora_inicio = dt_inicio.strftime("%H:%M")

        if hora_fin is None and fin_iso:
            dt_fin = datetime.fromisoformat(fin_iso.replace("Z", "+00:00"))
            hora_fin = dt_fin.strftime("%H:%M")
    except Exception:
        pass

    if fecha and hora_fin:
        return fecha, hora_inicio, hora_fin

    # Estrategia 2: cualquier <time datetime="..."> en la página. El primero
    # suele ser el inicio y el segundo el fin (mismo criterio de completado).
    try:
        datetimes = pagina.eval_on_selector_all(
            "time[datetime]", "els => els.map(e => e.getAttribute('datetime'))"
        )
        if datetimes:
            if fecha is None:
                dt_inicio = datetime.fromisoformat(datetimes[0].replace("Z", "+00:00"))
                fecha = dt_inicio.strftime("%d/%m/%Y")
                hora_inicio = dt_inicio.strftime("%H:%M")
            if hora_fin is None and len(datetimes) > 1:
                dt_fin = datetime.fromisoformat(datetimes[1].replace("Z", "+00:00"))
                hora_fin = dt_fin.strftime("%H:%M")
    except Exception:
        pass

    if fecha and hora_fin:
        return fecha, hora_inicio, hora_fin

    # Estrategia 3 (fallback): regex sobre el texto visible de la página,
    # solo si todavía no tenemos ni fecha ni hora de fin.
    if fecha is None or hora_fin is None:
        try:
            texto_completo = pagina.locator("body").inner_text()
            match = PATRON_FECHA_HORA_TEXTO.search(texto_completo)
            if match:
                fecha_texto, hora_inicio_texto, hora_fin_texto = match.groups()
                fecha = fecha or fecha_texto
                hora_inicio = hora_inicio or hora_inicio_texto
                hora_fin = hora_fin or hora_fin_texto
        except Exception:
            pass

    return fecha, hora_inicio, hora_fin


def extraer_detalle_evento(browser: Browser, url: str) -> dict | None:
    """Abre la página de detalle de un evento y extrae nombre, descripción,
    fecha, hora de inicio y hora de fin.
    """
    pagina = browser.new_page(user_agent=USER_AGENT)
    try:
        pagina.goto(url, wait_until="networkidle", timeout=30000)
        pagina.wait_for_timeout(1500)

        # --- Nombre ---
        nombre = None
        try:
            nombre = pagina.locator("h1").first.inner_text(timeout=3000).strip()
        except Exception:
            pass
        if not nombre:
            nombre = pagina.title().strip()

        # --- Descripción (meta description / og:description) ---
        descripcion = pagina.get_attribute('meta[name="description"]', "content")
        if not descripcion:
            descripcion = pagina.get_attribute('meta[property="og:description"]', "content")
        descripcion = (descripcion or "").strip()

        # --- Fecha y horario ---
        fecha, hora_inicio, hora_fin = _extraer_fecha_hora(pagina)
        if not fecha:
            log.warning(f"No se encontró fecha/hora para: {url}")

        return {
            "nombre": nombre,
            "descripcion": descripcion,
            "fecha": fecha,
            "hora_inicio": hora_inicio,
            "hora_fin": hora_fin,
            "url": url,
        }
    except Exception as e:
        log.error(f"No se pudo procesar el evento ({url}): {e}")
        return None
    finally:
        pagina.close()


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            urls_eventos: set[str] = set()
            for pagina_origen in PAGINAS_ORIGEN:
                urls_eventos.update(extraer_links_eventos(browser, pagina_origen))
            urls_eventos = sorted(urls_eventos)
            log.info(f"Total de eventos únicos combinando todas las páginas de origen: {len(urls_eventos)}")

            eventos = []
            for i, url_evento in enumerate(urls_eventos, start=1):
                log.info(f"[{i}/{len(urls_eventos)}] Procesando evento: {url_evento}")
                detalle = extraer_detalle_evento(browser, url_evento)
                if detalle:
                    eventos.append(detalle)
        finally:
            browser.close()

    print(f"\n=== {len(eventos)} eventos encontrados ===\n")
    for e in eventos:
        print(f"Nombre       : {e['nombre']}")
        print(f"Fecha        : {e['fecha']}")
        print(f"Hora inicio  : {e['hora_inicio']}")
        print(f"Hora fin     : {e['hora_fin']}")
        print(f"Descripción  : {e['descripcion']}")
        print(f"URL          : {e['url']}")
        print("-" * 80)


if __name__ == "__main__":
    main()