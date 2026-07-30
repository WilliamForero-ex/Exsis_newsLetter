"""
Script para scrapear la agenda de eventos de Hola TD SYNNEX desde el mes actual
hasta diciembre del año en curso, extrayendo la información en formato JSON enriquecido.

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

# Patrón de las páginas de detalle de un evento
PATRON_EVENTO = re.compile(r"/agenda-[^/]+\.html$", re.IGNORECASE)

# Patrón fecha + hora de inicio + hora de fin, ej: "31/07/2026 11:00 - 12:00"
PATRON_FECHA_HORA = re.compile(
    r"(\d{2}/\d{2}/\d{4})\D{0,10}?(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})"
)

def construir_urls_hasta_diciembre() -> list[str]:
    """Genera las URLs de la agenda desde el mes actual hasta diciembre."""
    ahora = datetime.now()
    anio_actual = ahora.year
    mes_actual = ahora.month
    
    urls = []
    for mes in range(mes_actual, 13):
        urls.append(f"{DOMINIO}/agenda_0_0_{mes}_{anio_actual}.html")
    return urls

def extraer_links_eventos(browser: Browser, urls_meses: list[str], espera_extra_ms: int = 2000) -> list[str]:
    """Recorre las páginas de la agenda de cada mes y devuelve las URLs únicas de los eventos."""
    urls_eventos: set[str] = set()
    pagina = browser.new_page(user_agent=USER_AGENT)

    try:
        for url_mes in urls_meses:
            log.info(f"Navegando a agenda del mes: {url_mes} ...")
            try:
                pagina.goto(url_mes, wait_until="networkidle", timeout=30000)
                pagina.wait_for_timeout(espera_extra_ms)

                anchors = pagina.eval_on_selector_all(
                    "a[href]", "els => els.map(e => e.getAttribute('href'))"
                )
                
                enlaces_mes = 0
                for href in anchors:
                    if not href:
                        continue
                    url_absoluta = urljoin(DOMINIO, href)
                    parsed = urlparse(url_absoluta)

                    if "holatdsynnex.com" not in parsed.netloc:
                        continue

                    if PATRON_EVENTO.search(parsed.path):
                        if url_absoluta not in urls_eventos:
                            urls_eventos.add(url_absoluta)
                            enlaces_mes += 1
                
                log.info(f"  -> Encontrados {enlaces_mes} eventos nuevos en esta página.")
            except Exception as e:
                log.warning(f"No se pudo cargar o procesar la agenda ({url_mes}): {e}")

    finally:
        pagina.close()

    log.info(f"Total de eventos únicos recopilados hasta diciembre: {len(urls_eventos)}")
    return sorted(urls_eventos)

def extraer_detalle_evento(browser: Browser, url: str) -> dict | None:
    """Abre la página de detalle de un evento y extrae sus campos estructurados."""
    pagina = browser.new_page(user_agent=USER_AGENT)
    try:
        pagina.goto(url, wait_until="networkidle", timeout=30000)
        pagina.wait_for_timeout(1500)

        texto_completo = pagina.locator("body").inner_text()

        # 1. Nombre del evento
        nombre = None
        try:
            nombre = pagina.locator("h1").first.inner_text(timeout=3000).strip()
        except Exception:
            pass
        if not nombre:
            nombre = pagina.title().strip()

        # 2. Descripción
        descripcion = pagina.get_attribute('meta[name="description"]', "content")
        if not descripcion:
            descripcion = pagina.get_attribute('meta[property="og:description"]', "content")
        descripcion = (descripcion or "").strip()

        # Fallback para descripción si la etiqueta meta está vacía
        if not descripcion:
            match_desc = re.search(r'(Te invitamos[^\n]+(?:\n[^\n]+){1,3})', texto_completo, re.IGNORECASE)
            if match_desc:
                descripcion = match_desc.group(1).strip()

        # 3. Fecha y Horas
        match_fecha = PATRON_FECHA_HORA.search(texto_completo)
        if match_fecha:
            fecha = match_fecha.group(1)
            hora = f"{match_fecha.group(2)} - {match_fecha.group(3)}"
        else:
            fecha, hora = "No especificada", "No especificada"

        # 4. Agenda del evento
        agenda = None
        match_agenda = re.search(
            r'(?:Agenda|Programa)[:\n]\s*(.*?)(?=Ponente|Speaker|¡Te esperamos!|Inscríbete|$)', 
            texto_completo, 
            re.IGNORECASE | re.DOTALL
        )
        if match_agenda and len(match_agenda.group(1).strip()) > 10:
            agenda = match_agenda.group(1).strip()

        # 5. Ponente / Speaker
        ponente = "No especificado"
        match_ponente = re.search(
            r'(?:Ponentes?|Speakers?|Presentador(?:es)?)\s*[:\n]\s*([^\n]+)', 
            texto_completo, 
            re.IGNORECASE
        )
        if match_ponente:
            ponente = match_ponente.group(1).strip()

        return {
            "fuente": "TD SyNNEX",
            "url": url,
            "nombre": nombre,
            "Descripcion": descripcion,
            "Agenda": agenda,
            "Ponente": ponente,
            "Fecha": fecha,
            "Hora": hora
        }
    except Exception as e:
        log.error(f"No se pudo procesar el evento ({url}): {e}")
        return None
    finally:
        pagina.close()

def scrape_tdsynnex_events() -> list:
    """Orquesta el scraping navegando por los meses desde el actual hasta diciembre."""
    urls_meses = construir_urls_hasta_diciembre()
    eventos = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            urls_eventos = extraer_links_eventos(browser, urls_meses)

            for i, url_evento in enumerate(urls_eventos, start=1):
                log.info(f"[{i}/{len(urls_eventos)}] Extrayendo detalle: {url_evento}")
                detalle = extraer_detalle_evento(browser, url_evento)
                if detalle:
                    eventos.append(detalle)
        finally:
            browser.close()
            
    return eventos

def ejecutar_scraper_tdsynnex_y_guardar(nombre_archivo="dataset_tdsynnex_events.json"):
    """Inicia la extracción de datos y los guarda en un archivo JSON."""
    log.info("Iniciando la extracción de eventos en TD SYNNEX hasta diciembre...")
    datos_eventos = scrape_tdsynnex_events()

    with open(nombre_archivo, "w", encoding="utf-8") as f:
        json.dump(datos_eventos, f, ensure_ascii=False, indent=4)

    log.info(f"\n=== Extracción completada. {len(datos_eventos)} eventos guardados en '{nombre_archivo}' ===")
    return datos_eventos

if __name__ == "__main__":
    ejecutar_scraper_tdsynnex_y_guardar()