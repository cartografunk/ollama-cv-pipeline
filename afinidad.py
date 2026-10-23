"""
afinidad.py — Lista de afinidad entre lo que pide una vacante y lo que el CV
realmente respalda.

Idea: cada CONCEPTO (ej. "power bi", "machine learning") dice con qué términos
se considera respaldado. Un concepto solo cuenta si esos términos aparecen
TEXTUALMENTE en algún chunk de kb_index.json (build_afinidad.py lo valida).
La afinidad decide qué palabra de la vacante puedes usar y con qué evidencia;
no cambia lo que hiciste.

Niveles de un concepto ya validado:
  - directo:      hay evidencia directa      -> VERIFIED
  - relacionado:  solo hay algo emparentado  -> PARTIAL
  - ninguno:      sin evidencia              -> no aplica (sigue el flujo normal)

Este módulo es de uso en runtime (rag_match.py) y compartido con el generador.
"""

import json
import os
import re
import unicodedata

# Palabras de relleno que no cambian el requisito ("experience with Power BI"
# equivale a "Power BI"). Los adjetivos de nivel (advanced, senior...) NO están
# aquí a propósito: "Advanced SQL" no debe verificarse como "SQL" sin más.
STOPWORDS = {
    "experience", "knowledge", "skills", "skill", "proficiency", "with", "of",
    "in", "and", "the", "a", "an", "for", "using", "tools", "tool",
    "de", "en", "con", "y", "el", "la", "los", "las",
    "experiencia", "conocimiento", "conocimientos", "manejo", "uso",
    "herramientas",
}


def tokens(texto: str) -> list:
    """Minúsculas, sin acentos, solo letras/dígitos. 'Sci-Kit Learn' ->
    ['sci', 'kit', 'learn']."""
    t = unicodedata.normalize("NFKD", texto.lower())
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    return re.findall(r"[a-z0-9]+", t)


def contiene_termino(termino: str, toks: list) -> bool:
    """¿Aparece `termino` en la lista de tokens de un texto? Tolera guiones,
    espacios y juntado: 'scikit-learn' == 'Sci-Kit Learn', 'PowerBI' ==
    'Power BI'. Compara por tokens completos, así 'excel' NO coincide con
    'excellence'."""
    term_toks = tokens(termino)
    n = len(term_toks)
    if n == 0:
        return False

    # 1) secuencia contigua de tokens
    for i in range(len(toks) - n + 1):
        if toks[i:i + n] == term_toks:
            return True

    # 2) mismo término pero escrito junto/separado distinto
    junto = "".join(term_toks)
    for i in range(len(toks)):
        acc = ""
        for j in range(i, min(i + 5, len(toks))):
            acc += toks[j]
            if acc == junto:
                return True
            if len(acc) >= len(junto):
                break
    return False


def _clave(texto: str) -> tuple:
    return tuple(t for t in tokens(texto) if t not in STOPWORDS)


def buscar_afinidad(item: str, afinidad: dict):
    """Busca un concepto cuyo nombre o alias sea EXACTAMENTE el requisito
    (ignorando palabras de relleno). Devuelve un dict listo para rag_match o
    None si no hay afinidad aplicable.

    Conservador a propósito: 'Advanced SQL optimization' no coincide con el
    concepto 'sql'; ese caso sigue el flujo semántico normal."""
    clave = _clave(item)
    if not clave:
        return None

    for concepto, d in afinidad.items():
        nombres = [concepto] + list(d.get("alias_requisito") or [])
        if not any(_clave(n) == clave for n in nombres):
            continue

        nivel = d.get("nivel")
        ids = list(d.get("evidence_ids") or [])
        if nivel == "directo":
            return {
                "classification": "VERIFIED",
                "evidence_ids": ids,
                "rationale": f"afinidad '{concepto}': respaldado por {', '.join(ids) or 'el CV'}",
                "concepto": concepto,
            }
        if nivel == "relacionado":
            return {
                "classification": "PARTIAL",
                "evidence_ids": ids,
                "rationale": (f"afinidad '{concepto}': solo hay evidencia relacionada "
                              f"({', '.join(d.get('relacionado_encontrado') or [])})"),
                "concepto": concepto,
            }
        return None   # concepto conocido pero sin respaldo: no afirmar nada
    return None


def cargar_afinidad(path: str):
    """Carga afinidad.json. Devuelve None (sin error) si no existe, para que
    rag_match funcione igual que antes cuando aún no se ha generado."""
    if not os.path.isfile(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)
