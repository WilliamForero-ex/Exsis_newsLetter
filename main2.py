import json
import logging
import asyncio
import re
from playwright.async_api import async_playwright

# Configuración del log
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("meetup_events_json")

# Constantes de configuración
URL_CATALOGO = "https://www.meetup.com/pro/azuretechgroups/"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

TAMAÑO_LOTE = 10

async def bloquear_recursos_innecesarios(route):
    """Bloquea imágenes, fuentes y medios para acelerar el scraping en paralelo."""
    if route.request.resource_type in ["image", "media", "font"]:
        await route.abort()
    else:
        await route.continue_()

async def obtener_detalles_evento(context, url: str) -> dict:
    """
    Abre una nueva pestaña y extrae los detalles del evento de Meetup.
    Utiliza JSON-LD embebido como fuente principal y selectores DOM como respaldo.
    """
    detalles = {
        "Fuente": "meetup",
        "url": url,
        "Nombre": "No especificado",
        "Cuando": "No especificado",
        "Hora": "No especificada",
        "Donde": "Online",
        "Descripcion": "No especificada"
    }

    pagina = await context.new_page()

    try:
        # Esperamos a que la red esté inactiva o la página cargada para asegurar que el contenido dinámico esté listo
        await pagina.goto(url, wait_until="networkidle", timeout=30000)

        # -------------------------------------------------------------------
        # ESTRATEGIA 1: Extracción vía JSON-LD (Estructurado y 100% Preciso)
        # -------------------------------------------------------------------
        try:
            scripts_json_ld = await pagina.locator('script[type="application/ld+json"]').all()
            for script in scripts_json_ld:
                contenido = await script.inner_text()
                if not contenido:
                    continue
                data = json.loads(contenido)
                
                # A veces es una lista de objetos o un objeto directo
                if isinstance(data, list):
                    data = data[0] if data else {}
                    
                if data.get("@type") == "Event":
                    # Nombre
                    if data.get("name"):
                        detalles["Nombre"] = data["name"].strip()
                    
                    # Descripción
                    if data.get("description"):
                        detalles["Descripcion"] = data["description"].strip()
                    
                    # Fecha y Hora (startDate / endDate ISO)
                    start_date = data.get("startDate", "")
                    end_date = data.get("endDate", "")
                    
                    if start_date:
                        # Extraer fecha y hora en formato legible
                        partes_start = start_date.split("T")
                        detalles["Cuando"] = partes_start[0]
                        if len(partes_start) > 1:
                            hora_inicio = partes_start[1].split("+")[0].split("-")[0][:5]
                            hora_fin = ""
                            if end_date and "T" in end_date:
                                hora_fin = " a " + end_date.split("T")[1].split("+")[0].split("-")[0][:5]
                            detalles["Hora"] = f"{hora_inicio}{hora_fin}"

                    # Ubicación / Donde
                    location = data.get("location", {})
                    if isinstance(location, dict):
                        loc_type = location.get("@type", "")
                        if "VirtualLocation" in loc_type or "Online" in location.get("name", ""):
                            detalles["Donde"] = "Online"
                        else:
                            nombre_lugar = location.get("name", "")
                            address = location.get("address", {})
                            direccion = ""
                            if isinstance(address, dict):
                                direccion = address.get("streetAddress", "")
                            detalles["Donde"] = f"{nombre_lugar}, {direccion}".strip(", ")
                    break
        except Exception as e_json:
            log.debug(f"JSON-LD no disponible en {url}: {e_json}")

        # -------------------------------------------------------------------
        # ESTRATEGIA 2: Fallback por Selectores DOM (Si falla JSON-LD)
        # -------------------------------------------------------------------
        if detalles["Nombre"] == "No especificado":
            try:
                # Selectores amplios para capturar el título
                titulo_el = pagina.locator("h1, [data-testid='event-title'], main h1").first
                if await titulo_el.is_visible(timeout=2000):
                    detalles["Nombre"] = (await titulo_el.inner_text()).strip()
            except Exception:
                pass

        if detalles["Cuando"] == "No especificado":
            try:
                time_container = pagina.locator("time, [data-testid='date-info'], [data-testid='event-date']").first
                if await time_container.is_visible(timeout=2000):
                    texto_tiempo = (await time_container.inner_text()).strip()
                    lineas = [l.strip() for l in texto_tiempo.split("\n") if l.strip()]
                    if len(lineas) >= 2:
                        detalles["Cuando"] = lineas[0]
                        detalles["Hora"] = lineas[1]
                    else:
                        detalles["Cuando"] = texto_tiempo
            except Exception:
                pass

        if detalles["Donde"] == "Online" or detalles["Donde"] == "No especificado":
            try:
                location_container = pagina.locator("[data-testid='location-info'], [data-testid='venue-info']").first
                if await location_container.is_visible(timeout=2000):
                    texto_loc = await location_container.inner_text()
                    detalles["Donde"] = texto_loc.strip().replace("\n", ", ")
            except Exception:
                pass

        if detalles["Descripcion"] == "No especificada":
            try:
                desc_container = pagina.locator("div.break-words, div[data-testid='event-details'], #event-details").first
                if await desc_container.is_visible(timeout=2000):
                    detalles["Descripcion"] = (await desc_container.inner_text()).strip()
            except Exception:
                pass

    except Exception as e:
        log.warning(f"No se pudo procesar la URL {url}: {e}")
    finally:
        await pagina.close()

    return detalles

async def scrape_azure_events() -> list:
    """Maneja la recolección de links y la extracción asíncrona por lotes."""
    resultados = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(user_agent=USER_AGENT, viewport={"width": 1920, "height": 1080})
        await context.route("**/*", bloquear_recursos_innecesarios)

        pagina_catalogo = await context.new_page()
        log.info(f"Navegando al catálogo principal: {URL_CATALOGO}")
        await pagina_catalogo.goto(URL_CATALOGO, wait_until="domcontentloaded")

        # 1. Aceptar banner de cookies
        try:
            btn_cookies = pagina_catalogo.locator("#onetrust-accept-btn-handler")
            if await btn_cookies.is_visible(timeout=3000):
                await btn_cookies.click()
                log.info("Cookies aceptadas.")
        except Exception:
            pass

        # 2. Desplazamiento dinámico para cargar las tarjetas
        log.info("Cargando la lista completa de eventos mediante scroll...")
        last_height = await pagina_catalogo.evaluate("document.body.scrollHeight")
        intentos_sin_cambio = 0

        while intentos_sin_cambio < 3:
            await pagina_catalogo.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await pagina_catalogo.wait_for_timeout(2000)

            for texto_btn in ["Show more", "Mostrar más", "Ver más"]:
                try:
                    btn = pagina_catalogo.locator("button").filter(has_text=texto_btn).first
                    if await btn.is_visible():
                        await btn.click()
                        await pagina_catalogo.wait_for_timeout(1500)
                except Exception:
                    pass

            new_height = await pagina_catalogo.evaluate("document.body.scrollHeight")
            if new_height == last_height:
                intentos_sin_cambio += 1
            else:
                intentos_sin_cambio = 0
                last_height = new_height

        # 3. Recopilar enlaces únicos
        anchors = await pagina_catalogo.locator("a[href*='/events/']").all()
        urls_vistas = set()

        for anchor in anchors:
            enlace = await anchor.get_attribute("href")
            if not enlace or any(k in enlace for k in ["manage", "settings", "attendees"]):
                continue
            if enlace.startswith("/"):
                enlace = f"https://www.meetup.com{enlace}"

            urls_vistas.add(enlace)

        urls_list = sorted(list(urls_vistas))
        await pagina_catalogo.close()

        log.info(f"Se identificaron {len(urls_list)} eventos únicos.")
        log.info(f"Iniciando extracción paralela en lotes de {TAMAÑO_LOTE}...")

        # 4. Extracción paralela en lotes de 10
        for i in range(0, len(urls_list), TAMAÑO_LOTE):
            lote = urls_list[i : i + TAMAÑO_LOTE]
            log.info(f"  Procesando lote {i//TAMAÑO_LOTE + 1} (Eventos {i+1} a {i+len(lote)})...")

            tareas = [obtener_detalles_evento(context, url_evento) for url_evento in lote]
            resultados_lote = await asyncio.gather(*tareas)
            resultados.extend(resultados_lote)

        await browser.close()

    return resultados

async def ejecutar_scraper_y_guardar(nombre_archivo="eventos_azure_tech_detallado.json"):
    log.info("Iniciando el proceso de extracción de Meetup (Modo Paralelo)...")
    datos_eventos = await scrape_azure_events()

    with open(nombre_archivo, "w", encoding="utf-8") as f:
        json.dump(datos_eventos, f, ensure_ascii=False, indent=4)

    log.info(f"¡Extracción completada! Se guardaron {len(datos_eventos)} eventos en '{nombre_archivo}'.")
    return datos_eventos

if __name__ == "__main__":
    asyncio.run(ejecutar_scraper_y_guardar())