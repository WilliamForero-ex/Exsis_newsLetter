"""
Script para scrapear el catálogo de eventos de Microsoft:
https://www.microsoft.com/en-us/events/search-catalog/

RESUMEN DE CÓMO FUNCIONA (después de varias iteraciones de diagnóstico):

1. PAGINACIÓN: se controla con el parámetro de URL "page=N". Navegar
   directo a "...&page=2", "...&page=3" ya trae esos resultados — no
   hace falta clickear ni scrollear.

2. LOS DATOS COMPLETOS (descripción, horario exacto, formato/ubicación)
   NO siempre están en la tarjeta chica del catálogo — para muchos
   eventos solo están en su PÁGINA DE DETALLE individual
   (msevents.microsoft.com/event?id=...). Por eso el script:
     a) Recorre las páginas del catálogo para juntar los links de los
        eventos (y de paso saca lo que pueda de la tarjeta: nombre, fecha).
     b) Visita cada página de detalle para completar/confirmar nombre,
        descripción, horario y formato/ubicación con más precisión
        (soporta tanto "12:00 - 13:00 GMT-5" como "12:00 – 14:00 (GMT+08:00)").

Uso:
    python scraping_microsoft_eventos.py
"""

import logging
import os
import re
from typing import Optional
from urllib.parse import quote

from dotenv import load_dotenv
from langchain_google_genai import ChatGoogleGenerativeAI
from playwright.sync_api import sync_playwright
from pydantic import BaseModel, Field

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("microsoft_eventos")


# ============================================================
# Esquema de salida estricto para la extracción con IA (igual criterio
# que tu OfertaEducativa: campos obligatorios + opcionales con Field)
# ============================================================
class DetalleEvento(BaseModel):
    nombre: Optional[str] = Field(None, description="Título oficial del evento.")
    descripcion: Optional[str] = Field(None, description="Resumen breve del evento en 2-3 oraciones, en el idioma original del texto.")
    formato: Optional[str] = Field(None, description="Modalidad del evento: Digital, Onsite, Hybrid, Online, Presencial, etc.")
    fecha: Optional[str] = Field(None, description="Fecha del evento en formato AAAA-MM-DD.")
    horario: Optional[str] = Field(None, description="Horario de inicio y fin con su zona horaria, ej. '09:00 - 17:00 GMT+05:30'.")


def _cargar_gemini_api_key() -> str:
    load_dotenv()
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("No se encontró GEMINI_API_KEY en el entorno (.env)")
    return api_key


URL_BASE = "https://www.microsoft.com/en-us/events/search-catalog/"
FILTROS = "audience:developers,primary-language:english"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

LIMITE_EVENTOS = 24
SELECTOR_LINKS = 'a[href*="msevents.microsoft.com/event"]'

SELECTORES_COOKIES = [
    "#onetrust-accept-btn-handler",
    "button:has-text('Accept all')",
    "button:has-text('Accept')",
]

# --- Patrones generales ---
PATRON_FECHA = re.compile(r"\d{4}-\d{2}-\d{2}")
# Horario flexible: acepta guion normal "-" o guion largo "–"/"—", y un
# sufijo de zona horaria opcional en varios formatos:
#   "22:30 - 23:30 GMT-5"        (catálogo)
#   "12:00 – 14:00 (GMT+08:00)"  (página de detalle)
PATRON_HORARIO = re.compile(
    r"\d{1,2}:\d{2}\s*[\-\u2013\u2014]\s*\d{1,2}:\d{2}"
    r"(\s*\(?GMT[+-]?\d{1,2}(:\d{2})?\)?)?"
)
FORMATOS_CONOCIDOS = {"digital", "onsite", "hybrid", "virtual", "in person", "on-demand"}
PREFIJOS_CTA_A_LIMPIAR = [
    "registration and details", "register now", "learn more",
    "share results", "details",
]
LINEAS_A_IGNORAR = {
    "registration and details", "register now", "learn more", "details",
    "share results", "regístrate", "inscríbete", "registrarse",
}

# JS: sube por los ancestros del link de la TARJETA del catálogo mientras
# siga teniendo UNA SOLA fecha AAAA-MM-DD. Se queda con el último
# contenedor válido (el más grande) antes de que aparezca una segunda
# fecha (que indicaría que ya se coló la tarjeta vecina).
JS_SUBIR_HASTA_TARJETA = """
(anchor) => {
    let el = anchor;
    let mejorTexto = el.innerText || "";
    const patronFecha = /\\d{4}-\\d{2}-\\d{2}/g;
    for (let i = 0; i < 14; i++) {
        if (!el.parentElement) break;
        el = el.parentElement;
        const texto = el.innerText || "";
        const matches = texto.match(patronFecha);
        if (matches && matches.length === 1 && texto.length < 3000) {
            mejorTexto = texto;
        } else if (matches && matches.length > 1) {
            break;
        }
    }
    return mejorTexto;
}
"""


def construir_url_catalogo(numero_pagina: int) -> str:
    return f"{URL_BASE}?filters={quote(FILTROS)}&scenario=events&page={numero_pagina}"


def cerrar_banner_cookies(pagina):
    for selector in SELECTORES_COOKIES:
        try:
            boton = pagina.locator(selector).first
            if boton.is_visible(timeout=1500):
                boton.click()
                log.info(f"Banner de cookies cerrado con selector: {selector}")
                pagina.wait_for_timeout(1000)
                return
        except Exception:
            continue


def _quitar_prefijos_cta(linea: str) -> str:
    """Si la línea empieza con una frase de call-to-action (a veces viene
    pegada sin salto de línea al título real), la recorta."""
    resultado = linea
    cambiado = True
    while cambiado:
        cambiado = False
        for prefijo in PREFIJOS_CTA_A_LIMPIAR:
            if resultado.lower().startswith(prefijo):
                resultado = resultado[len(prefijo):].strip(" :\u2022\u2013\u2014-")
                cambiado = True
    return resultado


def parsear_tarjeta_catalogo(texto: str, link: str) -> dict:
    """Extrae lo que se pueda de la tarjeta chica del catálogo. Puede
    quedar incompleto (se completa después visitando el detalle)."""
    lineas = [l.strip(" •*\t") for l in texto.split("\n")]
    lineas = [_quitar_prefijos_cta(l) for l in lineas]
    lineas = [l for l in lineas if l]

    fecha = None
    horario = None
    formato = None
    restantes = []

    for linea in lineas:
        if PATRON_FECHA.fullmatch(linea):
            fecha = linea
            continue
        if PATRON_HORARIO.fullmatch(linea.strip()):
            horario = linea.strip()
            continue
        if linea.lower() in FORMATOS_CONOCIDOS:
            formato = linea
            continue
        restantes.append(linea)

    nombre = restantes[0] if restantes else None
    descripcion = " ".join(restantes[1:]) if len(restantes) > 1 else None

    return {
        "nombre": nombre, "descripcion": descripcion, "formato": formato,
        "fecha": fecha, "horario": horario, "link": link,
    }


def recolectar_eventos_del_catalogo(pagina) -> tuple[list[dict], int]:
    anchors = pagina.locator(SELECTOR_LINKS)
    total = anchors.count()
    eventos = []
    for i in range(total):
        anchor = anchors.nth(i)
        try:
            href = anchor.get_attribute("href")
            if not href:
                continue
            texto_tarjeta = anchor.evaluate(JS_SUBIR_HASTA_TARJETA)
            evento = parsear_tarjeta_catalogo(texto_tarjeta, href)

            if not evento["nombre"]:
                aria_label = anchor.get_attribute("aria-label") or ""
                nombre_limpio = _quitar_prefijos_cta(aria_label.strip())
                evento["nombre"] = nombre_limpio or None

            eventos.append(evento)
        except Exception as e:
            log.warning(f"No se pudo procesar una tarjeta: {e}")
    return eventos, total


def extraer_detalle_evento(pagina, url: str, llm_estricto) -> dict:
    """Visita la página INDIVIDUAL del evento, y en vez de tratar de
    cubrir con regex todos los formatos de fecha/hora que usa Microsoft
    (varían: "2026-07-15" vs "Wednesday, July 15, 2026, 9:00 AM – 5:00 PM",
    inglés/español, con o sin AM/PM...), le pasamos el texto crudo a un
    modelo de lenguaje para que lo interprete y normalice."""
    detalle = {
        "nombre": None, "descripcion": None, "formato": None,
        "fecha": None, "horario": None,
    }
    try:
        pagina.goto(url, wait_until="domcontentloaded", timeout=30000)
        pagina.wait_for_timeout(3500)
        texto_completo = pagina.locator("body").inner_text()
    except Exception as e:
        log.warning(f"No se pudo abrir el detalle de {url}: {e}")
        return detalle

    # Recortamos a un tamaño razonable para no gastar de más en tokens
    texto_recortado = texto_completo[:6000]

    try:
        detalle_ia = pedir_extraccion_a_ia(llm_estricto, texto_recortado)
        detalle.update(detalle_ia)
    except Exception as e:
        log.warning(f"La IA no pudo procesar {url}: {e}")

    return detalle


def pedir_extraccion_a_ia(llm_estricto, texto: str) -> dict:
    prompt = (
        "Extrae los datos de este evento de Microsoft. El texto puede estar "
        "en inglés o español, y la fecha/hora puede venir en cualquier "
        "formato (ej. 'Wednesday, July 15, 2026, 9:00 AM – 5:00 PM "
        "(GMT+05:30)') — normalizalas al formato pedido en el esquema. "
        "Si algún dato no aparece en el texto, dejalo en null (no inventes "
        f"información).\n\nTexto de la página:\n{texto}"
    )
    resultado: DetalleEvento = llm_estricto.invoke(prompt)
    return resultado.model_dump(exclude_none=True)


def completar_con_detalle(pagina, evento: dict, llm_estricto) -> dict:
    """Si al evento le falta descripción, horario o formato, visita su
    página individual y usa la IA para completarlo."""
    if evento["descripcion"] and evento["horario"] and evento["formato"]:
        return evento

    detalle = extraer_detalle_evento(pagina, evento["link"], llm_estricto)
    for campo in ("nombre", "descripcion", "formato", "fecha", "horario"):
        if not evento.get(campo) and detalle.get(campo):
            evento[campo] = detalle[campo]
    return evento


def main():
    eventos = []
    vistos = set()

    llm_estricto = ChatGoogleGenerativeAI(
        model="gemini-3.1-flash-lite",
        temperature=0.1,
        google_api_key=_cargar_gemini_api_key(),
    ).with_structured_output(DetalleEvento)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        pagina = browser.new_page(user_agent=USER_AGENT, viewport={"width": 1440, "height": 900})

        # --- Paso 1: recorrer el catálogo paginado y juntar eventos ---
        numero_pagina = 1
        cookies_cerradas = False

        while len(eventos) < LIMITE_EVENTOS and numero_pagina <= 15:
            url = construir_url_catalogo(numero_pagina)
            log.info(f"Navegando al catálogo, página {numero_pagina}: {url}")
            try:
                pagina.goto(url, wait_until="domcontentloaded", timeout=45000)
            except Exception as e:
                log.error(f"Error al navegar a la página {numero_pagina}: {e}")
                break

            pagina.wait_for_timeout(3000)
            if not cookies_cerradas:
                cerrar_banner_cookies(pagina)
                cookies_cerradas = True
            pagina.wait_for_timeout(3000)

            nuevos, total_crudo = recolectar_eventos_del_catalogo(pagina)
            nuevos_sin_repetir = [e for e in nuevos if e["link"] not in vistos]
            for e in nuevos_sin_repetir:
                vistos.add(e["link"])
            eventos.extend(nuevos_sin_repetir)

            log.info(
                f"  Página {numero_pagina}: {total_crudo} links, "
                f"{len(nuevos_sin_repetir)} nuevos. Total acumulado: {len(eventos)}"
            )

            if total_crudo == 0:
                log.info("  Página vacía: se llegó al final del catálogo.")
                break

            numero_pagina += 1

        eventos = eventos[:LIMITE_EVENTOS]

        # --- Paso 2: completar cada evento visitando su página de detalle ---
        log.info(f"Completando {len(eventos)} eventos con su página de detalle...")
        for i, evento in enumerate(eventos, start=1):
            log.info(f"  [{i}/{len(eventos)}] {evento['link']}")
            eventos[i - 1] = completar_con_detalle(pagina, evento, llm_estricto)

        browser.close()

    print(f"\n=== {len(eventos)} eventos encontrados ===\n")
    for e in eventos:
        print(f"Nombre       : {e['nombre']}")
        print(f"Descripción  : {e['descripcion']}")
        print(f"Formato      : {e['formato']}")
        print(f"Fecha        : {e['fecha']}")
        print(f"Horario      : {e['horario']}")
        print(f"Link         : {e['link']}")
        print("-" * 80)


if __name__ == "__main__":
    main()