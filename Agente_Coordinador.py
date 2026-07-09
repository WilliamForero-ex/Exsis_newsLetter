import os
import traceback
from dotenv import load_dotenv
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.tools import tool
from langchain.agents import create_agent
from langchain_core.messages import HumanMessage

from Agente_Analizainador import AgenteAnalizadorChat
from Agente_Cursoinador import AgenteCursoinador


# ============================================================
# HERRAMIENTAS DE DELEGACIÓN
# Cada "herramienta" del coordinador en realidad es una llamada
# completa a un agente especialista (su propio LLM + sus propias
# herramientas de Gmail). El coordinador nunca toca Gmail.
# ============================================================
def construir_tools_coordinador(agente_analizador: AgenteAnalizadorChat, agente_cursoinador: AgenteCursoinador):

    @tool
    def consultar_agente_analizador(instruccion: str) -> str:
        """
        Delega la tarea al Agente Analizador, especialista en BUSCAR y ANALIZAR correos
        ya existentes en la bandeja de Gmail para detectar ofertas académicas (cursos, tutorías).

        Pásale una instrucción clara y autocontenida en lenguaje natural, por ejemplo:
        "Revisa los últimos 5 correos de la bandeja de entrada y dime cuáles son ofertas de cursos."

        Devuelve la respuesta final ya redactada por ese agente (incluyendo, si las hay,
        las ofertas detectadas con sus datos: quién la ofrece, nombre del curso, fecha/hora
        y enlaces de interés).
        """
        return agente_analizador.enviar_mensaje(instruccion)

    @tool
    def consultar_agente_cursoinador(instruccion: str) -> str:
        """
        Delega la tarea al Agente Cursoinador, especialista en REDACTAR y ENVIAR convocatorias
        de cursos por correo a una lista de destinatarios.

        Pásale una instrucción clara y autocontenida en lenguaje natural con TODOS los datos
        necesarios: nombre del curso, fecha, hora, modalidad, ponente, enlace de registro y
        la lista de correos destinatarios.

        Si faltan datos, este agente devolverá una pregunta pidiéndolos; debes trasladarle
        esa pregunta al usuario tal cual, sin inventar la información que falta.
        """
        return agente_cursoinador.enviar_mensaje(instruccion)

    return [consultar_agente_analizador, consultar_agente_cursoinador]


# ============================================================
# AGENTE COORDINADOR
# ============================================================
class AgenteCoordinador:
    """
    Único punto de contacto con el usuario. No tiene acceso directo a Gmail:
    su trabajo es interpretar la solicitud y delegarla en el especialista correcto
    (o en ambos, en orden, si la solicitud lo requiere).
    """

    PROMPT_COORDINADOR = """
Eres el Agente Coordinador. Hablas directamente con el usuario, pero TÚ NO tienes acceso
a Gmail ni a ninguna herramienta de bajo nivel. Tu única función es interpretar lo que pide
el usuario y DELEGAR el trabajo real en tus dos agentes especialistas:

1) consultar_agente_analizador
   Úsala cuando el usuario quiera REVISAR, BUSCAR o ANALIZAR correos ya existentes en su
   bandeja (ej: "¿hay ofertas de cursos en mis últimos correos?", "revisa los correos de
   tutorias@matematicasconjohan.com").

2) consultar_agente_cursoinador
   Úsala cuando el usuario quiera REDACTAR y ENVIAR una convocatoria de un curso a una lista
   de correos (ej: "envía una convocatoria del curso X el 5 de julio a estos correos...").

REGLAS:
- Nunca le digas al usuario que hiciste algo (buscar, enviar) si no llamaste a la herramienta
  de delegación correspondiente.
- Si la solicitud combina ambas cosas (ej: "revisa mis correos y si encuentras un curso
  interesante, envíaselo a mi equipo"), llama PRIMERO a consultar_agente_analizador, toma los
  datos de la oferta encontrada y LUEGO llama a consultar_agente_cursoinador pasándole esos
  datos completos junto con los destinatarios que pidió el usuario.
- Si un agente delegado responde pidiendo datos faltantes, traslada esa pregunta al usuario
  tal cual, sin inventar la información que falta.
- Una vez tengas la respuesta final de el/los agente(s) delegado(s), preséntala al usuario de
  forma clara y conversacional, sin duplicarla ni alterar la información que contiene.
"""

    def __init__(
        self,
        agente_analizador: AgenteAnalizadorChat,
        agente_cursoinador: AgenteCursoinador,
        model_name: str = "gemini-3.1-flash-lite",
        temperature: float = 0.2,
    ):
        self._cargar_entorno()

        self.tools = construir_tools_coordinador(agente_analizador, agente_cursoinador)

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
            system_prompt=self.PROMPT_COORDINADOR,
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

            print(f"DEBUG [Coordinador]: decidiendo a qué especialista delegar (herramientas: {len(self.tools)})...")

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
            return {"output": f"Error interno en Agente Coordinador: {str(e)}"}

    def enviar_mensaje(self, mensaje_usuario: str) -> str:
        return self.procesar_solicitud(mensaje_usuario)["output"]
