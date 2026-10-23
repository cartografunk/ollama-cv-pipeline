"""
cv_rewriter.py — Agente Redactor (v2).

Cierra los gaps que gap_closure.py marcó como cerrables reescribiendo el
párrafo del CV que YA sirvió de evidencia, sin pedirle al LLM que "busque"
dónde meterlo:

  gap_closure.json -> evidence_ids -> chunk (kb_index.json) -> párrafo del .docx

Por qué así:
  - Cero llamadas al LLM para ubicar el párrafo (antes: el CV completo en cada
    gap). El chunk de evidencia ES el lugar honesto donde va el vocabulario.
  - Recorre tablas igual que build_kb.py (antes: doc.paragraphs, que se las
    saltaba y desalineaba los índices).
  - Conserva el formato: respeta la etiqueta en negrita (empresa/cargo) y solo
    resalta las palabras que cambiaron (reutiliza humanizar.py).
  - Solo "rewritable" entra al CV. "stretch" se reporta como pendiente de
    estudio (--incluir-stretch lo fuerza, bajo tu responsabilidad).
  - Cada reescritura se valida (cifras iguales, largo razonable, menciona el
    requisito) y, si falla, el párrafo queda intacto.

Uso:
    python cv_rewriter.py CV.docx matches/Empresa_gap_closure.json CV_salida.docx
    python cv_rewriter.py CV.docx matches/Empresa_gap_closure.json CV_salida.docx --dry-run
    python cv_rewriter.py CV.docx matches/Empresa_gap_closure.json CV_salida.docx --kb kb_index.json

Además del .docx escribe CV_salida.cambios.json (antes/después de cada cambio).
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

import requests
from docx import Document

from humanizar import (
    COLORES_RESALTADO,
    SEPARADOR_RE,
    _numeros,
    aplicar_texto_diff,
    dividir_etiqueta,
    iter_paragraphs_incl_tables,
    limpiar_salida,
)

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = "mistral:7b"
TIMEOUT = 180            # antes 300; un 7B responde un párrafo en segundos
MAX_RATIO_LARGO = 1.6    # el párrafo nuevo no puede ser >60% más largo
CIERRES_DEFAULT = {"rewritable"}

PROMPT_REESCRITURA = """Eres un redactor de CV. Reescribe el PÁRRAFO ORIGINAL \
para que use de forma natural el vocabulario del REQUISITO, sin cambiar lo que \
el candidato realmente hizo.

REQUISITO DE LA VACANTE: {requisito}
GUÍA DE VOCABULARIO (solo orientación, no la copies tal cual): {bullet}

PÁRRAFO ORIGINAL:
{texto}

REGLAS ESTRICTAS:
1. NO agregues tecnologías, herramientas, métricas, años ni logros que no \
estén ya en el párrafo original.
2. Conserva nombres de empresa, cargos, fechas, herramientas y cifras EXACTAMENTE.
3. Mismo idioma que el original. No traduzcas.
4. Sin primera persona. Tono conciso; no alargues el texto.
5. Devuelve SOLO el párrafo reescrito, sin comillas ni explicaciones.

PÁRRAFO REESCRITO:"""


# --------------------------------------------------------------------------
# Utilidades
# --------------------------------------------------------------------------
def _norm(s: str) -> str:
    return " ".join(s.split())


def cargar_chunks(kb_path: str) -> dict:
    """id -> chunk. Solo necesitamos id/text/section; los embeddings no se usan."""
    if not os.path.isfile(kb_path):
        print(f"❌ No existe {kb_path}. Corre primero: python build_kb.py mi_cv.docx")
        sys.exit(1)
    with open(kb_path, "r", encoding="utf-8") as f:
        kb = json.load(f)
    return {c["id"]: c for c in kb["chunks"]}


def localizar_parrafo(doc, chunk_text: str):
    """Busca el párrafo (incluyendo celdas de tabla) cuyo texto coincide con
    el chunk de evidencia. build_kb guarda el texto verbatim, así que no hace
    falta re-embeber nada."""
    objetivo = _norm(chunk_text)
    for para in iter_paragraphs_incl_tables(doc):
        if _norm(para.text) == objetivo:
            return para
    return None


def _menciona(requisito: str, texto: str) -> bool:
    """Chequeo suave: el texto nuevo usa al menos una palabra significativa
    del requisito (o el requisito completo si es una sigla corta, ej. SQL)."""
    bajo = texto.lower()
    tokens = [t for t in re.findall(r"\w+", requisito.lower()) if len(t) >= 4]
    if not tokens:
        return requisito.lower() in bajo
    return any(t in bajo for t in tokens)


def reescribir_parrafo(texto: str, requisito: str, bullet: str,
                       model: str, intentos: int = 2):
    """Devuelve (nuevo_texto | None, motivo). None = dejar el párrafo intacto."""
    prompt = PROMPT_REESCRITURA.format(
        requisito=requisito, bullet=bullet or "(sin guía)", texto=texto)
    motivo = "sin intentos"

    for intento in range(1, intentos + 1):
        try:
            resp = requests.post(OLLAMA_URL, json={
                "model": model, "prompt": prompt, "stream": False,
                "options": {"temperature": 0.3, "num_ctx": 4096},
            }, timeout=TIMEOUT)
            resp.raise_for_status()
        except requests.exceptions.ConnectionError:
            return None, "Ollama no disponible (¿corriste 'ollama serve'?)"
        except Exception as e:
            motivo = f"error de Ollama: {e}"
            continue

        nuevo = limpiar_salida(resp.json().get("response", ""))
        if not nuevo:
            motivo = "respuesta vacía"
        elif _numeros(nuevo) != _numeros(texto):
            motivo = "cifras/fechas distintas al original"
        elif len(nuevo) > len(texto) * MAX_RATIO_LARGO:
            motivo = "el texto nuevo es demasiado largo"
        elif not _menciona(requisito, nuevo):
            motivo = "el texto nuevo no incorpora el requisito"
        else:
            return nuevo, "ok"
    return None, motivo


# --------------------------------------------------------------------------
# Pipeline
# --------------------------------------------------------------------------
def procesar_cv(cv_path, gap_json_path, salida, kb_path, model,
                cierres, color, dry_run) -> None:
    with open(gap_json_path, "r", encoding="utf-8") as f:
        gaps_data = json.load(f)

    requisitos = gaps_data.get("requisitos", [])
    a_cerrar = [r for r in requisitos
                if r.get("clasificacion_cierre") in cierres and r.get("bullet_propuesto")]
    pendientes_estudio = [r for r in requisitos
                          if r.get("clasificacion_cierre") == "stretch"
                          and "stretch" not in cierres]

    if not a_cerrar:
        print("✅ No hay gaps cerrables para el cuerpo del CV.")
    else:
        print(f"🔧 {len(a_cerrar)} gap(s) a cerrar "
              f"({', '.join(sorted(cierres))})...\n")

    chunks = cargar_chunks(kb_path)
    doc = Document(cv_path)
    cambios, omitidos = [], []

    for gap in a_cerrar:
        requisito = gap["item"]
        print(f"   • {requisito}")

        # 1) Ubicar el párrafo ancla por evidencia (sin LLM).
        para, chunk_id = None, None
        for cid in gap.get("evidence_ids") or []:
            if cid in chunks:
                para = localizar_parrafo(doc, chunks[cid]["text"])
                if para is not None:
                    chunk_id = cid
                    break
        if para is None:
            motivo = ("sin evidence_ids" if not gap.get("evidence_ids")
                      else "no encontré el párrafo de la evidencia en el .docx "
                           "(¿kb_index.json es de otro CV? corre build_kb.py)")
            print(f"       ↳ omitido: {motivo}")
            omitidos.append({"item": requisito, "motivo": motivo})
            continue

        # 2) Separar etiqueta en negrita (empresa/cargo) del cuerpo editable.
        runs_etiqueta, runs_cuerpo = dividir_etiqueta(para)
        cuerpo = "".join(r.text for r in runs_cuerpo)
        sep = ""
        if runs_etiqueta:
            m = SEPARADOR_RE.match(cuerpo)
            sep = m.group(0) if m else ""
        texto = cuerpo[len(sep):].strip()
        if not texto:
            omitidos.append({"item": requisito, "motivo": "párrafo ancla sin cuerpo editable"})
            print("       ↳ omitido: el párrafo ancla es solo etiqueta")
            continue

        print(f"       ↳ ancla {chunk_id}: {texto[:70]}...")
        if dry_run:
            continue

        # 3) Reescribir (único uso del LLM) y validar.
        nuevo, motivo = reescribir_parrafo(texto, requisito, gap["bullet_propuesto"], model)
        if nuevo is None:
            print(f"       ↳ omitido: {motivo}")
            omitidos.append({"item": requisito, "motivo": motivo})
            continue
        if nuevo == texto:
            print("       ↳ sin cambios")
            continue

        # 4) Escribir conservando formato; resalta solo lo que cambió.
        aplicar_texto_diff(para, runs_cuerpo, cuerpo, sep + nuevo, color)
        cambios.append({"item": requisito, "chunk_id": chunk_id,
                        "antes": texto, "despues": nuevo})
        doc.save(salida)   # guardado incremental: si algo falla, no pierdes lo hecho
        print("       ↳ ✅ reescrito")

    if not dry_run:
        doc.save(salida)
        ruta_cambios = Path(salida).with_suffix(".cambios.json")
        with open(ruta_cambios, "w", encoding="utf-8") as f:
            json.dump({"cambios": cambios, "omitidos": omitidos,
                       "pendientes_estudio": [
                           {"item": r["item"], "que_estudiar": r.get("que_estudiar")}
                           for r in pendientes_estudio]},
                      f, ensure_ascii=False, indent=2)
        print(f"\n💾 CV: {salida}  |  detalle: {ruta_cambios}")
        print(f"📊 {len(cambios)} reescrito(s), {len(omitidos)} omitido(s)")

    if pendientes_estudio:
        print("\n📚 Pendientes de estudio (NO se agregaron al CV):")
        for r in pendientes_estudio:
            print(f"   - {r['item']}: {r.get('que_estudiar') or 'sin sugerencia'}")


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cv_docx", help="CV base (.docx)")
    ap.add_argument("gap_json", help="matches/Empresa_gap_closure.json")
    ap.add_argument("salida", help="CV de salida (.docx)")
    ap.add_argument("--kb", default="kb_index.json")
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--color", default="verde", choices=sorted(COLORES_RESALTADO))
    ap.add_argument("--incluir-stretch", action="store_true",
                    help="También escribe gaps 'stretch' (requieren estudio previo). "
                         "Úsalo solo si de verdad vas a preparar ese tema.")
    ap.add_argument("--dry-run", action="store_true",
                    help="Solo muestra qué párrafo se tocaría por cada gap (sin LLM)")
    args = ap.parse_args()

    for ruta in (args.cv_docx, args.gap_json):
        if not os.path.isfile(ruta):
            print(f"❌ No existe: {ruta}")
            sys.exit(1)

    cierres = set(CIERRES_DEFAULT)
    if args.incluir_stretch:
        cierres.add("stretch")

    procesar_cv(args.cv_docx, args.gap_json, args.salida, args.kb, args.model,
                cierres, COLORES_RESALTADO[args.color], args.dry_run)


if __name__ == "__main__":
    main()
