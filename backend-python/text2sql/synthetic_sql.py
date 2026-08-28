"""Synthetic (question, SQL) training-pair generator.

Builds generic question/SQL examples from the learned schema. It uses only
metadata, structural semantic types, FK relationships, and real sample
values; no table or column names are hardcoded.

The generator is deliberately conservative: examples should be valid and
useful training signals, not merely a large number of low-quality pairs.
"""

import random
import re
from typing import Dict, List, Optional


_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _quote_identifier(name: str, dialect: str = "sqlite") -> str:
    """Quote a schema identifier after validating it came from metadata."""
    name = str(name)

    if not _SAFE_IDENTIFIER.match(name):
        raise ValueError(
            f"Unsafe schema identifier rejected: {name!r}"
        )

    if dialect == "mysql":
        return f"`{name}`"

    return f'"{name}"'


def _quote_literal(value) -> str:
    """Safely render a literal value for generated training SQL.

    Values come from the database, not directly from the user, but escaping
    them is still important because generated examples are later consumed
    by an LLM.
    """
    if value is None:
        return "NULL"

    if isinstance(value, bool):
        return "1" if value else "0"

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)

    text = str(value).replace("'", "''")
    return f"'{text}'"


class SyntheticSQLExampleGenerator:
    """Generate high-quality (question, SQL, reasoning) triples.

    The generated pairs intentionally cover:
      - row counts
      - simple retrieval
      - categorical filtering/grouping
      - aggregation
      - grouped aggregation
      - ordering/top-k
      - numeric filtering
      - date filtering
      - FK joins
      - grouped FK joins

    `max_examples_per_table` limits table-local examples. Relationship
    examples are generated separately so JOIN coverage is not accidentally
    removed by the per-table cap.
    """

    def __init__(
        self,
        max_examples_per_table: int = 12,
        seed: int = 0,
        max_join_examples: int = 24,
    ):
        self.max_examples_per_table = max(
            1,
            int(max_examples_per_table),
        )
        self.max_join_examples = max(
            0,
            int(max_join_examples),
        )
        self._rng = random.Random(seed)

    def generate(
        self,
        metadata,
        profiles: Dict[str, object],
        statistics: dict,
        graph=None,
    ) -> List[dict]:
        """Generate and lightly deduplicate training pairs."""
        examples: List[dict] = []

        for table in metadata.tables.values():
            examples.extend(
                self._table_examples(
                    table,
                    profiles.get(table.name),
                    statistics.get(table.name, {}),
                    dialect=getattr(
                        metadata,
                        "dialect",
                        "sqlite",
                    ),
                )
            )

        join_examples = self._join_examples(
            metadata,
            dialect=getattr(
                metadata,
                "dialect",
                "sqlite",
            ),
        )

        if self.max_join_examples:
            self._rng.shuffle(join_examples)
            examples.extend(
                join_examples[: self.max_join_examples]
            )

        # Deduplicate on question + SQL. This matters when schemas contain
        # repeated structural patterns.
        unique = []
        seen = set()

        for example in examples:
            key = (
                example.get("question", "").strip().lower(),
                example.get("sql", "").strip(),
            )
            if key in seen:
                continue
            seen.add(key)
            unique.append(example)

        self._rng.shuffle(unique)
        return unique

    def semantic_examples(
        self,
        semantic_schema,
        metadata,
        max_examples: int = 64,
    ) -> List[dict]:
        """Generate generic NL->SQL examples from learned meanings.

        These examples are intentionally database-agnostic. The natural
        language comes from the learned semantic profile while SQL always
        points to the real current-database identifiers.
        """
        examples = []

        for table_name, table_profile in (semantic_schema or {}).items():
            table_meta = metadata.tables.get(table_name)
            if table_meta is None:
                continue

            table_label = (
                table_profile.semantic_name
                or table_name
            )
            table_aliases = [
                table_label,
                *list(table_profile.synonyms or [])[:3],
            ]

            # Count/entity examples.
            for alias in table_aliases[:3]:
                examples.append({
                    "question": f"How many {alias} are there?",
                    "sql": (
                        f"SELECT COUNT(*) AS row_count "
                        f"FROM {_quote_identifier(table_name, metadata.dialect)};"
                    ),
                    "reasoning": (
                        f"Resolve the learned entity concept '{alias}' "
                        f"to table '{table_name}' and count its rows."
                    ),
                })

            identifier = next(
                (
                    c.name for c in table_meta.columns
                    if c.primary_key
                ),
                None,
            )

            for col_name, col in table_profile.columns.items():
                semantic = col.semantic_name or col_name
                aliases = [
                    semantic,
                    *list(col.synonyms or [])[:3],
                ]

                # Aggregate examples.
                for alias in aliases[:3]:
                    examples.append({
                        "question": f"What is the average {alias}?",
                        "sql": (
                            f"SELECT AVG({_quote_identifier(col_name, metadata.dialect)}) "
                            f"AS average_value FROM "
                            f"{_quote_identifier(table_name, metadata.dialect)};"
                        ),
                        "reasoning": (
                            f"Use the learned meaning '{alias}' for "
                            f"{table_name}.{col_name}."
                        ),
                    })

                # Superlative row examples.
                if identifier and (
                    col.structural_type == "measure"
                    or col.units
                    or re.search(
                        r"(financial|numeric|money|currency|salary|income|price|cost|revenue|amount|rate|value|score|quantity|compensation|pay)",
                        f"{col.semantic_name} {col.data_category} {col.business_meaning}",
                        re.I,
                    )
                ):
                    for alias in aliases[:2]:
                        examples.append({
                            "question": f"Who has the highest {alias}?",
                            "sql": (
                                f"SELECT {_quote_identifier(identifier, metadata.dialect)}, "
                                f"{_quote_identifier(col_name, metadata.dialect)} "
                                f"FROM {_quote_identifier(table_name, metadata.dialect)} "
                                f"ORDER BY {_quote_identifier(col_name, metadata.dialect)} DESC "
                                f"{self._limit_clause(metadata.dialect, 1)};"
                            ),
                            "reasoning": (
                                f"Resolve '{alias}' to the learned measure "
                                f"{table_name}.{col_name} and return the top row."
                            ),
                        })

        # Deduplicate and cap.
        unique = []
        seen = set()
        for ex in examples:
            key = (
                ex["question"].strip().lower(),
                ex["sql"].strip(),
            )
            if key in seen:
                continue
            seen.add(key)
            unique.append(ex)
            if len(unique) >= max_examples:
                break

        return unique

    def _table_examples(
        self,
        table,
        profile,
        stats,
        dialect: str = "sqlite",
    ) -> List[dict]:
        if profile is None:
            return []

        out: List[dict] = []
        name = table.name
        qtable = _quote_identifier(name, dialect)
        cols = profile.columns

        cat_cols = [
            c for c, p in cols.items()
            if p.semantic_type == "categorical"
        ]
        measure_cols = [
            c for c, p in cols.items()
            if p.semantic_type == "measure"
        ]
        date_cols = [
            c for c, p in cols.items()
            if p.semantic_type == "date_time"
        ]

        # ---------------------------------------------------------------
        # Basic retrieval
        # ---------------------------------------------------------------

        out.append({
            "question": f"How many rows are in {name}?",
            "sql": f"SELECT COUNT(*) AS Count FROM {qtable};",
            "reasoning": (
                f"Need a simple row count of {name}, "
                "with no filter or join."
            ),
        })

        limit_sql = self._limit_clause(
            dialect,
            5,
        )

        out.append({
            "question": f"Show me the first 5 rows of {name}.",
            "sql": (
                f"SELECT * FROM {qtable}"
                f"{limit_sql};"
            ),
            "reasoning": (
                f"Return a small sample of {name}; "
                "no aggregation or filter is required."
            ),
        })

        # ---------------------------------------------------------------
        # Categorical filters
        # ---------------------------------------------------------------

        for c in cat_cols[:2]:
            qc = _quote_identifier(c, dialect)
            values = [
                v for v in stats.get(c, [])
                if v not in (None, "")
            ]

            if values:
                value = self._rng.choice(values)
                literal = _quote_literal(value)

                out.append({
                    "question": (
                        f"Show all {name} where {c} is {value}."
                    ),
                    "sql": (
                        f"SELECT * FROM {qtable} "
                        f"WHERE {qc} = {literal};"
                    ),
                    "reasoning": (
                        f"Filter {name} using the categorical "
                        f"column {c} and the observed value."
                    ),
                })

                out.append({
                    "question": (
                        f"How many {name} rows have "
                        f"{c} equal to {value}?"
                    ),
                    "sql": (
                        f"SELECT COUNT(*) AS Count FROM {qtable} "
                        f"WHERE {qc} = {literal};"
                    ),
                    "reasoning": (
                        f"Count {name} rows after filtering "
                        f"{c} to the observed value."
                    ),
                })

            out.append({
                "question": (
                    f"How many {name} are there for each {c}?"
                ),
                "sql": (
                    f"SELECT {qc}, COUNT(*) AS Count "
                    f"FROM {qtable} "
                    f"GROUP BY {qc};"
                ),
                "reasoning": (
                    f"Group {name} by {c} and count "
                    "rows in each group."
                ),
            })

        # ---------------------------------------------------------------
        # Numeric aggregation
        # ---------------------------------------------------------------

        for m in measure_cols[:2]:
            qm = _quote_identifier(m, dialect)

            aggregations = [
                ("AVG", "Avg", f"average {m}"),
                ("SUM", "Total", f"total {m}"),
                ("MAX", "Max", f"maximum {m}"),
                ("MIN", "Min", f"minimum {m}"),
            ]

            for fn, alias, wording in aggregations:
                out.append({
                    "question": (
                        f"What is the {wording} in {name}?"
                    ),
                    "sql": (
                        f"SELECT {fn}({qm}) AS {alias}{m} "
                        f"FROM {qtable};"
                    ),
                    "reasoning": (
                        f"Aggregate measure {m} across {name} "
                        f"using {fn}."
                    ),
                })

        # ---------------------------------------------------------------
        # Grouped numeric aggregation
        # ---------------------------------------------------------------

        for c in cat_cols[:1]:
            qc = _quote_identifier(c, dialect)

            for m in measure_cols[:1]:
                qm = _quote_identifier(m, dialect)

                out.append({
                    "question": (
                        f"What is the average {m} "
                        f"for each {c} in {name}?"
                    ),
                    "sql": (
                        f"SELECT {qc}, AVG({qm}) AS Avg{m} "
                        f"FROM {qtable} "
                        f"GROUP BY {qc};"
                    ),
                    "reasoning": (
                        f"Group {name} by {c} and calculate "
                        f"the average of {m} per group."
                    ),
                })

                out.append({
                    "question": (
                        f"What is the total {m} "
                        f"for each {c} in {name}?"
                    ),
                    "sql": (
                        f"SELECT {qc}, SUM({qm}) AS Total{m} "
                        f"FROM {qtable} "
                        f"GROUP BY {qc};"
                    ),
                    "reasoning": (
                        f"Group {name} by {c} and sum "
                        f"the measure {m} per group."
                    ),
                })

        # ---------------------------------------------------------------
        # Ordering / top-k
        # ---------------------------------------------------------------

        for m in measure_cols[:1]:
            qm = _quote_identifier(m, dialect)
            limit_sql = self._limit_clause(
                dialect,
                5,
            )

            out.append({
                "question": (
                    f"Show the top 5 {name} rows "
                    f"with the highest {m}."
                ),
                "sql": (
                    f"SELECT * FROM {qtable} "
                    f"ORDER BY {qm} DESC"
                    f"{limit_sql};"
                ),
                "reasoning": (
                    f"Order {name} by {m} descending "
                    "and return the top five rows."
                ),
            })

            out.append({
                "question": (
                    f"Show the top 5 {name} rows "
                    f"with the lowest {m}."
                ),
                "sql": (
                    f"SELECT * FROM {qtable} "
                    f"ORDER BY {qm} ASC"
                    f"{limit_sql};"
                ),
                "reasoning": (
                    f"Order {name} by {m} ascending "
                    "and return the first five rows."
                ),
            })

            out.append({
                "question": (
                    f"Show {name} rows where {m} "
                    "is greater than 0."
                ),
                "sql": (
                    f"SELECT * FROM {qtable} "
                    f"WHERE {qm} > 0;"
                ),
                "reasoning": (
                    f"Filter {name} where measure {m} "
                    "is greater than zero."
                ),
            })

        # ---------------------------------------------------------------
        # Date filtering
        # ---------------------------------------------------------------

        for d in date_cols[:1]:
            qd = _quote_identifier(d, dialect)

            out.append({
                "question": (
                    f"Show {name} rows where {d} "
                    "is after 2020-01-01."
                ),
                "sql": (
                    f"SELECT * FROM {qtable} "
                    f"WHERE {qd} > '2020-01-01';"
                ),
                "reasoning": (
                    f"Filter {name} using the date column "
                    f"{d} with an after-date comparison."
                ),
            })

        self._rng.shuffle(out)

        return out[
            : self.max_examples_per_table
        ]

    def _join_examples(
        self,
        metadata,
        dialect: str = "sqlite",
    ) -> List[dict]:
        out: List[dict] = []

        for table in metadata.tables.values():
            qtable = _quote_identifier(
                table.name,
                dialect,
            )

            for fk in table.foreign_keys:
                qref = _quote_identifier(
                    fk.referenced_table,
                    dialect,
                )
                qlocal = _quote_identifier(
                    fk.column,
                    dialect,
                )
                qremote = _quote_identifier(
                    fk.referenced_column,
                    dialect,
                )

                out.append({
                    "question": (
                        f"List each {table.name} row together "
                        f"with its related {fk.referenced_table} details."
                    ),
                    "sql": (
                        f"SELECT * FROM {qtable} t "
                        f"JOIN {qref} r "
                        f"ON t.{qlocal} = r.{qremote};"
                    ),
                    "reasoning": (
                        f"Join {table.name} to "
                        f"{fk.referenced_table} using the "
                        f"foreign key {fk.column} -> "
                        f"{fk.referenced_column}."
                    ),
                })

                out.append({
                    "question": (
                        f"How many {table.name} rows are there "
                        f"for each {fk.referenced_table}?"
                    ),
                    "sql": (
                        f"SELECT r.{qremote}, COUNT(*) AS Count "
                        f"FROM {qtable} t "
                        f"JOIN {qref} r "
                        f"ON t.{qlocal} = r.{qremote} "
                        f"GROUP BY r.{qremote};"
                    ),
                    "reasoning": (
                        f"Join the related tables through the "
                        "foreign key, then count rows per "
                        "referenced entity."
                    ),
                })

        return out

    @staticmethod
    def _limit_clause(
        dialect: str,
        n: int,
    ) -> str:
        """Return the correct row-limit fragment for common dialects."""
        if dialect == "mssql":
            # MSSQL needs TOP immediately after SELECT, so this helper is
            # intentionally not used for MSSQL generated SELECT statements.
            return ""

        if dialect == "oracle":
            return f" FETCH FIRST {int(n)} ROWS ONLY"

        return f" LIMIT {int(n)}"


# ---------------------------------------------------------------------------
# MSSQL helper note
# ---------------------------------------------------------------------------
# The generator primarily targets the same dialect used by the connected
# pipeline. For MSSQL, `_limit_clause()` returns an empty string because TOP
# belongs after SELECT rather than at the end of the statement. The project's
# PromptBuilder remains the authoritative dialect-aware SQL layer.