import json
import logging
import asyncio
import re
from urllib.parse import urlparse
from playwright.async_api import async_playwright

# Configuración del log
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("sena_ofertas_json")

# Constantes de configuración
URL_BASE = "https://betowa.sena.edu.co/oferta" 
DOMINIO_BASE = "https://betowa.sena.edu.co"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

LIMITE_EVENTOS = 200
SELECTOR_LINKS = 'a[href*="/oferta/"], a[href*="programId"]'
TAMAÑO_LOTE = 10  

# Pre-compilación de Expresiones Regulares para mejor rendimiento
PATRON_SLUG = re.compile(r'/oferta/([^/?]+)')
PATRON_VIRTUAL = re.compile(r'\b(virtual|a distancia|online)\b', re.IGNORECASE)
PATRON_PRESENCIAL = re.compile(r'\b(presencial)\b', re.IGNORECASE)
PATRON_HIBRIDO = re.compile(r'\b(mixta|híbrida|b-learning)\b', re.IGNORECASE)
PATRON_DURACION = re.compile(r'(\d+\s*(?:horas?|meses?|semanas?))', re.IGNORECASE)
PATRON_EDAD_1 = re.compile(r'(\d+\+\s*años?|\b\d+\+|\+\d+\s*años?|\bmayores? de \d+\s*años?|\d+\s*años en adelante)', re.IGNORECASE)
PATRON_EDAD_2 = re.compile(r'(?:Edad mínima|Restricción de edad)[:\s\n]+([^\n]+)', re.IGNORECASE)
PATRON_HABILIDADES = re.compile(r'(?:Habilidades a desarrollar|Competencia|Habilidades)[:\n]\s*([^\n]+)', re.IGNORECASE)
PATRON_LUGAR = re.compile(r'(?:Lugar de realización|Lugar de ejecución|Lugar|Municipio|Sede|Centro de formación)[:\s\n]+([^\n]+)', re.IGNORECASE)
PATRON_PERIODO = re.compile(r'(desde el [^\n]+ hasta el [^\n]+|\d{1,2} de [a-z]+ de \d{4} - \d{1,2} de [a-z]+ de \d{4})', re.IGNORECASE)


def construir_url_catalogo(numero_pagina: int) -> str:
    """Construye la URL de paginación."""
    if numero_pagina == 1:
        return URL_BASE
    return f"{URL_BASE}?page={numero_pagina}"

def obtener_nombre_desde_url(url: str) -> str:
    """Saca el nombre del programa formateado a partir del slug de la URL."""
    try:
        path = urlparse(url).path
        match = PATRON_SLUG.search(path)
        if match:
            return match.group(1).replace('-', ' ').upper()
    except Exception:
        pass
    return "Programa SENA"

async def bloquear_recursos_innecesarios(route):
    """Bloquea la descarga de imágenes, fuentes, estilos y medios para acelerar la navegación."""
    if route.request.resource_type in ["image", "media", "font", "stylesheet"]:
        await route.abort()
    else:
        await route.continue_()

async def cerrar_banner_cookies(pagina):
    """Cierra modales o avisos flotantes."""
    for selector in ["button:has-text('Aceptar')", "button:has-text('Entendido')", ".close-modal", "button:has-text('Cerrar')"]:
        try:
            boton = pagina.locator(selector).first
            if await boton.is_visible(timeout=1000):
                await boton.click()
                log.info("Banner cerrado.")
                return
        except Exception:
            continue

async def desplegar_botones_ver_mas(pagina):
    """Hace clic en cualquier botón 'Ver más' o 'Mostrar más' si existe en la página de detalle."""
    for texto_btn in ["Ver más", "Mostrar más", "Ver contenido", "Leer más"]:
        try:
            botones = await pagina.locator(f"button:has-text('{texto_btn}'), a:has-text('{texto_btn}')").all()
            for btn in botones:
                if await btn.is_visible(timeout=500):
                    await btn.click()
                    await pagina.wait_for_timeout(300)
        except Exception:
            pass

async def extraer_detalle_completo(context, item: dict) -> dict:
    """Extrae y mapea exactamente los campos requeridos para las ofertas del SENA/Betowa."""
    url = item["url"]
    
    detalle = {
        "Fuente": "Sena",
        "Tipo": "No especificado",
        "url": url,
        "Nombre de evento": None,
        "Duracion": "No especificada",
        "Edad": "No especificada",
        "Habilidades a desarrollar": "No especificado",
        "Lugar": "No especificado",
        "Período académico": "No especificado",
        "Descripcion": "no hay descripcion"
    }
    
    pagina = await context.new_page()
    
    try:
        await pagina.goto(url, wait_until="domcontentloaded", timeout=40000)
        
        # Desplegar contenido oculto si hay botón 'Ver más'
        await desplegar_botones_ver_mas(pagina)

        # -------------------------------------------------------------------
        # 1. CAPTURA DEL NOMBRE DEL PROGRAMA
        # -------------------------------------------------------------------
        try:
            titulo_el = pagina.locator("main h1, article h1, .content h1, h1").first
            if await titulo_el.is_visible():
                texto_titulo = (await titulo_el.inner_text()).strip()
                if texto_titulo.lower() != "betowa" and len(texto_titulo) > 3:
                    detalle["Nombre de evento"] = texto_titulo
        except Exception:
            pass

        if not detalle["Nombre de evento"]:
            detalle["Nombre de evento"] = obtener_nombre_desde_url(url)

        # -------------------------------------------------------------------
        # 2. EXTRACCIÓN Y LIMPIEZA UNIFICADA EN JS (OPTIMIZADO)
        # -------------------------------------------------------------------
        contenido_extraido = await pagina.evaluate('''() => {
            // Extraer sección contenido
            let contenido = null;
            const encabezados = Array.from(document.querySelectorAll('h1, h2, h3, h4, h5, h6, strong, b, div, p'));
            const tituloContenido = encabezados.find(el => el.innerText && el.innerText.trim().toLowerCase() === 'contenido');
            
            if (tituloContenido) {
                let contenedor = tituloContenido.parentElement;
                while (contenedor && contenedor.querySelectorAll('li, p').length === 0 && contenedor.tagName !== 'BODY') {
                    contenedor = contenedor.parentElement;
                }

                if (contenedor) {
                    const items = Array.from(contenedor.querySelectorAll('li, p'));
                    const temas = [];
                    for (let item of items) {
                        const texto = item.innerText.trim();
                        const textoLower = texto.toLowerCase();

                        if (
                            textoLower.includes('requisitos para') || 
                            textoLower.includes('proceso de admisión') || 
                            textoLower.includes('habilidades que') || 
                            textoLower.includes('importante: esta plataforma')
                        ) {
                            break;
                        }

                        if (textoLower !== 'contenido' && texto.length > 2 && !temas.includes(texto)) {
                            temas.push(texto);
                        }
                    }
                    if (temas.length > 0) contenido = temas.join(', ');
                }
            }

            // Limpiar basura del DOM
            const elementosBasura = document.querySelectorAll(
                'header, footer, nav, .govco-header, .govco-footer, [role="banner"], [role="contentinfo"]'
            );
            elementosBasura.forEach(el => el.remove());

            return contenido;
        }''')

        if contenido_extraido and len(contenido_extraido.strip()) > 2:
            detalle["Descripcion"] = contenido_extraido.strip()

        # Captura de texto plano general para los demás campos
        main_container = pagina.locator("main, #root, body").first
        texto_raw = (await main_container.inner_text()).strip() if await main_container.is_visible() else ""

        for marcador in ["Todos los derechos reservados", "Términos y condiciones", "Política de privacidad", "Atención al ciudadano"]:
            idx = texto_raw.find(marcador)
            if idx != -1:
                texto_raw = texto_raw[:idx].strip()

        # 3. Tipo (Modalidad)
        if "modality=V" in url or PATRON_VIRTUAL.search(texto_raw):
            detalle["Tipo"] = "Virtual"
        elif "modality=P" in url or PATRON_PRESENCIAL.search(texto_raw):
            detalle["Tipo"] = "Presencial"
        elif PATRON_HIBRIDO.search(texto_raw):
            detalle["Tipo"] = "Híbrido"

        # 4. Duración
        match_duracion = PATRON_DURACION.search(texto_raw)
        if match_duracion:
            detalle["Duracion"] = match_duracion.group(1).lower()

        # 5. Edad
        match_edad = PATRON_EDAD_1.search(texto_raw)
        if match_edad:
            detalle["Edad"] = match_edad.group(1).strip()
        else:
            match_edad_label = PATRON_EDAD_2.search(texto_raw)
            if match_edad_label:
                detalle["Edad"] = match_edad_label.group(1).strip()

        # 6. Habilidades a desarrollar
        match_habilidades = PATRON_HABILIDADES.search(texto_raw)
        if match_habilidades:
            detalle["Habilidades a desarrollar"] = match_habilidades.group(1).strip()

        # 7. Lugar / Sede / Ambiente Virtual
        match_lugar = PATRON_LUGAR.search(texto_raw)
        if match_lugar:
            detalle["Lugar"] = match_lugar.group(1).strip()

        # 8. Período académico
        match_periodo = PATRON_PERIODO.search(texto_raw)
        if match_periodo:
            detalle["Período académico"] = match_periodo.group(1).strip()

    except Exception as e:
        log.warning(f"No se pudo procesar la URL {url}: {e}")
    finally:
        await pagina.close()

    return detalle

async def scrape_sena_ofertas() -> list:
    """Maneja la recolección de enlaces y extracción en paralelo por lotes."""
    eventos_extraidos = []
    vistos = set()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(user_agent=USER_AGENT, viewport={"width": 1440, "height": 900})
        
        await context.route("**/*", bloquear_recursos_innecesarios)
        
        pagina_catalogo = await context.new_page()

        numero_pagina = 1
        cookies_cerradas = False
        links_crudos = []

        log.info("Iniciando recolección de links del SENA...")
        
        while len(links_crudos) < LIMITE_EVENTOS and numero_pagina <= 20: 
            url = construir_url_catalogo(numero_pagina)
            log.info(f"Navegando a página {numero_pagina}: {url}")
            
            try:
                await pagina_catalogo.goto(url, wait_until="domcontentloaded", timeout=45000)
                try:
                    await pagina_catalogo.wait_for_selector(SELECTOR_LINKS, timeout=15000)
                except Exception:
                    log.warning(f"No se encontraron links de ofertas rápidamente en la página {numero_pagina}.")
            except Exception as e:
                log.error(f"Error navegando a {url}: {e}")
                break

            if not cookies_cerradas:
                await cerrar_banner_cookies(pagina_catalogo)
                cookies_cerradas = True

            # Extracción masiva en JS para optimizar el paso por Playwright CDP
            elementos_extraidos = await pagina_catalogo.locator(SELECTOR_LINKS).evaluate_all('''
                nodes => nodes.map(a => ({
                    href: a.getAttribute('href'),
                    text: a.innerText
                }))
            ''')

            links_en_pagina = 0
            
            for item in elementos_extraidos:
                href = item.get("href")
                if not href:
                    continue
                
                if href.startswith("/"):
                    href = DOMINIO_BASE + href
                    
                if href not in vistos:
                    lineas_tarjeta = [l.strip() for l in item["text"].split('\n') if l.strip()]
                    
                    links_crudos.append({
                        "url": href,
                        "tarjeta_backup": lineas_tarjeta
                    })
                    vistos.add(href)
                    links_en_pagina += 1

            log.info(f"  Página {numero_pagina}: {links_en_pagina} links nuevos. Total acumulado: {len(links_crudos)}")

            if links_en_pagina == 0:
                log.info("  No se encontraron más links. Fin de la paginación.")
                break
                 
            numero_pagina += 1

        links_crudos = links_crudos[:LIMITE_EVENTOS]
        await pagina_catalogo.close()

        log.info(f"\nExtrayendo información de {len(links_crudos)} ofertas en lotes de {TAMAÑO_LOTE}...")
        
        for i in range(0, len(links_crudos), TAMAÑO_LOTE):
            lote = links_crudos[i : i + TAMAÑO_LOTE]
            log.info(f"  Procesando lote {i//TAMAÑO_LOTE + 1} (Ofertas {i+1} a {i+len(lote)})...")
            
            tareas = [extraer_detalle_completo(context, item) for item in lote]
            resultados_lote = await asyncio.gather(*tareas, return_exceptions=True)
            
            for res in resultados_lote:
                if isinstance(res, dict):
                    eventos_extraidos.append(res)
                else:
                    log.error(f"Ocurrió un error no capturado en el lote: {res}")

        await browser.close()
        
    return eventos_extraidos

async def ejecutar_scraper_sena_y_guardar(nombre_archivo="dataset_sena_betowa_enriquecido.json"):
    """Inicia el proceso y guarda el dataset actualizado."""
    log.info("Iniciando extracción SENA...")
    datos_eventos = await scrape_sena_ofertas()
    
    with open(nombre_archivo, "w", encoding="utf-8") as f:
        json.dump(datos_eventos, f, ensure_ascii=False, indent=4)
        
    log.info(f"¡Extracción completada! Se guardaron {len(datos_eventos)} ofertas en '{nombre_archivo}'.")
    return datos_eventos

if __name__ == "__main__":
    asyncio.run(ejecutar_scraper_sena_y_guardar())