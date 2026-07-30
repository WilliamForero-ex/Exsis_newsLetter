import json
import os
import re
import unicodedata
from typing import Dict, List, Any
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

# Palabras genéricas que NO deben otorgar puntos
PALABRAS_GENERICAS = [
    "sistema", "sistemas", "gestion", "actualizacion", "proceso", "procesos", 
    "desarrollo", "control", "normativa", "programa", "servicio", "servicios",
    "unidades", "acciones", "generalidades", "manejo", "normas", "system", 
    "systems", "management", "process", "processes", "program", "service", 
    "services", "general", "overview", "introduction", "basics"
]


def normalizar_texto(texto: str) -> str:
    if not texto:
        return ""
    texto_nfkd = unicodedata.normalize('NFKD', texto)
    texto_sin_tildes = "".join([c for c in texto_nfkd if not unicodedata.combining(c)]).lower()
    return re.sub(r'[^a-z0-9\s]', ' ', texto_sin_tildes)


class TagificadorLigero:
    def __init__(self, ruta_config: str = "areas_conocimiento.json"):
        self.ruta_config = ruta_config
        self.mapa_subarea_a_macro = {}
        self.subareas_nombres = []
        self.textos_referencia = []
        
        self.vectorizer = TfidfVectorizer(
            ngram_range=(1, 3),
            stop_words=PALABRAS_GENERICAS,
            sublinear_tf=True
        )
        self.matriz_referencia = None
        self._cargar_y_entrenar()

    def _cargar_y_entrenar(self):
        if not os.path.exists(self.ruta_config):
            raise FileNotFoundError(f"Error: No se encontró '{self.ruta_config}'.")

        with open(self.ruta_config, "r", encoding="utf-8") as f:
            taxonomia = json.load(f)

        for macro_area, subareas in taxonomia.items():
            if isinstance(subareas, dict):
                for subarea, palabras_clave in subareas.items():
                    if isinstance(palabras_clave, list) and palabras_clave:
                        self.mapa_subarea_a_macro[subarea] = macro_area
                        self.subareas_nombres.append(subarea)
                        texto_ref = f"{subarea} " + " ".join(palabras_clave)
                        self.textos_referencia.append(normalizar_texto(texto_ref))

        if self.textos_referencia:
            self.matriz_referencia = self.vectorizer.fit_transform(self.textos_referencia)

    def tagificar_evento(self, evento: Dict[str, Any], umbral_similitud: float = 0.08, max_tags: int = 2) -> Dict[str, List[str]]:
        titulo = normalizar_texto(str(evento.get("Nombre de evento") or evento.get("Nombre") or ""))
        descripcion = normalizar_texto(str(evento.get("Descripcion") or evento.get("descripcion") or ""))

        # Filtro directo por patrones de títulos claros para evitar falsos positivos
        texto_completo = f"{titulo} {descripcion}"
        
        # 1. Caso explícito de Agricultura / Agroecología
        if any(w in texto_completo for w in ["agro", "finca", "cultiv", "rural", "campesin", "semilla", "hortaliza", "agri"]):
            return {
                "Areas_Conocimiento": ["Ciencias Agropecuarias y Naturales"],
                "Subareas_Conocimiento": ["Agronomía y Ecología Rural"]
            }

        # 2. Evaluación por Vectorizador TF-IDF
        texto_evento = f"{titulo} {titulo} {titulo} {descripcion}".strip()
        if not texto_evento or self.matriz_referencia is None:
            return self._aplicar_fallback(titulo, descripcion, max_tags)

        matriz_evento = self.vectorizer.transform([texto_evento])
        similitudes = cosine_similarity(matriz_evento, self.matriz_referencia)[0]
        indices_ordenados = similitudes.argsort()[::-1]
        
        subareas_encontradas = []
        macro_areas_encontradas = set()

        if len(indices_ordenados) > 0:
            top_score = similitudes[indices_ordenados[0]]
            if top_score >= umbral_similitud:
                for idx in indices_ordenados:
                    score = similitudes[idx]
                    if score >= umbral_similitud and score >= (top_score * 0.45):
                        subarea = self.subareas_nombres[idx]
                        if len(subareas_encontradas) < max_tags:
                            subareas_encontradas.append(subarea)
                            macro_areas_encontradas.add(self.mapa_subarea_a_macro[subarea])

        if not subareas_encontradas:
            return self._aplicar_fallback(titulo, descripcion, max_tags)

        return {
            "Areas_Conocimiento": sorted(list(macro_areas_encontradas))[:max_tags],
            "Subareas_Conocimiento": subareas_encontradas[:max_tags]
        }

    def _aplicar_fallback(self, titulo: str, descripcion: str, max_tags: int) -> Dict[str, List[str]]:
        texto = f"{titulo} {descripcion}"
        macros, subs = set(), set()

        if any(w in texto for w in ["softwar", "web", "dato", "digital", "tic", "ai", "azure", "cloud", "database", "data"]):
            macros.add("Tecnología e Informática")
            subs.add("Bases de Datos y Analítica" if "data" in texto or "dato" in texto else "Tecnología e Informática General")
        elif any(w in texto for w in ["salud", "medica", "paciente", "clinica", "sanitar", "health"]):
            macros.add("Salud y Psicología")
            subs.add("Salud Pública y Gestión")
        elif any(w in texto for w in ["gestion", "calidad", "admin", "financ", "venta", "comerc", "business"]):
            macros.add("Finanzas y Administración")
            subs.add("Gestión y Mejora Continua")
        else:
            macros.add("Talento Humano y Ciencias Sociales")
            subs.add("Desarrollo Humano y Social")

        return {
            "Areas_Conocimiento": sorted(list(macros))[:max_tags],
            "Subareas_Conocimiento": sorted(list(subs))[:max_tags]
        }


def tagificar_archivo_json(nombre_archivo: str, ruta_config_tags: str = "areas_conocimiento.json", max_tags: int = 2):
    print("==================================================")
    print("  EJECUTANDO TAGIFICACIÓN Y SOBREESCRITURA TOTAL")
    print("==================================================")

    tagificador = TagificadorLigero(ruta_config=ruta_config_tags)

    # 1. Lectura completa y cierre del archivo
    with open(nombre_archivo, "r", encoding="utf-8") as f:
        data = json.load(f)

    data_tagificada = []

    # 2. Procesamiento de cada elemento de forma completamente limpia
    for evento in data:
        # Copia aislada del diccionario
        item = dict(evento)

        # Eliminación de etiquetas antiguas
        if "Areas_Conocimiento" in item:
            del item["Areas_Conocimiento"]
        if "Subareas_Conocimiento" in item:
            del item["Subareas_Conocimiento"]

        # Generación de nuevas etiquetas independientes
        tags = tagificador.tagificar_evento(item, max_tags=max_tags)
        
        # Asignación explícita
        item["Areas_Conocimiento"] = list(tags["Areas_Conocimiento"])
        item["Subareas_Conocimiento"] = list(tags["Subareas_Conocimiento"])

        data_tagificada.append(item)

    # 3. Sobreescritura atómica limpia
    with open(nombre_archivo, "w", encoding="utf-8") as f:
        json.dump(data_tagificada, f, indent=4, ensure_ascii=False)

    print(f"-> ¡ÉXITO! Archivo '{nombre_archivo}' reescrito completamente con {len(data_tagificada)} elementos.")
    print("==================================================\n")


if __name__ == "__main__":
    archivo_dataset = "dataset_sena_betowa_enriquecido.json"
    archivo_config = "areas_conocimiento.json"

    tagificar_archivo_json(
        nombre_archivo=archivo_dataset,
        ruta_config_tags=archivo_config,
        max_tags=2
    )