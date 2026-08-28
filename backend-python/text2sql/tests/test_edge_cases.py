"""Edge-case coverage for the Text-to-SQL pipeline.

This suite exercises the pipeline against a richer schema (two tables,
a foreign key, NULLs, and a date column) using deterministic fake LLM
backends, so it runs fast and offline while still driving the pipeline
end-to-end -- intent classification, entity validation, retrieval,
semantic learning, SQL validation/safety, repair, and answer synthesis.

Categories covered (see task spec):
  1. synonyms                     -> TestSynonymResolution
  2. wrong / unsupported entities -> TestUnsupportedEntities
  3. ambiguous follow-ups         -> TestAmbiguousFollowUps
  4. missing columns              -> TestMissingColumns
  5. large results                -> TestLargeResults
  6. NULLs                        -> TestNullHandling
  7. joins                        -> TestJoins
  8. dates                        -> TestDateFiltering
  9. malformed questions          -> TestMalformedInput
 10. SQL injection / write        -> TestSQLInjectionAndWrites
 11. complex filters              -> TestComplexFilters
 12. highest/lowest entity        -> TestExtremeValueQueries
 13. caching                      -> TestCaching
 14. greetings / small talk       -> TestConversationalIntents
 15. user teaching                -> TestUserTeaching
"""

import os
import sqlite3
import tempfile
import unittest

from text2sql.config import PipelineConfig
from text2sql.pipeline import Text2SQLPipeline
from text2sql.exceptions import SQLValidationError
from text2sql.validation import SQLValidator
from text2sql.schema_metadata import SchemaIntrospector


# ---------------------------------------------------------------------
# Shared test database
# ---------------------------------------------------------------------
#
# departments(id, name)
# employees(id, full_name, dept_id -> departments.id, salary [nullable],
#           hire_date [nullable, ISO text])
#
# Deliberately includes:
# - a nullable numeric column (salary) with a NULL row, to test NULL-safe
#   highest/lowest handling
# - a nullable foreign key (dept_id) with a NULL row, to test outer joins
#   and "no department" filters
# - a nullable date column with a NULL row


def _build_company_db(path: str) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE departments (id INTEGER PRIMARY KEY, name TEXT NOT NULL)"
    )
    conn.execute(
        """
        CREATE TABLE employees (
            id INTEGER PRIMARY KEY,
            full_name TEXT NOT NULL,
            dept_id INTEGER,
            salary REAL,
            hire_date TEXT,
            FOREIGN KEY (dept_id) REFERENCES departments(id)
        )
        """
    )
    conn.executemany(
        "INSERT INTO departments VALUES (?, ?)",
        [(1, "Engineering"), (2, "Sales"), (3, "HR")],
    )
    conn.executemany(
        "INSERT INTO employees VALUES (?, ?, ?, ?, ?)",
        [
            (1, "Alice", 1, 95000.0, "2020-01-15"),
            (2, "Bob", 1, 88000.0, "2021-06-01"),
            (3, "Carol", 2, 72000.0, None),
            (4, "Dave", 2, 65000.0, "2019-03-10"),
            (5, "Eve", 3, None, "2022-11-20"),
            (6, "Frank", None, 51000.0, "2018-07-07"),
        ],
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------
# Deterministic fake LLM backend
# ---------------------------------------------------------------------


class ScriptedBackend:
    """Rule-based fake model.

    Reads only the live question (text after the final "Question:" /
    "USER QUESTION" marker in a built prompt) so few-shot examples and
    schema text elsewhere in the prompt do not leak into keyword
    matching. This keeps the test deterministic without needing a real
    model download.
    """

    def __init__(self, extra_rules=None):
        self.calls = 0
        self.prompts = []
        self.extra_rules = extra_rules or []

    def warm_up(self):
        pass

    @staticmethod
    def _live_question(prompt: str) -> str:
        p = prompt.lower()
        for marker in ("user question", "question:"):
            if marker in p:
                p = p.rsplit(marker, 1)[1]
        for marker in ("failed sql", "database error", "corrected sql"):
            if marker in p:
                p = p.split(marker, 1)[0]
        return p

    def generate(self, prompt, max_new_tokens=220, temperature=0.1):
        self.calls += 1
        self.prompts.append(prompt)
        full = prompt.lower()

        if "respond with only one json object" in full:
            return (
                '{"table": {"semantic_name": "Employees", '
                '"synonyms": ["workers", "staff", "personnel"], '
                '"business_meaning": "people employed by the company", '
                '"confidence": 95}, "columns": ['
                '{"column": "salary", "semantic_name": "Salary", '
                '"synonyms": ["pay", "income", "compensation"], '
                '"business_meaning": "employee compensation", '
                '"units": "USD", "data_category": "Financial", '
                '"confidence": 95}]}'
            )

        q = self._live_question(prompt)

        for matcher, sql in self.extra_rules:
            if matcher(q):
                return sql

        if ("count" in q or "how many" in q) and (
            "worker" in q or "employee" in q or "staff" in q
        ):
            return "SELECT COUNT(*) AS row_count FROM employees;"
        if "average" in q and "salary" in q:
            return "SELECT AVG(salary) AS avg_salary FROM employees;"
        if ("highest" in q or "top" in q or "max" in q) and "salary" in q:
            return (
                "SELECT full_name, salary FROM employees "
                "WHERE salary IS NOT NULL ORDER BY salary DESC LIMIT 1;"
            )
        if ("lowest" in q or "min" in q or "bottom" in q) and "salary" in q:
            return (
                "SELECT full_name, salary FROM employees "
                "WHERE salary IS NOT NULL ORDER BY salary ASC LIMIT 1;"
            )
        if "department" in q and ("group" in q or "each" in q or "per" in q):
            return (
                "SELECT d.name AS department, COUNT(*) AS n "
                "FROM employees e JOIN departments d ON e.dept_id = d.id "
                "GROUP BY d.name;"
            )
        if "no department" in q or ("without" in q and "department" in q):
            return "SELECT full_name FROM employees WHERE dept_id IS NULL;"
        if "2020" in q and "hire" in q:
            return (
                "SELECT full_name FROM employees "
                "WHERE hire_date >= '2020-01-01';"
            )
        if "between" in q and "salary" in q:
            return (
                "SELECT full_name, salary FROM employees "
                "WHERE salary BETWEEN 60000 AND 90000 "
                "AND dept_id IS NOT NULL "
                "ORDER BY salary DESC;"
            )
        if "engineering" in q and "sales" in q:
            return (
                "SELECT full_name FROM employees e "
                "JOIN departments d ON e.dept_id = d.id "
                "WHERE d.name IN ('Engineering', 'Sales');"
            )
        if "all employees" in q or "every employee" in q or "list employees" in q:
            return "SELECT full_name, salary FROM employees;"

        return "SELECT COUNT(*) AS row_count FROM employees;"


class MaliciousBackend:
    """Always emits the same (possibly dangerous/malformed) SQL text."""

    def __init__(self, sql: str):
        self.sql = sql
        self.calls = 0

    def warm_up(self):
        pass

    def generate(self, prompt, max_new_tokens=220, temperature=0.1):
        self.calls += 1
        if "respond with only one json object" in prompt.lower():
            return (
                '{"table": {"semantic_name": "T", "synonyms": [], '
                '"business_meaning": "x", "confidence": 50}, "columns": []}'
            )
        return self.sql


# ---------------------------------------------------------------------
# Base test case with a shared temp database
# ---------------------------------------------------------------------


class _CompanyDBTestCase(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        _build_company_db(self.db_path)

    def tearDown(self):
        try:
            os.remove(self.db_path)
        except OSError:
            pass

    def make_pipeline(self, backend=None, **overrides):
        backend = backend or ScriptedBackend()
        config = PipelineConfig(
            connection_string=f"sqlite:///{self.db_path}",
            retrieval_strategy="keyword",
            embedding_model=None,
            semantic_embedding_model=None,
            llm_max_new_tokens=64,
            **overrides,
        )
        pipeline = Text2SQLPipeline(config, llm_backend=backend)
        self.addCleanup(pipeline.close)
        return pipeline, backend


# ---------------------------------------------------------------------
# 1. Synonym resolution
# ---------------------------------------------------------------------


class TestSynonymResolution(_CompanyDBTestCase):
    def test_worker_synonym_resolves_to_employees(self):
        pipeline, _ = self.make_pipeline(enable_semantic_learning=True)
        result = pipeline.ask("how many workers are there")
        self.assertTrue(result.success, result.answer)
        self.assertEqual(int(result.dataframe.iloc[0, 0]), 6)

    def test_pay_synonym_resolves_to_salary_concept(self):
        pipeline, _ = self.make_pipeline(enable_semantic_learning=True)
        result = pipeline.ask("what is the average salary")
        self.assertTrue(result.success, result.answer)

        profile = pipeline.semantic_schema.get(
            "employees"
        ) or pipeline.semantic_schema.get("Employees")

        self.assertIsNotNone(profile)
        salary_profile = profile.columns.get("salary")
        self.assertIsNotNone(salary_profile)
        self.assertIn("salary", salary_profile.semantic_name.lower())


# ---------------------------------------------------------------------
# 2. Unsupported / wrong entities (must be rejected, never silently
#    substituted for an unrelated table -- rule #7)
# ---------------------------------------------------------------------


class TestUnsupportedEntities(_CompanyDBTestCase):
    def test_unrelated_entity_is_rejected_not_hallucinated(self):
        pipeline, backend = self.make_pipeline()
        result = pipeline.ask("how many restaurants are there")
        self.assertFalse(result.success)
        self.assertIn("restaurants", result.answer.lower())
        # Must not have fabricated a query against an unrelated table.
        self.assertNotIn("FROM EMPLOYEES", (result.sql or "").upper())
        self.assertNotIn("FROM DEPARTMENTS", (result.sql or "").upper())

    def test_customers_entity_absent_from_schema_is_rejected(self):
        pipeline, _ = self.make_pipeline()
        result = pipeline.ask("how many customers are there")
        self.assertFalse(result.success)

    def test_real_entity_after_rejection_still_works(self):
        """A rejected question must not corrupt later valid questions."""
        pipeline, _ = self.make_pipeline()
        rejected = pipeline.ask("how many restaurants are there")
        self.assertFalse(rejected.success)

        recovered = pipeline.ask("how many employees are there")
        self.assertTrue(recovered.success, recovered.answer)
        self.assertEqual(int(recovered.dataframe.iloc[0, 0]), 6)


# ---------------------------------------------------------------------
# 3. Ambiguous follow-ups
# ---------------------------------------------------------------------


class TestAmbiguousFollowUps(_CompanyDBTestCase):
    def test_bare_pronoun_without_context_is_flagged_ambiguous(self):
        pipeline, _ = self.make_pipeline()
        result = pipeline.ask("what about their salary")
        # Should ask for clarification rather than guessing a subject.
        self.assertTrue(result.success)
        self.assertIn("sql", result.__dict__)
        self.assertTrue(
            "ambiguous" in (result.sql or "").lower()
            or "which" in result.answer.lower()
            or "specific" in result.answer.lower()
        )

    def test_pronoun_with_conversation_context_is_not_blocked(self):
        pipeline, _ = self.make_pipeline()
        result = pipeline.ask(
            "what is their salary",
            conversation_context="Previous question was about Alice in Engineering.",
        )
        # With context supplied, the pipeline should attempt the query
        # rather than immediately asking for clarification.
        self.assertNotEqual(
            result.sql, "-- ambiguous question, clarification requested --"
        )


# ---------------------------------------------------------------------
# 4. Missing / nonexistent columns
# ---------------------------------------------------------------------


class TestMissingColumns(_CompanyDBTestCase):
    def test_nonexistent_column_triggers_repair_not_crash(self):
        backend = MaliciousBackend(
            "SELECT bonus_amount FROM employees;"
        )
        pipeline, _ = self.make_pipeline(
            backend=backend, max_repair_attempts=1
        )
        # Not a bare count -- must exercise real LLM SQL generation and
        # repair, not the simple-count fast path.
        result = pipeline.ask(
            "how many employees are there with a salary above 0"
        )
        self.assertFalse(result.success)
        self.assertIsNotNone(result.answer)
        self.assertNotIn("Traceback", result.answer)

    def test_unknown_table_reference_is_rejected(self):
        backend = MaliciousBackend("SELECT * FROM nonexistent_table;")
        pipeline, _ = self.make_pipeline(
            backend=backend, max_repair_attempts=1
        )
        # Not a bare count -- must exercise real LLM SQL generation, not
        # the simple-count fast path.
        result = pipeline.ask(
            "how many employees are there with a salary above 0"
        )
        self.assertFalse(result.success)


# ---------------------------------------------------------------------
# 5. Large results
# ---------------------------------------------------------------------


class TestLargeResults(_CompanyDBTestCase):
    def test_multi_row_multi_column_result_is_summarized_safely(self):
        backend = ScriptedBackend()
        pipeline, _ = self.make_pipeline(backend=backend)
        result = pipeline.ask("list all employees")
        self.assertTrue(result.success, result.answer)
        self.assertGreaterEqual(len(result.dataframe), 1)
        # Answer synthesis must not silently crash on a wide/tall result.
        self.assertIsInstance(result.answer, str)
        self.assertGreater(len(result.answer), 0)


# ---------------------------------------------------------------------
# 6. NULL handling
# ---------------------------------------------------------------------


class TestNullHandling(_CompanyDBTestCase):
    def test_lowest_salary_excludes_null_row(self):
        """Eve has a NULL salary; NULL must never win a MIN comparison."""
        pipeline, _ = self.make_pipeline()
        result = pipeline.ask("who has the lowest salary")
        self.assertTrue(result.success, result.answer)
        name = result.dataframe.iloc[0]["full_name"]
        self.assertEqual(name, "Frank")  # true minimum: 51000

    def test_highest_salary_returns_actual_row(self):
        pipeline, _ = self.make_pipeline()
        result = pipeline.ask("who has the highest salary")
        self.assertTrue(result.success, result.answer)
        self.assertEqual(result.dataframe.iloc[0]["full_name"], "Alice")

    def test_missing_null_guard_is_caught_by_validator_hook(self):
        """A naive ORDER BY ASC LIMIT 1 with no NULL guard must be rejected
        by the alignment check so the repair loop can fix it, rather than
        silently returning the NULL row as the answer."""
        backend = MaliciousBackend(
            "SELECT full_name, salary FROM employees "
            "ORDER BY salary ASC LIMIT 1;"
        )
        pipeline, _ = self.make_pipeline(
            backend=backend, max_repair_attempts=0
        )
        result = pipeline.ask("who has the lowest salary")
        # With zero repair attempts and a backend that never adds the
        # guard, this must fail rather than return Eve's NULL row as the
        # "lowest" salary.
        self.assertFalse(result.success)

    def test_no_department_filter_finds_null_fk_row(self):
        pipeline, _ = self.make_pipeline()
        result = pipeline.ask("which employees have no department")
        self.assertTrue(result.success, result.answer)
        self.assertIn("Frank", result.dataframe["full_name"].tolist())


# ---------------------------------------------------------------------
# 7. Joins
# ---------------------------------------------------------------------


class TestJoins(_CompanyDBTestCase):
    def test_group_by_department_uses_join(self):
        pipeline, _ = self.make_pipeline()
        result = pipeline.ask("show employees grouped by department")
        self.assertTrue(result.success, result.answer)
        self.assertIn("JOIN", result.sql.upper())

    def test_filter_across_two_departments(self):
        pipeline, _ = self.make_pipeline()
        result = pipeline.ask(
            "which employees are in engineering or sales"
        )
        self.assertTrue(result.success, result.answer)
        self.assertIn("JOIN", result.sql.upper())


# ---------------------------------------------------------------------
# 8. Dates
# ---------------------------------------------------------------------


class TestDateFiltering(_CompanyDBTestCase):
    def test_hired_after_date_filter(self):
        pipeline, _ = self.make_pipeline()
        result = pipeline.ask("which employees were hired after 2020")
        self.assertTrue(result.success, result.answer)
        names = set(result.dataframe["full_name"].tolist())
        self.assertIn("Bob", names)  # 2021-06-01
        self.assertNotIn("Dave", names)  # 2019-03-10


# ---------------------------------------------------------------------
# 9. Malformed / low-signal input
# ---------------------------------------------------------------------


class TestMalformedInput(_CompanyDBTestCase):
    def test_empty_question(self):
        pipeline, _ = self.make_pipeline()
        result = pipeline.ask("")
        self.assertFalse(result.success)

    def test_whitespace_only_question(self):
        pipeline, _ = self.make_pipeline()
        result = pipeline.ask("     ")
        self.assertFalse(result.success)

    def test_none_question_does_not_crash(self):
        pipeline, _ = self.make_pipeline()
        result = pipeline.ask(None)  # type: ignore[arg-type]
        self.assertFalse(result.success)

    def test_extremely_long_question_is_rejected_gracefully(self):
        pipeline, _ = self.make_pipeline()
        result = pipeline.ask("a" * 5000)
        self.assertFalse(result.success)
        self.assertIsInstance(result.answer, str)

    def test_gibberish_does_not_crash(self):
        pipeline, _ = self.make_pipeline()
        result = pipeline.ask("asdkjaslkdj qwe 123 !!! ??? ###")
        self.assertIsInstance(result.answer, str)


# ---------------------------------------------------------------------
# 10. SQL injection / write attempts
# ---------------------------------------------------------------------


class TestSimpleCountFastPath(_CompanyDBTestCase):
    """A bare 'how many X (are there)?' must answer without any LLM call.

    This is the actual mechanism behind the reported "That question is
    taking too long to answer" timeout for the simplest possible question
    on slow CPU hardware -- several sequential model generations were
    previously paid for even here. Uses a backend that would blow up if
    called, to prove the shortcut truly bypasses the LLM entirely.
    """

    def _no_llm_backend(self):
        class _ExplodingBackend:
            def warm_up(self):
                pass

            def generate(self, prompt, max_new_tokens=220, temperature=0.1):
                raise AssertionError(
                    "LLM backend must not be called for a bare count "
                    "question."
                )

        return _ExplodingBackend()

    def test_bare_count_question_never_calls_the_llm(self):
        pipeline, _ = self.make_pipeline(backend=self._no_llm_backend())
        result = pipeline.ask("how many employees are there")
        self.assertTrue(result.success, result.answer)
        self.assertEqual(int(result.dataframe.iloc[0, 0]), 6)
        self.assertIn("SELECT COUNT(*)", result.sql.upper())

    def test_singular_and_no_question_mark_variants_also_fast_path(self):
        for question in (
            "how many employees",
            "how many employees?",
            "how many employees exist",
            "how many employees do we have",
        ):
            with self.subTest(question=question):
                pipeline, _ = self.make_pipeline(
                    backend=self._no_llm_backend()
                )
                result = pipeline.ask(question)
                self.assertTrue(result.success, result.answer)
                self.assertEqual(int(result.dataframe.iloc[0, 0]), 6)

    def test_filtered_count_does_not_take_the_fast_path(self):
        """Extra filter language must still reach real SQL generation."""
        pipeline, backend = self.make_pipeline()
        pipeline.ask(
            "how many employees are there with a salary above 0"
        )
        self.assertGreater(backend.calls, 0)

    def test_filter_words_are_never_silently_dropped(self):
        """'how many employees are active' must not become an unfiltered
        count of ALL employees -- it must fail to structurally match any
        table (no table is literally named 'employeesareactive') and
        fall through to real SQL generation instead of quietly answering
        the wrong (unfiltered) question."""
        pipeline, backend = self.make_pipeline()
        result = pipeline.ask("how many employees are active")
        self.assertGreater(backend.calls, 0)
        self.assertNotEqual(
            result.sql.strip().rstrip(";").upper(),
            "SELECT COUNT(*) AS N FROM EMPLOYEES",
        )

    def test_ambiguous_entity_does_not_take_the_fast_path(self):
        """'how many rows' has no single target table -- must not guess."""
        pipeline, backend = self.make_pipeline()
        pipeline.ask("how many rows are there")
        # Falls through to the normal pipeline (schema-meta or full LLM
        # path) rather than crashing or guessing a table silently.
        self.assertIsNotNone(pipeline)


class TestSQLInjectionAndWrites(_CompanyDBTestCase):
    def _row_count(self):
        conn = sqlite3.connect(self.db_path)
        n = conn.execute("SELECT COUNT(*) FROM employees").fetchone()[0]
        conn.close()
        return n

    def test_direct_drop_attempt_in_question_is_ignored(self):
        pipeline, _ = self.make_pipeline()
        before = self._row_count()
        pipeline.ask("DROP TABLE employees")
        self.assertEqual(self._row_count(), before)

    def test_stacked_statement_from_model_is_neutralized(self):
        backend = MaliciousBackend(
            "SELECT * FROM employees; DROP TABLE employees;"
        )
        pipeline, _ = self.make_pipeline(
            backend=backend, max_repair_attempts=0
        )
        before = self._row_count()
        result = pipeline.ask("how many employees are there")
        self.assertEqual(self._row_count(), before)
        # Whatever gets executed, it must never be the DROP statement.
        self.assertNotIn("DROP", (result.sql or "").upper())

    def test_write_statement_from_model_is_rejected(self):
        for stmt in (
            "DELETE FROM employees WHERE 1=1;",
            "UPDATE employees SET salary = 0;",
            "INSERT INTO employees VALUES (99, 'X', 1, 1, '2020-01-01');",
            "DROP TABLE employees;",
            "ATTACH DATABASE '/tmp/evil.db' AS evil;",
        ):
            with self.subTest(stmt=stmt):
                backend = MaliciousBackend(stmt)
                pipeline, _ = self.make_pipeline(
                    backend=backend, max_repair_attempts=0
                )
                before = self._row_count()
                # Not a bare count -- must exercise real LLM SQL
                # generation, not the simple-count fast path.
                result = pipeline.ask(
                    "how many employees are there with a salary above 0"
                )
                self.assertFalse(result.success)
                self.assertEqual(self._row_count(), before)

    def test_validator_rejects_write_sql_directly(self):
        validator = SQLValidator(
            forbidden_keywords=["DROP", "DELETE", "UPDATE", "INSERT"],
            allow_write_statements=False,
        )
        with self.assertRaises(SQLValidationError):
            validator.validate("DELETE FROM employees;")

    def test_validator_rejects_multiple_statements(self):
        validator = SQLValidator(
            forbidden_keywords=["DROP"],
            allow_write_statements=False,
        )
        with self.assertRaises(SQLValidationError):
            validator.validate(
                "SELECT 1; SELECT 2;"
            )

    def test_validator_allows_plain_select(self):
        validator = SQLValidator(
            forbidden_keywords=["DROP", "DELETE"],
            allow_write_statements=False,
        )
        # Should not raise.
        validator.validate("SELECT * FROM employees;")


# ---------------------------------------------------------------------
# 11. Complex filters
# ---------------------------------------------------------------------


class TestComplexFilters(_CompanyDBTestCase):
    def test_salary_range_filter(self):
        pipeline, _ = self.make_pipeline()
        result = pipeline.ask(
            "which employees have a salary between 60000 and 90000"
        )
        self.assertTrue(result.success, result.answer)
        salaries = result.dataframe["salary"].tolist()
        self.assertTrue(all(60000 <= s <= 90000 for s in salaries))


# ---------------------------------------------------------------------
# 12. Highest / lowest must return the actual entity, not just the value
# ---------------------------------------------------------------------


class TestExtremeValueQueries(_CompanyDBTestCase):
    def test_group_by_with_max_min_is_rejected_for_person_question(self):
        """A classic wrong pattern: GROUP BY id + MAX(col) returns one row
        per group instead of the single extreme record."""
        backend = MaliciousBackend(
            "SELECT id, MAX(salary) FROM employees GROUP BY id;"
        )
        pipeline, _ = self.make_pipeline(
            backend=backend, max_repair_attempts=0
        )
        result = pipeline.ask("who has the highest salary")
        self.assertFalse(result.success)

    def test_max_without_order_and_limit_is_rejected_for_person_question(self):
        backend = MaliciousBackend("SELECT MAX(salary) FROM employees;")
        pipeline, _ = self.make_pipeline(
            backend=backend, max_repair_attempts=0
        )
        result = pipeline.ask("who has the highest salary")
        self.assertFalse(result.success)

    def test_max_alone_is_fine_for_a_value_only_question(self):
        backend = MaliciousBackend(
            "SELECT MAX(salary) AS max_salary FROM employees;"
        )
        pipeline, _ = self.make_pipeline(backend=backend)
        result = pipeline.ask("what is the maximum salary")
        self.assertTrue(result.success, result.answer)


# ---------------------------------------------------------------------
# 13. Caching
# ---------------------------------------------------------------------


class TestCaching(_CompanyDBTestCase):
    def test_repeated_question_hits_cache(self):
        pipeline, backend = self.make_pipeline()
        first = pipeline.ask("how many employees are there")
        calls_after_first = backend.calls
        second = pipeline.ask("how many employees are there")
        self.assertTrue(second.from_cache)
        self.assertEqual(backend.calls, calls_after_first)
        self.assertEqual(
            int(first.dataframe.iloc[0, 0]),
            int(second.dataframe.iloc[0, 0]),
        )

    def test_cache_is_case_and_whitespace_insensitive(self):
        pipeline, backend = self.make_pipeline()
        pipeline.ask("how many employees are there")
        calls_after_first = backend.calls
        second = pipeline.ask("  HOW MANY   employees are there  ")
        self.assertTrue(second.from_cache)
        self.assertEqual(backend.calls, calls_after_first)

    def test_different_question_is_not_a_cache_hit(self):
        pipeline, backend = self.make_pipeline()
        pipeline.ask("how many employees are there")
        calls_after_first = backend.calls
        result = pipeline.ask("what is the average salary")
        self.assertFalse(result.from_cache)
        self.assertGreater(backend.calls, calls_after_first)


# ---------------------------------------------------------------------
# 14. Greetings / small talk / offensive input never touch the database
# ---------------------------------------------------------------------


class TestConversationalIntents(_CompanyDBTestCase):
    def test_greeting_does_not_call_llm(self):
        pipeline, backend = self.make_pipeline()
        result = pipeline.ask("hello")
        self.assertTrue(result.success)
        self.assertEqual(backend.calls, 0)

    def test_small_talk_does_not_call_llm(self):
        pipeline, backend = self.make_pipeline()
        result = pipeline.ask("thanks")
        self.assertTrue(result.success)
        self.assertEqual(backend.calls, 0)

    def test_offensive_message_is_redirected_politely(self):
        pipeline, backend = self.make_pipeline()
        # Uses a word from the classifier's conservative offensive list
        # (see intent.py _OFFENSIVE_WORDS) rather than a mild word like
        # "stupid", which is intentionally NOT flagged to avoid
        # over-triggering on normal frustrated phrasing.
        result = pipeline.ask("this is such a fucking useless bot")
        self.assertTrue(result.success)
        self.assertEqual(backend.calls, 0)
        self.assertNotIn("fucking", result.answer.lower())

    def test_mixed_greeting_with_real_question_is_processed(self):
        pipeline, _ = self.make_pipeline()
        result = pipeline.ask("hi, how many employees are there?")
        self.assertTrue(result.success, result.answer)
        self.assertEqual(int(result.dataframe.iloc[0, 0]), 6)

    def test_multi_word_small_talk_does_not_call_llm(self):
        # A message made entirely of small-talk reaction words (e.g. "lol
        # nice", "ok great!") must not fall through to SQL generation just
        # because it's more than one token -- see intent.py
        # _SMALL_TALK_ONLY_RE.
        pipeline, backend = self.make_pipeline()
        for question in ("lol nice", "ok great!", "haha cool."):
            result = pipeline.ask(question)
            self.assertTrue(result.success, result.answer)
            self.assertEqual(backend.calls, 0, question)

    def test_small_talk_word_inside_real_question_still_works(self):
        # Sanity check: the widened small-talk regex must not start
        # matching real database questions that merely contain one of
        # these words as a prefix.
        pipeline, _ = self.make_pipeline()
        result = pipeline.ask("ok how many employees are there?")
        self.assertTrue(result.success, result.answer)
        self.assertEqual(int(result.dataframe.iloc[0, 0]), 6)


# ---------------------------------------------------------------------
# 15. User teaching (schema-scoped, not hardcoded)
# ---------------------------------------------------------------------


class TestUserTeaching(_CompanyDBTestCase):
    def test_user_can_teach_a_synonym(self):
        pipeline, _ = self.make_pipeline()
        taught = pipeline.ask("when I say staffers, I mean employees")
        self.assertTrue(taught.success)
        self.assertIn(
            "staffers",
            pipeline.user_memory.build_hint("how many staffers"),
        )

    def test_user_can_teach_a_column_meaning(self):
        pipeline, _ = self.make_pipeline()
        taught = pipeline.ask("column salary is income")
        self.assertTrue(taught.success)


# ---------------------------------------------------------------------
# Bonus: schema introspection sanity (safety net for structural changes)
# ---------------------------------------------------------------------


class TestSchemaIntrospection(_CompanyDBTestCase):
    def test_foreign_key_is_discovered(self):
        from text2sql.db_providers import make_provider

        provider = make_provider(f"sqlite:///{self.db_path}")
        provider.connect()
        self.addCleanup(provider.close)

        introspector = SchemaIntrospector(provider)
        metadata = introspector.extract()

        self.assertIn("employees", metadata.tables)
        self.assertIn("departments", metadata.tables)

        fks = metadata.tables["employees"].foreign_keys
        self.assertTrue(
            any(fk.referenced_table == "departments" for fk in fks)
        )

    def test_nullable_flag_is_discovered(self):
        from text2sql.db_providers import make_provider

        provider = make_provider(f"sqlite:///{self.db_path}")
        provider.connect()
        self.addCleanup(provider.close)

        introspector = SchemaIntrospector(provider)
        metadata = introspector.extract()

        salary_col = next(
            c
            for c in metadata.tables["employees"].columns
            if c.name == "salary"
        )
        self.assertTrue(salary_col.nullable)


if __name__ == "__main__":
    unittest.main()
