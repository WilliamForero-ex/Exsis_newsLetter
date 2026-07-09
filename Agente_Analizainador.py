import os
import json
import traceback
from typing import List, Optional
from pydantic import BaseModel, Field
from dotenv import load_dotenv
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.tools import tool
from langchain.agents import create_agent
from langchain_core.messages import HumanMessage


# ============================================================
# 1. ESQUEMAS DE SALIDA (Campos Obligatorios Estrictos)
# ============================================================
class OfertaEducativa(BaseModel):
    # CAMPOS ESTRICTAMENTE OBLIGATORIOS
    nombre_curso: str = Field(description="Nombre oficial del curso, evento o tutoría académica.")
    fecha_evento: str = Field(description="Fecha exacta en la que se realizará el evento (ej. 01/07/2026).")
    hora_evento: str = Field(description="Hora de inicio del evento (ej. 08:00).")
    modalidad: str = Field(description="Modalidad del evento (ej. Virtual, Presencial).")
    ponente: str = Field(description="Nombre del profesor o instructor que imparte el curso (ej. Johan Matiz).")
    enlace_registro: str = Field(description="URL o enlace directo para registro o conexión al evento.")
    
    # CAMPOS OPCIONALES (Si existen en el correo, se extraen; si no, quedan como None)
    publico_objetivo: Optional[str] = Field(None, description="A quién va dirigido el curso (opcional).")
    descripcion_curso: Optional[str] = Field(None, description="Breve resumen del contenido o temario (opcional).")
    duracion: Optional[str] = Field(None, description="Duración de la sesión (opcional).")
    idioma: Optional[str] = Field(None, description="Idioma del evento (opcional).")
    entidad_organiza: Optional[str] = Field(None, description="Institución o empresa que organiza (opcional).")
    prerequisitos: Optional[str] = Field(None, description="Conocimientos previos requeridos (opcional).")

class RespuestaFinal(BaseModel):
    ofertas: List[OfertaEducativa] = Field(description="Lista de ofertas detectadas. Vacía si no hay ninguna.")

# ============================================================
# 2. HERRAMIENTA DE EXTRACCIÓN ESTRUCTURADA
# ============================================================
@tool
def analizar_oferta_estructurada(texto_correo: str) -> str:
    """
    Usa esta herramienta para analizar el contenido de un correo específico y extraer
    estrictamente la información de ofertas académicas en formato JSON.
    """
    llm_estricto = ChatGoogleGenerativeAI(
        model="gemini-3.1-flash-lite",
        temperature=0.1,
        google_api_key=os.environ.get("GEMINI_API_KEY")
    ).with_structured_output(RespuestaFinal)

    prompt = f"Extrae ofertas educativas de este correo. Si es spam o personal, devuelve una lista vacía:\n\n{texto_correo}"
    try:
        res = llm_estricto.invoke(prompt)
        return json.dumps(res.model_dump(exclude_none=True), ensure_ascii=False)
    except Exception:
        return json.dumps({"ofertas": []})


# ============================================================
# 3. CLASE DEL AGENTE ESPECIALISTA EN ANÁLISIS DE CORREOS
# ============================================================
class AgenteAnalizadorChat:
    """
    Especialista: SOLO sabe buscar correos en Gmail y analizarlos para detectar
    ofertas académicas. No envía correos ni conoce al Cursoinador.
    Es invocado por el Agente Coordinador, nunca directamente por el usuario final.
    """

    PROMPT_COORDINADOR = """
Eres un asistente experto en revisar el correo del usuario para detectar cursos,
tutorías u ofertas académicas. Recibes instrucciones de un Agente Coordinador, no
directamente del usuario, así que responde de forma clara y completa para que ese
coordinador pueda reenviar tu respuesta.

PASOS A SEGUIR:
1. Usa 'buscar_correos_gmail' para traer los correos que se te pida revisar.
2. Usa 'analizar_oferta_estructurada' para evaluar el texto de CADA correo recuperado.
3. Devuelve una respuesta estructurada y conversacional, mostrando solo las ofertas
   detectadas basándote en los JSON devueltos por la herramienta de análisis.
4. Si no se detecta ninguna oferta, dilo explícitamente.
"""

    def __init__(self, herramientas_gmail: list, model_name: str = "gemini-3.1-flash-lite", temperature: float = 0.2):
        self._cargar_entorno()

        # Combinamos las herramientas de Gmail con la herramienta estructurada
        self.tools = herramientas_gmail + [analizar_oferta_estructurada]

        self.llm = ChatGoogleGenerativeAI(
            model=model_name,
            temperature=temperature,
            google_api_key=self.gemini_api_key,
        )

        self.agent = self._crear_agente()

        self.historial = []

    def _cargar_entorno(self):
        load_dotenv()
        self.gemini_api_key = os.getenv("GEMINI_API_KEY")
        if not self.gemini_api_key:
            raise ValueError("No se encontró GEMINI_API_KEY")

    def _crear_agente(self):
        return create_agent(
            model=self.llm,
            tools=self.tools,
            system_prompt=self.PROMPT_COORDINADOR
        )

    def _extraer_texto(self, content) -> str:
        """
        Normaliza el contenido del mensaje a texto plano.
        Algunos modelos (como Gemini) devuelven el content como una lista
        de bloques [{'type': 'text', 'text': '...'}], en vez de un string.
        """
        if isinstance(content, str):
            return content

        if isinstance(content, list):
            partes = [
                bloque.get("text", "")
                for bloque in content
                if isinstance(bloque, dict) and bloque.get("type") == "text"
            ]
            return " ".join(p for p in partes if p)

        return str(content)

    def procesar_solicitud(self, mensaje_usuario: str) -> dict:
        try:
            self.historial.append(HumanMessage(content=mensaje_usuario))

            inputs = {"messages": self.historial}

            print(f"DEBUG [Analizador]: Evaluando solicitud con {len(self.tools)} herramientas disponibles...")

            resultado = self.agent.invoke(inputs)

            mensajes = resultado.get("messages", [])

            if not mensajes:
                return {"output": "Sin respuesta."}

            ultimo = mensajes[-1]
            contenido = self._extraer_texto(getattr(ultimo, "content", str(ultimo)))

            # Guardamos el historial completo devuelto por el agente (incluye tool calls)
            self.historial = mensajes

            return {"output": contenido}

        except Exception as e:
            traceback.print_exc()
            return {"output": f"Error interno en Agente Analizador: {str(e)}"}

    # Punto de entrada simple usado por el Agente Coordinador
    def enviar_mensaje(self, mensaje_usuario: str) -> str:
        return self.procesar_solicitud(mensaje_usuario)["output"]
