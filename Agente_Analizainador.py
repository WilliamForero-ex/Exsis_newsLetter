import json
import os
import asyncio
from dotenv import load_dotenv
from pydantic import BaseModel, Field
from typing import List
from langchain_core.prompts import ChatPromptTemplate
from langchain_google_genai import ChatGoogleGenerativeAI

# ====================================================================
# 1. DEFINICIÓN DE LA ESTRUCTURA DE DATOS (PYDANTIC)
# ====================================================================
class EventoEstructurado(BaseModel):
    fuente: str = Field(description="La fuente del evento (ej. Microsoft, Microsoft Events, etc.)")
    url: str = Field(description="El enlace URL de registro o detalles del evento")
    nombre: str = Field(description="El nombre completo del evento")
    
    # --- Instrucciones enfocadas en leer el contenido, no la estructura ---
    fechas: List[str] = Field(description="Lee todo el texto de principio a fin. Extrae literalmente todas las fechas mencionadas (ej. Tuesday, 28th July 2026). Guárdalas como elementos separados.")
    hora_inicio_final: List[str] = Field(description="Lee todo el texto de principio a fin. Extrae literalmente todos los bloques de horarios, incluyendo sus zonas horarias (ej. GMT+10:00) y ciudades. Guárdalos como elementos separados.")
    
    tipo_de_eventos: str = Field(description="El formato o tipo de evento (ej. Virtual, Presencial, Hackathon, Taller)")
    descripcion: str = Field(description="Un resumen breve y claro de la descripción del evento")
    tags: List[str] = Field(description="Una lista de 3 a 5 tags clave generados a partir del contenido, tema y descripción del curso")
    prerequisitos: str = Field(description="Conocimientos, herramientas o requisitos previos mencionados. Si no hay, indicar 'Ninguno'")
    nivel: str = Field(description="Nivel del curso o evento (ej. Principiante, Intermedio, Avanzado, No especificado)")

# ====================================================================
# 2. FUNCIONES AUXILIARES Y DE PROCESAMIENTO
# ====================================================================
def limpiar_consola():
    """Limpia la consola dependiendo del sistema operativo."""
    os.system('cls' if os.name == 'nt' else 'clear')

async def procesar_lote_agente(lote: list, modelo) -> list:
    """Procesa un bloque de eventos de forma asíncrona y paralela usando Gemini."""
    
    # --- CAMBIO CLAVE: Ordenamos ignorar la estructura JSON ---
    prompt = ChatPromptTemplate.from_messages([
        ("system", "Eres un experto en lectura de comprensión y extracción de datos. "
                   "REGLA PRINCIPAL: NO te guíes por la estructura, las llaves o el formato del JSON. "
                   "Trata el texto que vas a recibir como si fuera un documento de texto plano y continuo. "
                   "Lee TODA la información contenida, de principio a fin, ignorando la jerarquía de los datos.\n\n"
                   "Busca en todo el texto cualquier mención literal de:\n"
                   "1. Fechas exactas.\n"
                   "2. Horarios, rangos de horas, zonas horarias (ej. GMT+12:00) y ciudades (ej. Auckland, Sydney).\n"
                   "Extrae absolutamente TODAS las fechas y TODOS los horarios tal cual están escritos en la información."),
        ("human", "Ignora la estructura JSON y extrae la información basándote ÚNICAMENTE en todo el texto contenido aquí:\n\n{evento_raw}")
    ])
    
    cadena = prompt | modelo.with_structured_output(EventoEstructurado)
    inputs = [{"evento_raw": json.dumps(evento, ensure_ascii=False)} for evento in lote]
    resultados = await cadena.abatch(inputs)
    return resultados

# ====================================================================
# 3. FUNCIÓN PRINCIPAL DE EJECUCIÓN
# ====================================================================
async def mostrar_eventos_con_agente(archivo: str):
    try:
        with open(archivo, 'r', encoding='utf-8') as f:
            eventos = json.load(f)
    except FileNotFoundError:
        print(f"Error: No se encontró el archivo '{archivo}'.")
        return
    except json.JSONDecodeError:
        print("Error: El archivo no tiene un formato JSON válido.")
        return

    total_eventos = len(eventos)
    if total_eventos == 0:
        print("El archivo está vacío. No hay eventos para mostrar.")
        return

    llm = ChatGoogleGenerativeAI(
        model="gemini-3.1-flash-lite", 
        temperature=0.0,
        max_retries=3 
    )
    
    tamano_lote = 10
    
    for i in range(0, total_eventos, tamano_lote):
        if i > 0:
            limpiar_consola()
            
        print(f"--- EVENTOS {i + 1} AL {min(i + tamano_lote, total_eventos)} DE {total_eventos} ---")
        print("El agente Gemini está leyendo todo el contenido como texto plano...\n")
        
        lote_raw = eventos[i:i + tamano_lote]
        lote_procesado = await procesar_lote_agente(lote_raw, llm)
        
        for j, evento_estructurado in enumerate(lote_procesado):
            if not evento_estructurado:
                print(f"[{i + j + 1}] Error al procesar este evento con el modelo.\n")
                continue

            numero_evento = i + j + 1
            
            fechas_formateadas = "\n        - ".join(evento_estructurado.fechas) if evento_estructurado.fechas else "No encontradas en el texto"
            horarios_formateados = "\n        - ".join(evento_estructurado.hora_inicio_final) if evento_estructurado.hora_inicio_final else "No encontrados en el texto"

            print(f"[{numero_evento}] {evento_estructurado.nombre.upper()}")
            print(f"    * Fuente: {evento_estructurado.fuente}")
            print(f"    * URL: {evento_estructurado.url}")
            print(f"    * Fechas:\n        - {fechas_formateadas}")
            print(f"    * Horario:\n        - {horarios_formateados}")
            print(f"    * Tipo de Evento: {evento_estructurado.tipo_de_eventos}")
            print(f"    * Nivel: {evento_estructurado.nivel}")
            print(f"    * Prerrequisitos: {evento_estructurado.prerequisitos}")
            print(f"    * Tags Generados: {', '.join(evento_estructurado.tags)}")
            print(f"    * Descripción: {evento_estructurado.descripcion}\n")
            print("-" * 60 + "\n")
        
        if i + tamano_lote < total_eventos:
            input("Presiona ENTER para continuar...")
        else:
            print("--- FIN DEL ARCHIVO ---")

# ====================================================================
# 4. PUNTO DE ENTRADA (ENTRY POINT)
# ====================================================================
if __name__ == "__main__":
    load_dotenv()
    archivo_json = 'dataset_microsoft_events.json' 
    asyncio.run(mostrar_eventos_con_agente(archivo_json))