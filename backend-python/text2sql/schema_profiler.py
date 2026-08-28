"""Semantic schema profiling — lightweight structural meaning for any schema.

This module does NOT call an LLM. It infers column/table roles from schema
metadata and the already-collected sample values, so it remains fast even
when semantic schema learning is disabled.

The resulting profile is useful to retrieval/prompt construction because it
helps a small local model distinguish identifiers, dates, categories,
measures, and free text.
"""

import re
from dataclasses import dataclass, field
from typing import Dict, List, Set

from .schema_metadata import (
    ColumnMetadata,
    DatabaseMetadata,
    TableMetadata,
)


_DATE_NAME_HINT = re.compile(
    r"(date|_at$|_dt$|time|timestamp|year|month|day)",
    re.IGNORECASE,
)

_DATE_TYPE_HINT = re.compile(
    r"(DATE|TIME|TIMESTAMP)",
    re.IGNORECASE,
)

# Keep this conservative. A generic "id" substring can incorrectly classify
# business columns such as "identity_score" or "middle_identifier".
_ID_NAME_HINT = re.compile(
    r"(^id$|_id$|id$)",
    re.IGNORECASE,
)

_FREE_TEXT_NAME_HINT = re.compile(
    r"(name|title|description|desc|comment|note|address|bio|summary|content|text)",
    re.IGNORECASE,
)

_NUMERIC_TYPE_HINT = re.compile(
    r"(INT|DECIMAL|NUMERIC|FLOAT|DOUBLE|REAL|MONEY|NUMBER)",
    re.IGNORECASE,
)


@dataclass
class ColumnProfile:
    semantic_type: str
    distinct_sample_count: int = 0
    is_low_cardinality: bool = False


@dataclass
class TableProfile:
    role: str
    columns: Dict[str, ColumnProfile] = field(
        default_factory=dict
    )


class SchemaProfiler:
    """Infer semantic meaning from schema structure and sample values only."""

    def __init__(self, max_sample_values: int = 5):
        self.max_sample_values = max(
            1,
            min(int(max_sample_values), 10),
        )

    def profile(
        self,
        metadata: DatabaseMetadata,
        statistics: dict,
    ) -> Dict[str, TableProfile]:
        # Count incoming FK references once. This gives a cheap structural
        # signal for reference/core tables.
        incoming_fk_count = {
            name: 0
            for name in metadata.tables
        }

        # Track actual FK columns as identifiers even when their names do not
        # follow the usual *_id convention.
        foreign_key_columns: Dict[str, Set[str]] = {
            name: set()
            for name in metadata.tables
        }

        for table in metadata.tables.values():
            for fk in table.foreign_keys:
                referenced = fk.referenced_table

                if referenced in incoming_fk_count:
                    incoming_fk_count[referenced] += 1

                foreign_key_columns.setdefault(
                    table.name,
                    set(),
                ).add(fk.column)

        profiles: Dict[str, TableProfile] = {}

        for table in metadata.tables.values():
            role = self._infer_table_role(
                table,
                incoming_fk_count.get(
                    table.name,
                    0,
                ),
            )

            table_stats = statistics.get(
                table.name,
                {},
            )

            column_profiles = {}

            for column in table.columns:
                sample_values = table_stats.get(
                    column.name,
                    [],
                )

                column_profiles[column.name] = (
                    self._infer_column_profile(
                        column=column,
                        sample_values=sample_values,
                        is_foreign_key=(
                            column.name
                            in foreign_key_columns.get(
                                table.name,
                                set(),
                            )
                        ),
                    )
                )

            profiles[table.name] = TableProfile(
                role=role,
                columns=column_profiles,
            )

        return profiles

    @staticmethod
    def _infer_table_role(
        table: TableMetadata,
        incoming_fk_count: int,
    ) -> str:
        outgoing = len(table.foreign_keys)

        # Preserve the original project's terminology and intent.
        if outgoing >= 2:
            return "junction table (links multiple entities)"

        if outgoing == 1:
            return (
                "transactional table "
                "(records events tied to a reference entity)"
            )

        if outgoing == 0 and incoming_fk_count > 0:
            return (
                "reference table "
                "(a core entity other tables point to)"
            )

        return "standalone table"

    def _infer_column_profile(
        self,
        column: ColumnMetadata,
        sample_values: list,
        is_foreign_key: bool = False,
    ) -> ColumnProfile:
        # Use unique values because statistics normally contains DISTINCT
        # samples, but this keeps the profiler robust if another provider
        # supplies duplicates.
        try:
            distinct_count = len(
                {repr(value) for value in sample_values}
            )
        except Exception:
            distinct_count = len(sample_values)

        # Because sampling is capped, reaching the cap does NOT prove that
        # the column has low cardinality. Only classify as low-cardinality
        # when the observed distinct count is below the sampling ceiling.
        is_low_cardinality = (
            0 < distinct_count < self.max_sample_values
        )

        # Real FK metadata is stronger evidence than a naming convention.
        if column.primary_key or is_foreign_key:
            semantic_type = "identifier"

        elif _DATE_TYPE_HINT.search(
            column.data_type or ""
        ) or _DATE_NAME_HINT.search(
            column.name or ""
        ):
            semantic_type = "date_time"

        elif _NUMERIC_TYPE_HINT.search(
            column.data_type or ""
        ):
            semantic_type = "measure"

        elif _FREE_TEXT_NAME_HINT.search(
            column.name or ""
        ):
            # Names/descriptions are conceptually open-ended even if a small
            # current sample happens to contain repeated values.
            semantic_type = "free_text"

        elif is_low_cardinality:
            semantic_type = "categorical"

        else:
            semantic_type = "free_text"

        return ColumnProfile(
            semantic_type=semantic_type,
            distinct_sample_count=distinct_count,
            is_low_cardinality=is_low_cardinality,
        )