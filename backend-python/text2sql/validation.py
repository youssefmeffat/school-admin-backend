"""SQL validation and safety checks.

Validates generated SQL before execution. The validator remains lightweight
and database-agnostic, while using sqlglot when available for syntax parsing.

Design goals:
- read-only enforcement
- single-statement enforcement
- forbidden write-operation detection
- known-table validation against actual metadata
- undefined alias detection
- dialect-aware syntax validation
- conservative behavior: do not reject valid complex SQL just because a
  lightweight regex cannot fully understand it
"""

import re
from typing import List, Optional, Set

from .exceptions import SQLValidationError
from .schema_metadata import DatabaseMetadata


_LEADING_WITH_OR_SELECT = re.compile(
    r"^\s*(?:--[^\n]*\n|/\*.*?\*/\s*)*(WITH|SELECT)\b",
    re.IGNORECASE | re.DOTALL,
)

_TABLE_REF_RE = re.compile(
    r"\b(?:FROM|JOIN)\s+"
    r"(?:[A-Za-z_][A-Za-z0-9_]*\s*\.\s*)?"
    r"([A-Za-z_][A-Za-z0-9_]*)"
    r"(?:\s+(?:AS\s+)?([A-Za-z_][A-Za-z0-9_]*))?",
    re.IGNORECASE,
)

_DERIVED_ALIAS_RE = re.compile(
    r"\)\s*(?:AS\s+)?([A-Za-z_][A-Za-z0-9_]*)\b",
    re.IGNORECASE,
)

_CTE_NAME_RE = re.compile(
    r"\b([A-Za-z_][A-Za-z0-9_]*)\s+AS\s*\(",
    re.IGNORECASE,
)

_QUALIFIED_REF_RE = re.compile(
    r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\.\s*"
    r"[A-Za-z_][A-Za-z0-9_]*\b"
)

_RESERVED_AFTER_TABLE = {
    "WHERE", "ON", "GROUP", "ORDER", "LIMIT", "HAVING", "JOIN",
    "LEFT", "RIGHT", "INNER", "OUTER", "FULL", "CROSS", "UNION",
    "WHEN", "THEN", "AND", "OR", "SET", "OFFSET", "USING",
    "RETURNING", "WINDOW", "QUALIFY", "FETCH",
}

_SCHEMA_PREFIXES = {
    "main", "temp", "dbo", "public", "pg_catalog",
}

# These are operations rather than simple English words. They are checked
# conservatively so a value such as "delete" inside a quoted string is not
# rejected merely because the value contains a forbidden word.
_WRITE_STATEMENT_RE = re.compile(
    r"\b(?:INSERT|UPDATE|DELETE|DROP|ALTER|TRUNCATE|CREATE|GRANT|REVOKE|"
    r"ATTACH|DETACH|MERGE|REPLACE)\b",
    re.IGNORECASE,
)


def _strip_sql_comments(sql: str) -> str:
    """Remove SQL comments while preserving quoted string contents."""
    result = []
    i = 0
    n = len(sql)

    while i < n:
        ch = sql[i]

        # Single quoted SQL string.
        if ch == "'":
            start = i
            i += 1
            while i < n:
                if sql[i] == "'":
                    # SQL escapes a quote as ''.
                    if i + 1 < n and sql[i + 1] == "'":
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
            result.append(sql[start:i])
            continue

        # Double quoted identifier/string.
        if ch == '"':
            start = i
            i += 1
            while i < n:
                if sql[i] == '"':
                    if i + 1 < n and sql[i + 1] == '"':
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
            result.append(sql[start:i])
            continue

        # Backtick identifier (MySQL).
        if ch == "`":
            start = i
            i += 1
            while i < n:
                if sql[i] == "`":
                    if i + 1 < n and sql[i + 1] == "`":
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
            result.append(sql[start:i])
            continue

        # -- line comment
        if ch == "-" and i + 1 < n and sql[i + 1] == "-":
            i += 2
            while i < n and sql[i] != "\n":
                i += 1
            result.append("\n")
            continue

        # /* block comment */
        if ch == "/" and i + 1 < n and sql[i + 1] == "*":
            i += 2
            while i + 1 < n and not (sql[i] == "*" and sql[i + 1] == "/"):
                i += 1
            i = min(n, i + 2)
            result.append(" ")
            continue

        result.append(ch)
        i += 1

    return "".join(result)


def _count_top_level_statements(sql: str) -> int:
    """Count semicolon-terminated statements outside quoted strings."""
    count = 0
    i = 0
    n = len(sql)

    quote = None
    while i < n:
        ch = sql[i]

        if quote:
            if ch == quote:
                if i + 1 < n and sql[i + 1] == quote:
                    i += 2
                    continue
                quote = None
            i += 1
            continue

        if ch in ("'", '"', "`"):
            quote = ch
        elif ch == ";":
            count += 1

        i += 1

    # A non-empty SQL statement without a final semicolon is still one
    # statement.
    stripped = sql.strip()
    if stripped and count == 0:
        return 1

    # If the last semicolon is not the only terminator, the text after it
    # represents another statement.
    if stripped.endswith(";"):
        return count
    return count + 1


class SQLValidator:
    def __init__(
        self,
        forbidden_keywords: List[str],
        allow_write_statements: bool = False,
        metadata: Optional[DatabaseMetadata] = None,
    ):
        self.forbidden_keywords = {
            str(k).upper()
            for k in (forbidden_keywords or [])
        }
        self.allow_write_statements = allow_write_statements
        self.metadata = metadata

    def validate(self, sql: str, dialect: str = "sqlite") -> None:
        if not sql or not sql.strip():
            raise SQLValidationError("Generated SQL is empty.")

        cleaned = _strip_sql_comments(sql).strip()

        if not cleaned:
            raise SQLValidationError("Generated SQL contains only comments.")

        if not self.allow_write_statements:
            if not _LEADING_WITH_OR_SELECT.match(cleaned):
                raise SQLValidationError(
                    "Only read-only SELECT/CTE statements are allowed. "
                    f"Got: {sql[:120]!r}"
                )

            self._check_read_only(cleaned)

        statement_count = _count_top_level_statements(cleaned)
        if statement_count != 1:
            raise SQLValidationError(
                "Multiple SQL statements are not allowed."
            )

        self._check_forbidden_keywords(cleaned)
        self._check_syntax(cleaned, dialect)

        if self.metadata is not None:
            self._check_known_tables(cleaned)
            self._check_alias_references(cleaned)

    def _check_read_only(self, sql: str) -> None:
        """Reject write operations even when hidden inside a WITH statement."""
        # WITH ... DELETE/UPDATE/INSERT is syntactically possible in several
        # dialects. A read-only pipeline must reject those operations.
        match = _WRITE_STATEMENT_RE.search(sql)
        if match:
            keyword = match.group(0).upper()
            raise SQLValidationError(
                f"Write operation is not allowed: {keyword}"
            )

    def _check_forbidden_keywords(self, sql: str) -> None:
        """Check configured forbidden operations outside quoted literals."""
        # Remove quoted strings/identifiers from the scan so a legitimate
        # value such as 'DELETE' does not trigger a false positive.
        scan = self._mask_quoted_content(sql)

        for keyword in self.forbidden_keywords:
            if re.search(rf"\b{re.escape(keyword)}\b", scan):
                raise SQLValidationError(
                    f"Forbidden keyword detected: {keyword}"
                )

    @staticmethod
    def _mask_string_literals(sql: str) -> str:
        """Mask single-quoted SQL string literals but preserve identifiers."""
        chars = list(sql)
        i = 0
        n = len(chars)
        while i < n:
            if chars[i] == "'":
                i += 1
                while i < n:
                    if chars[i] == "'":
                        if i + 1 < n and chars[i + 1] == "'":
                            chars[i] = " "
                            chars[i + 1] = " "
                            i += 2
                            continue
                        chars[i] = " "
                        i += 1
                        break
                    chars[i] = " "
                    i += 1
            else:
                i += 1
        return "".join(chars)

    @staticmethod
    def _mask_quoted_content(sql: str) -> str:
        chars = list(sql)
        i = 0
        n = len(chars)

        while i < n:
            if chars[i] in ("'", '"', "`"):
                quote = chars[i]
                i += 1

                while i < n:
                    if chars[i] == quote:
                        if i + 1 < n and chars[i + 1] == quote:
                            chars[i] = " "
                            chars[i + 1] = " "
                            i += 2
                            continue
                        chars[i] = " "
                        i += 1
                        break

                    chars[i] = " "
                    i += 1
            else:
                i += 1

        return "".join(chars)

    def _check_syntax(self, sql: str, dialect: str) -> None:
        try:
            import sqlglot
        except ImportError:
            return

        try:
            sqlglot.parse_one(
                sql,
                read=self._sqlglot_dialect(dialect),
            )
        except Exception as exc:
            raise SQLValidationError(
                f"SQL syntax error: {exc}"
            ) from exc

    @staticmethod
    def _sqlglot_dialect(dialect: str) -> str:
        mapping = {
            "mssql": "tsql",
            "postgresql": "postgres",
        }
        return mapping.get(
            (dialect or "sqlite").lower(),
            dialect,
        )

    def _check_known_tables(self, sql: str) -> None:
        """Check FROM/JOIN table names against actual metadata.

        This remains intentionally lightweight. It recognizes:
        - table
        - schema.table
        - CTE names
        - quoted identifiers handled by the SQL parser
        """
        known_tables = {
            t.lower()
            for t in self.metadata.tables.keys()
        }

        cte_names = {
            m.lower()
            for m in _CTE_NAME_RE.findall(sql)
        }

        mentioned = set()

        for match in _TABLE_REF_RE.finditer(sql):
            table = match.group(1)
            if table:
                mentioned.add(table.lower())

        unknown = (
            mentioned
            - known_tables
            - cte_names
        )

        if unknown:
            raise SQLValidationError(
                f"Query references unknown table(s): {sorted(unknown)}. "
                f"Known tables: {sorted(known_tables)}"
            )

    def _check_alias_references(self, sql: str) -> None:
        """Catch undefined prefixes such as m.FullName.

        A prefix is considered valid if it is:
        - an actual table name
        - a table alias
        - a CTE
        - a derived-table alias
        - a common schema qualifier
        """
        known: Set[str] = {
            t.lower()
            for t in self.metadata.tables.keys()
        }
        known |= _SCHEMA_PREFIXES

        for match in _TABLE_REF_RE.finditer(sql):
            table, alias = match.groups()

            if table:
                known.add(table.lower())

            if (
                alias
                and alias.upper() not in _RESERVED_AFTER_TABLE
            ):
                known.add(alias.lower())

        known |= {
            alias.lower()
            for alias in _DERIVED_ALIAS_RE.findall(sql)
        }

        known |= {
            cte.lower()
            for cte in _CTE_NAME_RE.findall(sql)
        }

        # Ignore dots inside quoted string literals. For example, an email
        # value like 'bishoy.meantias@flairstech.com' contains tokens that
        # look like alias.column references to a regex-only scan.
        scan = self._mask_string_literals(sql)
        referenced = {
            prefix.lower()
            for prefix in _QUALIFIED_REF_RE.findall(scan)
        }

        undefined = referenced - known

        if undefined:
            raise SQLValidationError(
                "Query references undefined table alias(es): "
                f"{sorted(undefined)}. "
                "Every 'alias.column' reference must correspond "
                "to a table, schema, CTE, or subquery introduced "
                "in the query. "
                f"Known tables/aliases: {sorted(known)}"
            )