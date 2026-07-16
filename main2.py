import json
from playwright.sync_api import sync_playwright

def obtener_detalles_evento(page, url):
    """
    Visita la página específica de un evento y extrae detalles adicionales (ej. descripción).
    """
    try:
        # Navegamos a la URL del evento
        page.goto(url, wait_until="domcontentloaded", timeout=15000)
        
        # Damos un momento para que cargue el contenido principal
        page.wait_for_timeout(2000)
        
        # Extraer la descripción. (Meetup suele guardar el texto en divs con la clase 'break-words' o ids específicos)
        descripcion = "Descripción no encontrada"
        
        # Intentamos capturar el bloque de texto principal de los detalles
        try:
            # Estos selectores buscan elementos típicos del detalle en Meetup
            selector_detalles = page.locator("div.break-words, div[data-testid='event-details']").first
            if selector_detalles.is_visible():
                descripcion = selector_detalles.inner_text().strip()
        except Exception:
            pass

        return {
            "descripcion": descripcion
        }
    except Exception as e:
        print(f"  [!] Error al cargar detalles de {url}: {e}")
        return {
            "descripcion": "Error al cargar la página del evento"
        }

def scrape_azure_events():
    url = "https://www.meetup.com/pro/azuretechgroups/"
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(viewport={'width': 1920, 'height': 1080})
        page = context.new_page()
        
        print(f"Navegando a: {url}")
        page.goto(url, wait_until="domcontentloaded")
        
        # 1. Aceptar banner de cookies
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

        while intentos_sin_cambio < 3:
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(2000)
            
            for texto_btn in ["Show more", "Mostrar más", "Ver más"]:
                try:
                    btn = page.locator("button").filter(has_text=texto_btn).first
                    if btn.is_visible():
                        btn.click()
                        page.wait_for_timeout(2000)
                except:
                    pass
            
            new_height = page.evaluate("document.body.scrollHeight")
            if new_height == last_height:
                intentos_sin_cambio += 1
            else:
                intentos_sin_cambio = 0
                last_height = new_height

        print("Extrayendo enlaces de las tarjetas...")
        
        # 3. Extraer información básica
        eventos_links = page.locator("a[href*='/events/']").all()
        resultados_basicos = []
        urls_vistas = set()

        for evento in eventos_links:
            enlace = evento.get_attribute("href")
            if not enlace or "manage" in enlace or "settings" in enlace:
                continue
            if enlace.startswith("/"):
                enlace = f"https://www.meetup.com{enlace}"
            if enlace in urls_vistas:
                continue
            urls_vistas.add(enlace)
            
            texto_tarjeta = evento.inner_text().strip()
            if not texto_tarjeta:
                continue
                
            lineas = [linea.strip() for linea in texto_tarjeta.split('\n') if linea.strip()]
            
            if len(lineas) >= 2:
                resultados_basicos.append({
                    "fecha_y_hora": lineas[0],
                    "titulo": lineas[1],
                    "url_evento": enlace,
                    "grupo_organizador": lineas[2] if len(lineas) > 2 else "No especificado"
                })

        print(f"Se encontraron {len(resultados_basicos)} eventos únicos.")
        
        # ---------------------------------------------------------
        # NUEVO PASO: Extraer detalles profundos de cada evento
        # ---------------------------------------------------------
        print("\nExtrayendo detalles individuales de cada evento (Esto tomará algo de tiempo)...")
        
        for i, evento in enumerate(resultados_basicos):
            print(f"Analizando evento {i+1}/{len(resultados_basicos)}: {evento['titulo'][:30]}...")
            
            # Llamamos a nuestra nueva función
            detalles_extra = obtener_detalles_evento(page, evento["url_evento"])
            
            # Fusionamos los detalles nuevos en nuestro diccionario original
            evento.update(detalles_extra)
            
            # Pausa obligatoria para evitar ser bloqueados por scraping intensivo
            page.wait_for_timeout(1500) 

        print("\n¡Extracción profunda finalizada!")
        browser.close()
        
        return resultados_basicos

def ejecutar_scraper_y_guardar(nombre_archivo="eventos_azure_tech_detallado.json"):
    print("Iniciando el proceso de extracción...")
    datos_eventos = scrape_azure_events()
    
    with open(nombre_archivo, "w", encoding="utf-8") as f:
        json.dump(datos_eventos, f, ensure_ascii=False, indent=4)
        
    print(f"Los datos detallados se han guardado exitosamente en '{nombre_archivo}'")
    return datos_eventos

if __name__ == "__main__":
    ejecutar_scraper_y_guardar()