"""
retrieve.py — Prueba el retrieval semántico de forma AISLADA, sin pasar por
el LLM de clasificación (rag_match.py). Útil para depurar si la base
vectorial recupera evidencia razonable antes de meter a Ollama en el loop
de clasificación, y para el ejercicio de validación de la Fase 4 (ver si
"geospatial database" recupera el chunk de PostGIS aunque no comparta
palabras exactas).

Uso:
    python retrieve.py "geospatial database management"
    python retrieve.py "AWS production experience" --top-k 3 --kb kb_index.json
"""

import argparse
import json
import sys

from embeddings import cosine_similarity, embed_text


def cargar_kb(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def buscar(kb: dict, query: str, top_k: int = 5) -> list:
    q_vec = embed_text(query, model=kb["model"])
    resultados = [
        (cosine_similarity(q_vec, chunk["embedding"]), chunk)
        for chunk in kb["chunks"]
    ]
    resultados.sort(key=lambda x: x[0], reverse=True)
    return resultados[:top_k]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("query", help="Requisito/keyword a buscar")
    parser.add_argument("--kb", default="kb_index.json")
    parser.add_argument("--top-k", type=int, default=5)
    args = parser.parse_args()

    try:
        kb = cargar_kb(args.kb)
    except FileNotFoundError:
        print(f"❌ No existe {args.kb}. Corre primero: python build_kb.py mi_cv.docx")
        sys.exit(1)

    try:
        resultados = buscar(kb, args.query, args.top_k)
    except RuntimeError as e:
        print(f"❌ {e}")
        sys.exit(1)

    print(f"\n🔎 Top {len(resultados)} para: {args.query!r}\n")
    for score, chunk in resultados:
        print(f"  [{score:.3f}] ({chunk['section']}) {chunk['text'][:100]}")
