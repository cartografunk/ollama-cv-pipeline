# Afinidad CV ↔ vacante

`afinidad.json` es el **banco editable** de habilidades y capacidades. Fue
curado a partir del CV, equivalencias técnicas y experiencia que el candidato
confirmó. Puedes ampliarlo directamente; no necesitas generar otro archivo
ni ejecutar un script después de editarlo. `rag_match.py` lo lee en cada uso.

## Cómo leer una entrada

```json
"power bi": {
  "requiere_alguno": ["Power BI"],
  "aliases_jd": ["Microsoft Power BI", "PowerBI"],
  "evidence_ids": ["chunk_0087"],
  "nivel": "mencion",
  "source": "cv",
  "relacionados": ["excel", "power query"]
}
```

- `aliases_jd`: formas en que una vacante puede nombrar la misma habilidad.
- `evidence_ids`: fragmentos del CV en `kb_index.json` que la sustentan.
- `relacionados`: habilidades transferibles; aportan afinidad parcial si falta
  la herramienta exacta.
- `source: "usuario"` y `frase`: experiencia que confirmaste pero no aparece
  todavía en el CV basal. En el resultado se muestra como `evidence_user`.
- `confirmacion_usuario`: experiencia adicional confirmada para una herramienta
  que también figura en el CV, como tus dashboards en Power BI y Excel.
- `soporte_complementario`: trabajo concreto que refuerza una habilidad listada.
  Por ejemplo, SQL se muestra junto a la experiencia con PostgreSQL/PostGIS.

Una capacidad amplia puede reunir varios conceptos:

```json
"business intelligence": {
  "tipo": "capacidad",
  "aliases_jd": ["BI", "inteligencia de negocios"],
  "fuentes": ["power bi", "excel", "power query", "dashboards"],
  "minimo": 2
}
```

El matcher distingue coincidencia directa, capacidad amplia, requisito
compuesto y habilidad transferible. Guarda la ruta y el score de cada relación
para que puedas revisar cómo llegó al resultado.

La capacidad `data science` reúne análisis de datos, Python, ETL, análisis
geoespacial y modelado; reconoce también vacantes que dicen *data scientist* o
*científico de datos*. `data analytics` reconoce *data analyst* y *analista de
datos*. Para SQL, la evidencia de PostgreSQL/PostGIS refuerza la habilidad:
GIS por sí solo no activa una coincidencia de SQL.

## Uso

```bash
cd /home/cartografunk/ollama-cv-pipeline
python3 rag_match.py jds/Alesso.txt
python3 -m unittest discover -s tests -v
```

Si actualizas el CV, regenera `cv_perfil.json` y `kb_index.json` con los scripts
existentes y revisa los `evidence_ids` del banco. El matcher verifica que cada
ID siga apuntando a un fragmento que contiene el concepto antes de usarlo.
`afinidad.json`, el CV y los índices personales están excluidos de Git.

Los otros pasos son: `extract_cv.py` extrae el Word, `build_kb.py` crea el
índice, `match.py` compara literalmente, `rag_match.py` evalúa afinidad y
evidencia, `gap_closure.py` propone cierres, `cv_rewriter.py` adapta el Word y
`humanizar.py` ajusta su estilo.
