import json
import logging
from playwright.sync_api import sync_playwright

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("microsoft_events_json")

URL_BASE = "https://www.microsoft.com/en-us/events/search-catalog/"
FILTROS = "audience:developers,primary-language:english"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

LIMITE_EVENTOS = 50
SELECTOR_LINKS = 'a[href*="msevents.microsoft.com/event"]'

def construir_url_catalogo(numero_pagina: int) -> str:
    return f"{URL_BASE}?filters={FILTROS}&scenario=events&page={numero_pagina}"

def cerrar_banner_cookies(pagina):
    for selector in ["#onetrust-accept-btn-handler", "button:has-text('Accept all')", "button:has-text('Accept')"]:
        try:
            boton = pagina.locator(selector).first
            if boton.is_visible(timeout=1500):
                boton.click()
                log.info("Banner de cookies cerrado.")
                pagina.wait_for_timeout(1000)
                return
        except Exception:
            continue

def extraer_detalle_completo(pagina, url: str) -> dict:
    """Extrae TODA la información de la página individual del evento en Microsoft Events."""
    detalle = {
        "url_evento": url,
        "titulo": None,
        "fecha_y_hora": None,
        "ubicacion_o_formato": None,
        "descripcion_completa": None,
        "ponente_o_speaker": None,
        "texto_crudo_html": None # El campo más importante para que lo procese el LLM después
    }
    try:
        pagina.goto(url, wait_until="domcontentloaded", timeout=30000)
        pagina.wait_for_timeout(3500) # Microsoft Events tarda un poco más en renderizar el React
        
        # 1. Título (Usualmente un h1)
        try:
            detalle["titulo"] = pagina.locator("h1").first.inner_text().strip()
        except:
            pass
            
        # 2. Fecha y hora 
        try:
            time_elements = pagina.locator("div[class*='time'], div[class*='date'], time").all()
            if time_elements:
                detalle["fecha_y_hora"] = " | ".join([el.inner_text().strip() for el in time_elements[:2]])
        except:
            pass

        # 3. Descripción
        try:
            desc_el = pagina.locator("div[class*='description'], section[class*='description']").first
            if desc_el.is_visible():
                detalle["descripcion_completa"] = desc_el.inner_text().strip()
        except:
            pass

        # 4. Texto crudo: La clave de este script
        try:
            main_container = pagina.locator("main, #root, #mainContent").first
            if main_container.is_visible():
                 detalle["texto_crudo_html"] = main_container.inner_text().strip()
            else:
                 detalle["texto_crudo_html"] = pagina.locator("body").inner_text().strip()
        except:
             pass

    except Exception as e:
        log.warning(f"No se pudo procesar la URL {url}: {e}")

    return detalle


def scrape_microsoft_events() -> list:
    """
    Función que maneja todo el proceso de scraping del catálogo y las páginas individuales.
    Retorna la lista de eventos extraídos.
    """
    eventos_extraidos = []
    vistos = set()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(user_agent=USER_AGENT, viewport={"width": 1440, "height": 900})
        pagina = context.new_page()

        numero_pagina = 1
        cookies_cerradas = False
        links_crudos = []

        # -- PASO 1: Navegar por la paginación para recolectar links --
        log.info("Iniciando recolección de links en el catálogo...")
        
        while len(links_crudos) < LIMITE_EVENTOS and numero_pagina <= 15:
            url = construir_url_catalogo(numero_pagina)
            log.info(f"Navegando a página {numero_pagina}: {url}")
            
            try:
                pagina.goto(url, wait_until="domcontentloaded", timeout=45000)
            except Exception as e:
                log.error(f"Error navegando a {url}: {e}")
                break

            pagina.wait_for_timeout(3000)
            if not cookies_cerradas:
                cerrar_banner_cookies(pagina)
                cookies_cerradas = True

            # Recolectar links visibles
            anchors = pagina.locator(SELECTOR_LINKS).all()
            links_en_pagina = 0
            
            for anchor in anchors:
                try:
                    href = anchor.get_attribute("href")
                    if not href:
                        continue
                        
                    if href not in vistos:
                        # Sacamos un pequeño backup del texto de la tarjeta del catálogo
                        tarjeta_texto = anchor.inner_text().strip()
                        lineas_tarjeta = [l.strip() for l in tarjeta_texto.split('\n') if l.strip()]
                        
                        links_crudos.append({
                            "url": href,
                            "tarjeta_backup": lineas_tarjeta
                        })
                        vistos.add(href)
                        links_en_pagina += 1
                except:
                    pass

            log.info(f"  Página {numero_pagina}: {links_en_pagina} links nuevos. Total acumulado: {len(links_crudos)}")

            if links_en_pagina == 0:
                 log.info("  No se encontraron más links. Fin de la paginación.")
                 break
                 
            numero_pagina += 1

        links_crudos = links_crudos[:LIMITE_EVENTOS]

        # -- PASO 2: Visitar cada página individual y extraer la data pesada --
        log.info(f"\nExtrayendo la información cruda de {len(links_crudos)} eventos de Microsoft para el Agente...")
        for i, item in enumerate(links_crudos, start=1):
            log.info(f"  [{i}/{len(links_crudos)}] -> {item['url']}")
            
            datos_evento = extraer_detalle_completo(pagina, item["url"])
            
            # Usar la tarjeta del catálogo para rellenar lo básico si falla el DOM individual
            backup = item["tarjeta_backup"]
            
            # En el catálogo de Microsoft, usualmente la fecha/hora y formato están al final de la tarjeta
            if backup:
                if not datos_evento["titulo"] and len(backup) > 0:
                    # El título suele ser el primer texto relevante
                    for linea in backup:
                        if len(linea) > 10 and not linea.lower().startswith(("register", "details", "learn")):
                            datos_evento["titulo"] = linea
                            break

            eventos_extraidos.append(datos_evento)

        browser.close()
        
    return eventos_extraidos


def ejecutar_scraper_microsoft_y_guardar(nombre_archivo="dataset_microsoft_events.json"):
    """
    Función principal que invoca el scraper y guarda la información en disco.
    """
    log.info("Iniciando el proceso de extracción de Microsoft Events...")
    datos_eventos = scrape_microsoft_events()
    
    # -- PASO 3: Guardar el Dataset --
    with open(nombre_archivo, "w", encoding="utf-8") as f:
        json.dump(datos_eventos, f, ensure_ascii=False, indent=4)
        
    log.info(f"¡Extracción completada! Se guardaron {len(datos_eventos)} eventos en '{nombre_archivo}'.")
    
    return datos_eventos


# Bloque de ejecución principal
if __name__ == "__main__":
    # Puedes modificar el nombre del archivo enviándolo como parámetro
    ejecutar_scraper_microsoft_y_guardar()