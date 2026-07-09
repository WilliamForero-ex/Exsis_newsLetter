import sys

from GmailClient import GmailClient, obtener_tools_analizainador
from Agente_Analizainador import AgenteAnalizadorChat
from Agente_Cursoinador import AgenteCursoinador
from Agente_Coordinador import AgenteCoordinador


def main():
    print("=" * 60)
    print("Chat Unificado - Agente Coordinador de Correos")
    print("=" * 60)

    try:
        print("Conectando con Gmail (sesión compartida para ambos especialistas)...")
        cliente_gmail = GmailClient()

        print("Inicializando Agente Analizador (búsqueda + análisis de ofertas)...")
        herramientas_analizador = obtener_tools_analizainador(cliente_gmail)
        agente_analizador = AgenteAnalizadorChat(herramientas_analizador)

        print("Inicializando Agente Cursoinador (redacción + envío de convocatorias)...")
        agente_cursoinador = AgenteCursoinador(gmail_client=cliente_gmail, temperature=0.3)

        print("Inicializando Agente Coordinador...")
        agente_coordinador = AgenteCoordinador(agente_analizador, agente_cursoinador)

        print("\n¡Listo! Habla con el Coordinador; él decide a qué especialista delegar.")
        print("Ejemplo 1: '¿Cuáles de mis últimos 5 correos son ofertas académicas?'")
        print("Ejemplo 2: 'Envía una convocatoria del curso de Python el 5 de julio a las 6pm, ")
        print("            modalidad virtual, ponente Juan Pérez, enlace https://... a juan@x.com'")
        print("Ejemplo 3: 'Revisa mis últimos correos y si encuentras un curso interesante,")
        print("            envíaselo a maria@x.com'")
        print("Escribe 'salir' para terminar.\n")
    except Exception as e:
        print(f"Error crítico al inicializar los servicios: {e}")
        sys.exit(1)

    print("-" * 60)

    while True:
        try:
            usuario = input("\nTú: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nCerrando. ¡Hasta luego!")
            break

        if not usuario:
            continue

        if usuario.lower() in ["salir", "exit", "quit"]:
            print("Cerrando. ¡Hasta luego!")
            break

        print("Coordinador delegando tarea... (espera unos segundos)\n")

        respuesta = agente_coordinador.enviar_mensaje(usuario)

        print(f"Coordinador:\n{respuesta}")
        print("-" * 60)


if __name__ == "__main__":
    main()
