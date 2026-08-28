"""Foreign-key relationship graph and lightweight column statistics.

This module keeps schema relationships dynamic while collecting only a small
amount of real data for SQL generation.

Performance/accuracy goals:
- use real FK metadata for JOIN expansion;
- keep relationship expansion bounded;
- collect a few representative values for WHERE-clause accuracy;
- avoid expensive/unbounded scans;
- generate dialect-correct sampling SQL;
- never interpolate user-controlled identifiers.
"""

from typing import Dict, List

from .db_providers import DatabaseProvider
from .schema_metadata import DatabaseMetadata


def _quote_identifier(name: str, dialect: str) -> str:
    """Quote an identifier after validating it against a strict safe form.

    Identifiers come from database metadata, not directly from user input.
    """
    if not isinstance(name, str) or not name:
        raise ValueError(f"Unsafe identifier rejected: {name!r}")

    # Permit normal SQL identifiers only. '*' is handled separately below.
    if name != "*" and not (
        name[0].isalpha() or name[0] == "_"
    ):
        raise ValueError(f"Unsafe identifier rejected: {name!r}")

    if name != "*" and not all(
        ch.isalnum() or ch == "_"
        for ch in name
    ):
        raise ValueError(f"Unsafe identifier rejected: {name!r}")

    if name == "*":
        return "*"

    if dialect == "mysql":
        return f"`{name}`"

    # PostgreSQL, SQLite, MSSQL, Oracle and Snowflake all accept
    # double-quoted identifiers in the relevant SQL contexts.
    return f'"{name}"'


def _limit_clause(limit: int, dialect: str) -> str:
    """Return a dialect-correct row limiting clause."""
    limit = max(1, int(limit))

    if dialect == "mssql":
        # MSSQL uses TOP in the SELECT clause, so this helper is not used
        # for MSSQL queries below.
        return ""

    if dialect == "oracle":
        return f"FETCH FIRST {limit} ROWS ONLY"

    return f"LIMIT {limit}"


class RelationshipGraph:
    """Undirected adjacency graph of tables connected by foreign keys."""

    def __init__(self):
        self.graph: Dict[str, List[str]] = {}

    def build(self, metadata: DatabaseMetadata) -> None:
        self.graph.clear()

        for table in metadata.tables.values():
            self.graph.setdefault(table.name, [])

            for fk in table.foreign_keys:
                referenced = fk.referenced_table

                if not referenced:
                    continue

                if referenced not in self.graph[table.name]:
                    self.graph[table.name].append(referenced)

                self.graph.setdefault(referenced, [])

                if table.name not in self.graph[referenced]:
                    self.graph[referenced].append(table.name)

    def neighbors(self, table: str) -> List[str]:
        return self.graph.get(table, [])

    def related_tables(
        self,
        tables: List[str],
        hops: int = 1,
    ) -> List[str]:
        """Expand retrieved tables by a bounded number of FK hops.

        This is important for Text-to-SQL accuracy:

            Orders
              ↓ FK
            Customers

        If retrieval finds Orders, the related Customers table can still be
        added so the SQL model has enough information to construct the JOIN.

        The expansion is intentionally bounded to prevent large prompts.
        """

        if not tables:
            return []

        hops = max(0, min(int(hops), 2))

        seen = set()
        frontier = set()

        for table in tables:
            if table in self.graph:
                seen.add(table)
                frontier.add(table)

        for _ in range(hops):
            next_frontier = set()

            for table in frontier:
                for neighbor in self.neighbors(table):
                    if neighbor not in seen:
                        seen.add(neighbor)
                        next_frontier.add(neighbor)

            frontier = next_frontier

            if not frontier:
                break

        # Preserve the original retrieval order first, then add discovered
        # related tables. This keeps the most relevant tables near the front
        # of the prompt.
        ordered = [t for t in tables if t in seen]
        ordered.extend(
            t for t in seen
            if t not in ordered
        )

        return ordered


class StatisticsService:
    """Collects a small number of representative values per column.

    These values help the SQL generator choose exact WHERE values such as:

        City -> ["Cairo", "Giza"]

    The service is intentionally lightweight. It does not calculate expensive
    distributions or scan entire tables.
    """

    def __init__(
        self,
        provider: DatabaseProvider,
        max_values: int = 5,
    ):
        self.provider = provider
        self.max_values = max(1, min(int(max_values), 10))

    def _sample_query(
        self,
        table_name: str,
        column_name: str,
        dialect: str,
    ) -> str:
        qtable = _quote_identifier(
            table_name,
            dialect,
        )
        qcolumn = _quote_identifier(
            column_name,
            dialect,
        )

        # MSSQL requires TOP in the SELECT list.
        if dialect == "mssql":
            return (
                f"SELECT DISTINCT TOP ({self.max_values}) "
                f"{qcolumn} AS {qcolumn} "
                f"FROM {qtable} "
                f"WHERE {qcolumn} IS NOT NULL"
            )

        limit = _limit_clause(
            self.max_values,
            dialect,
        )

        return (
            f"SELECT DISTINCT {qcolumn} "
            f"FROM {qtable} "
            f"WHERE {qcolumn} IS NOT NULL "
            f"{limit}"
        )

    def analyze(
        self,
        metadata: DatabaseMetadata,
    ) -> dict:
        """Collect representative values with one bounded query per table.

        Sampling every column separately is unnecessarily expensive on wide
        unknown schemas. A single ``SELECT *`` sample gives the same evidence
        needed by retrieval and structural profiling with dramatically fewer
        round trips.
        """

        dialect = metadata.dialect
        stats: dict = {}

        for table in metadata.tables.values():
            table_stats = {column.name: [] for column in table.columns}
            stats[table.name] = table_stats

            try:
                qtable = _quote_identifier(table.name, dialect)

                if dialect == "mssql":
                    query = (
                        f"SELECT TOP ({self.max_values}) * "
                        f"FROM {qtable}"
                    )
                elif dialect == "oracle":
                    query = (
                        f"SELECT * FROM {qtable} "
                        f"FETCH FIRST {self.max_values} ROWS ONLY"
                    )
                else:
                    query = (
                        f"SELECT * FROM {qtable} "
                        f"LIMIT {self.max_values}"
                    )

                values = self.provider.execute(query)

                if values is None:
                    continue

                for column in table.columns:
                    column_name = column.name
                    if column_name not in values.columns:
                        continue

                    try:
                        raw_values = values[column_name].tolist()
                        cleaned = []
                        seen = set()

                        for value in raw_values:
                            if value is None:
                                continue

                            if hasattr(value, "item"):
                                try:
                                    value = value.item()
                                except Exception:
                                    pass

                            marker = repr(value)
                            if marker in seen:
                                continue
                            seen.add(marker)
                            cleaned.append(value)

                            if len(cleaned) >= self.max_values:
                                break

                        table_stats[column_name] = cleaned
                    except Exception:
                        table_stats[column_name] = []

            except Exception:
                # Statistics are an accuracy enhancement, never a reason for
                # schema discovery to fail.
                pass

        return stats
