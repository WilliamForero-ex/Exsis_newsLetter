import json
import os
import time
from typing import Dict, List, Any
from dotenv import load_dotenv
from google import genai

# ---------------------------------------------------------
# 1. CARGAR VARIABLES DE ENTORNO Y CONFIGURAR GEMINI
# ---------------------------------------------------------
load_dotenv()
api_key = os.getenv("GEMINI_API_KEY")

if not api_key:
    raise ValueError("No se encontró la variable 'GEMINI_API_KEY' en el archivo .env")

client = genai.Client(api_key=api_key)
MODELO = "gemini-3.1-flash-lite"

# Palabras ambiguas/genéricas que no deben ir como keywords
PALABRAS_PROHIBIDAS = {
    # Español
    "normas", "manejo", "sistema", "sistemas", "gestion", "proceso", 
    "procesos", "desarrollo", "control", "normativa", "programa", 
    "servicio", "servicios", "unidades", "acciones", "generalidades",
    # Inglés
    "system", "systems", "management", "process", "processes", 
    "development", "control", "program", "service", "services", 
    "general", "overview", "introduction", "basics", "fundamentals"
}


# ---------------------------------------------------------
# 2. FUSIÓN DINÁMICA DE MACRO ÁREAS Y SUBÁREAS
# ---------------------------------------------------------
def fusionar_taxonomias(master: Dict[str, Dict[str, List[str]]], nueva: Any) -> Dict[str, Dict[str, List[str]]]:
    """
    Combina dinámicamente cualquier Macro Área nueva o existente devuelta por la IA
    sin eliminar ni sobrescribir los datos previos.
    """
    if not isinstance(nueva, dict):
        return master

    # Desempaquetar si Gemini envolvió el resultado en una clave raíz secundaria
    if len(nueva) == 1 and not any(isinstance(v, dict) for v in nueva.values()):
        primer_valor = list(nueva.values())[0]
        if isinstance(primer_valor, dict):
            nueva = primer_valor

    for macro_area, subareas in nueva.items():
        if not isinstance(subareas, dict):
            continue
        
        macro_clean = str(macro_area).strip()

        # Evitar nombres redundantes o repetidos en la Macro Área
        macro_destino = None
        for macro_existente in master.keys():
            if macro_existente.lower() == macro_clean.lower():
                macro_destino = macro_existente
                break
        
        # Si es una nueva área, la agregamos respetando el español
        if not macro_destino:
            macro_destino = macro_clean
            master[macro_destino] = {}

        for subarea, palabras in subareas.items():
            if not isinstance(palabras, list):
                continue
            
            subarea_clean = str(subarea).strip()
            
            # Buscar subárea equivalente para evitar duplicados en la subárea
            subarea_destino = None
            for sub_existente in master[macro_destino].keys():
                if sub_existente.lower() == subarea_clean.lower():
                    subarea_destino = sub_existente
                    break

            if not subarea_destino:
                subarea_destino = subarea_clean
                master[macro_destino][subarea_destino] = []
            
            for palabra in palabras:
                palabra_clean = str(palabra).strip().lower()
                
                # Filtrar términos vacíos, excesivamente largos o ambiguos
                if not palabra_clean or len(palabra_clean) >= 40 or palabra_clean in PALABRAS_PROHIBIDAS:
                    continue
                
                # Agregar la palabra clave si no existe (mantiene inglés y español)
                if palabra_clean not in master[macro_destino][subarea_destino]:
                    master[macro_destino][subarea_destino].append(palabra_clean)
                    
    return master


# ---------------------------------------------------------
# 3. GENERACIÓN AUTÓNOMA DE TAXONOMÍA CON GEMINI
# ---------------------------------------------------------
def generar_taxonomia_dinamica(
    archivo_dataset: str, 
    archivo_salida: str = "areas_conocimiento.json",
    tamano_lote: int = 10,
    pausa_entre_lotes: float = 4.5
) -> Dict[str, Any]:
    
    if not os.path.exists(archivo_dataset):
        print(f"Error: No se encontró el archivo '{archivo_dataset}'.")
        return {}

    # 1. VERIFICAR Y CARGAR EL JSON ANTERIOR PARA NO ELIMINAR/SOBREESCRIBIR CONTENIDO
    master_taxonomia: Dict[str, Dict[str, List[str]]] = {}
    if os.path.exists(archivo_salida):
        try:
            with open(archivo_salida, "r", encoding="utf-8") as f:
                master_taxonomia = json.load(f)
            print(f"Se cargó el archivo existente '{archivo_salida}' con {len(master_taxonomia)} Macro Áreas previas.")
        except Exception as e:
            print(f"Advertencia: No se pudo leer '{archivo_salida}', se creará uno nuevo. Error: {e}")

    with open(archivo_dataset, "r", encoding="utf-8") as f:
        eventos = json.load(f)

    total_elementos = len(eventos)

    print(f"Iniciando generación y complemento de taxonomía para {total_elementos} elementos...\n")

    # PROMPT OPTIMIZADO PARA IDIOMA Y NO REDUNDANCIA
    prompt_sistema = (
        "Eres un arquitecto de taxonomías educativas y profesionales bilingüe (Español / Inglés).\n"
        "Tu objetivo es analizar un conjunto de eventos/capacitaciones y GENERAR O COMPLEMENTAR "
        "las Macro Áreas, Subáreas y Palabras Clave según la temática real del contenido.\n\n"
        "REGLAS OBLIGATORIAS DE DISEÑO:\n"
        "1. NOMBRES DE MACRO ÁREAS Y SUBÁREAS: DEBEN ESTAR SIEMPRE EN ESPAÑOL.\n"
        "2. NO REDUNDANCIA: Evita tautologías o redundancias en los nombres (Ejemplo PROHIBIDO: 'Tecnología en Tecnología', 'Salud de la Salud'). Usa nombres claros (Ej: 'Tecnología e Informática', 'Salud y Ciencias de la Vida', 'Diseño y Confección').\n"
        "3. PALABRAS CLAVE (KEYWORDS): Incluye tanto términos en ESPAÑOL como en INGLÉS (ej: ['desarrollo web', 'web development', 'base de datos', 'sql database', 'machine learning']).\n"
        "4. Extrae palabras clave que sean frases compuestas o términos representativos en minúsculas.\n"
        "5. NUNCA crees categorías genéricas como 'General', 'Otros' o 'Varios'.\n"
        "6. NUNCA agregues palabras sueltas ambiguas como 'sistema', 'proceso', 'manejo', 'gestion'.\n\n"
        "RESPONDE ÚNICAMENTE CON UN OBJETO JSON CON ESTA ESTRUCTURA:\n"
        "{\n"
        '  "Nombre de Macro Área en Español": {\n'
        '    "Nombre de Subárea en Español": ["término en español", "english term"]\n'
        "  }\n"
        "}"
    )

    total_lotes = (total_elementos + tamano_lote - 1) // tamano_lote

    for i in range(0, total_elementos, tamano_lote):
        lote = eventos[i:i + tamano_lote]
        num_lote = (i // tamano_lote) + 1

        print(f"Procesando lote {num_lote} de {total_lotes} (Elementos {i+1} a {min(i+tamano_lote, total_elementos)})...")

        textos_lote = []
        for idx, item in enumerate(lote, start=1):
            titulo = item.get("Nombre de evento") or item.get("Nombre") or ""
            descripcion = item.get("Descripcion") or item.get("descripcion") or ""
            textos_lote.append(f"Elemento {idx}: Título: '{titulo}' | Descripción: '{descripcion[:250]}'")

        prompt_usuario = f"{prompt_sistema}\n\nAnaliza los siguientes elementos y devuelve la taxonomía en JSON:\n\n" + "\n".join(textos_lote)

        exito = False
        reintentos = 0
        max_reintentos = 5

        while not exito and reintentos < max_reintentos:
            try:
                response = client.models.generate_content(
                    model=MODELO,
                    contents=prompt_usuario,
                )
                
                if not response.text:
                    raise ValueError("La respuesta de la API devolvió contenido vacío.")

                respuesta_raw = response.text.strip()

                if "```json" in respuesta_raw:
                    respuesta_raw = respuesta_raw.split("```json")[1].split("```")[0].strip()
                elif "```" in respuesta_raw:
                    respuesta_raw = respuesta_raw.split("```")[1].split("```")[0].strip()

                lote_taxonomia = json.loads(respuesta_raw)
                
                # Fusionar enriqueciendo el JSON base
                master_taxonomia = fusionar_taxonomias(master_taxonomia, lote_taxonomia)
                print(f"  Status: Lote {num_lote} procesado exitosamente.")
                exito = True

            except Exception as e:
                error_str = str(e)
                if "429" in error_str or "RESOURCE_EXHAUSTED" in error_str:
                    reintentos += 1
                    tiempo_espera = 45
                    print(f"  Advertencia: Límite de RPM alcanzado (429). Esperando {tiempo_espera}s ({reintentos}/{max_reintentos})...")
                    time.sleep(tiempo_espera)
                else:
                    reintentos += 1
                    print(f"  Advertencia: Error procesando lote {num_lote} ({e}). Reintentando ({reintentos}/{max_reintentos})...")
                    time.sleep(2)

        time.sleep(pausa_entre_lotes)

    # Limpieza final de llaves vacías
    master_limpia = {}
    for macro, subareas in master_taxonomia.items():
        if isinstance(subareas, dict) and len(subareas) > 0:
            subareas_validas = {s: kw for s, kw in subareas.items() if isinstance(kw, list) and len(kw) > 0}
            if len(subareas_validas) > 0:
                master_limpia[macro] = subareas_validas

    # Guardar/Actualizar la taxonomía acumulada
    with open(archivo_salida, "w", encoding="utf-8") as f:
        json.dump(master_limpia, f, indent=4, ensure_ascii=False)

    print(f"\n¡Proceso finalizado! Se preservaron y complementaron {len(master_limpia)} Macro Áreas.")
    print(f"Taxonomía guardada exitosamente en '{archivo_salida}'.")
    return master_limpia


# ---------------------------------------------------------
# EJECUCIÓN
# ---------------------------------------------------------
if __name__ == "__main__":
    archivo_dataset = "dataset_microsoft_events.json"  

    resultado = generar_taxonomia_dinamica(
        archivo_dataset=archivo_dataset,
        archivo_salida="areas_conocimiento.json",
        tamano_lote=10,
        pausa_entre_lotes=4.5
    )

    print("\nEstructura de Macro Áreas consolidadas:")
    print(json.dumps(list(resultado.keys()), indent=4, ensure_ascii=False))