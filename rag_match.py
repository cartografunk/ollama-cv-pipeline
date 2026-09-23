"""
rag_match.py — Extiende match.py con retrieval semántico + clasificación
"grounded" vía Ollama, para requisitos que el match literal (match.py) no
detecta por simple diferencia de redacción ("PostgreSQL/PostGIS" en tu CV
vs "geospatial database management" en la JD).

REUTILIZA match.py en vez de duplicarlo: extraer_keywords_jd,
leer_jd_y_empresa, _contiene_termino, _variantes y las constantes de
Ollama vienen todas de ahí.

Flujo por cada requisito de la JD:
  1. Match literal (idéntico a match.py) -> si hay match exacto, VERIFIED
     inmediato, sin gastar una llamada al LLM.
  2. Si no hay match literal: retrieval semántico contra kb_index.json
     (top-k fragmentos de evidencia real, tal cual salen del CV).
  3. Si ni el mejor fragmento pasa un piso mínimo de similitud (--sim-floor)
     -> GAP directo. No tiene sentido preguntarle al LLM con evidencia
     irrelevante: eso es justo lo que abre la puerta a que "reconozca"
     algo que no está.
  4. Si hay evidencia candidata: se le pasa SOLO esa evidencia (nunca el CV
     completo, nunca el resto de la JD) a Ollama, pidiéndole clasificar
     VERIFIED / PARTIAL / GAP citando qué chunk(s) respaldan la conclusión.
     El modelo no puede inventar experiencia porque su única fuente son
     fragmentos verbatim del CV — si falla el parseo o Ollama no responde,
     el resultado por default es GAP, nunca VERIFIED.

Requiere: cv_perfil.json (extract_cv.py) y kb_index.json (build_kb.py).

Uso:
    python rag_match.py ~/jds/Siemens.txt
    python rag_match.py ~/jds/Siemens.txt --top-k 4 --sim-floor 0.15
"""

import argparse
import json
import os
import sys

import requests

from embeddings import cosine_similarity, embed_text
from match import (
    CARPETA_MATCHES,
    MODEL,
    OLLAMA_URL,
    PESO_CATEGORIA,
    PESO_DEFAULT,
    TIMEOUT,
    _contiene_termino,
    _extraer_json,
    _variantes,
    extraer_keywords_jd,
    leer_jd_y_empresa,
)

PESO_CLASIFICACION = {"VERIFIED": 1.0, "PARTIAL": 0.5, "GAP": 0.0}

PROMPT_CLASIFICACION = """Eres un evaluador ESTRICTO de evidencia para un CV. \
Tu única fuente de verdad son los fragmentos de evidencia listados abajo, \
extraídos TEXTUALMENTE del CV real del candidato. No uses conocimiento \
externo ni asumas nada que no esté escrito en esos fragmentos.

REQUISITO DE LA VACANTE:
{requisito}

EVIDENCIA DISPONIBLE (texto real del CV, con su id):
{evidencia}

Clasifica la relación entre el requisito y la evidencia en una de tres \
categorías:
- "VERIFIED": la evidencia respalda el requisito de forma directa y \
suficiente (aunque use palabras distintas, el significado es equivalente).
- "PARTIAL": hay evidencia relacionada o parcial, pero NO es suficiente \
para afirmar el requisito literalmente (ej. el requisito pide "liderazgo \
de proyectos" y la evidencia solo dice "coordiné entregables técnicos": \
relacionado, no es lo mismo).
- "GAP": la evidencia no respalda el requisito, o no hay evidencia \
suficiente.

REGLAS ESTRICTAS:
- Nunca clasifiques como VERIFIED solo por similitud temática superficial.
- Nunca inventes evidencia que no esté en el texto de arriba.
- Cita los ids de los fragmentos que usaste en "evidence_ids" (lista vacía \
si es GAP).

Devuelve SOLO este JSON, sin texto antes ni después, sin ```:
{{"classification": "VERIFIED|PARTIAL|GAP", "evidence_ids": ["chunk_0001"], "rationale": "una frase breve"}}

JSON:"""


def cargar_kb(path: str) -> dict:
    if not os.path.isfile(path):
        print(f"❌ No existe {path}. Corre primero: python build_kb.py mi_cv.docx")
        sys.exit(1)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def clasificar_con_evidencia(requisito: str, evidencia: list, intentos: int = 2) -> dict:
    texto_evidencia = "\n".join(f"- [{c['id']}] {c['text']}" for c in evidencia)
    prompt = PROMPT_CLASIFICACION.format(requisito=requisito, evidencia=texto_evidencia)

    for intento in range(1, intentos + 1):
        try:
            resp = requests.post(OLLAMA_URL, json={
                "model": MODEL, "prompt": prompt, "stream": False,
                "options": {"temperature": 0.0, "num_ctx": 4096},
            }, timeout=TIMEOUT)
            resp.raise_for_status()
        except requests.exceptions.ConnectionError:
            print("❌ No se pudo conectar con Ollama en http://localhost:11434.")
            sys.exit(1)
        except Exception as e:
            print(f"⚠️  Intento {intento}/{intentos}: error de Ollama ({e}).")
            continue

        crudo = _extraer_json(resp.json()["response"].strip())
        try:
            data = json.loads(crudo)
        except json.JSONDecodeError:
            print(f"⚠️  Intento {intento}/{intentos}: JSON inválido del clasificador.")
            continue

        clasificacion = str(data.get("classification", "")).upper()
        if clasificacion not in PESO_CLASIFICACION:
            print(f"⚠️  Intento {intento}/{intentos}: clasificación desconocida "
                  f"{clasificacion!r}.")
            continue

        ids_validos = {c["id"] for c in evidencia}
        evidence_ids = [i for i in data.get("evidence_ids", []) if i in ids_validos]
        return {
            "classification": clasificacion,
            "evidence_ids": evidence_ids,
            "rationale": str(data.get("rationale", "")).strip(),
        }

    # Falló tras varios intentos: por seguridad, GAP (nunca VERIFIED por default).
    return {"classification": "GAP", "evidence_ids": [],
            "rationale": "clasificador no disponible tras varios intentos, se marcó GAP por seguridad"}


def evaluar_requisito(item: str, texto_cv: str, kb: dict, top_k: int, sim_floor: float) -> dict:
    # 1) Match literal, igual que match.py: barato, determinista, 100%
    #    trazable a texto exacto del CV.
    if any(_contiene_termino(texto_cv, v) for v in _variantes(item)):
        return {"item": item, "classification": "VERIFIED", "evidence_ids": [],
                "rationale": "match literal en el CV", "via": "literal"}

    # 2) Retrieval semántico contra la knowledge base.
    try:
        q_vec = embed_text(item, model=kb["model"])
    except RuntimeError as e:
        return {"item": item, "classification": "GAP", "evidence_ids": [],
                "rationale": str(e), "via": "error_embedding"}

    candidatos = sorted(
        ((cosine_similarity(q_vec, c["embedding"]), c) for c in kb["chunks"]),
        key=lambda x: x[0], reverse=True,
    )[:top_k]

    if not candidatos or candidatos[0][0] < sim_floor:
        return {"item": item, "classification": "GAP", "evidence_ids": [],
                "rationale": "sin evidencia semánticamente cercana",
                "via": "semantic_none",
                "top_similarity": round(candidatos[0][0], 3) if candidatos else 0.0}

    # 3) Clasificación grounded: el LLM solo ve el requisito + esta evidencia.
    evidencia = [c for _, c in candidatos]
    resultado = clasificar_con_evidencia(item, evidencia)
    resultado["item"] = item
    resultado["via"] = "rag"
    resultado["top_similarity"] = round(candidatos[0][0], 3)
    return resultado


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("jd", nargs="?", help="Ruta a la JD (.txt)")
    parser.add_argument("--kb", default="kb_index.json")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--sim-floor", type=float, default=0.15,
                         help="Similitud mínima para molestar al LLM (default: 0.15)")
    args = parser.parse_args()

    # leer_jd_y_empresa() lee sys.argv[1] directamente (viene de match.py),
    # así que le pasamos un argv limpio con solo la ruta de la JD.
    sys.argv = [sys.argv[0], args.jd] if args.jd else [sys.argv[0]]

    if not os.path.isfile("cv_perfil.json"):
        print("❌ No existe cv_perfil.json. Corre primero: python extract_cv.py mi_cv.docx")
        sys.exit(1)
    with open("cv_perfil.json", "r", encoding="utf-8") as f:
        perfil_cv = json.load(f)
    texto_cv = perfil_cv.get("texto_completo", "")

    kb = cargar_kb(args.kb)

    jd_text, empresa = leer_jd_y_empresa()
    if not jd_text.strip():
        print("❌ No pegaste ninguna JD.")
        sys.exit(1)

    print(f"\n🔍 Extrayendo requisitos de la JD de '{empresa}' con Ollama...")
    keywords_jd = extraer_keywords_jd(jd_text)

    resultados = []
    total_items = sum(len(v) for v in keywords_jd.values())
    procesados = 0

    for categoria, items in keywords_jd.items():
        peso = PESO_CATEGORIA.get(categoria, PESO_DEFAULT)
        for item in items:
            procesados += 1
            print(f"   [{procesados}/{total_items}] {item}...")
            r = evaluar_requisito(item, texto_cv, kb, args.top_k, args.sim_floor)
            r["categoria"] = categoria
            r["peso"] = peso
            resultados.append(r)

    peso_total = sum(r["peso"] for r in resultados)
    peso_logrado = sum(r["peso"] * PESO_CLASIFICACION[r["classification"]] for r in resultados)
    score = (peso_logrado / peso_total * 100) if peso_total else 0.0

    must_have = [r for r in resultados if r["categoria"] == "must_have"]
    score_must = (
        sum(PESO_CLASIFICACION[r["classification"]] for r in must_have) / len(must_have) * 100
        if must_have else 100.0
    )

    os.makedirs(CARPETA_MATCHES, exist_ok=True)
    ruta_out = os.path.join(CARPETA_MATCHES, f"{empresa}_rag_match.json")
    with open(ruta_out, "w", encoding="utf-8") as f:
        json.dump({
            "empresa": empresa,
            "score": round(score, 1),
            "score_must_have": round(score_must, 1),
            "requisitos": resultados,
        }, f, ensure_ascii=False, indent=2)

    print(f"\n{'='*60}")
    print(f"📊 MATCH SCORE (RAG, ponderado — VERIFIED=1, PARTIAL=0.5): {score:.1f}%")
    print(f"🎯 MATCH SOLO EN MUST-HAVE: {score_must:.1f}%")
    print(f"{'='*60}\n")

    for r in resultados:
        icono = {"VERIFIED": "✅", "PARTIAL": "🟡", "GAP": "❌"}[r["classification"]]
        marca = (" ⚠️ MUST HAVE"
                  if r["categoria"] == "must_have" and r["classification"] != "VERIFIED"
                  else "")
        print(f"  {icono} [{r['categoria']}] {r['item']}{marca}")
        if r["classification"] != "GAP" and r.get("rationale"):
            print(f"       ↳ {r['rationale']}")

    print(f"\n💾 Resultado detallado (con evidencia citada por chunk) en: {ruta_out}")
    print("ℹ️  Compáralo contra el match literal de match.py — el score de "
          "aquí debería ser igual o mayor, nunca menor, porque el match "
          "literal ya está incluido como paso 1.")


if __name__ == "__main__":
    main()
