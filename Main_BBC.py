import os
import logging
from typing import Optional

import pandas as pd
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from pydantic import BaseModel, Field
from langchain_google_genai import ChatGoogleGenerativeAI
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

# ============================================================
# CONFIGURACIÓN
# ============================================================
URL_BASE = "https://www.bbc.com/mundo/topics/cyx5krnw38vt" # Sección de Tecnología
LIMITE_NOTICIAS = 15

MODELO_GEMINI = "gemini-3.1-flash-lite"
TEMPERATURA_LLM = 0.1

ARCHIVO_SALIDA_EXCEL = "noticias_bbc_tecnologia.xlsx"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# Selector flexible para capturar los enlaces a los artículos de la BBC
SELECTOR_LINKS_NOTICIA = 'a[href*="/mundo/articles/"], a[href*="/mundo/noticias-"]'

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("bbc_tecnologia_llm")


# ============================================================
# 1. ESQUEMA DE SALIDA ESTRICTO PARA NOTICIAS
# ============================================================
class NoticiaBBC(BaseModel):
    # Obligatorios
    titulo: str = Field(description="Título principal de la noticia o artículo.")
    resumen_ejecutivo: str = Field(description="Un resumen conciso y sólido de la noticia (3-5 frases) que sintetice los hechos clave.")
    fecha_publicacion: str = Field(description="Fecha de publicación o actualización mencionada en el texto. Si no aparece, 'No especificado'.")
    enlace_detalle: str = Field(description="URL de la noticia (se completa automáticamente, no la infieras del texto).")

    # Opcionales / Extraídos por el LLM si existen
    temas_clave: Optional[str] = Field(None, description="Lista de palabras clave o subtemas tecnológicos principales separados por comas (ej: Inteligencia Artificial, Regulación, Apple).")
    entidades_mencionadas: Optional[str] = Field(None, description="Empresas, personas o países importantes que protagonizan la nota.")
    tono_noticia: Optional[str] = Field(None, description="Tono de la nota (ej: Informativo, Alarmante, Crítico, Optimista).")


# ============================================================
# 2. EXTRACTOR LLM
# ============================================================
def crear_extractor_llm():
    load_dotenv()
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("No se encontró GEMINI_API_KEY en el entorno (.env)")

    llm = ChatGoogleGenerativeAI(
        model=MODELO_GEMINI,
        temperature=TEMPERATURA_LLM,
        google_api_key=api_key,
    )
    return llm.with_structured_output(NoticiaBBC)


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, min=2, max=15),
    retry=retry_if_exception_type(Exception),
    reraise=True,
)
def extraer_noticia_con_llm(extractor, texto_pagina: str, url: str) -> NoticiaBBC:
    """Le pide al LLM que analice y resuma el texto de la noticia."""
    prompt = (
        "Analiza el texto de esta noticia de BBC Mundo y extrae la información requerida. "
        "Sé preciso y objetivo con el resumen ejecutivo. Si algún campo opcional no puede "
        "deducirse del texto, déjalo como null.\n\n"
        f"URL de la noticia: {url}\n\n"
        f"TEXTO DEL ARTÍCULO:\n{texto_pagina[:8000]}"
    )
    resultado = extractor.invoke(prompt)
    resultado.enlace_detalle = url  # Aseguramos que la URL real quede grabada
    return resultado


# ============================================================
# 3. NAVEGACIÓN Y CAPTURA DE TEXTO (Playwright)
# ============================================================
def recolectar_links_noticias(pagina) -> list[str]:
    """Busca los enlaces de los artículos en la página principal de Tecnología."""
    anchors = pagina.locator(SELECTOR_LINKS_NOTICIA)
    total = anchors.count()
    links = []
    vistos = set()
    
    for i in range(total):
        href = anchors.nth(i).get_attribute("href")
        if href:
            # Asegurar URL absoluta si la BBC usa rutas relativas
            if href.startswith("/"):
                href = f"https://www.bbc.com{href}"
            
            if href not in vistos and "topics" not in href:
                links.append(href)
                vistos.add(href)
    return links


def obtener_texto_noticia(pagina, url: str) -> Optional[str]:
    """Navega a la noticia y extrae el texto limpio del cuerpo del artículo."""
    try:
        pagina.goto(url, wait_until="domcontentloaded", timeout=30000)
        pagina.wait_for_timeout(2000)
        
        # Intentamos capturar el contenido del artículo de forma limpia. 
        # Si falla, recurrimos al body completo.
        article_locator = pagina.locator("main")
        if article_locator.count() > 0:
            return article_locator.first.inner_text()
        return pagina.locator("body").inner_text()
    except Exception as e:
        log.warning(f"No se pudo abrir la noticia {url}: {e}")
        return None


# ============================================================
# 4. ORQUESTADOR PRINCIPAL
# ============================================================
def main():
    extractor = crear_extractor_llm()
    noticias_procesadas: list[NoticiaBBC] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        pagina = browser.new_page(user_agent=USER_AGENT, viewport={"width": 1440, "height": 900})

        log.info(f"Paso 1: Recolectando enlaces desde la portada de tecnología: {URL_BASE}")
        try:
            pagina.goto(URL_BASE, wait_until="domcontentloaded", timeout=45000)
            pagina.wait_for_timeout(3000)
        except PlaywrightTimeoutError as e:
            log.error(f"Timeout al cargar la portada principal: {e}")
            browser.close()
            return

        links = recolectar_links_noticias(pagina)
        links = links[:LIMITE_NOTICIAS] # Aplicamos el límite configurado
        log.info(f"Se encontraron {len(links)} noticias para procesar.")

        log.info("Paso 2: Extrayendo y resumiendo contenido con Gemini...")
        for i, url in enumerate(links, start=1):
            log.info(f"  [{i}/{len(links)}] {url}")
            texto = obtener_texto_noticia(pagina, url)
            
            if not texto or len(texto.strip()) < 200:
                log.warning("    Texto insuficiente o página vacía. Saltando...")
                continue
                
            try:
                noticia_estructurada = extraer_noticia_con_llm(extractor, texto, url)
                noticias_procesadas.append(noticia_estructurada)
                log.info(f"    ✓ Resumido con éxito: '{noticia_estructurada.titulo[:40]}...'")
            except Exception as e:
                log.error(f"  Falló la extracción LLM para {url}: {e}")

        browser.close()

    # --- Exportar resultados ---
    if not noticias_procesadas:
        log.warning("No se procesó ninguna noticia. No se genera el archivo Excel.")
        return

    df = pd.DataFrame([n.model_dump() for n in noticias_procesadas])
    
    # Ordenamos las columnas del Excel
    columnas_orden = [
        "titulo", "resumen_ejecutivo", "fecha_publicacion", 
        "temas_clave", "entidades_mencionadas", "tono_noticia", "enlace_detalle"
    ]
    df = df[[c for c in columnas_orden if c in df.columns]]
    df.to_excel(ARCHIVO_SALIDA_EXCEL, index=False, engine="openpyxl")

    log.info(f"\n=== {len(noticias_procesadas)} noticias resumidas y exportadas a '{ARCHIVO_SALIDA_EXCEL}' ===")


if __name__ == "__main__":
    main()