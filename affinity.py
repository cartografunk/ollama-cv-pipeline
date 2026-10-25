"""Lee la taxonomía editable de afinidad.json y explica cada coincidencia.

Los nombres, alias, familias y relaciones viven en JSON. Este módulo solo
valida evidencia y aplica las reglas declaradas allí.
"""

import re
import unicodedata
from typing import Optional


def normalizar(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", texto.casefold())
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return " ".join(texto.split())


def menciona(texto: str, termino: str) -> bool:
    termino = normalizar(termino)
    texto = normalizar(texto)
    return re.search(r"(?<![a-z0-9])" + re.escape(termino) +
                     r"(?![a-z0-9])", texto) is not None


def buscar_afinidad(item: str, banco: dict, kb: dict) -> Optional[dict]:
    """Evalúa un requisito contra la taxonomía curada y evidencia disponible."""
    item_normalizado = normalizar(item)
    chunks_kb = {c["id"]: c for c in kb["chunks"]}
    conceptos = {k: v for k, v in banco.items()
                 if not k.startswith("_") and isinstance(v, dict)}

    def soporte(clave: str) -> Optional[dict]:
        entrada = conceptos.get(clave)
        if not entrada or entrada.get("tipo") == "capacidad":
            return None
        if entrada.get("source") == "usuario":
            frase = entrada.get("frase", "").strip()
            if not frase:
                return None
            return {"concepto": clave, "ids": [], "usuario": frase,
                    "nivel": "confirmado_por_usuario"}
        terminos = entrada.get("requiere_alguno", [clave])
        ids = [i for i in entrada.get("evidence_ids", [])
               if i in chunks_kb and any(menciona(chunks_kb[i]["text"], t)
                                         for t in terminos)]
        if not ids:
            return None
        return {"concepto": clave, "ids": ids,
                "usuario": entrada.get("confirmacion_usuario"),
                "nivel": entrada.get("nivel", "mencion")}

    def resultado(estado: str, via: str, apoyos: list, razon: str,
                  peso: float = 1.0) -> dict:
        ids = list(dict.fromkeys(i for a in apoyos for i in a["ids"]))
        usuario = list(dict.fromkeys(a["usuario"] for a in apoyos if a["usuario"]))
        return {"item": item, "classification": estado, "evidence_ids": ids,
                "evidence_user": usuario,
                "supporting_concepts": [a["concepto"] for a in apoyos],
                "rationale": razon, "affinity_score": peso, "via": via}

    # Una herramienta o experiencia declarada directamente.
    for clave, entrada in conceptos.items():
        if entrada.get("tipo") == "capacidad":
            continue
        nombres = [clave] + entrada.get("aliases_jd", [])
        if item_normalizado not in (normalizar(n) for n in nombres):
            continue
        apoyo = soporte(clave)
        if apoyo:
            complementos = [a for c in entrada.get("soporte_complementario", [])
                            if (a := soporte(c))]
            apoyos = [apoyo] + complementos
            origen = "CV y declaración del candidato" if apoyo["usuario"] else "CV"
            return resultado("VERIFIED", "afinidad_directa", apoyos,
                             f"{clave} respaldado por {origen}: " +
                             ", ".join(a["concepto"] for a in apoyos))

    # Una capacidad reúne varias herramientas o experiencias afines.
    candidatos = []
    for familia, regla in conceptos.items():
        if regla.get("tipo") != "capacidad":
            continue
        if (any(menciona(item, x) for x in regla.get("excluir_si", []))
                and not menciona(item, familia)):
            continue
        nombres = [familia] + regla.get("aliases_jd", [])
        if not any(menciona(item, n) for n in nombres):
            continue
        apoyos = [a for c in regla.get("fuentes", []) if (a := soporte(c))]
        if len(apoyos) < regla.get("minimo", 1):
            continue
        fuerte = any(a["nivel"] in {"experiencia", "confirmado_por_usuario"}
                     for a in apoyos)
        exacto = item_normalizado in (normalizar(n) for n in nombres)
        verificado = exacto and fuerte and not regla.get("transferible", False)
        estado = "VERIFIED" if verificado else "PARTIAL"
        peso = 1.0 if verificado else (0.85 if fuerte else 0.7)
        if regla.get("transferible", False):
            peso = min(peso, regla.get("peso_transferible", 0.7))
        longitud = max(len(normalizar(n)) for n in nombres if menciona(item, n))
        candidatos.append(((exacto, longitud, len(apoyos)),
                           resultado(estado, "afinidad_capacidad", apoyos,
                                     "Capacidad respaldada por " +
                                     ", ".join(a["concepto"] for a in apoyos), peso)))
    if candidatos:
        return max(candidatos, key=lambda c: c[0])[1]

    # Una frase compuesta contiene herramientas conocidas más un modificador.
    apoyos = []
    for clave, entrada in conceptos.items():
        if entrada.get("tipo") == "capacidad" or len(clave) < 2:
            continue
        nombres = [clave] + entrada.get("aliases_jd", [])
        if any(menciona(item, n) for n in nombres):
            apoyo = soporte(clave)
            if apoyo:
                apoyos.append(apoyo)
    if apoyos:
        fuerte = any(a["nivel"] in {"experiencia", "confirmado_por_usuario"}
                     for a in apoyos)
        peso = 0.85 if fuerte or len(apoyos) > 1 else 0.7
        return resultado("PARTIAL", "afinidad_compuesta", apoyos,
                         "Requisito compuesto relacionado con " +
                         ", ".join(a["concepto"] for a in apoyos), peso)

    # Relación entre herramientas diferentes, definida en el JSON.
    for origen, entrada in conceptos.items():
        if entrada.get("tipo") == "capacidad":
            continue
        apoyo = soporte(origen)
        if not apoyo:
            continue
        for destino in entrada.get("relacionados", []):
            nombres = [destino] + conceptos.get(destino, {}).get("aliases_jd", [])
            if item_normalizado in (normalizar(n) for n in nombres):
                return resultado("PARTIAL", "afinidad_relacionada", [apoyo],
                                 f"{origen} aporta habilidades transferibles hacia {destino}",
                                 0.75)
    return None
