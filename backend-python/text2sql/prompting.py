"""Prompt construction and raw LLM-output cleanup.

Optimized for small local instruct models such as Qwen2.5-1.5B:

- compact prompts to reduce CPU inference time,
- strong schema grounding for accuracy,
- explicit semantic/entity grounding,
- dialect-aware SQL rules,
- few-shot examples,
- optional semantic hints,
- repair prompts that expose the exact database error,
- safer SQL extraction from model output.
"""

import re
from typing import List, Optional


_RECURSIVE_CTE_SYNTAX = {
    "sqlite": "WITH RECURSIVE cte_name AS (...)",
    "postgresql": "WITH RECURSIVE cte_name AS (...)",
    "mysql": "WITH RECURSIVE cte_name AS (...)  -- requires MySQL 8.0+",
    "mssql": "WITH cte_name AS (...)  -- T-SQL has no RECURSIVE keyword; "
             "a CTE that references itself is recursive automatically",
    "oracle": "WITH cte_name (col1, col2, ...) AS (...)  -- Oracle has no "
              "RECURSIVE keyword; alternatively use "
              "CONNECT BY PRIOR parent_col = child_col",
    "snowflake": "WITH RECURSIVE cte_name AS (...)",
}


def _recursive_cte_block(dialect: str) -> str:
    syntax = _RECURSIVE_CTE_SYNTAX.get(
        dialect,
        "WITH RECURSIVE cte_name AS (...)",
    )
    return (
        "HIERARCHY / RECURSIVE RULE\n"
        "The schema below flags at least one SELF-REFERENCING foreign key "
        "(e.g. an employee/manager or parent/child column pointing back to "
        "the same table). For questions about a hierarchy, org chart, "
        "reporting chain, ancestors, descendants, or \"all levels\" of such "
        "a relationship, generate a recursive CTE instead of a single join:\n"
        f"  {syntax}\n"
        "  - anchor member: rows matching the starting condition "
        "(e.g. the given manager/parent id)\n"
        "  - recursive member: UNION ALL joining the table to the CTE on "
        "the self-referencing column\n"
        "  - final SELECT reads FROM the CTE\n"
        "Only do this when the question actually asks for a multi-level "
        "chain; a direct-reports-only question can stay a normal filter.\n"
        "\n"
    )


_DIALECT_NOTES = {
    "sqlite": (
        "Use SQLite syntax. Use LIMIT for row limits. "
        "Use single quotes for string values. Dates are commonly TEXT."
    ),
    "postgresql": (
        "Use PostgreSQL syntax. Use LIMIT for row limits. "
        "Use ILIKE for case-insensitive text matching."
    ),
    "mysql": (
        "Use MySQL syntax. Use LIMIT for row limits. "
        "Use backticks only when an identifier requires escaping."
    ),
    "mssql": (
        "Use T-SQL syntax. Use TOP (n) instead of LIMIT. "
        "Use GETDATE() only when the question requires the current date."
    ),
    "oracle": (
        "Use Oracle syntax. Use FETCH FIRST n ROWS ONLY for row limits."
    ),
    "snowflake": (
        "Use Snowflake syntax. Use LIMIT for row limits."
    ),
}


class PromptBuilder:
    def __init__(
        self,
        max_schema_context_chars: int = 6000,
        few_shot_examples: Optional[List[dict]] = None,
    ):
        self.max_schema_context_chars = max_schema_context_chars
        self.few_shot_examples = few_shot_examples or []

    def _schema_context(self, retrieved_documents: List[dict]) -> str:
        """Build a compact schema block without exceeding the configured cap."""
        parts = []
        total = 0

        for item in retrieved_documents:
            document = item.get("document")
            if document is None:
                continue

            text = str(getattr(document, "text", "")).strip()
            if not text:
                continue

            remaining = self.max_schema_context_chars - total
            if remaining <= 0:
                break

            if len(text) <= remaining:
                parts.append(text)
                total += len(text)
            elif remaining >= 200:
                parts.append(text[:remaining] + "\n[...schema truncated...]")
                total += remaining
                break

        return "\n\n".join(parts)

    def _few_shot_block(self) -> str:
        """Keep examples compact; examples are high-value but consume tokens."""
        if not self.few_shot_examples:
            return ""

        lines = ["Examples:"]
        for ex in self.few_shot_examples[:3]:
            question = str(ex.get("question", "")).strip()
            sql = str(ex.get("sql", "")).strip()
            if question and sql:
                lines.append(f"Q: {question}\nSQL: {sql}")

        return "\n".join(lines) + "\n\n" if len(lines) > 1 else ""

    def build(
        self,
        question: str,
        retrieved_documents: List[dict],
        dialect: str = "sqlite",
        conversation_context: Optional[str] = None,
        use_reasoning: bool = True,
        semantic_hints: Optional[str] = None,
    ) -> str:
        schema = self._schema_context(retrieved_documents)
        dialect_note = _DIALECT_NOTES.get(
            dialect,
            f"Use valid {dialect} SQL syntax.",
        )

        few_shot = self._few_shot_block()

        hierarchy_block = (
            _recursive_cte_block(dialect)
            if "SELF-REFERENCING" in schema
            else ""
        )

        # Keep conversation context short. It is only for resolving references,
        # not a second source of database facts.
        history = ""
        if conversation_context:
            history_text = conversation_context.strip()
            if len(history_text) > 1800:
                history_text = history_text[-1800:]
            history = (
                "Recent conversation for reference resolution only:\n"
                f"{history_text}\n\n"
            )

        semantic_block = ""
        if semantic_hints:
            semantic_block = (
                "Semantic Column Mapping:\n"
                f"{semantic_hints}\n\n"
            )

        if use_reasoning:
            output_rule = (
                'Output exactly two parts: first one short line starting with '
                '"Reasoning:" identifying the needed table/filter/aggregation; '
                'then one line starting with "SQL:" containing ONLY the query.'
            )
        else:
            output_rule = "Output ONLY the SQL query."

        prompt = f"""You are a precise {dialect} Text-to-SQL engine.

TASK
Translate the user's question into ONE executable SQL query.

OUTPUT
{output_rule}

HARD RULES

1. Use ONLY tables and columns present in the Database Schema.
2. Never invent a table, column, relationship, value, or number.
3. Resolve natural-language concepts against the learned semantic mappings
   and the actual schema. A literal word does NOT have to appear as a table
   or column name when a learned mapping provides grounded evidence.
4. Do not silently substitute an unrelated entity. A synonym/concept mapping
   is allowed only when the learned mapping points to a real table/column in
   the current database and its confidence/evidence supports the match.
5. If the requested entity/attribute has no grounded match in the current
   schema or learned semantic memory, do not manufacture one. If the question
   is unrelated to the database, do not force a database answer.
6. Use explicit JOIN ... ON ... for joins.
7. For COUNT/AVG/SUM/MIN/MAX, use the appropriate aggregate and GROUP BY
   only when grouping is actually requested.
8. CRITICAL SUPERLATIVE RULE:
   - "Who has the highest X?", "which employee has the highest X?",
     "highest X", "top X", "most X", "lowest X", "cheapest X", etc.
     normally ask for ROWS, not an aggregate value.
   - For a single highest row, use ORDER BY X DESC LIMIT 1.
   - For a single lowest row, use ORDER BY X ASC LIMIT 1.
   - For "top N", use ORDER BY X DESC LIMIT N.
   - Do NOT use MAX(X) together with GROUP BY the row identifier unless
     the question explicitly asks for a maximum value PER GROUP.
   - If the question asks "who/which employee/customer/restaurant has the
     highest..." return the identifying columns plus the requested value,
     not one MAX() result per identifier.
9. For questions containing "for each", "per", "by", or "grouped by",
   grouping is explicitly requested; use GROUP BY for the requested dimension.
10. Apply every explicit filter from the question. Do not silently omit
    location, category, name, status, date, or numeric conditions.
11. Match categorical sample values exactly when the schema provides them.
12. Use date/time comparisons only when supported by the schema and question.
13. Prefer the table/column whose name, type, sample values, relationships,
    structural profile, and learned semantic meaning best match the question.
14. If a learned semantic mapping is provided, treat it as evidence rather
    than a hardcoded rule. Use it only when the mapped real identifier exists
    in the current schema.
15. Use conversation context only to resolve references such as "it", "that",
    or "same one".
16. Do not use facts from conversation history as database facts unless they
    are also in the schema or explicitly supplied in the current question.
17. Return a single query. No markdown, comments, explanation, or alternatives.
18. End the SQL with a semicolon.
19. Never generate INSERT, UPDATE, DELETE, DROP, ALTER, CREATE, TRUNCATE,
    GRANT, REVOKE, ATTACH, or DETACH.
20. Before writing SQL, mentally check:
    a) Does every referenced table exist?
    b) Does every referenced column exist?
    c) Does the query answer the exact entity asked about?
    d) Did every explicit filter survive?
    e) If this is "who/which has the highest/lowest", is the result ordered
       and limited to the requested row(s), rather than grouped incorrectly?

21. SUPERLATIVE EXAMPLE:
    User: "Who has the highest MonthlyRate?"
    Correct pattern:
    SELECT EmployeeNumber, MonthlyRate
    FROM <employee_table>
    ORDER BY MonthlyRate DESC
    LIMIT 1;
    Incorrect pattern:
    SELECT EmployeeNumber, MAX(MonthlyRate)
    FROM <employee_table>
    GROUP BY EmployeeNumber;

DIALECT
{dialect_note}

{hierarchy_block}{few_shot}{semantic_block}{history}DATABASE SCHEMA
{schema}

USER QUESTION
{question}

{("Reasoning:" if use_reasoning else "")}
"""
        return prompt.strip() + "\n"

    def build_repair(
        self,
        question: str,
        retrieved_documents: List[dict],
        dialect: str,
        failed_sql: str,
        error_message: str,
    ) -> str:
        """Compact repair prompt using the exact failing SQL/error."""
        schema = self._schema_context(retrieved_documents)
        dialect_note = _DIALECT_NOTES.get(
            dialect,
            f"Use valid {dialect} SQL syntax.",
        )

        hierarchy_block = (
            _recursive_cte_block(dialect)
            if "SELF-REFERENCING" in schema
            else ""
        )

        prompt = f"""You are a precise {dialect} Text-to-SQL engine.

The previous SQL failed. Correct it while preserving the user's exact intent.

{hierarchy_block}RULES

1. Output ONLY one corrected SQL query.
2. Use ONLY tables and columns in the schema.
3. Do not invent columns or tables to fix the error.
4. Do not substitute another entity for the entity requested by the user.
5. Preserve all explicit filters, joins, grouping, ordering, and calculations.
6. If the question asks who/which entity has the highest or lowest value,
   return the actual relevant row using ORDER BY ... DESC/ASC with LIMIT 1
   (or the dialect equivalent). Do NOT repair such a query by using
   MAX()/MIN() with GROUP BY the entity ID. Use MAX()/MIN() only when the
   user explicitly asks for the maximum/minimum VALUE itself or a maximum/
   minimum per group.
7. If the question says "for each", "per", "by", or "grouped by", preserve the
   requested GROUP BY.
8. {dialect_note}
9. Do not generate write operations.
10. Return one executable query and end with a semicolon.

DATABASE SCHEMA
{schema}

USER QUESTION
{question}

FAILED SQL
{failed_sql}

DATABASE ERROR
{error_message}

CORRECTED SQL:"""

        return prompt.strip()


class SQLCleaner:
    """Extracts one safe SELECT/WITH statement from raw LLM output."""

    _SQL_MARKER_RE = re.compile(r"\bSQL\s*:\s*", re.IGNORECASE)
    _START_RE = re.compile(r"\b(SELECT|WITH)\b", re.IGNORECASE)

    @staticmethod
    def _strip_fences(text: str) -> str:
        text = re.sub(r"```(?:sql)?", "", text, flags=re.IGNORECASE)
        return text.strip()

    def clean(self, text: str) -> str:
        if not text:
            return ""

        text = self._strip_fences(text)

        # Prefer the explicit SQL section when reasoning is enabled.
        marker = self._SQL_MARKER_RE.search(text)
        if marker:
            text = text[marker.end():].strip()

        start = self._START_RE.search(text)
        if not start:
            return text.strip()

        sql = text[start.start():].strip()

        # Remove common model continuation markers after the query.
        for marker_text in (
            "\nReasoning:",
            "\nQuestion:",
            "\nQ:",
            "\nAnswer:",
            "\nFriendly answer:",
        ):
            idx = sql.find(marker_text)
            if idx != -1:
                sql = sql[:idx].rstrip()

        # Keep only the first SQL statement. Validation will reject any
        # genuinely chained statement, but this prevents natural-language
        # continuation after a valid semicolon from being treated as SQL.
        semicolon = sql.find(";")
        if semicolon != -1:
            sql = sql[:semicolon + 1].strip()
        else:
            sql = sql.strip() + ";"

        return sql