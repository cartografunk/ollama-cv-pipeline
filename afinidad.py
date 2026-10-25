"""
afinidad.py — Lista de afinidad entre lo que pide una vacante y lo que el CV
realmente respalda.

Idea: cada CONCEPTO (ej. "power bi", "machine learning", o una categoría como
"familia/nubes") dice con qué términos se considera respaldado. Un concepto
solo cuenta si esos términos aparecen TEXTUALMENTE en algún chunk de
kb_index.json (build_afinidad.py lo valida). La afinidad decide qué palabra de
la vacante puedes usar y con qué evidencia; no cambia lo que hiciste.

Niveles de un concepto ya validado:
  - directo:      hay evidencia directa      -> VERIFIED
  - relacionado:  solo hay algo emparentado  -> PARTIAL
  - ninguno:      sin evidencia              -> no aplica (sigue el flujo normal)

Este módulo es de uso en runtime (rag_match.py, cobertura_afinidad.py) y
compartido con el generador.
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
    "ability", "to",
    "de", "en", "con", "y", "el", "la", "los", "las",
    "experiencia", "conocimiento", "conocimientos", "manejo", "uso",
    "herramientas",
}

# Solo para conceptos con "ignorar_nivel": true (soft skills): "strong
# judgment" y "judgment" son lo mismo para una vacante. En herramientas NO se
# aplica.
PALABRAS_NIVEL = {
    "strong", "excellent", "good", "solid", "proven", "great", "outstanding",
    "advanced", "basic", "intermediate", "senior", "confident", "confidently",
    "sound", "effective", "effectively",
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


def _clave(texto: str, ignorar_nivel: bool = False) -> tuple:
    omitir = STOPWORDS | PALABRAS_NIVEL if ignorar_nivel else STOPWORDS
    return tuple(t for t in tokens(texto) if t not in omitir)


def _encontrar_concepto(item: str, afinidad: dict):
    """(nombre, definición) del concepto cuyo nombre o alias sea EXACTAMENTE
    el requisito (ignorando palabras de relleno), o None."""
    for concepto, d in afinidad.items():
        ign = bool(d.get("ignorar_nivel"))
        clave = _clave(item, ign)
        if not clave:
            continue
        nombres = [concepto] + list(d.get("alias_requisito") or [])
        if any(_clave(n, ign) == clave for n in nombres):
            return concepto, d
    return None


def buscar_afinidad(item: str, afinidad: dict):
    """Devuelve un dict listo para rag_match si el requisito coincide con un
    concepto respaldado (directo -> VERIFIED, relacionado -> PARTIAL), o None.

    Conservador a propósito: 'Advanced SQL optimization' no coincide con el
    concepto 'sql'; ese caso sigue el flujo semántico normal."""
    hallado = _encontrar_concepto(item, afinidad)
    if hallado is None:
        return None
    concepto, d = hallado

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


def estado_cobertura(item: str, afinidad: dict) -> tuple:
    """(estado, concepto) para el reporte de cobertura. estado:
    'directo' | 'relacionado' | 'ninguno' (concepto conocido, el CV no lo
    respalda) | 'desconocido' (ningún concepto ni familia lo contempla: hay
    que ampliar afinidad_familias.json o dejar que lo juzgue el LLM)."""
    hallado = _encontrar_concepto(item, afinidad)
    if hallado is None:
        return "desconocido", None
    concepto, d = hallado
    return d.get("nivel", "ninguno"), concepto


def cargar_afinidad(path: str):
    """Carga afinidad.json. Devuelve None (sin error) si no existe, para que
    rag_match funcione igual que antes cuando aún no se ha generado."""
    if not os.path.isfile(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)
