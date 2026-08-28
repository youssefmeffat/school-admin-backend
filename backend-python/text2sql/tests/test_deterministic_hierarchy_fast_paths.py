"""End-to-end regression tests for deterministic fast paths.

These cases must not invoke the LLM when the schema contains enough structural
information to answer them directly.
"""

import os
import sqlite3
import tempfile
import unittest

from text2sql.config import PipelineConfig
from text2sql.pipeline import Text2SQLPipeline


class _Backend:
    def __init__(self):
        self.calls = 0

    def warm_up(self):
        pass

    def generate(self, prompt, max_new_tokens=128, temperature=0.0):
        self.calls += 1
        return "SELECT 1;"


class TestDeterministicHierarchyFastPaths(unittest.TestCase):
    def setUp(self):
        fd, self.db = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        conn = sqlite3.connect(self.db)
        conn.execute(
            "CREATE TABLE users ("
            "Id INTEGER PRIMARY KEY, "
            "UserReferenceId TEXT UNIQUE NOT NULL, "
            "EmailAddress TEXT, Name TEXT, Title TEXT, "
            "ManagerReferenceId TEXT, Department TEXT, "
            "FOREIGN KEY (ManagerReferenceId) "
            "REFERENCES users(UserReferenceId))"
        )
        conn.executemany(
            "INSERT INTO users VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (1, "u1", "a@example.com", "Alice", "Director", None, "A"),
                (2, "u2", "b@example.com", "Bob", "Manager", "u1", "A"),
                (3, "u3", "c@example.com", "Carol", "Engineer", "u2", "A"),
                (4, "u4", "d@example.com", "David", "Engineer", "u2", "A"),
                (5, "u5", "e@example.com", "Eve", "Engineer", "u3", "A"),
                (6, "u6", "f@example.com", "Frank", "Engineer", "u5", "A"),
            ],
        )
        conn.commit()
        conn.close()

        self.backend = _Backend()
        config = PipelineConfig(
            connection_string=f"sqlite:///{self.db}",
            retrieval_strategy="keyword",
            embedding_model=None,
            semantic_embedding_model=None,
            enable_semantic_learning=False,
            enable_recommendations=False,
        )
        self.pipeline = Text2SQLPipeline(config, llm_backend=self.backend)

    def tearDown(self):
        self.pipeline.close()
        try:
            os.remove(self.db)
        except OSError:
            pass

    def test_user_by_id_is_zero_llm(self):
        result = self.pipeline.ask("which user has id 2")
        self.assertTrue(result.success)
        self.assertEqual(len(result.dataframe), 1)
        self.assertEqual(result.dataframe.iloc[0]["Name"], "Bob")
        self.assertEqual(self.backend.calls, 0)

    def test_direct_reporters_by_manager_email_are_zero_llm(self):
        result = self.pipeline.ask(
            "Show me the direct reporters of the manager whose email is b@example.com."
        )
        self.assertTrue(result.success)
        self.assertEqual(set(result.dataframe["Name"]), {"Carol", "David"})
        self.assertEqual(self.backend.calls, 0)
        self.assertNotIn("WITH RECURSIVE", result.sql.upper())

    def test_full_hierarchy_by_manager_email_is_zero_llm(self):
        result = self.pipeline.ask(
            "Show me everyone under the manager whose email is b@example.com at every level."
        )
        self.assertTrue(result.success)
        self.assertEqual(
            result.dataframe["Name"].tolist(),
            ["Carol", "David", "Eve", "Frank"],
        )
        self.assertEqual(result.dataframe["Level"].tolist(), [1, 1, 2, 3])
        self.assertEqual(self.backend.calls, 0)
        self.assertIn("WITH RECURSIVE", result.sql.upper())

    def test_title_lookup_is_zero_llm(self):
        result = self.pipeline.ask("who is the Manager")
        self.assertTrue(result.success)
        self.assertEqual(result.dataframe.iloc[0]["Name"], "Bob")
        self.assertEqual(self.backend.calls, 0)


if __name__ == "__main__":
    unittest.main()
