import unittest

from affinity import buscar_afinidad


class AffinityJsonTest(unittest.TestCase):
    def setUp(self):
        self.kb = {"chunks": [{"id": "c1", "text": "Power BI, Excel and Power Query",
                               "section": "SKILLS"}]}
        self.banco = {
            "_meta": {"version": 2},
            "power bi": {"requiere_alguno": ["Power BI"], "aliases_jd": ["PowerBI"],
                         "evidence_ids": ["c1"], "nivel": "mencion", "source": "cv",
                         "relacionados": ["excel"]},
            "excel": {"requiere_alguno": ["Excel"], "aliases_jd": [],
                      "evidence_ids": ["c1"], "nivel": "mencion", "source": "cv",
                      "relacionados": ["power bi"]},
            "dashboards": {"source": "usuario", "frase": "He creado dashboards.",
                           "aliases_jd": ["tableros"], "evidence_ids": []},
            "business intelligence": {"tipo": "capacidad", "aliases_jd": ["BI"],
                                      "fuentes": ["power bi", "excel", "dashboards"],
                                      "minimo": 2},
            "dashboard development": {"tipo": "capacidad", "aliases_jd": ["tableros interactivos"],
                           "fuentes": ["power bi"], "minimo": 1,
                           "transferible": True, "peso_transferible": 0.55},
        }

    def test_directo_y_usuario(self):
        self.assertEqual(buscar_afinidad("PowerBI", self.banco, self.kb)
                         ["evidence_ids"], ["c1"])
        r = buscar_afinidad("tableros", self.banco, self.kb)
        self.assertEqual(r["classification"], "VERIFIED")
        self.assertEqual(r["evidence_user"], ["He creado dashboards."])

    def test_capacidad_y_compuesto(self):
        self.assertEqual(buscar_afinidad("BI", self.banco, self.kb)
                         ["classification"], "VERIFIED")
        r = buscar_afinidad("advanced Excel dashboards", self.banco, self.kb)
        self.assertEqual(r["classification"], "PARTIAL")
        self.assertGreater(r["affinity_score"], 0.5)
        self.assertEqual(buscar_afinidad("tableros interactivos", self.banco, self.kb)
                         ["affinity_score"], 0.55)

    def test_relacion_y_evidencia_invalida(self):
        del self.banco["power bi"]
        self.assertEqual(buscar_afinidad("Power BI", self.banco, self.kb)
                         ["classification"], "PARTIAL")
        self.banco["excel"]["evidence_ids"] = ["otro_cv"]
        self.assertIsNone(buscar_afinidad("Power BI", self.banco, self.kb))

    def test_sql_y_data_science_desde_trabajo_geoespacial(self):
        kb = {"chunks": [
            {"id": "sql", "text": "SQL, PostgreSQL and PostGIS"},
            {"id": "trabajo", "text": "Built PostgreSQL/PostGIS reports and ETL pipelines with Python"},
            {"id": "modelo", "text": "Applied semantic segmentation to satellite imagery"},
        ]}
        banco = {
            "sql": {"requiere_alguno": ["SQL"], "evidence_ids": ["sql"],
                    "source": "cv", "soporte_complementario": ["postgresql", "postgis"]},
            "postgresql": {"requiere_alguno": ["PostgreSQL"],
                           "evidence_ids": ["trabajo"], "nivel": "experiencia"},
            "postgis": {"requiere_alguno": ["PostGIS"],
                        "evidence_ids": ["trabajo"], "nivel": "experiencia"},
            "python": {"requiere_alguno": ["Python"],
                       "evidence_ids": ["trabajo"], "nivel": "experiencia"},
            "etl": {"requiere_alguno": ["ETL"],
                    "evidence_ids": ["trabajo"], "nivel": "experiencia"},
            "semantic segmentation": {"requiere_alguno": ["semantic segmentation"],
                                      "evidence_ids": ["modelo"], "nivel": "experiencia"},
            "data science": {"tipo": "capacidad", "aliases_jd": ["data scientist"],
                             "fuentes": ["python", "etl", "semantic segmentation"],
                             "minimo": 3},
            "data analytics": {"tipo": "capacidad", "aliases_jd": ["data analyst",
                                 "analisis de datos"], "fuentes": ["python", "etl"],
                               "minimo": 2},
            "sql analytics": {"tipo": "capacidad", "aliases_jd": ["sql para analisis de datos"],
                              "fuentes": ["sql", "postgresql", "postgis"],
                              "minimo": 2},
        }
        sql = buscar_afinidad("SQL", banco, kb)
        self.assertEqual(sql["supporting_concepts"], ["sql", "postgresql", "postgis"])
        self.assertEqual(buscar_afinidad("data scientist", banco, kb)
                         ["classification"], "VERIFIED")
        self.assertEqual(buscar_afinidad("data analyst", banco, kb)
                         ["classification"], "VERIFIED")
        self.assertEqual(buscar_afinidad("SQL para análisis de datos", banco, kb)
                         ["supporting_concepts"], ["sql", "postgresql", "postgis"])


if __name__ == "__main__":
    unittest.main()
