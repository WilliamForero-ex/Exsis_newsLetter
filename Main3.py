"""
Script para scrapear la agenda de eventos de Hola TD SYNNEX (mes actual)
y guardar, de cada evento: nombre, descripción, fecha, hora de inicio,
hora de fin y el texto crudo en un archivo JSON.

Uso:
    python scraper_tdsynnex_dataset.py
"""

import json
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
log = logging.getLogger("agenda_tdsynnex_json")

DOMINIO = "https://www.holatdsynnex.com"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# Patrón de las páginas de detalle de un evento: /agenda-<slug>.html
PATRON_EVENTO = re.compile(r"/agenda-[^/]+\.html$", re.IGNORECASE)

# Patrón fecha + hora de inicio + hora de fin, ej: "10/07/2026 11:00 - 12:00"
PATRON_FECHA_HORA = re.compile(
    r"(\d{2}/\d{2}/\d{4})\D{0,10}?(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})"
)

def construir_url_mes_actual() -> str:
    """Arma la URL de la agenda para el mes y año actuales, según el
    patrón observado en el sitio: agenda_0_0_<mes>_<anio>.html
    """
    ahora = datetime.now()
    return f"{DOMINIO}/agenda_0_0_{ahora.month}_{ahora.year}.html"

def extraer_links_eventos(browser: Browser, url: str, espera_extra_ms: int = 3000) -> list[str]:
    """Abre la página de agenda del mes, espera a que cargue el contenido
    dinámico, y devuelve las URLs únicas de los eventos individuales.
    """
    urls_eventos: set[str] = set()
    pagina = browser.new_page(user_agent=USER_AGENT)

    try:
        log.info(f"Navegando a {url} ...")
        pagina.goto(url, wait_until="networkidle", timeout=30000)
        pagina.wait_for_timeout(espera_extra_ms)

        anchors = pagina.eval_on_selector_all(
            "a[href]", "els => els.map(e => e.getAttribute('href'))"
        )
        log.info(f"Se encontraron {len(anchors)} enlaces en total en la página.")
    except Exception as e:
        log.error(f"No se pudo cargar la agenda ({url}): {e}")
        anchors = []
    finally:
        pagina.close()

    for href in anchors:
        if not href:
            continue
        url_absoluta = urljoin(DOMINIO, href)
        parsed = urlparse(url_absoluta)

        if "holatdsynnex.com" not in parsed.netloc:
            continue

        if PATRON_EVENTO.search(parsed.path):
            urls_eventos.add(url_absoluta)

    log.info(f"Se identificaron {len(urls_eventos)} eventos únicos para el mes actual.")
    return sorted(urls_eventos)

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
        texto_completo = pagina.locator("body").inner_text()
        match = PATRON_FECHA_HORA.search(texto_completo)

        if match:
            fecha, hora_inicio, hora_fin = match.group(1), match.group(2), match.group(3)
        else:
            fecha, hora_inicio, hora_fin = None, None, None
            log.warning(f"No se encontró patrón de fecha/hora en: {url}")

        # Añadimos texto_crudo_html para mantener la consistencia con los otros scripts
        return {
            "nombre": nombre,
            "descripcion": descripcion,
            "fecha": fecha,
            "hora_inicio": hora_inicio,
            "hora_fin": hora_fin,
            "url": url,
            "texto_crudo_html": texto_completo.strip()
        }
    except Exception as e:
        log.error(f"No se pudo procesar el evento ({url}): {e}")
        return None
    finally:
        pagina.close()

def main():
    url_agenda = construir_url_mes_actual()
    eventos = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            urls_eventos = extraer_links_eventos(browser, url_agenda)

            for i, url_evento in enumerate(urls_eventos, start=1):
                log.info(f"[{i}/{len(urls_eventos)}] Procesando evento: {url_evento}")
                detalle = extraer_detalle_evento(browser, url_evento)
                if detalle:
                    eventos.append(detalle)
        finally:
            browser.close()

    # Guardar en archivo JSON
    nombre_archivo = "dataset_tdsynnex_events.json"
    with open(nombre_archivo, "w", encoding="utf-8") as f:
        json.dump(eventos, f, ensure_ascii=False, indent=4)

    log.info(f"\n=== Extracción completada. {len(eventos)} eventos guardados en '{nombre_archivo}' ===")

if __name__ == "__main__":
    main()