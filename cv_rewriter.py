"""
cv_rewriter.py — Agente Redactor.
Toma el CV original, lee las instrucciones del gap_closure.json y reescribe
los párrafos específicos para cerrar los gaps, SIN perder la estructura.
"""

import json
import os
import sys
from docx import Document
import requests
from docx.shared import RGBColor

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = "mistral:7b" # O qwen2.5-coder:7b
TIMEOUT = 300

def reescribir_parrafo(texto_original, requisito, sugerencia_bullet):
    prompt = f"""Eres un redactor de CV experto. 
Tu tarea es REESCRIBIR el siguiente párrafo del CV para incorporar de forma natural el requisito de la vacante, basándote en la sugerencia.

REQUISITO FALTANTE: {requisito}
SUGERENCIA DE CÓMO CERRARLO: {sugerencia_bullet}

PÁRRAFO ORIGINAL:
{texto_original}

REGLAS ESTRICTAS:
1. NO inventes métricas, años ni tecnologías que no estén en el original o en la sugerencia.
2. Mantén el mismo idioma del original.
3. Solo devuelve el texto reescrito, sin explicaciones.
"""
    try:
        resp = requests.post(OLLAMA_URL, json={
            "model": MODEL, "prompt": prompt, "stream": False,
            "options": {"temperature": 0.3, "num_ctx": 4096}
        }, timeout=TIMEOUT)
        return resp.json()["response"].strip()
    except Exception as e:
        print(f"❌ Error en Ollama: {e}")
        return texto_original

def procesar_cv(cv_path, gap_json_path, output_path):
    # 1. Cargar el análisis de gaps
    with open(gap_json_path, "r", encoding="utf-8") as f:
        gaps_data = json.load(f)
    
    # 2. Filtrar solo lo que se puede cerrar
    gaps_a_cerrar = [
        r for r in gaps_data["requisitos"] 
        if r.get("clasificacion_cierre") in ["rewritable", "stretch"] and r.get("bullet_propuesto")
    ]
    
    if not gaps_a_cerrar:
        print("✅ No hay gaps cerrables en el JSON. Copiando original...")
        os.system(f"cp {cv_path} {output_path}")
        return

    print(f"🔧 Cerrando {len(gaps_a_cerrar)} gaps en el documento...")
    
    # 3. Procesar el documento
    doc = Document(cv_path)
    
    # Como no sabemos qué párrafo exacto toca cada gap, 
    # le pedimos al LLM que busque en todo el CV dónde encaja mejor.
    # (Simplificación: iteramos sobre los párrafos y usamos el LLM para ver si es el lugar)
    
    for gap in gaps_a_cerrar:
        requisito = gap['item']
        sugerencia = gap['bullet_propuesto']
        
        print(f"   Buscando lugar para: {requisito}...")
        
        # En un sistema multiagente real, aquí usaríamos embeddings para buscar el párrafo.
        # Por ahora, se lo pasamos a Ollama para que encuentre el mejor párrafo.
        textos_cv = "\n".join([f"[{i}] {p.text}" for i, p in enumerate(doc.paragraphs) if len(p.text) > 30])
        
        prompt_busqueda = f"""Necesito incorporar este requisito: '{requisito}'.
La sugerencia es: '{sugerencia}'.

Aquí están los párrafos del CV:
{textos_cv}

¿En cuál índice de párrafo encaja mejor esta experiencia para que sea honesto?
Devuelve SOLO el número del índice (ejemplo: 42). Si no encaja en ninguno, devuelve -1.
"""
        try:
            resp = requests.post(OLLAMA_URL, json={
                "model": MODEL, "prompt": prompt_busqueda, "stream": False,
                "options": {"temperature": 0.0}
            }, timeout=TIMEOUT)
            idx_str = resp.json()["response"].strip()
            # Extraer el primer número que aparezca
            import re
            match = re.search(r'-?\d+', idx_str)
            idx = int(match.group()) if match else -1
            
            if idx != -1 and idx < len(doc.paragraphs):
                original = doc.paragraphs[idx].text
                print(f"      -> Modificando párrafo {idx}...")
                nuevo_texto = reescribir_parrafo(original, requisito, sugerencia)
                
                # Limpiar el párrafo y escribir el nuevo
                doc.paragraphs[idx].text = nuevo_texto
                # Resaltar en verde para que el usuario lo vea
                for run in doc.paragraphs[idx].runs:
                    run.font.highlight_color = 4 # 4 es verde en python-docx
            else:
                print(f"      -> No se encontró un lugar claro para '{requisito}'. Saltando.")
                
        except Exception as e:
            print(f"      -> Error buscando párrafo: {e}")

    doc.save(output_path)
    print(f"✅ CV reescrito guardado en: {output_path}")
    print("👉 Abre el archivo en Word. Los cambios están RESALTADOS EN VERDE.")

if __name__ == "__main__":
    if len(sys.argv) != 4:
        print("Uso: python cv_rewriter.py CV_original.docx matches/Empresa_gap_closure.json CV_salida.docx")
        sys.exit(1)
    procesar_cv(sys.argv[1], sys.argv[2], sys.argv[3])
