import os
import traceback
from dotenv import load_dotenv
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain.agents import create_agent
from langchain_core.messages import HumanMessage
from GmailClient import GmailClient, obtener_tools_cursoinador


class AgenteCursoinador:
    """
    Especialista: SOLO sabe redactar y despachar convocatorias de cursos por correo.
    No busca ni analiza correos existentes; eso es trabajo del Analizador.
    Es invocado por el Agente Coordinador, nunca directamente por el usuario final.
    """

    PROMPT_COORDINADOR = """
Eres un asistente técnico especializado en redactar y DESPACHAR correos de formación
complementaria. Recibes instrucciones de un Agente Coordinador, no directamente del
usuario, así que responde de forma clara para que ese coordinador pueda reenviar tu
respuesta o pedir los datos que falten.

FASE 1: VALIDACIÓN ESTRICTA DE OBLIGATORIOS
Extrae de la instrucción recibida los siguientes campos obligatorios:
- nombre_curso
- fecha_evento
- hora_evento
- modalidad
- ponente
- enlace_registro
Además de la 'lista_emails_destinatarios'.

REGLA CRÍTICA: Si falta cualquiera de estos 6 campos obligatorios o la lista de destinatarios, 
NO uses herramientas. Responde indicando exactamente cuáles campos obligatorios hacen falta 
para poder proceder con el despacho. Los campos adicionales (duracion, idioma, etc.) son opcionales.

FASE 2: EJECUCIÓN SECUENCIAL ESTRICTA (¡MUY IMPORTANTE!)
Si tienes todos los datos obligatorios, tu ÚNICA tarea operativa es invocar la herramienta 'enviar_convocatoria_curso'.
- NO redactes un mensaje diciendo que enviaste el correo si no has llamado a la herramienta.
- Si te piden MÚLTIPLES CURSOS a la vez, DEBES esperar la respuesta de la herramienta para el primer 
  curso ANTES de generar la llamada para el siguiente. Genera los llamados uno por uno, de forma 
  completamente síncrona y secuencial. No intentes llamar a la herramienta en paralelo para varios cursos.
- La lista de destinatarios ("lista_emails_destinatarios") DEBE pasarse como una lista válida
  de Python (ej: ["correo1@ejemplo.com", "correo2@ejemplo.com"]).

FORMATO OBLIGATORIO DEL CUERPO DEL CORREO (Pásalo así al argumento 'cuerpo' de la herramienta):

Hola,

Te invitamos a participar en [nombre_curso], una sesión diseñada para [publico_objetivo si se proporcionó].

[descripcion_curso si se proporcionó]

Detalles del evento:
- Fecha: [fecha_evento]
- Hora: [hora_evento]
- Modalidad: [modalidad]
- Ponente: [ponente]
- Duración: [duracion si se proporcionó]
- Idioma: [idioma si se proporcionó]
- Organiza: [entidad_organiza si se proporcionó]
- Prerrequisitos: [prerequisitos si se proporcionó]

Registro:
Regístrate aquí: [enlace_registro]

Esperamos contar con tu participación.

Atentamente,
Equipo Organizador

FASE 3: CONFIRMACIÓN
Solo cuando la herramienta haya retornado el éxito de los envíos para TODOS los cursos
solicitados, responde con un resumen breve indicando cuáles cursos fueron enviados y a quién.
"""

    def __init__(self, gmail_client: GmailClient = None, model_name: str = "gemini-3.1-flash-lite", temperature: float = 0.1):
        self._cargar_entorno()

        # Si no se pasa un cliente, crea uno propio (modo standalone).
        # Si se pasa uno (modo coordinado), reutiliza la misma sesión autenticada.
        self.gmail_client = gmail_client or GmailClient()

        self.tools = obtener_tools_cursoinador(self.gmail_client)

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

            print(f"DEBUG [Cursoinador]: Evaluando solicitud con {len(self.tools)} herramientas disponibles...")

            resultado = self.agent.invoke(inputs)

            mensajes = resultado.get("messages", [])

            if not mensajes:
                return {"output": "Sin respuesta."}

            ultimo = mensajes[-1]
            contenido = self._extraer_texto(getattr(ultimo, "content", str(ultimo)))

            self.historial = mensajes

            return {"output": contenido}

        except Exception as e:
            traceback.print_exc()
            return {"output": f"Error interno en Agente Cursoinador: {str(e)}"}

    # Punto de entrada simple usado por el Agente Coordinador
    def enviar_mensaje(self, mensaje_usuario: str) -> str:
        return self.procesar_solicitud(mensaje_usuario)["output"]
