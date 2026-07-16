"""
Newsletter Script — Diseño de 25 Ítems con Scrollbars y Cabeceras Fijas
========================================================================
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from google import genai
from google.genai import types

# ══════════════════════════════════════════════════════════════════════════════
# CONFIGURACIÓN
# ══════════════════════════════════════════════════════════════════════════════

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("newsletter_agent")

BASE_DIR = Path(__file__).parent
load_dotenv(dotenv_path=BASE_DIR / ".env", override=False)

# Mapeo exacto a los nombres de los archivos JSON
RUTA_JSON: dict[str, str] = {
    "bbc_tecnologia": str(BASE_DIR / "dataset_bbc_tecnologia.json"),
    "azure": str(BASE_DIR / "eventos_azure_tech_detallado.json"),
    "tdsynnex": str(BASE_DIR / "dataset_tdsynnex_events.json"),
    "microsoft": str(BASE_DIR / "dataset_microsoft_events.json"),
}

ARCHIVO_SALIDA_HTML = str(BASE_DIR / "newsletter_output.html")

_GEMINI_MODEL = "gemini-3.1-flash-lite"
_gemini_client = genai.Client()  


# ══════════════════════════════════════════════════════════════════════════════
# FUNCIONES NÚCLEO
# ══════════════════════════════════════════════════════════════════════════════

def _load_json(path: str) -> list[dict]:
    p = Path(path)
    if not p.exists():
        log.warning(f"Archivo no encontrado: {path}")
        return []
    with p.open(encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, list) else [data]


def _recortar_texto_limpio(texto: str | None, max_chars: int) -> str:
    """Aligera el peso de la API cortando el texto eficientemente."""
    if not texto:
        return ""
    texto = texto.strip()
    texto = re.sub(r'\s+', ' ', texto) 
    
    if len(texto) <= max_chars:
        return texto
    
    recorte = texto[:max_chars]
    ultimo_espacio = recorte.rfind(' ')
    if ultimo_espacio > 0:
        recorte = recorte[:ultimo_espacio]
        
    recorte = re.sub(r'[,;\.\-\:]$', '', recorte)
    return recorte + "..."


def _cerrar_etiquetas_rotas(html: str) -> str:
    """Función de emergencia: Si Gemini omite cierres, esta función los inyecta para evitar colapsos."""
    html = html.strip()
    faltan_td = html.count("<td") - html.count("</td>")
    faltan_tr = html.count("<tr") - html.count("</tr>")
    faltan_table = html.count("<table") - html.count("</table>")
    
    if faltan_td > 0: html += "</td>" * faltan_td
    if faltan_tr > 0: html += "</tr>" * faltan_tr
    if faltan_table > 0: html += "</table>" * faltan_table
    
    return html


def _generar_html_con_gemini(titulo_seccion: str, datos: list[dict], es_doble_columna: bool = True, reglas_adicionales: str = "") -> str:
    if not datos:
        return f"<table width='100%'><tr><td style='padding:20px; text-align:center; color:#666;'>No hay datos recientes para esta sección.</td></tr></table>"

    # LÍMITE AUMENTADO: Hasta 25 ítems por sección
    datos_visibles = datos[:25] 
    cantidad_items = len(datos_visibles)
    contenido_json = json.dumps(datos_visibles, ensure_ascii=False, indent=2)

    if es_doble_columna:
        plantilla_exacta = f"""
    <table width="100%" border="0" cellpadding="0" cellspacing="0" style="background-color: #FFFFFF; font-family: Arial, sans-serif;">
      <!-- CABECERA STICKY (SE QUEDA FIJA AL HACER SCROLL) -->
      <tr>
        <td colspan="2" style="background-color: #0078D4; color: #FFFFFF; padding: 15px 20px; position: sticky; top: 0; z-index: 10;">
          <h2 style="margin: 0; font-size: 18px;">{titulo_seccion}</h2>
        </td>
      </tr>
      
      <!-- INICIO FILA DE TARJETAS (AGRUPA EXACTAMENTE 2 ITEMS POR CADA <tr>) -->
      <tr>
        <!-- ITEM IZQUIERDO -->
        <td width="50%" valign="top" style="padding: 15px 10px 15px 15px;">
          <table width="100%" height="100%" border="0" cellpadding="0" cellspacing="0" style="border: 1px solid #E0E0E0; border-radius: 4px; background-color: #FAFAFA;">
            <tr>
              <td valign="top" style="padding: 15px;">
                <h3 style="color: #0078D4; font-size: 15px; margin: 0 0 10px 0;">[TITULO]</h3>
                <p style="font-size: 12px; color: #605E5C; margin: 0 0 10px 0;">[FECHA]</p>
                <p style="font-size: 13px; color: #323130; margin: 0; line-height: 1.4;">[RESUMEN]</p>
              </td>
            </tr>
            <tr>
              <td valign="bottom" style="padding: 0 15px 15px 15px;">
                <a href="[URL]" style="display: inline-block; padding: 8px 16px; background-color: #0078D4; color: #ffffff; text-decoration: none; border-radius: 4px; font-size: 12px; font-weight: bold;">Leer más</a>
              </td>
            </tr>
          </table>
        </td>
        
        <!-- ITEM DERECHO -->
        <td width="50%" valign="top" style="padding: 15px 15px 15px 10px;">
          <table width="100%" height="100%" border="0" cellpadding="0" cellspacing="0" style="border: 1px solid #E0E0E0; border-radius: 4px; background-color: #FAFAFA;">
            <tr>
              <td valign="top" style="padding: 15px;">
                <h3 style="color: #0078D4; font-size: 15px; margin: 0 0 10px 0;">[TITULO]</h3>
                <p style="font-size: 12px; color: #605E5C; margin: 0 0 10px 0;">[FECHA]</p>
                <p style="font-size: 13px; color: #323130; margin: 0; line-height: 1.4;">[RESUMEN]</p>
              </td>
            </tr>
            <tr>
              <td valign="bottom" style="padding: 0 15px 15px 15px;">
                <a href="[URL]" style="display: inline-block; padding: 8px 16px; background-color: #0078D4; color: #ffffff; text-decoration: none; border-radius: 4px; font-size: 12px; font-weight: bold;">Leer más</a>
              </td>
            </tr>
          </table>
        </td>
      </tr>
      <!-- FIN FILA -->

    </table>
    """
    else:
        plantilla_exacta = f"""
    <table width="100%" border="0" cellpadding="0" cellspacing="0" style="background-color: #FFFFFF; font-family: Arial, sans-serif;">
      <!-- CABECERA STICKY (SE QUEDA FIJA AL HACER SCROLL) -->
      <tr>
        <td style="background-color: #0078D4; color: #FFFFFF; padding: 15px 20px; position: sticky; top: 0; z-index: 10;">
          <h2 style="margin: 0; font-size: 18px;">{titulo_seccion}</h2>
        </td>
      </tr>
      <!-- REPETIR ESTE BLOQUE <tr> POR CADA ELEMENTO -->
      <tr>
        <td valign="top" style="padding: 15px 20px; border-bottom: 1px solid #E0E0E0;">
          <h3 style="color: #0078D4; font-size: 16px; margin: 0 0 10px 0;">[TITULO]</h3>
          <p style="font-size: 13px; color: #605E5C; margin: 0 0 10px 0;">[FECHA]</p>
          <p style="font-size: 13px; color: #323130; margin: 0 0 15px 0; line-height: 1.5;">[RESUMEN]</p>
          <a href="[URL]" style="display: inline-block; padding: 8px 16px; background-color: #0078D4; color: #ffffff; text-decoration: none; border-radius: 4px; font-size: 13px; font-weight: bold;">Leer más</a>
        </td>
      </tr>
    </table>
    """

    prompt = f"""
    Eres un desarrollador experto en maquetación de correos electrónicos.
    Transforma el siguiente JSON en HTML, usando EXACTAMENTE la estructura de la PLANTILLA OBLIGATORIA.

    REGLAS OBLIGATORIAS:
    1. DEVUELVE ÚNICAMENTE EL CÓDIGO HTML CRUDO. Cero comentarios, cero bloques markdown.
    2. Cierra TODAS las etiquetas. Un <tr> o <td> abierto destruirá el correo completo.
    3. {"La estructura es de DOS COLUMNAS. Debes meter 2 items por cada etiqueta <tr>. IMPORTANTE: Si el total de items es impar y sobra un item, en la última fila coloca el item en la izquierda y en la derecha pon una celda vacía: `<td width='50%' style='padding: 15px;'></td>`." if es_doble_columna else "Cada item va en su propia fila <tr>."}
    4. Procesa y maqueta la totalidad de los {cantidad_items} elementos.
    
    {reglas_adicionales}

    PLANTILLA OBLIGATORIA A REPLICAR (Sustituye los corchetes):
    {plantilla_exacta}

    DATOS JSON:
    {contenido_json}
    """
    
    log.info(f"⏳ Procesando {cantidad_items} ítems para '{titulo_seccion}' ...")
    
    for intento in range(3):
        try:
            response = _gemini_client.models.generate_content(
                model=_GEMINI_MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.0, 
                    max_output_tokens=8192
                )
            )
            
            log.info(f"✅ HTML generado para: '{titulo_seccion}'.")
            
            html = response.text or ""
            html = re.sub(r"```html?\s*", "", html)
            html = re.sub(r"```\s*$", "", html)
            inicio = html.find('<')
            fin = html.rfind('>')
            if inicio != -1 and fin != -1:
                html = html[inicio:fin+1]
                
            # AUTO-CURACIÓN: Cerramos etiquetas por si la IA se quedó corta
            html = _cerrar_etiquetas_rotas(html)
            
            return html.strip()
            
        except Exception as e:
            log.warning(f"⚠️ Retraso de red o timeout con Gemini (Intento {intento+1}/3). Reintentando...")
            if intento == 2:
                log.error(f"❌ Error al generar la sección '{titulo_seccion}': {e}")
                return ""
            time.sleep(3)


# ══════════════════════════════════════════════════════════════════════════════
# PROCESAMIENTO DE SECCIONES 
# ══════════════════════════════════════════════════════════════════════════════

def procesar_bbc(clave_json: str, titulo_seccion: str) -> str:
    datos = _load_json(RUTA_JSON[clave_json])
    resumidos = [
        {
            "titulo": i.get("titulo"), 
            "fecha": i.get("fecha_publicacion"), 
            "url": i.get("url_noticia"), 
            # Reducido a 120 chars para aligerar la carga de 25 tarjetas
            "resumen": _recortar_texto_limpio(i.get("texto_crudo_html"), 120)
        } for i in datos
    ]
    
    reglas_bbc = "- En el campo [RESUMEN], añade el prefijo 'Resumen Ejecutivo:' en negrita al principio del texto."
    return _generar_html_con_gemini(titulo_seccion, resumidos, es_doble_columna=False, reglas_adicionales=reglas_bbc)


def procesar_azure() -> str:
    datos = _load_json(RUTA_JSON["azure"])
    resumidos = [
        {
            "titulo": i.get("titulo"), 
            "fecha_y_hora": i.get("fecha_y_hora"), 
            "url": i.get("url_evento"),
            "resumen": _recortar_texto_limpio(i.get("descripcion"), 120) 
        } for i in datos
    ]
    
    reglas_eventos = "- En [FECHA], usa emojis sutiles, ejemplo: '📅 15 de Oct. | ⏰ 10:00 AM'."
    return _generar_html_con_gemini("☁️ Eventos Azure Tech Groups", resumidos, es_doble_columna=True, reglas_adicionales=reglas_eventos)


def procesar_tdsynnex() -> str:
    datos = _load_json(RUTA_JSON["tdsynnex"])
    resumidos = [
        {
            "titulo": i.get("nombre"), 
            "fecha_y_hora": f"{i.get('fecha', '')} {i.get('hora_inicio', '')}", 
            "url": i.get("url"), 
            "resumen": _recortar_texto_limpio(i.get("descripcion"), 120)
        } for i in datos
    ]
    
    reglas_eventos = "- En [FECHA], usa emojis sutiles, ejemplo: '📅 15 de Oct. | ⏰ 10:00 AM'."
    return _generar_html_con_gemini("🌐 Eventos Hola TD SYNNEX", resumidos, es_doble_columna=True, reglas_adicionales=reglas_eventos)


def procesar_microsoft() -> str:
    datos = _load_json(RUTA_JSON["microsoft"])
    resumidos = [
        {
            "titulo": i.get("titulo"), 
            "fecha_y_hora": i.get("fecha_y_hora"), 
            "url": i.get("url_evento"), 
            "resumen": _recortar_texto_limpio(i.get("descripcion_completa"), 120) 
        } for i in datos
    ]
    
    reglas_eventos = "- En [FECHA], usa emojis sutiles, ejemplo: '📅 15 de Oct. | ⏰ 10:00 AM'."
    return _generar_html_con_gemini("🚀 Microsoft Official Events", resumidos, es_doble_columna=True, reglas_adicionales=reglas_eventos)


def ensamblar_newsletter(bloques_html: list[str]) -> str:
    log.info("⚙️ Ensamblando el boletín final con soporte de Scrollbars...")
    
    fecha_edicion = datetime.now().strftime("%d de %B, %Y")

    # Envolvemos cada bloque de Gemini en un DIV con scroll y estilos corporativos
    bloques_limpios = []
    for b in bloques_html:
        if b:
            bloques_limpios.append(f"""
            <tr>
              <td align="center" style="padding: 0 10px 30px 10px; width: 100%;">
                <div class="scroll-container">
                  {b}
                </div>
              </td>
            </tr>
            """)

    html_final = f"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Exsis Newsletter</title>
  <style>
    body {{ margin: 0; padding: 0; background-color: #F3F2F1; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; -webkit-text-size-adjust: 100%; -ms-text-size-adjust: 100%; }}
    table {{ border-collapse: collapse; mso-table-lspace: 0pt; mso-table-rspace: 0pt; }}
    
    /* Configuración de Scrollbar por sección */
    .scroll-container {{
       max-height: 650px; 
       overflow-y: auto; 
       overflow-x: hidden; 
       border-radius: 8px; 
       border: 1px solid #E0E0E0;
       background-color: #FFFFFF;
       box-shadow: 0 4px 6px rgba(0,0,0,0.05);
    }}
    /* Diseño de la barra de desplazamiento */
    .scroll-container::-webkit-scrollbar {{ width: 8px; }}
    .scroll-container::-webkit-scrollbar-track {{ background: #f1f1f1; border-radius: 4px; }}
    .scroll-container::-webkit-scrollbar-thumb {{ background: #0078D4; border-radius: 4px; }}
    .scroll-container::-webkit-scrollbar-thumb:hover {{ background: #005a9e; }}
    
    @media screen and (max-width: 600px) {{
      .responsive-table {{ width: 100% !important; }}
    }}
  </style>
</head>
<body>

  <!-- HEADER AZUL PRINCIPAL -->
  <table width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color:#0078D4;">
    <tr>
      <td align="center" style="padding: 30px 20px;">
        <table class="responsive-table" width="700" cellpadding="0" cellspacing="0" border="0">
          <tr>
            <td width="60" valign="middle">
              <table cellpadding="0" cellspacing="0" border="0" style="background-color:#FFFFFF; border-radius:50%; width:50px; height:50px; text-align:center;">
                <tr>
                  <td align="center" valign="middle" style="font-family:'Segoe UI', Arial, sans-serif; font-size:24px; font-weight:bold; color:#0078D4; height:50px; width:50px; line-height:50px;">
                    E
                  </td>
                </tr>
              </table>
            </td>
            <td valign="middle" style="padding-left: 15px;">
              <h1 style="color:#FFFFFF; font-size:26px; font-weight:600; margin:0; letter-spacing:-0.5px; font-family:Arial, sans-serif;">
                Exsis Newsletter
              </h1>
              <p style="color:#C8E6FF; font-size:14px; margin:4px 0 0; font-weight:400; font-family:Arial, sans-serif;">
                Edición: {fecha_edicion}
              </p>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>

  <!-- BODY CONTENT (Con inyección de contenedores con scroll) -->
  <table width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color:#F3F2F1;">
    <tr>
      <td align="center" style="padding: 30px 0;">
        <table class="responsive-table" width="700" cellpadding="0" cellspacing="0" border="0">
          
          {"".join(bloques_limpios)}
          
        </table>
      </td>
    </tr>
  </table>

  <!-- FOOTER OSCURO -->
  <table width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color:#323130;">
    <tr>
      <td align="center" style="padding:30px 20px;">
        <p style="color:#A19F9D; font-size:12px; margin:0; line-height:1.6; font-family:Arial, sans-serif;">
          © {datetime.now().year} Exsis. Todos los derechos reservados.<br>
          Recibes este correo en base a nuestras actualizaciones de tecnología corporativa.
        </p>
      </td>
    </tr>
  </table>

</body>
</html>"""

    Path(ARCHIVO_SALIDA_HTML).write_text(html_final, encoding="utf-8")
    return ARCHIVO_SALIDA_HTML


# ══════════════════════════════════════════════════════════════════════════════
# PUNTO DE ENTRADA
# ══════════════════════════════════════════════════════════════════════════════

def main():
    if not os.getenv("GEMINI_API_KEY"):
        raise EnvironmentError("No se encontró GEMINI_API_KEY en tu .env o entorno.")

    print("\n" + "=" * 60)
    log.info("▶ Iniciando generador de Exsis Newsletter (Layout: 25 items + Scroll)...")
    
    try:
        html_bbc_tech = procesar_bbc("bbc_tecnologia", "📰 Noticias BBC: Tecnología")
        html_azure = procesar_azure()
        html_tdsynnex = procesar_tdsynnex()
        html_microsoft = procesar_microsoft()
        
        ruta_archivo = ensamblar_newsletter([
            html_azure,          
            html_tdsynnex, 
            html_microsoft,
            html_bbc_tech
        ])
        
        log.info("✔ Ejecución completada con éxito.")
        print("=" * 60)
        print(f"🎉 ¡ÉXITO! El boletín ha sido creado en:")
        print(f"📁 {ruta_archivo}")
        print("=" * 60 + "\n")
        
    except Exception as e:
        log.error(f"❌ Ocurrió un error inesperado durante la generación: {e}", exc_info=True)

if __name__ == "__main__":
    main()