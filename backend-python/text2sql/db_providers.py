"""Database providers.

Database-agnostic execution layer for the Text-to-SQL pipeline.

The preferred implementation uses SQLAlchemy so the same pipeline can work
with SQLite, PostgreSQL, MySQL, MSSQL, Oracle, Snowflake, etc.

A lightweight SQLite fallback is retained for environments where SQLAlchemy
is unavailable.
"""

import concurrent.futures
from abc import ABC, abstractmethod
from typing import Any, List, Optional

import pandas as pd

from .exceptions import DatabaseConnectionError, SQLExecutionError


def _run_with_timeout(
    fn,
    timeout_seconds: Optional[float],
    executor: Optional[concurrent.futures.ThreadPoolExecutor] = None,
):
    """Run a database operation with a wall-clock timeout.

    The timeout is implemented at the Python level so the same behavior can
    be used across database dialects.

    Important:
    A Python thread cannot forcibly terminate a database driver's running
    operation. When the timeout is reached, we immediately return a clean
    SQLExecutionError to the pipeline instead of waiting for the operation.

    Performance:
    Callers may pass a persistent single-worker executor (owned by the
    provider) so a fresh OS thread is not spawned/torn down on every
    single query -- creating a ThreadPoolExecutor per call is measurable
    overhead when many small queries run in a row.
    """

    if timeout_seconds is None or timeout_seconds <= 0:
        return fn()

    owns_executor = executor is None
    if owns_executor:
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)

    future = executor.submit(fn)

    try:
        return future.result(timeout=timeout_seconds)

    except concurrent.futures.TimeoutError as exc:
        # We cannot guarantee that the underlying DB driver stops
        # immediately, but we must not block the caller waiting for it.
        future.cancel()

        raise SQLExecutionError(
            f"Query exceeded the {timeout_seconds:g}s timeout."
        ) from exc

    finally:
        # Do NOT use "with ThreadPoolExecutor" here.
        #
        # Executor.__exit__ calls shutdown(wait=True), which could make
        # the caller wait for a slow database operation even after the
        # timeout has fired. A persistent, caller-owned executor must
        # never be shut down here -- only a locally-created one is.
        if owns_executor:
            executor.shutdown(wait=False, cancel_futures=True)


class DatabaseProvider(ABC):
    """Uniform database interface used by the rest of the pipeline."""

    @abstractmethod
    def connect(self) -> None:
        ...

    @abstractmethod
    def execute(
        self,
        sql: str,
        params: Optional[dict] = None,
    ) -> pd.DataFrame:
        ...

    @abstractmethod
    def list_tables(self) -> List[str]:
        ...

    @abstractmethod
    def get_columns(self, table_name: str) -> List[dict]:
        """Return dictionaries containing:

        name, type, nullable, primary_key
        """
        ...

    @abstractmethod
    def get_foreign_keys(self, table_name: str) -> List[dict]:
        """Return dictionaries containing:

        column, referenced_table, referenced_column
        """
        ...

    @property
    @abstractmethod
    def dialect(self) -> str:
        ...

    def close(self) -> None:
        pass


class SQLAlchemyProvider(DatabaseProvider):
    """Universal provider backed by SQLAlchemy Engine + Inspector."""

    def __init__(
        self,
        connection_string: str,
        query_timeout_seconds: Optional[float] = 30.0,
        **engine_kwargs: Any,
    ):
        self.connection_string = connection_string
        self.query_timeout_seconds = query_timeout_seconds
        self.engine_kwargs = engine_kwargs

        self.engine = None
        self._inspector = None
        self._executor: Optional[
            concurrent.futures.ThreadPoolExecutor
        ] = None

    def _get_executor(
        self,
    ) -> Optional[concurrent.futures.ThreadPoolExecutor]:
        """Lazily create one reusable worker thread for timed queries.

        Reusing a single-worker executor across calls avoids spawning and
        tearing down an OS thread for every query, which is measurable
        overhead when many small questions run back-to-back.
        """

        if (
            self.query_timeout_seconds is None
            or self.query_timeout_seconds <= 0
        ):
            return None

        if self._executor is None:
            self._executor = concurrent.futures.ThreadPoolExecutor(
                max_workers=1,
                thread_name_prefix="text2sql-sql-exec",
            )

        return self._executor

    def connect(self) -> None:
        import sqlalchemy as sa

        try:
            # Give SQLAlchemy a lightweight connection-health check.
            #
            # Caller-supplied engine options remain supported.
            if "pool_pre_ping" not in self.engine_kwargs:
                self.engine_kwargs["pool_pre_ping"] = True

            self.engine = sa.create_engine(
                self.connection_string,
                **self.engine_kwargs,
            )

            self._inspector = sa.inspect(self.engine)

            # Fail early if the connection string / driver / credentials
            # are invalid.
            with self.engine.connect():
                pass

        except Exception as exc:
            self.engine = None
            self._inspector = None

            raise DatabaseConnectionError(
                f"Could not connect to the database: {exc}"
            ) from exc

    def _require_connection(self) -> None:
        if self.engine is None:
            raise SQLExecutionError(
                "connect() must be called before using the database."
            )

    def execute(
        self,
        sql: str,
        params: Optional[dict] = None,
    ) -> pd.DataFrame:
        self._require_connection()

        def _run():
            with self.engine.connect() as conn:
                return pd.read_sql_query(
                    sql,
                    conn,
                    params=params,
                )

        try:
            return _run_with_timeout(
                _run,
                self.query_timeout_seconds,
                executor=self._get_executor(),
            )

        except SQLExecutionError:
            raise

        except Exception as exc:
            raise SQLExecutionError(
                str(exc)
            ) from exc

    def list_tables(self) -> List[str]:
        self._require_connection()

        tables = []

        schemas = self._inspector.get_schema_names() or [None]

        for schema in schemas:
            try:
                schema_tables = self._inspector.get_table_names(
                    schema=schema
                )

                tables.extend(schema_tables)

            except Exception:
                # One inaccessible schema should not prevent the rest
                # of the database from being discovered.
                continue

        return sorted(set(tables))

    def get_columns(
        self,
        table_name: str,
    ) -> List[dict]:
        self._require_connection()

        try:
            pk_info = self._inspector.get_pk_constraint(
                table_name
            )

            pk_cols = set(
                pk_info.get("constrained_columns") or []
            )

            columns = []

            for col in self._inspector.get_columns(table_name):
                columns.append(
                    {
                        "name": col["name"],
                        "type": str(col["type"]),
                        "nullable": bool(
                            col.get("nullable", True)
                        ),
                        "primary_key": (
                            col["name"] in pk_cols
                        ),
                    }
                )

            return columns

        except Exception as exc:
            raise DatabaseConnectionError(
                f"Could not inspect columns for "
                f"table '{table_name}': {exc}"
            ) from exc

    def get_foreign_keys(
        self,
        table_name: str,
    ) -> List[dict]:
        self._require_connection()

        try:
            fks = []

            for fk in self._inspector.get_foreign_keys(
                table_name
            ):
                referred_table = fk.get(
                    "referred_table"
                )

                local_cols = (
                    fk.get("constrained_columns")
                    or []
                )

                remote_cols = (
                    fk.get("referred_columns")
                    or []
                )

                for local, remote in zip(
                    local_cols,
                    remote_cols,
                ):
                    fks.append(
                        {
                            "column": local,
                            "referenced_table": referred_table,
                            "referenced_column": remote,
                        }
                    )

            return fks

        except Exception as exc:
            raise DatabaseConnectionError(
                f"Could not inspect foreign keys for "
                f"table '{table_name}': {exc}"
            ) from exc

    @property
    def dialect(self) -> str:
        if self.engine is None:
            return "unknown"

        return self.engine.dialect.name

    def close(self) -> None:
        if self.engine is not None:
            self.engine.dispose()

        self.engine = None
        self._inspector = None

        if self._executor is not None:
            self._executor.shutdown(wait=False, cancel_futures=True)
            self._executor = None


class SQLiteFallbackProvider(DatabaseProvider):
    """SQLite-only fallback when SQLAlchemy is unavailable."""

    def __init__(
        self,
        connection_string: str,
        query_timeout_seconds: Optional[float] = 30.0,
    ):
        self.path = (
            connection_string
            .replace("sqlite:///", "")
            .replace("sqlite://", "")
        )

        self.query_timeout_seconds = query_timeout_seconds
        self.connection = None
        self._executor: Optional[
            concurrent.futures.ThreadPoolExecutor
        ] = None

    def _get_executor(
        self,
    ) -> Optional[concurrent.futures.ThreadPoolExecutor]:
        if (
            self.query_timeout_seconds is None
            or self.query_timeout_seconds <= 0
        ):
            return None

        if self._executor is None:
            self._executor = concurrent.futures.ThreadPoolExecutor(
                max_workers=1,
                thread_name_prefix="text2sql-sql-exec",
            )

        return self._executor

    def connect(self) -> None:
        import sqlite3

        try:
            self.connection = sqlite3.connect(
                self.path,
                check_same_thread=False,
            )

            # Enforce foreign-key constraints.
            self.connection.execute(
                "PRAGMA foreign_keys = ON;"
            )

        except Exception as exc:
            raise DatabaseConnectionError(
                f"Could not open SQLite database: {exc}"
            ) from exc

    def _require_connection(self) -> None:
        if self.connection is None:
            raise SQLExecutionError(
                "connect() must be called before using the database."
            )

    def execute(
        self,
        sql: str,
        params: Optional[dict] = None,
    ) -> pd.DataFrame:
        self._require_connection()

        def _run():
            return pd.read_sql_query(
                sql,
                self.connection,
                params=params,
            )

        try:
            return _run_with_timeout(
                _run,
                self.query_timeout_seconds,
                executor=self._get_executor(),
            )

        except SQLExecutionError:
            raise

        except Exception as exc:
            raise SQLExecutionError(
                str(exc)
            ) from exc

    def list_tables(self) -> List[str]:
        self._require_connection()

        df = self.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table'
              AND name NOT LIKE 'sqlite_%'
            ORDER BY name;
            """
        )

        return df["name"].tolist()

    def get_columns(
        self,
        table_name: str,
    ) -> List[dict]:
        self._require_connection()

        df = self.execute(
            f"PRAGMA table_info('{table_name}')"
        )

        return [
            {
                "name": row["name"],
                "type": row["type"],
                "nullable": not bool(row["notnull"]),
                "primary_key": bool(row["pk"]),
            }
            for _, row in df.iterrows()
        ]

    def get_foreign_keys(
        self,
        table_name: str,
    ) -> List[dict]:
        self._require_connection()

        df = self.execute(
            f"PRAGMA foreign_key_list('{table_name}')"
        )

        return [
            {
                "column": row["from"],
                "referenced_table": row["table"],
                "referenced_column": row["to"],
            }
            for _, row in df.iterrows()
        ]

    @property
    def dialect(self) -> str:
        return "sqlite"

    def close(self) -> None:
        if self.connection is not None:
            self.connection.close()

        self.connection = None

        if self._executor is not None:
            self._executor.shutdown(wait=False, cancel_futures=True)
            self._executor = None


def make_provider(
    connection_string: str,
    query_timeout_seconds: Optional[float] = 30.0,
    **engine_kwargs: Any,
) -> DatabaseProvider:
    """Create the best available provider for the connection string."""

    try:
        import sqlalchemy  # noqa: F401

        return SQLAlchemyProvider(
            connection_string,
            query_timeout_seconds=query_timeout_seconds,
            **engine_kwargs,
        )

    except ImportError:
        if not connection_string.startswith("sqlite"):
            raise ImportError(
                "SQLAlchemy (plus the relevant DB driver) is required "
                "for non-SQLite connections. Install with: "
                "pip install sqlalchemy\n"
                f"Connection string was: {connection_string}"
            )

        return SQLiteFallbackProvider(
            connection_string,
            query_timeout_seconds=query_timeout_seconds,
        )