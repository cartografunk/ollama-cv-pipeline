#!/usr/bin/env python3
"""
mejorar_cv.py — Reescribe el contenido de un CV (.docx) usando un modelo
local vía Ollama, con foco en lenguaje de CV: verbos de acción, logros
cuantificables, tono conciso, sin primera persona.

Los párrafos reescritos quedan RESALTADOS (highlight) para revisarlos de un
vistazo, en amarillo por defecto (cambia con --color-resaltado).
Si un párrafo empieza con una "etiqueta" en negrita (p. ej. nombre de empresa
en negrita seguido de la descripción sin negrita), la etiqueta se conserva
intacta y solo se reescribe la descripción.

Con --listar-estilos ves qué estilos de Word usa tu CV; con --solo-estilos /
--omitir-estilos eliges cuáles reescribir, y con --estilos-etiqueta declaras
estilos de carácter (p. ej. "Strong" o uno propio para la empresa) que cuentan
como etiqueta protegida, además de la negrita directa o heredada de un estilo.

Uso:
    python mejorar_cv.py entrada.docx --listar-estilos
    python mejorar_cv.py entrada.docx salida.docx --solo-estilos "List Bullet,Viñeta"
    python mejorar_cv.py entrada.docx salida.docx --omitir-estilos "Heading 1,Título"
    python mejorar_cv.py entrada.docx salida.docx
    python mejorar_cv.py entrada.docx salida.docx --model llama3.1:8b
    python mejorar_cv.py entrada.docx salida.docx --min-chars 40 --dry-run
    python mejorar_cv.py entrada.docx salida.docx --color-resaltado verde
    python mejorar_cv.py entrada.docx salida.docx --sin-resaltado   # versión final limpia
"""

import argparse
import difflib
import logging
import re
import sys
from collections import Counter
from pathlib import Path

import requests
from docx import Document
from docx.enum.text import WD_COLOR_INDEX

COLORES_RESALTADO = {
    "amarillo": WD_COLOR_INDEX.YELLOW,
    "verde": WD_COLOR_INDEX.BRIGHT_GREEN,
    "turquesa": WD_COLOR_INDEX.TURQUOISE,
    "rosa": WD_COLOR_INDEX.PINK,
    "gris": WD_COLOR_INDEX.GRAY_25,
}

OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_TAGS_URL = "http://localhost:11434/api/tags"
DEFAULT_MODEL = "mistral:7b"
DEFAULT_TIMEOUT = 1800

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("mejorar_cv")

PROMPT_TEMPLATE = """You are a professional resume (CV) editor. Rewrite the \
following CV text in its ORIGINAL LANGUAGE so it sounds sharper and more \
professional, keeping the exact same meaning and every fact.

STRICT RULES:
- Preserve company names, job titles, dates, degrees, tools, and numbers \
EXACTLY as they are. Never invent metrics, dates, or achievements that \
aren't in the original.
- DO NOT translate the text. If the input is in Spanish, output in Spanish. \
If in English, output in English.
- Use strong action verbs at the start of bullet points (e.g. "Led", \
"Built", "Reduced", "Diseñé", "Lideré", "Reduje") instead of passive or \
vague phrasing.
- Remove filler words and first-person pronouns ("I", "yo", "mi equipo y \
yo") — CV bullets are implied first person.
- Keep it concise: prefer shorter, punchier phrasing over the original if \
the original is wordy. Do not pad or lengthen the text.
- Do not add generic buzzwords ("synergy", "results-driven", "dynamic \
professional") that aren't backed by the original content.
- Output ONLY the rewritten text: no introduction, no quotes, no comments.

ORIGINAL TEXT:
{texto}

REWRITTEN TEXT:"""

# Separador entre la etiqueta en negrita y la descripción: ": ", " – ", " | ", etc.
SEPARADOR_RE = re.compile(r"^[\s:–—\-|·,;]+")
PREAMBULO_RE = re.compile(
    r"(?i)^(here('s| is)|rewritten|texto reescrito|aquí (está|tienes)|claro)"
)


# --------------------------------------------------------------------------
# Modelo
# --------------------------------------------------------------------------
def check_model_available(model: str) -> bool:
    try:
        resp = requests.get(OLLAMA_TAGS_URL, timeout=10)
        resp.raise_for_status()
        names = [m["name"] for m in resp.json().get("models", [])]
        return model in names
    except Exception as e:
        log.warning("No se pudo verificar el modelo en Ollama (%s). "
                    "Asumo que existe y sigo.", e)
        return True


def limpiar_salida(s: str) -> str:
    """Quita preámbulos tipo 'Here is the rewritten text:', comillas
    envolventes y saltos de línea (un bullet = un párrafo)."""
    s = s.strip()
    lineas = s.splitlines()
    if len(lineas) > 1 and PREAMBULO_RE.match(lineas[0].strip()):
        s = "\n".join(lineas[1:]).strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'“”":
        s = s[1:-1].strip()
    return " ".join(s.split())


def _numeros(s: str) -> set[str]:
    """Secuencias numéricas normalizadas (1,000 / 1.000 -> 1000)."""
    s = re.sub(r"(?<=\d)[.,](?=\d{3}\b)", "", s)
    return set(re.findall(r"\d+(?:[.,]\d+)?", s))


def mejorar_texto(texto: str, model: str, intentos: int = 2) -> tuple[str, bool]:
    """Devuelve (texto_resultante, exito)."""
    prompt = PROMPT_TEMPLATE.format(texto=texto)

    for intento in range(1, intentos + 1):
        try:
            response = requests.post(
                OLLAMA_URL,
                json={
                    "model": model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {"temperature": 0.6, "num_ctx": 4096, "top_p": 0.9},
                },
                timeout=DEFAULT_TIMEOUT,
            )
            response.raise_for_status()
            nuevo = limpiar_salida(response.json()["response"])
            if not nuevo:
                log.warning("Respuesta vacía en intento %d/%d", intento, intentos)
                continue
            # Salvaguarda: no debe perder ni inventar cifras/fechas.
            if _numeros(nuevo) != _numeros(texto):
                log.warning("Intento %d/%d: las cifras/fechas no coinciden "
                            "con el original, descarto la respuesta.",
                            intento, intentos)
                continue
            return nuevo, True
        except requests.exceptions.Timeout:
            log.warning("Timeout en intento %d/%d", intento, intentos)
        except Exception as e:
            log.warning("Error en intento %d/%d: %s", intento, intentos, e)

    log.error("Falló tras %d intentos, dejo el texto original", intentos)
    return texto, False


# --------------------------------------------------------------------------
# Documento
# --------------------------------------------------------------------------
def iter_paragraphs_incl_tables(doc: Document):
    """Recorre párrafos del cuerpo Y de celdas de tabla (CVs suelen usar
    tablas para maquetar experiencia/columnas)."""
    for para in doc.paragraphs:
        yield para
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                for para in cell.paragraphs:
                    yield para


def _bold_efectivo(run, para) -> bool:
    """Negrita real de un run: directa, o heredada del estilo de carácter /
    de párrafo (siguiendo la cadena de estilos base)."""
    if run.bold is not None:
        return run.bold
    for st in (run.style, para.style):
        while st is not None:
            if st.font.bold is not None:
                return st.font.bold
            st = st.base_style
    return False


def _es_etiqueta(run, para, estilos_etiqueta: set) -> bool:
    nombre = ((run.style.name if run.style is not None else "") or "").lower()
    return nombre in estilos_etiqueta or _bold_efectivo(run, para)


def dividir_etiqueta(para, estilos_etiqueta: set = frozenset()) -> tuple[list, list]:
    """Separa los runs iniciales que son 'etiqueta' (negrita directa o
    heredada de un estilo, o estilo de carácter listado en estilos_etiqueta)
    del resto.

    Devuelve (runs_etiqueta, runs_cuerpo). Si el párrafo no empieza con
    etiqueta, runs_etiqueta queda vacío y todo el párrafo es cuerpo.
    """
    runs = para.runs
    n = 0
    hay_etiqueta = False
    while n < len(runs):
        r = runs[n]
        if r.text.strip() and _es_etiqueta(r, para, estilos_etiqueta):
            hay_etiqueta = True
        elif r.text.strip():          # run con texto que no es etiqueta: termina
            break
        n += 1                        # runs vacíos o solo espacios se absorben
    if not hay_etiqueta:
        return [], runs
    return runs[:n], runs[n:]


def aplicar_texto(para, runs_cuerpo: list, nuevo: str, color_resaltado) -> None:
    """Escribe `nuevo` en el primer run del cuerpo (conserva su formato) y
    vacía el resto. Los runs de la etiqueta no se tocan.
    color_resaltado: un WD_COLOR_INDEX para marcar el cambio, o None.
    Resalta el párrafo COMPLETO — usado solo con --resaltado-linea-completa;
    por default se usa aplicar_texto_diff, que resalta nada más lo que
    cambió."""
    if runs_cuerpo:
        runs_cuerpo[0].text = nuevo
        for r in runs_cuerpo[1:]:
            r.text = ""
        objetivo = runs_cuerpo[0]
    else:
        para.text = nuevo
        objetivo = para.runs[0]
    if color_resaltado is not None:
        objetivo.font.highlight_color = color_resaltado


# --------------------------------------------------------------------------
# Diff a nivel de palabra: resaltar solo lo que cambió, no la línea entera
# --------------------------------------------------------------------------
def _tokenizar(s: str) -> list[str]:
    """Separa en palabras y espacios, cada uno como su propio token, para
    poder reconstruir el texto exacto y comparar palabra por palabra (no
    carácter por carácter, que sería demasiado ruidoso: cambiar "reduje" por
    "reducí" no debe marcar la palabra completa desde la primera letra)."""
    return re.findall(r"\S+|\s+", s)


def _segmentos_diff(original: str, nuevo: str) -> list[tuple[str, bool]]:
    """Compara `original` contra `nuevo` palabra por palabra y devuelve el
    texto de `nuevo` partido en [(texto, cambio), ...], donde cambio=True
    marca los tramos que NO estaban así en el original (para resaltar solo
    esos). Los tramos iguales (nombres de empresa, herramientas, cifras que
    sobrevivieron intactas) salen con cambio=False."""
    tok_orig = _tokenizar(original)
    tok_nuevo = _tokenizar(nuevo)
    sm = difflib.SequenceMatcher(None, tok_orig, tok_nuevo, autojunk=False)
    segmentos = []
    for tag, _i1, _i2, j1, j2 in sm.get_opcodes():
        if j1 == j2:
            continue  # tramo que solo borra texto del original: nada que escribir
        segmentos.append(("".join(tok_nuevo[j1:j2]), tag != "equal"))
    return segmentos


def _clonar_formato(run_base, run_nuevo) -> None:
    """Copia el formato de caracter relevante de run_base a run_nuevo, para
    que los runs nuevos (creados por el diff) se vean igual que el resto
    del párrafo."""
    run_nuevo.bold = run_base.bold
    run_nuevo.italic = run_base.italic
    run_nuevo.underline = run_base.underline
    if run_base.font.size is not None:
        run_nuevo.font.size = run_base.font.size
    if run_base.font.name is not None:
        run_nuevo.font.name = run_base.font.name
    try:
        if run_base.font.color and run_base.font.color.rgb is not None:
            run_nuevo.font.color.rgb = run_base.font.color.rgb
    except AttributeError:
        pass  # color heredado del tema, sin rgb explícito: no hay nada que copiar


def aplicar_texto_diff(para, runs_cuerpo: list, original: str, nuevo: str,
                        color_resaltado) -> None:
    """Como aplicar_texto, pero en vez de resaltar el párrafo completo,
    parte el nuevo texto en varios runs y resalta SOLO los tramos que
    cambiaron respecto a `original` (diff palabra por palabra). Los runs
    de la etiqueta no se tocan."""
    run_base = runs_cuerpo[0] if runs_cuerpo else (para.runs[0] if para.runs else None)
    segmentos = _segmentos_diff(original, nuevo)

    for r in runs_cuerpo:
        r.text = ""

    if not segmentos:
        return

    primero = True
    for texto, cambio in segmentos:
        if not texto:
            continue
        if primero and runs_cuerpo:
            run = runs_cuerpo[0]
            run.text = texto
            primero = False
        else:
            run = para.add_run(texto)
            if run_base is not None:
                _clonar_formato(run_base, run)
        run.font.highlight_color = color_resaltado if (cambio and color_resaltado is not None) else None


def _estilo(para) -> str:
    return ((para.style.name if para.style is not None else "") or "").lower()


def _lista(valor: str) -> set:
    return {x.strip().lower() for x in valor.split(",") if x.strip()}


def listar_estilos(entrada: Path) -> None:
    """Muestra los estilos de párrafo y de carácter usados en el documento."""
    doc = Document(str(entrada))
    parrafos = [p for p in iter_paragraphs_incl_tables(doc) if p.text.strip()]

    por_estilo: dict[str, list] = {}
    car: Counter = Counter()
    for p in parrafos:
        nombre = (p.style.name if p.style is not None else "(sin estilo)")
        por_estilo.setdefault(nombre, []).append(p)
        for r in p.runs:
            if r.style is not None and r.text.strip() \
                    and r.style.name != "Default Paragraph Font":
                car[r.style.name] += 1

    print("\nESTILOS DE PÁRRAFO (nombre · nº párrafos · long. media · ejemplo)")
    for nombre, ps in sorted(por_estilo.items(), key=lambda kv: -len(kv[1])):
        media = sum(len(p.text) for p in ps) // len(ps)
        ejemplo = ps[0].text.strip().replace("\n", " ")[:55]
        print(f"  {nombre!r:32} {len(ps):4}  {media:4}  {ejemplo}...")

    print("\nESTILOS DE CARÁCTER (nombre · nº runs)")
    if car:
        for nombre, n in car.most_common():
            print(f"  {nombre!r:32} {n:4}")
    else:
        print("  (ninguno)")
    print("\nUsa los nombres tal cual en --solo-estilos, --omitir-estilos "
          "o --estilos-etiqueta (separados por comas).\n")


def procesar_docx(entrada: Path, salida: Path, model: str, min_chars: int,
                  dry_run: bool, color_resaltado, proteger_etiqueta: bool,
                  solo_estilos: set = frozenset(), omitir_estilos: set = frozenset(),
                  estilos_etiqueta: set = frozenset(),
                  resaltado_linea_completa: bool = False) -> None:
    doc = Document(str(entrada))
    parrafos = list(iter_paragraphs_incl_tables(doc))
    total = len(parrafos)
    fallos = 0
    cambiados = 0

    for i, para in enumerate(parrafos, start=1):
        if not para.text.strip():
            continue

        est = _estilo(para)
        if solo_estilos and est not in solo_estilos:
            continue
        if est in omitir_estilos:
            continue

        if proteger_etiqueta:
            runs_etiqueta, runs_cuerpo = dividir_etiqueta(para, estilos_etiqueta)
        else:
            runs_etiqueta, runs_cuerpo = [], list(para.runs)

        cuerpo = "".join(r.text for r in runs_cuerpo)
        sep = ""
        if runs_etiqueta:
            m = SEPARADOR_RE.match(cuerpo)
            sep = m.group(0) if m else ""
        texto = cuerpo[len(sep):].strip()

        # Párrafo entero en negrita (título, empresa, fechas): no se toca.
        if runs_etiqueta and not texto:
            continue
        if len(texto) < min_chars or not texto:
            continue

        etiqueta_txt = "".join(r.text for r in runs_etiqueta).strip()
        prefijo = f"[{etiqueta_txt}] " if etiqueta_txt else ""
        log.info("[%d/%d] %s%s...", i, total, prefijo, texto[:70])

        if dry_run:
            continue

        nuevo_texto, ok = mejorar_texto(texto, model)
        if not ok:
            fallos += 1
            continue                  # texto original intacto, sin subrayar
        if nuevo_texto == texto:
            continue

        if resaltado_linea_completa:
            aplicar_texto(para, runs_cuerpo, sep + nuevo_texto, color_resaltado)
        else:
            aplicar_texto_diff(para, runs_cuerpo, cuerpo, sep + nuevo_texto, color_resaltado)
        cambiados += 1

        if i % 5 == 0:
            doc.save(str(salida))
            log.info("Progreso guardado (%d/%d)", i, total)

    if not dry_run:
        doc.save(str(salida))
        log.info("Guardado final en: %s (%d párrafo(s) reescritos)",
                 salida, cambiados)

    if fallos:
        log.warning("%d párrafo(s) no se pudieron reescribir y quedaron "
                    "con el texto original.", fallos)


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("entrada", type=Path, help="CV de entrada (.docx)")
    parser.add_argument("salida", type=Path, nargs="?",
                        help="CV de salida (.docx)")
    parser.add_argument("--model", default=DEFAULT_MODEL,
                        help=f"Modelo Ollama a usar (default: {DEFAULT_MODEL})")
    parser.add_argument("--min-chars", type=int, default=40,
                        help="Longitud mínima del texto a reescribir (default: 40)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Solo muestra qué párrafos se procesarían, sin llamar al modelo")
    parser.add_argument("--sin-resaltado", action="store_true",
                        help="No resaltar los párrafos reescritos")
    parser.add_argument("--resaltado-linea-completa", action="store_true",
                        help="Resaltar el párrafo reescrito COMPLETO en vez de "
                             "solo las palabras/frases que cambiaron (default: "
                             "resalta solo lo que cambió)")
    parser.add_argument("--color-resaltado", default="amarillo",
                        choices=sorted(COLORES_RESALTADO),
                        help="Color de resaltado para revisar cambios "
                             "(default: amarillo)")
    parser.add_argument("--sin-proteger-etiqueta", action="store_true",
                        help="Reescribir el párrafo completo, incluida la parte "
                             "inicial en negrita")
    parser.add_argument("--listar-estilos", action="store_true",
                        help="Lista los estilos de Word usados en el CV y termina")
    parser.add_argument("--solo-estilos", default="",
                        help="Reescribir SOLO párrafos con estos estilos "
                             "(nombres separados por comas)")
    parser.add_argument("--omitir-estilos", default="",
                        help="No tocar párrafos con estos estilos "
                             "(p. ej. títulos, fechas)")
    parser.add_argument("--estilos-etiqueta", default="",
                        help="Estilos de CARÁCTER que cuentan como etiqueta "
                             "protegida (además de la negrita)")
    args = parser.parse_args()

    if not args.entrada.exists():
        log.error("No existe el archivo de entrada: %s", args.entrada)
        sys.exit(1)

    if args.listar_estilos:
        listar_estilos(args.entrada)
        return

    if args.salida is None:
        parser.error("falta el archivo de salida (o usa --listar-estilos)")

    if not args.dry_run and not check_model_available(args.model):
        log.warning("El modelo '%s' no aparece en 'ollama list'. "
                    "Bájalo con: ollama pull %s", args.model, args.model)

    color_resaltado = None if args.sin_resaltado else COLORES_RESALTADO[args.color_resaltado]
    procesar_docx(args.entrada, args.salida, args.model, args.min_chars,
                  args.dry_run,
                  color_resaltado=color_resaltado,
                  proteger_etiqueta=not args.sin_proteger_etiqueta,
                  solo_estilos=_lista(args.solo_estilos),
                  omitir_estilos=_lista(args.omitir_estilos),
                  estilos_etiqueta=_lista(args.estilos_etiqueta),
                  resaltado_linea_completa=args.resaltado_linea_completa)

    # Copiar automáticamente a Google Drive
    if not args.dry_run:
        import shutil
        destino_drive = Path("/mnt/g/Mi unidad/(1) CV/humano")
        try:
            destino_drive.mkdir(parents=True, exist_ok=True)
            shutil.copy(args.salida, destino_drive / args.salida.name)
            log.info("📤 Copiado a Drive: %s", destino_drive / args.salida.name)
        except Exception as e:
            log.warning("No se pudo copiar a Drive: %s", e)


if __name__ == "__main__":
    main()
