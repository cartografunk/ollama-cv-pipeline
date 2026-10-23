"""
build_afinidad.py — Genera afinidad.json a partir de afinidad_seed.json y de
kb_index.json (el CV indexado por build_kb.py).

Cada concepto del seed lista los términos que lo respaldan. Aquí se VALIDA
cada uno contra el texto real de los chunks del CV:

  - requiere_alguno: basta con que aparezca alguno de estos términos -> directo.
  - requiere_juntos: lista de grupos; un solo chunk debe cumplir TODOS los
    grupos (cada grupo se cumple con cualquiera de sus alternativas). Sirve
    para afirmaciones compuestas como "Power BI + mapas".
  - relacionado:     términos emparentados. Si no hay evidencia directa pero sí
    de estos -> nivel "relacionado" (el requisito saldrá PARTIAL).

La frase solo se emite si el concepto quedó respaldado directamente: nunca
queda una frase lista para usar sin evidencia detrás.

Vuelve a correrlo cada vez que cambies el CV y regeneres kb_index.json.

Uso:
    python build_afinidad.py
    python build_afinidad.py --kb kb_index.json --seed afinidad_seed.json --out afinidad.json
"""

import argparse
import json
import os
import sys

from afinidad import contiene_termino, tokens


def _cumple_juntos(grupos: list, toks: list) -> bool:
    return all(any(contiene_termino(alt, toks) for alt in grupo) for grupo in grupos)


def evaluar_concepto(definicion: dict, chunks: list, chunk_tokens: dict) -> dict:
    juntos = definicion.get("requiere_juntos")

    # --- evidencia directa ---
    if juntos:
        ids_directo = [c["id"] for c in chunks if _cumple_juntos(juntos, chunk_tokens[c["id"]])]
        encontrados = []
    else:
        encontrados, ids_directo = [], []
        for termino in definicion.get("requiere_alguno", []):
            ids = [c["id"] for c in chunks if contiene_termino(termino, chunk_tokens[c["id"]])]
            if ids:
                encontrados.append(termino)
                ids_directo.extend(i for i in ids if i not in ids_directo)

    # --- evidencia relacionada ---
    rel_encontrados, ids_rel = [], []
    for termino in definicion.get("relacionado", []):
        ids = [c["id"] for c in chunks if contiene_termino(termino, chunk_tokens[c["id"]])]
        if ids:
            rel_encontrados.append(termino)
            ids_rel.extend(i for i in ids if i not in ids_rel)

    if ids_directo:
        nivel, ids_finales = "directo", ids_directo
    elif ids_rel:
        nivel, ids_finales = "relacionado", ids_rel
    else:
        nivel, ids_finales = "ninguno", []

    resultado = {
        "alias_requisito": definicion.get("alias_requisito", []),
        "nivel": nivel,
        "soportado": nivel == "directo",
        "evidence_ids": ids_finales,
        "requiere_alguno": encontrados,
        "relacionado_encontrado": rel_encontrados,
        "frase": definicion.get("frase") if nivel == "directo" else None,
    }
    if juntos:
        resultado["requiere_juntos"] = juntos
    if nivel != "directo" and definicion.get("si_falta"):
        resultado["si_falta"] = definicion["si_falta"]
    return resultado


def construir(kb: dict, seed: dict) -> dict:
    chunks = kb["chunks"]
    chunk_tokens = {c["id"]: tokens(c["text"]) for c in chunks}
    return {concepto: evaluar_concepto(d, chunks, chunk_tokens) for concepto, d in seed.items()}


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--kb", default="kb_index.json")
    ap.add_argument("--seed", default="afinidad_seed.json")
    ap.add_argument("--out", default="afinidad.json")
    args = ap.parse_args()

    for ruta, ayuda in ((args.kb, "python build_kb.py mi_cv.docx"),
                        (args.seed, "debe estar junto a este script")):
        if not os.path.isfile(ruta):
            print(f"❌ No existe {ruta} ({ayuda}).")
            sys.exit(1)

    with open(args.kb, "r", encoding="utf-8") as f:
        kb = json.load(f)
    with open(args.seed, "r", encoding="utf-8") as f:
        seed = json.load(f)

    afinidad = construir(kb, seed)

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(afinidad, f, ensure_ascii=False, indent=2)

    iconos = {"directo": "✅", "relacionado": "🟡", "ninguno": "❌"}
    print(f"\n📚 Afinidad generada ({len(afinidad)} conceptos) -> {args.out}\n")
    for concepto, d in afinidad.items():
        ids = ", ".join(d["evidence_ids"]) or "-"
        print(f"  {iconos[d['nivel']]} {concepto:22} {d['nivel']:12} {ids}")
        if d["nivel"] != "directo" and d.get("si_falta"):
            print(f"       ↳ {d['si_falta']}")
    print("\nℹ️  rag_match.py usa este archivo automáticamente (opción --afinidad).")


if __name__ == "__main__":
    main()
