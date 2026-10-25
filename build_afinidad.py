"""
build_afinidad.py — Genera afinidad.json a partir de kb_index.json (el CV
indexado por build_kb.py) y dos fuentes de reglas:

  1) afinidad_familias.json  (AMPLIA): familias de habilidades equivalentes o
     cercanas (BI, nubes, bases de datos, GIS, ML, web, DevOps, soft skills...).
     Por cada miembro y por cada familia se crea una entrada automática:
        - DIRECTO     si tu CV contiene el término (o un alias),
        - RELACIONADO si contiene otro miembro de la misma familia
                      (ej. pide Tableau, tu CV tiene Power BI -> PARTIAL),
        - NINGUNO     si no hay nada.
     La entrada "familia/<nombre>" responde a requisitos de categoría
     ("cloud platforms", "BI tools", "version control").

  2) afinidad_seed.json  (PRECISA, opcional): reglas manuales con más control:
        requiere_alguno: basta con que aparezca alguno -> directo.
        requiere_juntos: un mismo chunk debe cumplir TODOS los grupos
                         (cada grupo se cumple con cualquiera de sus
                         alternativas). Para afirmaciones compuestas como
                         "Power BI + mapas".
        relacionado:     términos emparentados -> PARTIAL.
        frase:           solo se emite si el concepto queda respaldado.
     Si un concepto del seed tiene el mismo nombre que un miembro de familia,
     manda el seed.

Cada término se valida contra el texto real de los chunks del CV: nunca queda
una afinidad sin evidencia (evidence_ids) detrás.

Vuelve a correrlo cada vez que cambies el CV y regeneres kb_index.json.

Uso:
    python build_afinidad.py
    python build_afinidad.py --kb kb_index.json --out afinidad.json
"""

import argparse
import json
import os
import sys

from afinidad import contiene_termino, tokens

CAP_IDS = 8   # máximo de chunks citados por entrada (mantiene el JSON legible)


def _buscar(terminos: list, chunks: list, chunk_tokens: dict):
    """(términos encontrados, ids de chunks donde aparecen)."""
    encontrados, ids = [], []
    for t in terminos:
        hit = [c["id"] for c in chunks if contiene_termino(t, chunk_tokens[c["id"]])]
        if hit:
            encontrados.append(t)
            ids.extend(i for i in hit if i not in ids)
    return encontrados, ids


def _cumple_juntos(grupos: list, toks: list) -> bool:
    return all(any(contiene_termino(alt, toks) for alt in grupo) for grupo in grupos)


# --------------------------------------------------------------------------
# Seed (reglas manuales)
# --------------------------------------------------------------------------
def evaluar_concepto(definicion: dict, chunks: list, chunk_tokens: dict) -> dict:
    juntos = definicion.get("requiere_juntos")

    if juntos:
        ids_directo = [c["id"] for c in chunks if _cumple_juntos(juntos, chunk_tokens[c["id"]])]
        encontrados = []
    else:
        encontrados, ids_directo = _buscar(definicion.get("requiere_alguno", []),
                                           chunks, chunk_tokens)

    rel_encontrados, ids_rel = _buscar(definicion.get("relacionado", []), chunks, chunk_tokens)

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
        "evidence_ids": ids_finales[:CAP_IDS],
        "requiere_alguno": encontrados,
        "relacionado_encontrado": rel_encontrados,
        "frase": definicion.get("frase") if nivel == "directo" else None,
    }
    if juntos:
        resultado["requiere_juntos"] = juntos
    if nivel != "directo" and definicion.get("si_falta"):
        resultado["si_falta"] = definicion["si_falta"]
    return resultado


# --------------------------------------------------------------------------
# Familias (cobertura amplia)
# --------------------------------------------------------------------------
def entradas_de_familias(familias: dict, chunks: list, chunk_tokens: dict,
                         reservados: set) -> dict:
    # Un mismo miembro puede estar en varias familias (ej. Python): se funden.
    miembros = {}
    for fam, fd in familias.items():
        for canon, aliases in fd.get("miembros", {}).items():
            m = miembros.setdefault(canon.lower(),
                                    {"canon": canon, "aliases": [], "familias": []})
            m["aliases"].extend(a for a in aliases if a not in m["aliases"])
            if fam not in m["familias"]:
                m["familias"].append(fam)

    evid = {k: _buscar([m["canon"]] + m["aliases"], chunks, chunk_tokens)
            for k, m in miembros.items()}

    entradas = {}

    # --- una entrada por miembro ---
    for k, m in miembros.items():
        if k in reservados:
            continue
        enc, ids = evid[k]

        rel, ids_rel = [], []
        for fam in m["familias"]:
            if familias[fam].get("relacionar_miembros") is False:
                continue   # ej. lenguajes: saber Python no hace PARTIAL a Java
            for otro in familias[fam]["miembros"]:
                ko = otro.lower()
                if ko == k or otro in rel:
                    continue
                if evid[ko][1]:
                    rel.append(otro)
                    ids_rel.extend(i for i in evid[ko][1] if i not in ids_rel)

        if ids:
            nivel, ids_finales = "directo", ids
        elif ids_rel:
            nivel, ids_finales = "relacionado", ids_rel
        else:
            nivel, ids_finales = "ninguno", []

        e = {
            "alias_requisito": m["aliases"],
            "nivel": nivel,
            "soportado": nivel == "directo",
            "evidence_ids": ids_finales[:CAP_IDS],
            "requiere_alguno": enc,
            "relacionado_encontrado": rel,
            "frase": None,
            "familias": m["familias"],
        }
        if any(familias[f].get("ignorar_nivel") for f in m["familias"]):
            e["ignorar_nivel"] = True
        entradas[m["canon"]] = e

    # --- una entrada por familia (requisitos de categoría) ---
    for fam, fd in familias.items():
        encontrados = [c for c in fd.get("miembros", {}) if evid[c.lower()][1]]
        ids = []
        for c in encontrados:
            ids.extend(i for i in evid[c.lower()][1] if i not in ids)

        nivel = "directo" if encontrados else "ninguno"
        if nivel == "directo" and fd.get("tope_familia") == "relacionado":
            nivel = "relacionado"

        e = {
            "alias_requisito": fd.get("aliases_familia", []),
            "nivel": nivel,
            "soportado": nivel == "directo",
            "evidence_ids": ids[:CAP_IDS],
            "requiere_alguno": encontrados,
            "relacionado_encontrado": encontrados if nivel == "relacionado" else [],
            "frase": None,
            "es_familia": True,
        }
        if fd.get("ignorar_nivel"):
            e["ignorar_nivel"] = True
        entradas[f"familia/{fam}"] = e

    return entradas


def construir(kb: dict, seed: dict, familias: dict = None) -> dict:
    """Orden del resultado = prioridad al buscar: seed -> miembros -> familias."""
    chunks = kb["chunks"]
    chunk_tokens = {c["id"]: tokens(c["text"]) for c in chunks}

    resultado = {c: evaluar_concepto(d, chunks, chunk_tokens) for c, d in seed.items()}
    if familias:
        reservados = {c.lower() for c in seed}
        resultado.update(entradas_de_familias(familias, chunks, chunk_tokens, reservados))
    return resultado


# --------------------------------------------------------------------------
def _cargar_json(ruta: str):
    with open(ruta, "r", encoding="utf-8") as f:
        return json.load(f)


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--kb", default="kb_index.json")
    ap.add_argument("--seed", default="afinidad_seed.json")
    ap.add_argument("--familias", default="afinidad_familias.json")
    ap.add_argument("--out", default="afinidad.json")
    args = ap.parse_args()

    if not os.path.isfile(args.kb):
        print(f"❌ No existe {args.kb} (corre primero: python build_kb.py mi_cv.docx).")
        sys.exit(1)

    kb = _cargar_json(args.kb)
    seed = _cargar_json(args.seed) if os.path.isfile(args.seed) else {}
    familias = None
    if os.path.isfile(args.familias):
        familias = _cargar_json(args.familias).get("familias", {})
    if not seed and not familias:
        print("❌ No hay reglas: faltan afinidad_familias.json y afinidad_seed.json.")
        sys.exit(1)

    afinidad = construir(kb, seed, familias)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(afinidad, f, ensure_ascii=False, indent=2)

    iconos = {"directo": "✅", "relacionado": "🟡", "ninguno": "❌"}

    if seed:
        print(f"\n📌 Reglas manuales ({len(seed)}):")
        for concepto in seed:
            d = afinidad[concepto]
            ids = ", ".join(d["evidence_ids"][:4]) or "-"
            print(f"  {iconos[d['nivel']]} {concepto:22} {d['nivel']:12} {ids}")
            if d["nivel"] != "directo" and d.get("si_falta"):
                print(f"       ↳ {d['si_falta']}")

    if familias:
        fams = {k: v for k, v in afinidad.items() if v.get("es_familia")}
        print(f"\n🗂️  Familias ({len(fams)}):")
        for k, d in fams.items():
            hallados = ", ".join(d["requiere_alguno"][:4]) or "-"
            print(f"  {iconos[d['nivel']]} {k[len('familia/'):]:28} {d['nivel']:12} {hallados}")

    por_nivel = {n: sum(1 for d in afinidad.values() if d["nivel"] == n)
                 for n in ("directo", "relacionado", "ninguno")}
    print(f"\n📚 {len(afinidad)} entradas en {args.out}: "
          f"{por_nivel['directo']} directo, {por_nivel['relacionado']} relacionado, "
          f"{por_nivel['ninguno']} sin respaldo.")
    print("ℹ️  rag_match.py las usa automáticamente (--afinidad). "
          "Para ver qué pide una vacante y no está cubierto: python cobertura_afinidad.py")


if __name__ == "__main__":
    main()
