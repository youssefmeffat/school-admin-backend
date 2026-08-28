"""Regression tests for self-referencing FK / recursive-hierarchy support.

Covers the "manager hierarchy" use case: a table with a self-referencing
foreign key (e.g. users.ManagerReferenceId -> users.UserReferenceId) should
(1) be flagged as SELF-REFERENCING in the retrieved schema text so the LLM
knows it represents a tree, (2) trigger dialect-correct recursive-CTE
guidance in the generated prompt, and (3) a WITH RECURSIVE query against
such a schema must pass SQL validation (known-table / alias checks).
"""

import unittest

from text2sql.prompting import PromptBuilder
from text2sql.retrieval import SchemaDocumentBuilder
from text2sql.schema_metadata import (
    ColumnMetadata,
    DatabaseMetadata,
    ForeignKeyMetadata,
    TableMetadata,
)
from text2sql.validation import SQLValidator


def _users_metadata(dialect: str = "mysql") -> DatabaseMetadata:
    users = TableMetadata(
        name="users",
        columns=[
            ColumnMetadata("UserReferenceId", "varchar", False, True),
            ColumnMetadata("Name", "varchar", True, False),
            ColumnMetadata("ManagerReferenceId", "varchar", True, False),
        ],
        primary_keys=["UserReferenceId"],
        foreign_keys=[
            ForeignKeyMetadata(
                "ManagerReferenceId", "users", "UserReferenceId"
            )
        ],
    )
    return DatabaseMetadata(dialect=dialect, tables={"users": users})


class TestSelfReferencingFKIsFlagged(unittest.TestCase):
    def test_document_text_flags_self_reference(self):
        metadata = _users_metadata()
        docs = SchemaDocumentBuilder().build(metadata, statistics={})
        users_doc = next(d for d in docs if d.table == "users")
        self.assertIn("SELF-REFERENCING", users_doc.text)

    def test_non_self_referencing_fk_is_not_flagged(self):
        parent = TableMetadata(
            name="departments",
            columns=[ColumnMetadata("Id", "int", False, True)],
            primary_keys=["Id"],
        )
        child = TableMetadata(
            name="employees",
            columns=[
                ColumnMetadata("Id", "int", False, True),
                ColumnMetadata("DepartmentId", "int", True, False),
            ],
            primary_keys=["Id"],
            foreign_keys=[
                ForeignKeyMetadata("DepartmentId", "departments", "Id")
            ],
        )
        metadata = DatabaseMetadata(
            dialect="mysql",
            tables={"departments": parent, "employees": child},
        )
        docs = SchemaDocumentBuilder().build(metadata, statistics={})
        emp_doc = next(d for d in docs if d.table == "employees")
        self.assertNotIn("SELF-REFERENCING", emp_doc.text)


class TestRecursiveCTEPromptGuidance(unittest.TestCase):
    def _doc_items(self, metadata):
        docs = SchemaDocumentBuilder().build(metadata, statistics={})
        return [{"document": d} for d in docs]

    def test_prompt_includes_dialect_correct_recursive_syntax(self):
        cases = {
            "mysql": "WITH RECURSIVE",
            "sqlite": "WITH RECURSIVE",
            "postgresql": "WITH RECURSIVE",
            "mssql": "T-SQL has no RECURSIVE keyword",
            "oracle": "CONNECT BY PRIOR",
        }
        for dialect, expected_fragment in cases.items():
            with self.subTest(dialect=dialect):
                metadata = _users_metadata(dialect=dialect)
                docs = self._doc_items(metadata)
                prompt = PromptBuilder().build(
                    "show me everyone under this manager at every level",
                    docs,
                    dialect=dialect,
                )
                self.assertIn("HIERARCHY / RECURSIVE RULE", prompt)
                self.assertIn(expected_fragment, prompt)

    def test_prompt_omits_hierarchy_block_when_no_self_reference(self):
        parent = TableMetadata(
            name="departments",
            columns=[ColumnMetadata("Id", "int", False, True)],
            primary_keys=["Id"],
        )
        child = TableMetadata(
            name="employees",
            columns=[
                ColumnMetadata("Id", "int", False, True),
                ColumnMetadata("DepartmentId", "int", True, False),
            ],
            primary_keys=["Id"],
            foreign_keys=[
                ForeignKeyMetadata("DepartmentId", "departments", "Id")
            ],
        )
        metadata = DatabaseMetadata(
            dialect="mysql",
            tables={"departments": parent, "employees": child},
        )
        docs = self._doc_items(metadata)
        prompt = PromptBuilder().build(
            "how many employees are there", docs, dialect="mysql"
        )
        self.assertNotIn("HIERARCHY / RECURSIVE RULE", prompt)


class TestRecursiveCTEValidatesAgainstSchema(unittest.TestCase):
    def test_with_recursive_manager_hierarchy_passes_validation(self):
        metadata = _users_metadata(dialect="mysql")
        sql = """
        WITH RECURSIVE EmployeeHierarchy AS (
            SELECT UserReferenceId, Name, ManagerReferenceId, 1 AS Level
            FROM users
            WHERE ManagerReferenceId = '6319a604f629bfb44017c498'
            UNION ALL
            SELECT u.UserReferenceId, u.Name, u.ManagerReferenceId,
                   eh.Level + 1
            FROM users u
            INNER JOIN EmployeeHierarchy eh
                ON u.ManagerReferenceId = eh.UserReferenceId
        )
        SELECT * FROM EmployeeHierarchy ORDER BY Level;
        """
        validator = SQLValidator(forbidden_keywords=[], metadata=metadata)
        # Must not raise.
        validator.validate(sql, dialect="mysql")


if __name__ == "__main__":
    unittest.main()
