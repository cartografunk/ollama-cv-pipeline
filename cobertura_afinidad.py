"""
cobertura_afinidad.py — Dice, para una o varias vacantes, qué pide la JD y qué
tan cubierto está por tu CV según afinidad.json. No usa Ollama.

Estados por requisito:
  ✅ directo       tu CV lo respalda (o respalda la categoría completa)
  🟡 relacionado   tu CV tiene algo cercano de la misma familia (saldrá PARTIAL)
  ❌ sin respaldo  el concepto existe en la lista, pero tu CV no lo respalda
  ❓ desconocido   ninguna familia ni regla lo contempla: o amplías
                   afinidad_familias.json, o lo juzga el LLM en rag_match.py

Los desconocidos se guardan en afinidad_pendientes.json: es la lista de trabajo
para ampliar la taxonomía con lo que piden las vacantes reales.

Entradas aceptadas (una o varias):
  - matches/Empresa_keywords.json     (lo genera match.py)
  - matches/Empresa_rag_match.json    (lo genera rag_match.py)
  - un .txt con un requisito por línea

Uso:
    python cobertura_afinidad.py matches/Siemens_keywords.json
    python cobertura_afinidad.py matches/*_keywords.json --afinidad afinidad.json
"""

import argparse
import json
import os
import sys

from afinidad import cargar_afinidad, estado_cobertura

ICONOS = {"directo": "✅", "relacionado": "🟡", "ninguno": "❌", "desconocido": "❓"}
ETIQUETAS = {
    "directo": "cubierto",
    "relacionado": "relacionado (PARTIAL)",
    "ninguno": "sin respaldo en tu CV",
    "desconocido": "sin familia ni regla (ampliar taxonomía)",
}


def leer_requisitos(ruta: str) -> list:
    """Lista de (item, categoria) desde un keywords.json, rag_match.json o .txt."""
    if ruta.lower().endswith(".txt"):
        with open(ruta, "r", encoding="utf-8") as f:
            return [(l.strip(), "texto") for l in f if l.strip()]

    with open(ruta, "r", encoding="utf-8") as f:
        data = json.load(f)

    if isinstance(data, dict) and isinstance(data.get("requisitos"), list):
        return [(r["item"], r.get("categoria", "?")) for r in data["requisitos"] if r.get("item")]

    items = []
    for categoria, lista in data.items():
        if isinstance(lista, list):
            items.extend((str(x), categoria) for x in lista if str(x).strip())
    return items


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("entradas", nargs="+", help="keywords.json / rag_match.json / .txt")
    ap.add_argument("--afinidad", default="afinidad.json")
    ap.add_argument("--pendientes", default="afinidad_pendientes.json",
                    help="Dónde guardar los requisitos desconocidos")
    args = ap.parse_args()

    afinidad = cargar_afinidad(args.afinidad)
    if afinidad is None:
        print(f"❌ No existe {args.afinidad}. Corre primero: python build_afinidad.py")
        sys.exit(1)

    pendientes = []
    for ruta in args.entradas:
        if not os.path.isfile(ruta):
            print(f"⚠️  No existe {ruta}, se omite.")
            continue

        fuente = os.path.basename(ruta)
        vistos, filas = set(), []
        for item, categoria in leer_requisitos(ruta):
            if item.lower() in vistos:
                continue
            vistos.add(item.lower())
            estado, concepto = estado_cobertura(item, afinidad)
            filas.append((item, categoria, estado, concepto))
            if estado == "desconocido":
                pendientes.append({"item": item, "categoria": categoria, "fuente": fuente})

        cuenta = {e: sum(1 for f in filas if f[2] == e) for e in ICONOS}
        total = len(filas) or 1
        cubierto = cuenta["directo"] + 0.5 * cuenta["relacionado"]

        print(f"\n{'=' * 64}\n📄 {fuente}  —  {len(filas)} requisitos\n{'=' * 64}")
        for estado in ("directo", "relacionado", "ninguno", "desconocido"):
            grupo = [f for f in filas if f[2] == estado]
            if not grupo:
                continue
            print(f"\n{ICONOS[estado]} {ETIQUETAS[estado]} ({len(grupo)})")
            for item, categoria, _, concepto in grupo:
                via = f"  ← {concepto}" if concepto and concepto.lower() != item.lower() else ""
                print(f"   [{categoria}] {item}{via}")
        print(f"\n📊 Cobertura por afinidad (directo=1, relacionado=0.5): "
              f"{cubierto / total * 100:.0f}%  |  desconocidos: {cuenta['desconocido']}")

    with open(args.pendientes, "w", encoding="utf-8") as f:
        json.dump(pendientes, f, ensure_ascii=False, indent=2)
    print(f"\n💾 {len(pendientes)} requisito(s) desconocido(s) en {args.pendientes} "
          f"(candidatos para afinidad_familias.json).")


if __name__ == "__main__":
    main()
