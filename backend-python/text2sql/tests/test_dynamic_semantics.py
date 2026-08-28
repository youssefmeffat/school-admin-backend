import os
import sqlite3
import tempfile
import unittest

from text2sql.config import PipelineConfig
from text2sql.pipeline import Text2SQLPipeline


class FakeBackend:
    """Deterministic test backend; no external model download is required."""

    def __init__(self):
        self.calls = 0

    def warm_up(self):
        pass

    def generate(self, prompt, max_new_tokens=220, temperature=0.1):
        self.calls += 1

        if "Respond with ONLY one JSON object" in prompt:
            return """
            {
              "table": {
                "semantic_name": "Employees",
                "synonyms": ["workers", "staff", "personnel"],
                "business_meaning": "People employed by the organization",
                "confidence": 98
              },
              "columns": [
                {
                  "column": "id",
                  "semantic_name": "Employee ID",
                  "synonyms": ["employee number", "worker id"],
                  "business_meaning": "Unique employee identifier",
                  "units": null,
                  "data_category": "Identifier",
                  "confidence": 99
                },
                {
                  "column": "xx",
                  "semantic_name": "Salary",
                  "synonyms": ["salary", "pay", "income", "compensation"],
                  "business_meaning": "Employee monetary compensation",
                  "units": "USD",
                  "data_category": "Financial",
                  "confidence": 99
                }
              ]
            }
            """

        if "how many workers" in prompt.lower():
            return "SELECT COUNT(*) AS row_count FROM EmployeeData;"

        if "highest salary" in prompt.lower():
            return (
                "SELECT id, xx FROM EmployeeData "
                "ORDER BY xx DESC LIMIT 1;"
            )

        return "SELECT COUNT(*) AS row_count FROM EmployeeData;"


class DynamicSemanticTests(unittest.TestCase):
    def setUp(self):
        fd, self.db = tempfile.mkstemp(suffix=".db")
        os.close(fd)

        conn = sqlite3.connect(self.db)
        conn.execute(
            "CREATE TABLE EmployeeData "
            "(id INTEGER PRIMARY KEY, xx TEXT, Department TEXT)"
        )
        conn.executemany(
            "INSERT INTO EmployeeData VALUES (?, ?, ?)",
            [
                (1, "$5000", "Engineering"),
                (2, "$7000", "Sales"),
                (3, "$9000", "Engineering"),
            ],
        )
        conn.commit()
        conn.close()

        self.backend = FakeBackend()
        config = PipelineConfig(
            connection_string=f"sqlite:///{self.db}",
            retrieval_strategy="keyword",
            embedding_model=None,
            semantic_embedding_model=None,
            llm_max_new_tokens=64,
            enable_semantic_learning=True,
        )
        self.pipeline = Text2SQLPipeline(
            config,
            llm_backend=self.backend,
        )

    def tearDown(self):
        try:
            self.pipeline.close()
        except Exception:
            pass
        try:
            os.remove(self.db)
        except OSError:
            pass

    def test_worker_concept_is_learned(self):
        result = self.pipeline.ask("how many workers")
        self.assertEqual(
            int(result.dataframe.iloc[0, 0]),
            3,
        )
        self.assertIn("Employees", self.pipeline.semantic_schema["EmployeeData"].semantic_name)
        self.assertIn("workers", self.pipeline.semantic_schema["EmployeeData"].synonyms)

    def test_currency_column_can_be_learned_as_salary(self):
        result = self.pipeline.ask("who has highest salary")
        self.assertEqual(
            result.sql,
            "SELECT id, xx FROM EmployeeData ORDER BY xx DESC LIMIT 1;",
        )
        self.assertEqual(
            self.pipeline.semantic_schema["EmployeeData"].columns["xx"].semantic_name,
            "Salary",
        )

    def test_user_teaching_is_schema_scoped(self):
        result = self.pipeline.ask(
            "when I say workers, I mean employees"
        )
        self.assertTrue(result.success)
        self.assertIn(
            "workers",
            self.pipeline.user_memory.build_hint("how many workers"),
        )

    def test_repeated_query_uses_cache(self):
        first = self.pipeline.ask("how many workers")
        calls_after_first = self.backend.calls
        second = self.pipeline.ask("how many workers")

        self.assertTrue(second.from_cache)
        self.assertEqual(self.backend.calls, calls_after_first)


if __name__ == "__main__":
    unittest.main()
