"""
embeddings.py — Cliente de embeddings locales vía Ollama + utilidades de
similaridad. Sin numpy: vectores como listas de float, todo en Python puro
para minimizar dependencias (mismo criterio que match.py/humanizar.py: solo
`requests` habla con Ollama).

Modelo por defecto: nomic-embed-text
    ollama pull nomic-embed-text
"""

import math

import requests

OLLAMA_EMBED_URL = "http://localhost:11434/api/embeddings"
EMBED_MODEL = "nomic-embed-text"
TIMEOUT = 120


def embed_text(texto: str, model: str = EMBED_MODEL) -> list:
    """Pide el embedding de un texto a Ollama. Lanza RuntimeError con un
    mensaje claro si Ollama no responde o el modelo no está disponible,
    para que quien llama decida cómo manejarlo (match.py/rag_match.py
    hacen sys.exit; los tests pueden atraparlo)."""
    try:
        resp = requests.post(
            OLLAMA_EMBED_URL,
            json={"model": model, "prompt": texto},
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
    except requests.exceptions.ConnectionError:
        raise RuntimeError(
            "No se pudo conectar con Ollama en http://localhost:11434. "
            "¿Corriste 'ollama serve'?"
        )
    except requests.exceptions.HTTPError as e:
        raise RuntimeError(
            f"Error de Ollama al generar embedding (¿corriste "
            f"'ollama pull {model}'?): {e}"
        )

    data = resp.json()
    vector = data.get("embedding")
    if not vector:
        raise RuntimeError(f"Ollama no devolvió 'embedding' para el modelo {model!r}.")
    return vector


def embed_batch(textos: list, model: str = EMBED_MODEL, on_progress=None) -> list:
    """Embebe una lista de textos uno por uno (la API /api/embeddings de
    Ollama no soporta batch en todas las versiones instaladas).
    on_progress(i, total) es opcional, para reportar avance en scripts
    largos como build_kb.py."""
    vectores = []
    total = len(textos)
    for i, t in enumerate(textos, start=1):
        vectores.append(embed_text(t, model))
        if on_progress:
            on_progress(i, total)
    return vectores


def cosine_similarity(a: list, b: list) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)
