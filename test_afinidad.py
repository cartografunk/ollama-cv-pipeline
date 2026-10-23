"""
test_afinidad.py — Pruebas de la lista de afinidad con un CV sintético (sin
datos personales, sin Ollama).

    python -m unittest test_afinidad -v
"""

import unittest

from afinidad import buscar_afinidad, contiene_termino, tokens
from build_afinidad import construir
from rag_match import evaluar_requisito

KB = {"model": "x", "chunks": [
    {"id": "chunk_0001", "text": "PyTorch, TensorFlow/Keras, OpenCV, and Sci-Kit Learn", "section": "S"},
    {"id": "chunk_0002", "text": "Power BI, Excel/Power Query, Plotly", "section": "S"},
    {"id": "chunk_0003", "text": "QGIS, PostGIS, GDAL and PostgreSQL for spatial reports", "section": "S"},
    {"id": "chunk_0004", "text": "Strong communication and excellence in teamwork", "section": "S"},
]}

SEED = {
    "machine learning": {"requiere_alguno": ["scikit-learn", "pytorch"], "frase": "ML frase"},
    "power bi": {"alias_requisito": ["powerbi"], "requiere_alguno": ["power bi"],
                 "relacionado": ["excel"], "frase": "PBI frase"},
    "power bi mapas": {"alias_requisito": ["power bi maps"],
                       "requiere_juntos": [["power bi"], ["map", "maps", "spatial", "gis"]],
                       "relacionado": ["postgis"], "frase": "PBI mapas frase", "si_falta": "agrega bullet"},
    "excel": {"requiere_alguno": ["excel"], "frase": "Excel frase"},
    "tableau": {"requiere_alguno": ["tableau"], "relacionado": ["power bi"], "frase": "T frase"},
    "kubernetes": {"requiere_alguno": ["kubernetes"], "frase": "K frase"},
    "sql": {"requiere_alguno": ["sql"], "frase": "SQL frase"},
}


class TestTerminos(unittest.TestCase):
    def test_variantes_de_escritura(self):
        toks = tokens("Sci-Kit Learn")
        self.assertTrue(contiene_termino("scikit-learn", toks))
        self.assertTrue(contiene_termino("scikit", toks))
        self.assertTrue(contiene_termino("PowerBI", tokens("Power BI, Excel")))
        self.assertTrue(contiene_termino("power bi", tokens("PowerBI dashboards")))

    def test_token_completo(self):
        self.assertFalse(contiene_termino("excel", tokens("excellence in teamwork")))
        self.assertFalse(contiene_termino("sql", tokens("PostgreSQL")))


class TestBuild(unittest.TestCase):
    def setUp(self):
        self.af = construir(KB, SEED)

    def test_directo_con_evidencia_y_frase(self):
        ml = self.af["machine learning"]
        self.assertEqual(ml["nivel"], "directo")
        self.assertEqual(ml["evidence_ids"], ["chunk_0001"])
        self.assertEqual(ml["frase"], "ML frase")

    def test_excel_no_coincide_con_excellence(self):
        self.assertEqual(self.af["excel"]["evidence_ids"], ["chunk_0002"])

    def test_juntos_exige_mismo_chunk(self):
        # Power BI está en el chunk 2 y 'spatial/gis' en el 3: NO es lo mismo.
        pm = self.af["power bi mapas"]
        self.assertEqual(pm["nivel"], "relacionado")
        self.assertFalse(pm["soportado"])
        self.assertIsNone(pm["frase"])
        self.assertIn("si_falta", pm)

    def test_relacionado_y_ninguno(self):
        self.assertEqual(self.af["tableau"]["nivel"], "relacionado")
        self.assertEqual(self.af["kubernetes"]["nivel"], "ninguno")
        self.assertIsNone(self.af["kubernetes"]["frase"])

    def test_sql_no_se_infla_desde_postgresql(self):
        self.assertEqual(self.af["sql"]["nivel"], "ninguno")


class TestRagMatch(unittest.TestCase):
    def setUp(self):
        self.af = construir(KB, SEED)

    def ev(self, item):
        # texto_cv vacío: aísla el paso de afinidad (no hay match literal)
        return evaluar_requisito(item, "", KB, 5, 0.15, afinidad=self.af)

    def test_directo_es_verified(self):
        r = self.ev("Experience with PowerBI")
        self.assertEqual((r["classification"], r["via"]), ("VERIFIED", "afinidad"))
        self.assertEqual(r["evidence_ids"], ["chunk_0002"])

    def test_relacionado_es_partial(self):
        r = self.ev("Tableau")
        self.assertEqual((r["classification"], r["via"]), ("PARTIAL", "afinidad"))

    def test_concepto_sin_respaldo_no_afirma(self):
        self.assertIsNone(buscar_afinidad("Kubernetes", self.af))

    def test_requisito_con_extras_no_se_verifica(self):
        # 'Advanced Power BI modeling' no es exactamente el concepto 'power bi'
        self.assertIsNone(buscar_afinidad("Advanced Power BI modeling", self.af))


if __name__ == "__main__":
    unittest.main()
