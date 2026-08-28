"""Regression tests for singular/plural entity-word matching.

Bug: `_requested_entity_is_unavailable()` used a `concept_matches()` helper
that only singularized the *question's* word (stripping a trailing "s" /
"ies") before checking it against `schema_words`. That only covers "question
word is plural, schema word is singular." It never tried the reverse --
"question word is singular, schema word is plural" -- so a question like
"how many user are there" was incorrectly rejected as unavailable even
though the `users` table exists.

These tests exercise `_requested_entity_is_unavailable()` directly against
an in-memory mock schema (no real database, no LLM calls needed) so they
run fast and deterministically, and cover both matching directions.
"""

import os
import sqlite3
import tempfile
import unittest

from text2sql.config import PipelineConfig
from text2sql.pipeline import Text2SQLPipeline
from text2sql.schema_metadata import (
    ColumnMetadata,
    DatabaseMetadata,
    TableMetadata,
)


class _NoOpBackend:
    """Minimal fake LLM backend; not exercised by these tests."""

    def warm_up(self):
        pass

    def generate(self, prompt, max_new_tokens=220, temperature=0.1):
        return "SELECT 1;"


def _mock_users_roles_metadata() -> DatabaseMetadata:
    """A small mock schema with `users` and `roles` tables (plural names,
    matching the acceptance-criteria schema in the bug report)."""

    return DatabaseMetadata(
        dialect="sqlite",
        tables={
            "users": TableMetadata(
                name="users",
                columns=[
                    ColumnMetadata(
                        name="id",
                        data_type="INTEGER",
                        nullable=False,
                        primary_key=True,
                    ),
                    ColumnMetadata(
                        name="name",
                        data_type="TEXT",
                        nullable=False,
                        primary_key=False,
                    ),
                ],
                primary_keys=["id"],
            ),
            "roles": TableMetadata(
                name="roles",
                columns=[
                    ColumnMetadata(
                        name="id",
                        data_type="INTEGER",
                        nullable=False,
                        primary_key=True,
                    ),
                    ColumnMetadata(
                        name="title",
                        data_type="TEXT",
                        nullable=False,
                        primary_key=False,
                    ),
                ],
                primary_keys=["id"],
            ),
        },
    )


class EntityWordMatchingTestCase(unittest.TestCase):
    """Builds a pipeline against a throwaway sqlite file (so the pipeline
    is otherwise fully functional / closeable) but swaps in a mock
    `users`/`roles` DatabaseMetadata so the entity-matching check can be
    tested in isolation, without needing real tables or LLM calls."""

    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        conn = sqlite3.connect(self.db_path)
        conn.execute("CREATE TABLE placeholder (id INTEGER PRIMARY KEY)")
        conn.commit()
        conn.close()

        config = PipelineConfig(
            connection_string=f"sqlite:///{self.db_path}",
            retrieval_strategy="keyword",
            embedding_model=None,
            semantic_embedding_model=None,
        )
        self.pipeline = Text2SQLPipeline(config, llm_backend=_NoOpBackend())
        self.addCleanup(self.pipeline.close)

        # Swap in the mock users/roles schema for the entity check.
        self.pipeline.metadata = _mock_users_roles_metadata()

    def test_singular_question_word_matches_plural_table_users(self):
        """'user' (singular) must resolve against the 'users' table."""
        result = self.pipeline._requested_entity_is_unavailable(
            "how many user are there"
        )
        self.assertIsNone(
            result,
            "singular 'user' should match the plural 'users' table, "
            f"but was rejected as unavailable: {result!r}",
        )

    def test_plural_question_word_still_matches_plural_table_users(self):
        """Existing correct behavior: 'users' (plural) already matches."""
        result = self.pipeline._requested_entity_is_unavailable(
            "how many users are there"
        )
        self.assertIsNone(result)

    def test_singular_question_word_matches_plural_table_roles(self):
        """'role' (singular) must resolve against the 'roles' table."""
        result = self.pipeline._requested_entity_is_unavailable(
            "how many role are there"
        )
        self.assertIsNone(
            result,
            "singular 'role' should match the plural 'roles' table, "
            f"but was rejected as unavailable: {result!r}",
        )

    def test_plural_question_word_still_matches_plural_table_roles(self):
        """Existing correct behavior: 'roles' (plural) already matches."""
        result = self.pipeline._requested_entity_is_unavailable(
            "how many roles are there"
        )
        self.assertIsNone(result)

    def test_truly_unrelated_entity_is_still_rejected(self):
        """The fix must not make the check overly permissive: an entity
        with no relationship at all to the schema should still be
        flagged as unavailable."""
        result = self.pipeline._requested_entity_is_unavailable(
            "how many restaurants are there"
        )
        self.assertEqual(result, "restaurants")


if __name__ == "__main__":
    unittest.main()
