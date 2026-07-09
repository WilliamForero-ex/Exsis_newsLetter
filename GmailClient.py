import os.path
import base64
import re
import time
from typing import List
from bs4 import BeautifulSoup

# Google API Imports
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from email.message import EmailMessage
from langchain_core.tools import tool


class GmailClient:
    """Clase independiente para manejar la autenticación y operaciones de la API de Gmail."""

    def __init__(self, credentials_file="credentials.json", token_file="token.json", scopes=None):
        if scopes is None:
            self.scopes = ["https://www.googleapis.com/auth/gmail.modify"]
        else:
            self.scopes = scopes

        self.credentials_file = credentials_file
        self.token_file = token_file
        self.service = self._authenticate()
        
        # Memoria caché para evitar envíos duplicados durante la ejecución del programa
        self._correos_enviados_sesion = set()

    def _authenticate(self):
        """Maneja el flujo de autenticación OAuth 2.0."""
        creds = None
        if os.path.exists(self.token_file):
            creds = Credentials.from_authorized_user_file(self.token_file, self.scopes)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(
                    self.credentials_file, self.scopes
                )
                creds = flow.run_local_server(port=0)

            with open(self.token_file, "w") as token:
                token.write(creds.to_json())

        try:
            return build("gmail", "v1", credentials=creds)
        except HttpError as error:
            print(f"Ocurrio un error al construir el servicio: {error}")
            return None

    # ==========================================
    # MÉTODOS PRIVADOS DE EXTRACCIÓN Y LIMPIEZA
    # ==========================================

    def _obtener_header(self, headers, nombre_header):
        """Busca un encabezado específico en la lista de la API."""
        for header in headers:
            if header['name'].lower() == nombre_header.lower():
                return header['value']
        return "(No especificado)"

    def _limpiar_html(self, html_content):
        """Usa BeautifulSoup para eliminar código basura y extraer solo texto limpio."""
        try:
            soup = BeautifulSoup(html_content, "html.parser")

            for elemento_oculto in soup(["script", "style", "head", "title", "meta"]):
                elemento_oculto.extract()

            texto = soup.get_text(separator="\n")
            lineas = [linea.strip() for linea in texto.splitlines()]
            texto_limpio = "\n".join([linea for linea in lineas if linea])

            return texto_limpio

        except Exception as e:
            print(f"Aviso: Fallo BeautifulSoup, aplicando fallback por Regex. Error: {e}")
            texto = re.sub(r'<[^>]+>', '', html_content)
            return texto.strip()

    def _procesar_cuerpo_y_adjuntos(self, payload):
        """Recorre la estructura JSON para extraer el texto y los adjuntos."""
        cuerpo_plain = ""
        cuerpo_html = ""
        adjuntos = []

        def recorrer_partes(partes):
            nonlocal cuerpo_plain, cuerpo_html
            for parte in partes:
                filename = parte.get('filename')
                mime_type = parte.get('mimeType')
                body = parte.get('body', {})

                if filename:
                    adjuntos.append(filename)

                if mime_type == 'text/plain' and not filename:
                    data = body.get('data')
                    if data:
                        data += '=' * (4 - len(data) % 4)
                        cuerpo_plain = base64.urlsafe_b64decode(data).decode('utf-8', errors='ignore')
                elif mime_type == 'text/html' and not filename:
                    data = body.get('data')
                    if data:
                        data += '=' * (4 - len(data) % 4)
                        cuerpo_html = base64.urlsafe_b64decode(data).decode('utf-8', errors='ignore')

                if 'parts' in parte:
                    recorrer_partes(parte['parts'])

        if 'parts' in payload:
            recorrer_partes(payload['parts'])
        else:
            mime_type = payload.get('mimeType')
            data = payload.get('body', {}).get('data')
            if data:
                data += '=' * (4 - len(data) % 4)
                texto = base64.urlsafe_b64decode(data).decode('utf-8', errors='ignore')
                if mime_type == 'text/plain':
                    cuerpo_plain = texto
                elif mime_type == 'text/html':
                    cuerpo_html = texto

        if cuerpo_plain:
            return cuerpo_plain, adjuntos
        if cuerpo_html:
            return self._limpiar_html(cuerpo_html), adjuntos

        return "(sin contenido)", adjuntos


    # ==========================================
    # MÉTODOS PÚBLICOS DE OPERACIÓN
    # ==========================================

    def enviar_correo(self, destinatarios: List[str], asunto: str, cuerpo: str) -> List[str]:
        """
        Envía correos usando la API de Gmail con reintentos y delay entre envíos.
        Incluye validación estricta, normalización y memoria de sesión para evitar duplicados.
        """
        if not self.service:
            print("CRÍTICO: El servicio de Gmail no está inicializado.")
            return []

        # 1. VALIDACIÓN Y EXTRACCIÓN
        if not isinstance(destinatarios, list):
            print(f"DEBUG: Advertencia - Se recibió {type(destinatarios)} en lugar de una lista. Corrigiendo...")
            if isinstance(destinatarios, str):
                destinatarios = [email.strip() for email in destinatarios.split(',')]
            else:
                destinatarios = list(destinatarios)
        
        # 2. NORMALIZACIÓN ESTRICTA (minúsculas y sin espacios)
        destinatarios_normalizados = []
        for email in destinatarios:
            if isinstance(email, str):
                email_limpio = email.strip().lower()
                if email_limpio:  # Evita agregar cadenas vacías
                    destinatarios_normalizados.append(email_limpio)
                    
        # 3. ELIMINAR DUPLICADOS EN LA LISTA ACTUAL (manteniendo orden)
        destinatarios_unicos = list(dict.fromkeys(destinatarios_normalizados))
        enviados: List[str] = []

        print(f"DEBUG: Intentando enviar correo con asunto '{asunto}' a: {destinatarios_unicos}")

        for destinatario in destinatarios_unicos:
            # 4. VERIFICACIÓN DE MEMORIA DE SESIÓN (Evita doble envío)
            registro_envio = (destinatario, asunto)
            if registro_envio in self._correos_enviados_sesion:
                print(f"Omitiendo: {destinatario} - Ya se le envió una convocatoria con este asunto previamente.")
                continue

            exito = False
            intentos = 0

            while not exito and intentos < 3:
                try:
                    msg = EmailMessage()
                    msg["Subject"] = asunto
                    msg["From"] = "me"
                    msg["To"] = destinatario
                    msg.set_content(cuerpo)

                    raw_message = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
                    body = {"raw": raw_message}

                    self.service.users().messages().send(
                        userId="me", body=body
                    ).execute()

                    print(f"EXITO: Correo enviado correctamente a: {destinatario}")
                    enviados.append(destinatario)
                    
                    # Registrar en la memoria de la sesión
                    self._correos_enviados_sesion.add(registro_envio)
                    
                    exito = True

                except HttpError as error:
                    print(f"ERROR API Gmail al enviar a {destinatario}: {error}")
                    break  # Error de API crítico, no reintenta
                except Exception as e:
                    intentos += 1
                    print(f"Intento {intentos}/3 fallido para {destinatario}: {e}")
                    if intentos < 3:
                        time.sleep(3)

            # Pausa entre destinatarios para no saturar la API
            time.sleep(1)

        return enviados
    
    def obtener_correos(self, query: str, cantidad: int = None) -> dict:
        """
        Recupera correos basándose en cualquier query de Gmail (ej. 'in:inbox' o 'from:x@x.com').
        """
        if not self.service:
            print("CRÍTICO: El servicio de Gmail no está inicializado.")
            return {"total_existentes": 0, "correos_recuperados": []}

        print(f"DEBUG: Ejecutando búsqueda en Gmail con query: '{query}'...")
        total_correos_existentes = 0
        page_token_conteo = None
        
        # FASE 1: Conteo
        while True:
            try:
                res_conteo = self.service.users().messages().list(
                    userId="me", q=query, pageToken=page_token_conteo, fields="nextPageToken,messages/id"
                ).execute()
                total_correos_existentes += len(res_conteo.get("messages", []))
                page_token_conteo = res_conteo.get("nextPageToken")
                if not page_token_conteo: break
            except Exception as e: break

        # FASE 2: Descarga
        correos_recuperados = []
        page_token_descarga = None

        if total_correos_existentes > 0:
            while True:
                try:
                    request_kwargs = {"userId": "me", "q": query, "pageToken": page_token_descarga}
                    if cantidad and cantidad <= 100:
                        request_kwargs["maxResults"] = cantidad

                    results = self.service.users().messages().list(**request_kwargs).execute()
                    mensajes_ref = results.get("messages", [])

                    for msg_ref in mensajes_ref:
                        if cantidad and len(correos_recuperados) >= cantidad: break

                        msg_data = self.service.users().messages().get(
                            userId="me", id=msg_ref["id"], format="full"
                        ).execute()

                        payload = msg_data.get("payload", {})
                        headers = payload.get("headers", [])

                        asunto = self._obtener_header(headers, "Subject")
                        fecha = self._obtener_header(headers, "Date")
                        cuerpo, adjuntos = self._procesar_cuerpo_y_adjuntos(payload)

                        correos_recuperados.append({
                            "id": msg_data["id"],
                            "asunto": asunto,
                            "fecha": fecha,
                            "cuerpo": cuerpo[:1500] # Limitamos el tamaño para no saturar al LLM
                        })

                    if cantidad and len(correos_recuperados) >= cantidad: break
                    page_token_descarga = results.get("nextPageToken")
                    if not page_token_descarga: break 

                except Exception as e:
                    print(f"ERROR al descargar correos: {e}")
                    break

        return {"total_existentes": total_correos_existentes, "correos_recuperados": correos_recuperados}


# =======================================
# Constructor Herramientas Cursoinador
# =======================================

def obtener_tools_cursoinador(gmail_client: GmailClient):
    
    @tool
    def enviar_convocatoria_curso(destinatarios: List[str], asunto: str, cuerpo: str) -> List[str]:
        """
        Úsala EXCLUSIVAMENTE para enviar convocatorias o correos masivos informativos.
        Llama a esta herramienta UNA VEZ POR CADA CURSO de forma separada.
        Asegúrate de pasar 'destinatarios' siempre como una lista de Python (ej: ["email1@ejemplo.com", "email2@ejemplo.com"]).
        """
        return gmail_client.enviar_correo(
            destinatarios=destinatarios,
            asunto=asunto,
            cuerpo=cuerpo
        )

    return [enviar_convocatoria_curso]


# =======================================
# Constructor Herramientas Analizainador
# =======================================
def obtener_tools_analizainador(gmail_client: GmailClient):
    
    @tool
    def buscar_correos_gmail(consulta: str, cantidad: int = 5) -> str:
        """
        Busca correos en Gmail. 
        Si el usuario pide "últimos correos", usa consulta="in:inbox".
        Si pide de un remitente, usa consulta="from:correo@ejemplo.com".
        Si pide por un tema, usa consulta="subject:palabra".
        """
        resultado = gmail_client.obtener_correos(consulta, cantidad)
        correos = resultado.get("correos_recuperados", [])
        
        if not correos:
            return "No se encontraron correos para esa consulta."
        
        texto_resultado = f"Se encontraron {len(correos)} correos.\n\n"
        for i, correo in enumerate(correos, 1):
            texto_resultado += f"[Correo {i}]\nAsunto: {correo['asunto']}\nCuerpo (fragmento): {correo['cuerpo']}...\n---\n"
            
        return texto_resultado

    return [buscar_correos_gmail]