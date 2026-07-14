import os
import json
import logging
import asyncio
from typing import Optional

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

# ============================================================
# CONFIGURACIÓN
# ============================================================
URL_BASE = "https://www.bbc.com/mundo/topics/cyx5krnw38vt" # Sección de Tecnología
LIMITE_NOTICIAS = 15

ARCHIVO_SALIDA_JSON = "dataset_bbc_tecnologia.json"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

SELECTOR_LINKS_NOTICIA = 'a[href*="/mundo/articles/"], a[href*="/mundo/noticias-"]'

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("bbc_tecnologia_scraper")

# ============================================================
# NAVEGACIÓN Y CAPTURA DE TEXTO (Playwright Asíncrono)
# ============================================================
async def recolectar_links_noticias(pagina) -> list[str]:
    """Busca los enlaces de los artículos en la página principal."""
    anchors = pagina.locator(SELECTOR_LINKS_NOTICIA)
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
# ORQUESTADOR PRINCIPAL
# ============================================================
async def main():
    noticias_procesadas = []

    # Inicializamos Playwright en modo asíncrono
    async with async_playwright() as p:
        # headless=False para que se ejecute con interfaz gráfica y poder depurar bloqueos
        browser = await p.chromium.launch(headless=False) 
        pagina = await browser.new_page(user_agent=USER_AGENT, viewport={"width": 1440, "height": 900})

        log.info(f"Paso 1: Recolectando enlaces desde la portada: {URL_BASE}")
        try:
            # Usar "commit" ayuda a acelerar la carga saltándose elementos bloqueantes
            await pagina.goto(URL_BASE, wait_until="commit", timeout=45000)
            await asyncio.sleep(3)
        except PlaywrightTimeoutError as e:
            log.error(f"Timeout al cargar la portada principal: {e}")
            await browser.close()
            return

        links = await recolectar_links_noticias(pagina)
        links = links[:LIMITE_NOTICIAS]
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

    # --- Exportar resultados a JSON ---
    if not noticias_procesadas:
        log.warning("No se procesó ninguna noticia. No se genera el archivo JSON.")
        return

    with open(ARCHIVO_SALIDA_JSON, "w", encoding="utf-8") as f:
        json.dump(noticias_procesadas, f, ensure_ascii=False, indent=4)

    log.info(f"\n=== {len(noticias_procesadas)} noticias extraídas y guardadas en '{ARCHIVO_SALIDA_JSON}' ===")

if __name__ == "__main__":
    # Importante usar el loop asyncio para entornos Windows
    if os.name == 'nt':
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    
    # Manejo de la interrupción manual
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.warning("\n[!] Ejecución interrumpida manualmente por el usuario (Ctrl+C).")