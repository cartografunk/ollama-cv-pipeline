"""
gap_closure.py — Toma el output de rag_match.py (con sus GAPs y PARTIALs)
y propone cierres honestos: bullets reformulados, planes de estudio, o
clasificación de requisitos como boilerplate irrelevante.

NO inventa experiencia. Distingue entre:
  - rewritable: hay evidencia real, solo falta vocabulario de la JD.
  - stretch: hay base relacionada, se cierra con preparación.
  - template_fill: requisito boilerplate (soft skill vaga, años imposibles).
  - not_rewritable: sin base honesta. No se toca.

Uso:
    python gap_closure.py matches/Siemens_rag_match.json
    python gap_closure.py matches/Siemens_rag_match.json --incluir-partials
    python gap_closure.py matches/Siemens_rag_match.json --kb kb_index.json --top-k 5
"""

import argparse
import json
import os
import sys

import requests

from embeddings import cosine_similarity, embed_text
from afinidad import cargar_afinidad
from match import MODEL, OLLAMA_URL, TIMEOUT, _extraer_json
from rag_match import cargar_kb


PESO_CIERRE = {
    "rewritable": 0.9,
    "stretch": 0.6,
    "template_fill": 0.8,
    "not_rewritable": 0.5,
}

PROMPT_GAP_CLOSURE = """Eres un asesor de carrera experto. Analizas un gap \
entre un requisito de vacante y la evidencia real del CV del candidato.

REQUISITO DE LA VACANTE:
{requisito}

CATEGORÍA: {categoria}

EVIDENCIA REAL DEL CV (fragmentos textuales del candidato, con id):
{evidencia}

CONTEXTO DEL MERCADO LABORAL:
- Muchas JDs usan plantillas con requisitos inflados o absurdos (ej. "5 años \
  de experiencia en X" cuando X existe hace 2 años). No todos los gaps son \
  culpa del candidato.
- Cerrar un gap reescribiendo honestamente es legítimo. Cerrar un gap \
  inventando experiencia es fraude y explota en la entrevista.

Clasifica el gap en UNA de estas cuatro categorías:

1. "rewritable": la evidencia conecta directamente con el requisito. \
   Reescribir un bullet existente con el vocabulario de la JD es honesto \
   y defendible.

2. "stretch": la evidencia tiene relación temática, pero falta un paso \
   (una semana de estudio, un proyecto pequeño). El candidato puede \
   cerrarlo con preparación explícita.

3. "template_fill": el requisito es boilerplate absurdo (soft skill vaga \
   como "strong judgment", años imposibles, sigla genérica). Cerrarlo \
   superficialmente es defendible porque el reclutador probablemente lo \
   copió sin pensar.

4. "not_rewritable": no hay base honesta. Cerrarlo sería inventar \
   experiencia. Ejemplo: la JD pide "Kubernetes" y el CV no menciona \
   contenedores en ningún lado.

REGLAS ESTRICTAS:
- NUNCA inventes tecnologías, métricas, años ni resultados.
- NUNCA afirmes experiencia que no esté en la evidencia de arriba.
- Cita los ids de los chunks usados en "evidence_ids" (lista vacía si es \
  not_rewritable o template_fill sin evidencia).
- Si es rewritable o stretch, propón UN bullet (máximo 30 palabras) en el \
  MISMO IDIOMA que la evidencia, con verbo de acción, sin mentir.
- Si es template_fill, propón cómo mencionar el requisito superficialmente \
  sin comprometerte (ej. "familiar with X", "actively learning X").
- Si es not_rewritable, bullet_propuesto = null.

Devuelve SOLO este JSON, sin texto antes ni después, sin ```:
{{
  "clasificacion_cierre": "rewritable|stretch|template_fill|not_rewritable",
  "evidence_ids": ["chunk_0001"],
  "bullet_propuesto": "texto o null",
  "por_que": "una frase breve",
  "riesgo_entrevista": "bajo|medio|alto",
  "que_estudiar": "tema concreto o null"
}}

JSON:"""


def cargar_rag_match(path: str) -> dict:
    if not os.path.isfile(path):
        print(f"❌ No existe {path}. Corre primero: python rag_match.py mi_jd.txt")
        sys.exit(1)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def recuperar_evidencia(item: str, kb: dict, top_k: int, sim_floor: float = 0.05) -> list:
    """Recupera top-k chunks relevantes para el requisito."""
    try:
        q_vec = embed_text(item, model=kb["model"])
    except RuntimeError as e:
        print(f"⚠️  Error de embedding para '{item}': {e}")
        return []

    candidatos = sorted(
        ((cosine_similarity(q_vec, c["embedding"]), c) for c in kb["chunks"]),
        key=lambda x: x[0], reverse=True,
    )[:top_k]

    return [c for sim, c in candidatos if sim >= sim_floor]


def clasificar_gap(requisito: str, categoria: str, evidencia: list,
                   intentos: int = 2) -> dict:
    if evidencia:
        texto_evidencia = "\n".join(f"- [{c['id']}] {c['text']}" for c in evidencia)
    else:
        texto_evidencia = "(no hay evidencia relacionada en el CV)"

    prompt = PROMPT_GAP_CLOSURE.format(
        requisito=requisito, categoria=categoria, evidencia=texto_evidencia)

    for intento in range(1, intentos + 1):
        try:
            resp = requests.post(OLLAMA_URL, json={
                "model": MODEL, "prompt": prompt, "stream": False,
                "options": {"temperature": 0.2, "num_ctx": 4096},
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

        clasificacion = str(data.get("clasificacion_cierre", "")).lower()
        if clasificacion not in PESO_CIERRE:
            print(f"⚠️  Intento {intento}/{intentos}: clasificación desconocida "
                  f"{clasificacion!r}.")
            continue

        ids_validos = {c["id"] for c in evidencia}
        evidence_ids = [i for i in data.get("evidence_ids", []) if i in ids_validos]

        return {
            "clasificacion_cierre": clasificacion,
            "evidence_ids": evidence_ids,
            "bullet_propuesto": data.get("bullet_propuesto"),
            "por_que": str(data.get("por_que", "")).strip(),
            "riesgo_entrevista": str(data.get("riesgo_entrevista", "")).lower(),
            "que_estudiar": data.get("que_estudiar"),
        }

    # Falló tras varios intentos: por seguridad, not_rewritable.
    return {
        "clasificacion_cierre": "not_rewritable",
        "evidence_ids": [],
        "bullet_propuesto": None,
        "por_que": "clasificador no disponible tras varios intentos",
        "riesgo_entrevista": "alto",
        "que_estudiar": None,
    }


def cierre_por_afinidad(r: dict, afinidad: dict, kb: dict):
    """Requisito que rag_match dio por VERIFIED vía afinidad.json."""
    entrada = (afinidad or {}).get(r.get("concepto") or "")
    if not entrada or not entrada.get("soportado") or not entrada.get("frase"):
        return None
    secciones = {c["id"]: c["section"] for c in kb["chunks"]}
    ids = list(r.get("evidence_ids") or [])
    ids.sort(key=lambda i: secciones.get(i) == "General")
    return {
        "clasificacion_cierre": "rewritable",
        "evidence_ids": ids[:3],
        "bullet_propuesto": entrada["frase"],
        "por_que": (f"Afinidad '{r['concepto']}': tu CV lo respalda con otras palabras; "
                    f"se agrega el término de la vacante donde ya hay evidencia."),
        "riesgo_entrevista": "bajo",
        "que_estudiar": None,
    }


def calcular_techo_honesto(requisitos: list, score_actual: float) -> float:
    peso_total = sum(r["peso"] for r in requisitos)
    if peso_total == 0:
        return score_actual

    peso_ganable = 0
    for r in requisitos:
        if r["classification"] == "GAP":
            cierre = r.get("clasificacion_cierre", "not_rewritable")
            peso_ganable += r["peso"] * PESO_CIERRE.get(cierre, 0.0)
        elif r["classification"] == "PARTIAL":
            cierre = r.get("clasificacion_cierre", "not_rewritable")
            peso_ganable += r["peso"] * 0.5 * PESO_CIERRE.get(cierre, 0.0)

    return score_actual + (peso_ganable / peso_total * 100)


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("rag_match_json",
                         help="Output de rag_match.py (matches/Empresa_rag_match.json)")
    parser.add_argument("--kb", default="kb_index.json")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--afinidad", default="afinidad.json",
                         help="Lista generada por build_afinidad.py (si no existe, se ignora)")
    parser.add_argument("--incluir-partials", action="store_true",
                         help="También procesar PARTIALs, no solo GAPs")
    args = parser.parse_args()

    rag = cargar_rag_match(args.rag_match_json)
    empresa = rag["empresa"]
    score_actual = rag["score"]
    requisitos = rag["requisitos"]

    objetivos = [r for r in requisitos if r["classification"] == "GAP"]
    if args.incluir_partials:
        objetivos += [r for r in requisitos if r["classification"] == "PARTIAL"]

    kb = cargar_kb(args.kb)
    afinidad = cargar_afinidad(args.afinidad)

    por_afinidad = []
    for r in requisitos:
        if r.get("via") == "afinidad" and r["classification"] == "VERIFIED":
            cierre = cierre_por_afinidad(r, afinidad, kb)
            if cierre:
                r.update(cierre)
                por_afinidad.append(r)

    if not objetivos and not por_afinidad:
        print(f"✅ No hay gaps que cerrar para '{empresa}'. "
              f"El CV ya cubre todos los requisitos.")
        print(f"📊 Score actual: {score_actual}%")
        sys.exit(0)

    print(f"\n🔍 Cerrando {len(objetivos)} gap(s) para '{empresa}' con Ollama "
          f"(+ {len(por_afinidad)} por afinidad, sin LLM)...\n")

    for r in objetivos:
        print(f"   [{r['categoria']}] {r['item']}...")
        evidencia = recuperar_evidencia(r["item"], kb, args.top_k)
        cierre = clasificar_gap(r["item"], r["categoria"], evidencia)
        r.update(cierre)

    techo = calcular_techo_honesto(requisitos, score_actual)

    salida = os.path.join(
        os.path.dirname(args.rag_match_json),
        f"{empresa}_gap_closure.json"
    )
    with open(salida, "w", encoding="utf-8") as f:
        json.dump({
            "empresa": empresa,
            "score_actual": score_actual,
            "techo_honesto": round(techo, 1),
            "requisitos": requisitos,
        }, f, ensure_ascii=False, indent=2)

    print(f"\n{'='*60}")
    print(f"📊 SCORE ACTUAL: {score_actual}%")
    print(f"🎯 TECHO HONESTO: {techo:.1f}%")
    print(f"{'='*60}\n")

    for r in objetivos + por_afinidad:
        icono = {
            "rewritable": "✅",
            "stretch": "🟡",
            "template_fill": "🔵",
            "not_rewritable": "❌",
        }[r["clasificacion_cierre"]]
        print(f"  {icono} [{r['categoria']}] {r['item']}")
        print(f"       {r['por_que']}")
        if r.get("bullet_propuesto"):
            print(f"       💡 Bullet: {r['bullet_propuesto']}")
        if r.get("que_estudiar"):
            print(f"       📚 Estudiar: {r['que_estudiar']}")
        print(f"       ⚠️  Riesgo entrevista: {r['riesgo_entrevista']}")
        print()

    print(f"💾 Guardado en: {salida}")


if __name__ == "__main__":
    main()
PYEOF