"""Schema metadata models and database-agnostic introspection.

The module uses DatabaseProvider exclusively, so the same metadata layer
works with SQLite, PostgreSQL, MySQL, MSSQL, Oracle, Snowflake, etc.

The introspector is intentionally lightweight: it reads table/column/FK
metadata once and produces an in-memory snapshot used by retrieval and
SQL generation.
"""

import warnings
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .db_providers import DatabaseProvider
from .exceptions import SchemaExtractionError

# Bookkeeping tables created by ORMs / migration tools. These are never
# meaningful for natural-language Q&A, and some are known to trip up
# generic SQLAlchemy reflection (e.g. casing quirks on MySQL). Matched
# case-insensitively against the table's simple name.
DEFAULT_EXCLUDED_TABLE_PATTERNS = {
    "__migrationhistory",
    "__efmigrationshistory",
    "alembic_version",
    "django_migrations",
    "schema_migrations",
    "flyway_schema_history",
    "sequelizemeta",
    "knex_migrations",
    "knex_migrations_lock",
}


@dataclass
class ColumnMetadata:
    name: str
    data_type: str
    nullable: bool
    primary_key: bool


@dataclass
class ForeignKeyMetadata:
    column: str
    referenced_table: str
    referenced_column: str


@dataclass
class TableMetadata:
    name: str
    columns: List[ColumnMetadata] = field(
        default_factory=list
    )
    primary_keys: List[str] = field(
        default_factory=list
    )
    foreign_keys: List[ForeignKeyMetadata] = field(
        default_factory=list
    )


@dataclass
class DatabaseMetadata:
    dialect: str = "unknown"
    tables: Dict[str, TableMetadata] = field(
        default_factory=dict
    )
    # Tables that were discovered but skipped -- either excluded by name
    # (e.g. migration-history tables) or that failed introspection and
    # were dropped rather than aborting the whole connection. Each entry
    # is (table_name, reason).
    skipped_tables: List[tuple] = field(
        default_factory=list
    )


class SchemaIntrospector:
    """Extract a DatabaseMetadata snapshot from a DatabaseProvider."""

    def __init__(
        self,
        provider: DatabaseProvider,
        include_tables: Optional[List[str]] = None,
        exclude_tables: Optional[List[str]] = None,
        auto_exclude_migration_tables: bool = True,
        skip_unreadable_tables: bool = True,
    ):
        self.provider = provider

        # Preserve the original list semantics while making membership tests
        # O(1) for large databases.
        self.include_tables = (
            set(include_tables)
            if include_tables
            else None
        )

        excluded = {
            name.lower() for name in (exclude_tables or [])
        }
        if auto_exclude_migration_tables:
            excluded |= DEFAULT_EXCLUDED_TABLE_PATTERNS
        self.exclude_tables = excluded

        self.skip_unreadable_tables = skip_unreadable_tables

    def extract(self) -> DatabaseMetadata:
        # ---------------------------------------------------------------
        # 1. Discover tables
        # ---------------------------------------------------------------

        try:
            table_names = self.provider.list_tables()
        except Exception as exc:
            raise SchemaExtractionError(
                f"Could not list tables: {exc}"
            ) from exc

        if self.include_tables is not None:
            table_names = [
                table
                for table in table_names
                if table in self.include_tables
            ]

        # Remove accidental duplicates while keeping discovery order.
        table_names = list(
            dict.fromkeys(table_names)
        )

        metadata = DatabaseMetadata(
            dialect=self.provider.dialect
        )

        # Drop known bookkeeping tables (migration history, etc.) up
        # front -- they're never useful for Q&A and some are known to
        # trip up generic reflection.
        if self.exclude_tables:
            filtered = []
            for table_name in table_names:
                if table_name.lower() in self.exclude_tables:
                    metadata.skipped_tables.append(
                        (table_name, "excluded by configuration")
                    )
                else:
                    filtered.append(table_name)
            table_names = filtered

        # ---------------------------------------------------------------
        # 2. Extract each table
        # ---------------------------------------------------------------

        for table_name in table_names:
            table = TableMetadata(
                name=table_name
            )

            try:
                # Columns
                columns = self.provider.get_columns(
                    table_name
                )

                for col in columns:
                    # Provider implementations are expected to expose these
                    # fields. Use explicit conversion for consistent metadata.
                    column = ColumnMetadata(
                        name=str(col["name"]),
                        data_type=str(col.get("type", "UNKNOWN")),
                        nullable=bool(
                            col.get("nullable", True)
                        ),
                        primary_key=bool(
                            col.get("primary_key", False)
                        ),
                    )

                    table.columns.append(
                        column
                    )

                    if column.primary_key:
                        table.primary_keys.append(
                            column.name
                        )

                # Foreign keys
                foreign_keys = (
                    self.provider.get_foreign_keys(
                        table_name
                    )
                )

                for fk in foreign_keys:
                    # Ignore malformed/incomplete FK metadata instead of
                    # allowing one optional relationship to break the entire
                    # schema snapshot.
                    local_column = fk.get(
                        "column"
                    )
                    referenced_table = fk.get(
                        "referenced_table"
                    )
                    referenced_column = fk.get(
                        "referenced_column"
                    )

                    if not (
                        local_column
                        and referenced_table
                        and referenced_column
                    ):
                        continue

                    table.foreign_keys.append(
                        ForeignKeyMetadata(
                            column=str(
                                local_column
                            ),
                            referenced_table=str(
                                referenced_table
                            ),
                            referenced_column=str(
                                referenced_column
                            ),
                        )
                    )

            except Exception as exc:
                reason = str(exc)
                if not self.skip_unreadable_tables:
                    raise SchemaExtractionError(
                        f"Failed to introspect table "
                        f"'{table_name}': {exc}"
                    ) from exc

                # Don't let one problem table (odd column type, casing
                # quirk, permission issue, ...) block every other table
                # in an otherwise perfectly usable database.
                warnings.warn(
                    f"Skipping table '{table_name}': could not "
                    f"introspect it ({reason}). The rest of the "
                    f"database will still be used.",
                    stacklevel=2,
                )
                metadata.skipped_tables.append(
                    (table_name, reason)
                )
                continue

            metadata.tables[
                table_name
            ] = table

        # ---------------------------------------------------------------
        # 3. Validate that something usable was discovered
        # ---------------------------------------------------------------

        if not metadata.tables:
            skipped_note = ""
            if metadata.skipped_tables:
                names = ", ".join(
                    f"'{name}' ({reason})"
                    for name, reason in metadata.skipped_tables
                )
                skipped_note = f" (skipped: {names})"

            if self.include_tables:
                raise SchemaExtractionError(
                    "No requested tables were discovered. "
                    "Check include_tables and connection_string."
                    f"{skipped_note}"
                )

            raise SchemaExtractionError(
                "No tables discovered. "
                "Check connection_string."
                f"{skipped_note}"
            )

        return metadata