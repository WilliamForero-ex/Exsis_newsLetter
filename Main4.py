import json
import logging
import asyncio
from playwright.async_api import async_playwright

# Configuración del logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("microsoft_dynamic_content_scraper")

URL_INICIAL = "https://www.microsoft.com/en-us/events/search-catalog/"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

META_EVENTOS = 200
LIMITE_MAX_PAGINAS = 50

async def bloquear_recursos(route):
    """Bloquea imágenes y recursos pesados para acelerar la carga del JS."""
    if route.request.resource_type in ["image", "media", "font"]:
        await route.abort()
    else:
        await route.continue_()

async def extraer_eventos_desde_dynamic_content(pagina) -> list:
    """
    Ejecuta JavaScript directamente dentro de .dynamic-content__content
    para extraer las tarjetas de la página actual.
    """
    return await pagina.evaluate("""
        () => {
            const contenedor = document.querySelector('.dynamic-content__content');
            if (!contenedor) return [];

            const tarjetas = Array.from(contenedor.querySelectorAll('.card__content'));
            
            return tarjetas.map(card => {
                // 1. Etiqueta (Ej: Digital, In-Person)
                const tagEl = card.querySelector('.tag .label');
                const etiqueta = tagEl ? tagEl.innerText.trim() : null;

                // 2. Nombre del Evento (Título h3/h4)
                const titleEl = card.querySelector('.block-feature__title');
                const titulo = titleEl ? titleEl.innerText.trim() : null;

                // 3. Descripción (Párrafo)
                const descEl = card.querySelector('.block-feature__paragraph');
                const descripcion = descEl ? descEl.innerText.trim() : null;

                // 4. Ubicación / Lugar
                const locEl = card.querySelector('.card__location');
                const ubicacion = locEl ? locEl.innerText.trim() : null;

                // 5 y 6. Fechas y Horas (Soporta eventos de 1 o más días)
                const bloquesFecha = Array.from(card.querySelectorAll('li.card__date'));
                let fechas = [];
                let horas = [];

                bloquesFecha.forEach(bloque => {
                    const fEl = bloque.querySelector('.block-feature__label');
                    const hEl = bloque.querySelector('.block-feature__date');
                    if (fEl && fEl.innerText.trim()) fechas.push(fEl.innerText.trim());
                    if (hEl && hEl.innerText.trim()) horas.push(hEl.innerText.replace('•', '').trim());
                });

                // 7. Link / URL del Evento
                const linkEl = card.querySelector('a.btn[href], a[href*="msevents.microsoft.com"]');
                const url = linkEl ? linkEl.href : null;

                return {
                    "Fuente": "Microsoft Events (es-co)",
                    "Etiqueta": etiqueta,
                    "Nombre de evento": titulo,
                    "Descripcion": descripcion,
                    "Lugar": ubicacion,
                    "Fecha": fechas.join(" / ") || null,
                    "Hora": horas.join(" / ") || null,
                    "url": url
                };
            });
        }
    """)

async def scrape_catalogo() -> list:
    todos_los_eventos = []
    urls_vistas = set()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(user_agent=USER_AGENT, viewport={"width": 1440, "height": 900})
        
        await context.route("**/*", bloquear_recursos)
        pagina = await context.new_page()

        log.info(f"Navegando al catálogo inicial de Microsoft Events (Meta: {META_EVENTOS} eventos)...")
        await pagina.goto(URL_INICIAL, wait_until="networkidle", timeout=60000)

        numero_pagina = 1

        while len(todos_los_eventos) < META_EVENTOS and numero_pagina <= LIMITE_MAX_PAGINAS:
            log.info(f"Procesando página {numero_pagina}...")

            # Esperar a que el contenedor dinámico esté presente en el DOM
            try:
                await pagina.wait_for_selector(".dynamic-content__content", timeout=20000)
            except Exception:
                log.warning(f"No se encontró .dynamic-content__content en la página {numero_pagina}.")
                break

            # Breve scroll para forzar lazy loading
            await pagina.evaluate("window.scrollBy(0, 400)")
            await pagina.wait_for_timeout(1500)

            # Extraer las tarjetas de la página actual
            eventos_pagina = await extraer_eventos_desde_dynamic_content(pagina)

            if not eventos_pagina:
                log.info("No se encontraron tarjetas en esta página. Fin del catálogo.")
                break

            nuevos_en_pagina = 0
            for evento in eventos_pagina:
                if len(todos_los_eventos) >= META_EVENTOS:
                    break

                identificador = evento["url"] or evento["Nombre de evento"]
                if identificador and identificador not in urls_vistas:
                    urls_vistas.add(identificador)
                    todos_los_eventos.append(evento)
                    nuevos_en_pagina += 1

            log.info(f"  Página {numero_pagina}: {nuevos_en_pagina} eventos nuevos. Total acumulado: {len(todos_los_eventos)}/{META_EVENTOS}")

            if len(todos_los_eventos) >= META_EVENTOS:
                log.info(f"¡Meta de {META_EVENTOS} eventos alcanzada!")
                break

            # Buscar botón de siguiente página
            selector_siguiente = (
                "a.page-link[aria-label*='Next'], "
                "a.page-link[aria-label*='Siguiente'], "
                "button[aria-label*='Next'], "
                "button[aria-label*='Siguiente'], "
                "a.pagination-next, "
                "button:has-text('>')"
            )
            siguiente_btn = pagina.locator(selector_siguiente).first

            if await siguiente_btn.is_visible():
                primer_titulo_anterior = todos_los_eventos[-1]["Nombre de evento"] if todos_los_eventos else ""

                await siguiente_btn.click()
                await pagina.wait_for_timeout(2500)

                # Confirmar actualización de página verificando cambio de primer título
                try:
                    await pagina.wait_for_function(
                        """(tituloAnterior) => {
                            const primerTitulo = document.querySelector('.dynamic-content__content .block-feature__title')?.innerText.trim();
                            return primerTitulo && primerTitulo !== tituloAnterior;
                        }""",
                        arg=primer_titulo_anterior,
                        timeout=10000
                    )
                except Exception:
                    log.info("La página no actualizó más contenido o se llegó al final de los resultados.")
                    break

                numero_pagina += 1
            else:
                log.info("No se encontró el botón de 'Siguiente página' activo.")
                break

        await browser.close()

    return todos_los_eventos

async def ejecutar_y_guardar(nombre_archivo="dataset_microsoft_events.json"):
    """Punto de entrada principal: Ejecuta la extracción y sobrescribe el JSON objetivo."""
    log.info(f"Iniciando extracción masiva sobre '{URL_INICIAL}'...")
    datos = await scrape_catalogo()

    # Sobrescribe el archivo de destino con los datos extraídos en formato JSON utf-8
    with open(nombre_archivo, "w", encoding="utf-8") as f:
        json.dump(datos, f, ensure_ascii=False, indent=4)

    log.info(f"¡Éxito! Se sobrescribió '{nombre_archivo}' con {len(datos)} eventos.")
    return datos

if __name__ == "__main__":
    asyncio.run(ejecutar_y_guardar("dataset_microsoft_events.json"))