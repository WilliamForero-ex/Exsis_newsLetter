import os
import json
import logging
import asyncio

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

# Configuración del logger (suele dejarse global por estándar de Python)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("bbc_tecnologia_scraper")

# ============================================================
# NAVEGACIÓN Y CAPTURA DE TEXTO (Playwright Asíncrono)
# ============================================================
async def recolectar_links_noticias(pagina, selector_links) -> list[str]:
    """Busca los enlaces de los artículos en la página principal."""
    anchors = pagina.locator(selector_links)
    total = await anchors.count()
    links = []
    vistos = set()
    
    for i in range(total):
        try:
            href = await anchors.nth(i).get_attribute("href")
            if href:
                if href.startswith("/"):
                    href = f"https://www.bbc.com{href}"
                
                if href not in vistos and "topics" not in href:
                    links.append(href)
                    vistos.add(href)
        except Exception:
            pass
            
    return links

async def extraer_detalle_noticia(pagina, url: str) -> dict | None:
    """Navega a la noticia y extrae el título, fecha y texto crudo."""
    detalle = {
        "url_noticia": url,
        "titulo": None,
        "fecha_publicacion": None,
        "texto_crudo_html": None
    }
    
    try:
        # Cambiamos a "commit" para evitar que se congele esperando recursos externos (anuncios)
        await pagina.goto(url, wait_until="commit", timeout=30000)
        await asyncio.sleep(2)
        
        # 1. Título
        try:
            h1_el = pagina.locator("h1").first
            if await h1_el.is_visible():
                detalle["titulo"] = (await h1_el.inner_text()).strip()
            else:
                detalle["titulo"] = (await pagina.title()).strip()
        except:
            detalle["titulo"] = (await pagina.title()).strip()

        # 2. Fecha de publicación
        try:
            time_el = pagina.locator("time").first
            if await time_el.is_visible():
                detalle["fecha_publicacion"] = (await time_el.inner_text()).strip()
        except:
            pass
            
        # 3. Texto crudo
        try:
            article_locator = pagina.locator("main")
            if await article_locator.count() > 0:
                detalle["texto_crudo_html"] = (await article_locator.first.inner_text()).strip()
            else:
                detalle["texto_crudo_html"] = (await pagina.locator("body").inner_text()).strip()
        except Exception as e:
            log.warning(f"No se pudo extraer texto de {url}: {e}")
            
        return detalle

    except Exception as e:
        log.warning(f"No se pudo abrir la noticia {url}: {e}")
        return None

# ============================================================
# ORQUESTADOR PRINCIPAL TOTALMENTE ENCAPSULADO
# ============================================================

async def scrape_bbc_news(
    url_base="https://www.bbc.com/mundo/topics/cyx5krnw38vt", 
    limite_noticias=15,
    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    selector_links='a[href*="/mundo/articles/"], a[href*="/mundo/noticias-"]'
) -> list:
    """
    Función asíncrona que maneja el scraping. Ahora recibe las configuraciones
    como parámetros (con valores por defecto por si no se le pasan explícitamente).
    """
    noticias_procesadas = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False) 
        pagina = await browser.new_page(user_agent=user_agent, viewport={"width": 1440, "height": 900})

        log.info(f"Paso 1: Recolectando enlaces desde la portada: {url_base}")
        try:
            await pagina.goto(url_base, wait_until="commit", timeout=45000)
            await asyncio.sleep(3)
        except PlaywrightTimeoutError as e:
            log.error(f"Timeout al cargar la portada principal: {e}")
            await browser.close()
            return noticias_procesadas

        # Le pasamos el parámetro del selector que ahora vive en la función
        links = await recolectar_links_noticias(pagina, selector_links)
        links = links[:limite_noticias]
        log.info(f"Se encontraron {len(links)} noticias para procesar.")

        log.info("Paso 2: Extrayendo contenido crudo de las noticias...")
        for i, url in enumerate(links, start=1):
            log.info(f"  [{i}/{len(links)}] {url}")
            
            detalle = await extraer_detalle_noticia(pagina, url)
            
            if detalle and detalle.get("texto_crudo_html") and len(detalle["texto_crudo_html"]) > 200:
                noticias_procesadas.append(detalle)
                log.info(f"    ✓ Extraída con éxito: '{str(detalle.get('titulo'))[:40]}...'")
            else:
                 log.warning("    Texto insuficiente o página vacía. Saltando...")

        await browser.close()
        
    return noticias_procesadas


async def ejecutar_scraper_bbc_y_guardar(
    nombre_archivo="dataset_bbc_tecnologia.json",
    url_base="https://www.bbc.com/mundo/topics/cyx5krnw38vt", 
    limite_noticias=15
):
    """
    Función para guardar. Expone los parámetros más comunes (nombre del archivo, URL y límite)
    para poder pasárselos a la función de scraping principal.
    """
    noticias_procesadas = await scrape_bbc_news(
        url_base=url_base,
        limite_noticias=limite_noticias
    )

    # --- Exportar resultados a JSON ---
    if not noticias_procesadas:
        log.warning("No se procesó ninguna noticia. No se genera el archivo JSON.")
        return []

    with open(nombre_archivo, "w", encoding="utf-8") as f:
        json.dump(noticias_procesadas, f, ensure_ascii=False, indent=4)

    log.info(f"\n=== {len(noticias_procesadas)} noticias extraídas y guardadas en '{nombre_archivo}' ===")
    
    return noticias_procesadas

# ============================================================
# BLOQUE DE EJECUCIÓN
# ============================================================
if __name__ == "__main__":
    if os.name == 'nt':
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    
    try:
        # Ahora puedes cambiar los parámetros directamente desde la llamada:
        # asyncio.run(ejecutar_scraper_bbc_y_guardar(
        #     nombre_archivo="noticias_ciencia.json", 
        #     url_base="https://www.bbc.com/mundo/topics/c40379e2ym4t", # URL de otra sección
        #     limite_noticias=5
        # ))
        
        asyncio.run(ejecutar_scraper_bbc_y_guardar())
    except KeyboardInterrupt:
        log.warning("\n[!] Ejecución interrumpida manualmente por el usuario (Ctrl+C).")