"""
match.py — Compara una descripción de puesto (JD) contra cv_perfil.json
(generado por extract_cv.py) usando Ollama para extraer las keywords de
la JD, y un match directo contra el texto del CV para decidir qué tienes.

CONVENCIÓN DE NOMBRES: guarda cada JD como ~/jds/Empresa.txt (el nombre del
archivo, sin extensión, se usa como identificador de la vacante). Así los
resultados no se sobreescriben entre vacantes y al final el script te
imprime el nombre sugerido para el CV adaptado a esa vacante.

Uso:
    python match.py                    # pega la JD, Ctrl+D; pide el nombre de la empresa
    python match.py ~/jds/Siemens.txt  # lee la JD y usa "Siemens" como identificador
"""

import sys
import json
import os
import re
import requests

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = "mistral:7b"
TIMEOUT = 600

# Ajusta esto una sola vez: se usa para sugerir el nombre del CV final.
NOMBRE_CANDIDATO = "CesarLopezMoyao"
ANIO_CV = "2026"

CARPETA_MATCHES = os.path.expanduser("~/matches")

# must_have pesa más que el resto al calcular el score global.
PESO_CATEGORIA = {"must_have": 2}
PESO_DEFAULT = 1


def sanitizar_empresa(nombre: str) -> str:
    """Convierte un nombre libre (archivo o texto tecleado) en un
    identificador seguro para nombres de archivo: sin espacios ni símbolos
    raros, conservando acentos y ñ."""
    nombre = nombre.strip().replace(" ", "_")
    return re.sub(r'[^\w-]+', '', nombre, flags=re.UNICODE) or "empresa"


def leer_jd_y_empresa() -> tuple:
    """Devuelve (texto_jd, empresa). Si se pasó un archivo, el nombre de la
    empresa sale del nombre del archivo (~/jds/Siemens.txt -> "Siemens").
    Si se pegó la JD por stdin, se pregunta el nombre de la empresa."""
    if len(sys.argv) >= 2 and os.path.isfile(sys.argv[1]):
        ruta = sys.argv[1]
        with open(ruta, "r", encoding="utf-8") as f:
            texto = f.read()
        empresa = sanitizar_empresa(os.path.splitext(os.path.basename(ruta))[0])
        return texto, empresa

    print("📋 Pega la descripción del puesto (Ctrl+D cuando termines):\n")
    texto = sys.stdin.read()
    nombre = input("\n🏢 Nombre de la empresa/puesto (para nombrar los archivos): ")
    return texto, sanitizar_empresa(nombre)


def _extraer_json(texto: str) -> str:
    """Se queda con lo que hay entre la primera '{' y la última '}',
    para tolerar que el modelo meta texto o ```json``` alrededor."""
    inicio, fin = texto.find("{"), texto.rfind("}")
    if inicio == -1 or fin == -1 or fin < inicio:
        return texto
    return texto[inicio:fin + 1]


def extraer_keywords_jd(jd_text: str, intentos: int = 2) -> dict:
    prompt = f"""Eres un reclutador técnico senior. Analiza esta descripción de puesto y extrae las keywords más importantes.

Devuelve SOLO un JSON válido con esta estructura exacta:
{{
  "must_have": ["skill1", "skill2"],
  "nice_to_have": ["skill3"],
  "soft_skills": ["skill4"],
  "herramientas": ["tool1", "tool2"]
}}

REGLAS ESTRICTAS:
- No inventes keywords que no estén en la JD.
- Preserva el nombre exacto de las herramientas (React, no "framework de React").
- Si una keyword aparece múltiples veces, inclúyela una sola vez.
- No incluyas frases largas, solo términos o nombres cortos.
- Devuelve SOLO el JSON, sin explicaciones antes ni después, sin ```.

DESCRIPCIÓN DEL PUESTO:
{jd_text}

JSON:"""

    for intento in range(1, intentos + 1):
        try:
            response = requests.post(OLLAMA_URL, json={
                "model": MODEL,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0.2, "num_ctx": 8192},
            }, timeout=TIMEOUT)
            response.raise_for_status()
        except requests.exceptions.ConnectionError:
            print("❌ No se pudo conectar con Ollama en http://localhost:11434. "
                  "¿Corriste 'ollama serve' / está prendido el servicio?")
            sys.exit(1)
        except requests.exceptions.Timeout:
            print(f"⚠️  Timeout esperando a Ollama (intento {intento}/{intentos}).")
            continue
        except requests.exceptions.HTTPError as e:
            print(f"❌ Error de Ollama: {e}")
            sys.exit(1)

        crudo = _extraer_json(response.json()["response"].strip())
        try:
            data = json.loads(crudo)
        except json.JSONDecodeError:
            print(f"⚠️  Intento {intento}/{intentos}: Ollama no devolvió JSON válido.")
            if intento == intentos:
                print(f"Respuesta cruda:\n{crudo}")
                sys.exit(1)
            continue

        # Validación mínima: cada categoría debe ser una lista de strings.
        limpio = {}
        for categoria, items in data.items():
            if isinstance(items, list):
                limpio[categoria] = [str(x) for x in items if str(x).strip()]
        if limpio:
            return limpio
        print(f"⚠️  Intento {intento}/{intentos}: JSON sin categorías usables.")

    print("❌ No se pudieron extraer keywords tras varios intentos.")
    sys.exit(1)


def _contiene_termino(texto: str, termino: str) -> bool:
    """Igual que en extract_cv.py: sin \\b, para que términos que terminan
    en símbolo (C++, C#) también se detecten."""
    patron = r'(?<![A-Za-z0-9])' + re.escape(termino) + r'(?![A-Za-z0-9])'
    return re.search(patron, texto, re.IGNORECASE) is not None


def _variantes(item: str) -> list:
    """Variantes razonables de un término (quitar .js/.py finales), para no
    fallar por diferencias tipo 'React.js' (JD) vs 'React' (CV)."""
    variantes = [item]
    sin_sufijo = re.sub(r'\.(js|py)$', '', item, flags=re.IGNORECASE)
    if sin_sufijo != item:
        variantes.append(sin_sufijo)
    return variantes


def calcular_match(perfil_cv: dict, keywords_jd: dict):
    texto_cv = perfil_cv.get("texto_completo", "")

    vistos = set()          # para no contar el mismo término 2 veces entre categorías
    tienes, faltan = [], []
    peso_total = peso_tienes = 0

    for categoria, items in keywords_jd.items():
        peso = PESO_CATEGORIA.get(categoria, PESO_DEFAULT)
        for item in items:
            clave = item.lower()
            if clave in vistos:
                continue
            vistos.add(clave)

            encontrado = any(_contiene_termino(texto_cv, v) for v in _variantes(item))
            peso_total += peso
            if encontrado:
                tienes.append((item, categoria))
                peso_tienes += peso
            else:
                faltan.append((item, categoria))

    score = (peso_tienes / peso_total * 100) if peso_total else 0.0

    must_have = keywords_jd.get("must_have", [])
    faltan_must = [i for i, c in faltan if c == "must_have"]
    score_must = (100.0 if not must_have
                  else (len(must_have) - len(faltan_must)) / len(must_have) * 100)

    return score, score_must, tienes, faltan


if __name__ == "__main__":
    if not os.path.isfile("cv_perfil.json"):
        print("❌ No existe cv_perfil.json. Corre primero: python extract_cv.py mi_cv.docx")
        sys.exit(1)

    with open("cv_perfil.json", "r", encoding="utf-8") as f:
        perfil_cv = json.load(f)

    jd_text, empresa = leer_jd_y_empresa()
    if not jd_text.strip():
        print("❌ No pegaste ninguna JD.")
        sys.exit(1)

    print(f"\n🔍 Analizando JD de '{empresa}' con Ollama...")
    keywords_jd = extraer_keywords_jd(jd_text)

    os.makedirs(CARPETA_MATCHES, exist_ok=True)
    ruta_keywords = os.path.join(CARPETA_MATCHES, f"{empresa}_keywords.json")
    with open(ruta_keywords, "w", encoding="utf-8") as f:
        json.dump(keywords_jd, f, ensure_ascii=False, indent=2)

    score, score_must, tienes, faltan = calcular_match(perfil_cv, keywords_jd)

    print(f"\n{'='*60}")
    print(f"📊 MATCH SCORE (ponderado, must_have pesa doble): {score:.1f}%")
    print(f"🎯 MATCH SOLO EN MUST-HAVE: {score_must:.1f}%")
    print(f"{'='*60}\n")

    print(f"✅ Tienes ({len(tienes)}):")
    for item, cat in tienes:
        print(f"   [{cat}] {item}")

    print(f"\n❌ Te faltan ({len(faltan)}):")
    for item, cat in faltan:
        marca = " ⚠️ MUST HAVE" if cat == "must_have" else ""
        print(f"   [{cat}] {item}{marca}")

    print(f"\n💾 Keywords guardadas en: {ruta_keywords}")

    cv_sugerido = f"CV_{ANIO_CV}_{NOMBRE_CANDIDATO}_{empresa}.docx"
    print(f"\n➡️  Para generar el CV adaptado a esta vacante:")
    print(f"   python3 ~/humanizar.py TU_CV.docx {cv_sugerido} "
          f"--solo-estilos \"Bullet,Body Text\" --min-chars 80")
