import json
from playwright.sync_api import sync_playwright

def scrape_azure_events():
    # URL oficial de la red Pro de Azure Tech Groups en Meetup
    url = "https://www.meetup.com/pro/azuretechgroups/"
    
    with sync_playwright() as p:
        # Iniciamos el navegador en modo visible (headless=False) 
        # Esto ayuda a evitar que Meetup bloquee la conexión por detectar un bot
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(viewport={'width': 1920, 'height': 1080})
        page = context.new_page()
        
        print(f"Navegando a: {url}")
        page.goto(url, wait_until="domcontentloaded")
        
        # 1. Aceptar banner de cookies (Meetup usa OneTrust)
        try:
            btn_cookies = page.locator("#onetrust-accept-btn-handler")
            btn_cookies.click(timeout=5000)
            print("Cookies aceptadas.")
        except:
            print("Aviso de cookies no encontrado o ya aceptado.")

        # 2. Hacer scroll para forzar la carga de los eventos dinámicos
        print("Haciendo scroll para cargar todos los eventos. Por favor espera...")
        last_height = page.evaluate("document.body.scrollHeight")
        intentos_sin_cambio = 0

        # Seguimos bajando hasta que ya no haya contenido nuevo
        while intentos_sin_cambio < 3:
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(2000) # Tiempo para que la API cargue las tarjetas
            
            # Buscar botones de "Mostrar más" si Meetup los requiere en lugar de infinite-scroll
            for texto_btn in ["Show more", "Mostrar más", "Ver más"]:
                try:
                    btn = page.locator("button").filter(has_text=texto_btn).first
                    if btn.is_visible():
                        btn.click()
                        page.wait_for_timeout(2000)
                except:
                    pass
            
            # Verificar si la altura de la página ha cambiado
            new_height = page.evaluate("document.body.scrollHeight")
            if new_height == last_height:
                intentos_sin_cambio += 1
            else:
                intentos_sin_cambio = 0
                last_height = new_height

        print("Extrayendo la información de las tarjetas...")
        
        # 3. Extraer información
        # En Meetup, las clases CSS cambian constantemente. Es más seguro buscar
        # directamente las etiquetas <a> que tengan "/events/" en su enlace.
        eventos_links = page.locator("a[href*='/events/']").all()
        
        resultados = []
        urls_vistas = set()

        for evento in eventos_links:
            enlace = evento.get_attribute("href")
            
            # Descartar enlaces vacíos o botones de administración ("manage")
            if not enlace or "manage" in enlace or "settings" in enlace:
                continue
                
            # Formatear el enlace si es una URL relativa
            if enlace.startswith("/"):
                enlace = f"https://www.meetup.com{enlace}"
                
            # Evitar registrar el mismo evento más de una vez
            if enlace in urls_vistas:
                continue
            urls_vistas.add(enlace)
            
            # Extraer el texto de la tarjeta
            texto_tarjeta = evento.inner_text().strip()
            if not texto_tarjeta:
                continue
                
            # Separamos el texto de la tarjeta por saltos de línea.
            # Usualmente la estructura es: [Fecha y hora, Título del Evento, Nombre del Grupo]
            lineas = [linea.strip() for linea in texto_tarjeta.split('\n') if linea.strip()]
            
            if len(lineas) >= 2:
                resultados.append({
                    "fecha_y_hora": lineas[0],
                    "titulo": lineas[1],
                    "url_evento": enlace,
                    "texto_completo": lineas # Guardamos todas las líneas como respaldo
                })

        print(f"¡Extracción finalizada! Se encontraron {len(resultados)} eventos únicos.")
        browser.close()
        
        return resultados

if __name__ == "__main__":
    datos_eventos = scrape_azure_events()
    
    # 4. Guardar los datos en un archivo JSON
    nombre_archivo = "eventos_azure_tech.json"
    with open(nombre_archivo, "w", encoding="utf-8") as f:
        json.dump(datos_eventos, f, ensure_ascii=False, indent=4)
        
    print(f"Los datos se han guardado exitosamente en '{nombre_archivo}'")