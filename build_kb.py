"""
build_kb.py — Construye/actualiza la base de conocimiento vectorial a partir
del CV (.docx). Reutiliza el recorrido de párrafos de extract_cv.py
(iter_paragraphs_incl_tables) para no duplicar esa lógica.

Cada párrafo no trivial del CV se convierte en un "chunk" de evidencia:
texto VERBATIM + la sección (heading) bajo la que aparece. No se reescribe
ni resume nada — el embedding se calcula sobre el texto original, así que
la evidencia que se recupere después siempre es texto real del candidato,
nunca una paráfrasis del modelo.

Uso:
    python build_kb.py mi_cv.docx
    python build_kb.py mi_cv.docx --out kb_index.json --model nomic-embed-text
"""

import argparse
import json
import re
import sys
from pathlib import Path

from docx import Document

from embeddings import EMBED_MODEL, embed_batch
from extract_cv import iter_paragraphs_incl_tables

# Párrafos que no aportan evidencia semántica (separadores, contacto suelto)
# — no vale la pena embeberlos ni mostrarlos como "evidencia".
_TRIVIAL_RE = re.compile(
    r"^[\s\-–—·|•.,:;()]*$|^https?://|^[\w.\-]+@[\w.\-]+\.\w+$"
)
MIN_CHARS = 12

_HEADING_STYLES = {"heading 1", "heading 2", "heading 3", "title", "subtitle"}


def _es_heading(para) -> bool:
    estilo = ((para.style.name if para.style is not None else "") or "").lower()
    if estilo in _HEADING_STYLES:
        return True
    # Heurística extra para CVs sin estilos de heading bien puestos: línea
    # corta y toda en mayúsculas casi siempre es un encabezado de sección
    # ("EXPERIENCIA PROFESIONAL", "EDUCACIÓN").
    texto = para.text.strip()
    return 0 < len(texto) <= 40 and texto.isupper()


def extraer_chunks(path_docx: str) -> list:
    doc = Document(path_docx)
    chunks = []
    seccion_actual = "General"
    contador = 0

    for para in iter_paragraphs_incl_tables(doc):
        texto = para.text.strip()
        if not texto:
            continue
        if _es_heading(para):
            seccion_actual = texto
            continue
        if len(texto) < MIN_CHARS or _TRIVIAL_RE.match(texto):
            continue

        contador += 1
        chunks.append({
            "id": f"chunk_{contador:04d}",
            "text": texto,
            "section": seccion_actual,
        })

    return chunks


def construir_kb(path_docx: str, path_out: str, model: str) -> None:
    print(f"📄 Leyendo párrafos de {path_docx}...")
    chunks = extraer_chunks(path_docx)
    if not chunks:
        print("❌ No se extrajo ningún chunk de evidencia del CV.")
        sys.exit(1)
    print(f"✅ {len(chunks)} chunk(s) de evidencia detectados.")

    print(f"🧠 Generando embeddings con Ollama ({model})...")

    def progreso(i, total):
        if i % 5 == 0 or i == total:
            print(f"   {i}/{total}")

    textos = [c["text"] for c in chunks]
    try:
        vectores = embed_batch(textos, model=model, on_progress=progreso)
    except RuntimeError as e:
        print(f"❌ {e}")
        sys.exit(1)

    for c, v in zip(chunks, vectores):
        c["embedding"] = v

    kb = {
        "model": model,
        "source_docx": str(path_docx),
        "chunks": chunks,
    }
    with open(path_out, "w", encoding="utf-8") as f:
        json.dump(kb, f, ensure_ascii=False)

    print(f"💾 Knowledge base guardada en: {path_out} "
          f"({len(chunks)} chunks, dim={len(vectores[0])})")
    print("ℹ️  Vuelve a correr este script cada vez que actualices el CV; "
          "así como extract_cv.py regenera cv_perfil.json, este regenera "
          "kb_index.json.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("cv_docx", help="CV de entrada (.docx)")
    parser.add_argument("--out", default="kb_index.json",
                         help="Archivo de salida del índice (default: kb_index.json)")
    parser.add_argument("--model", default=EMBED_MODEL,
                         help=f"Modelo de embeddings en Ollama (default: {EMBED_MODEL})")
    args = parser.parse_args()

    if not Path(args.cv_docx).exists():
        print(f"❌ No existe el archivo: {args.cv_docx}")
        sys.exit(1)

    construir_kb(args.cv_docx, args.out, args.model)
