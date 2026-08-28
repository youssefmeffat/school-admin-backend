"""Exception hierarchy for the Text2SQL package."""

from typing import Optional


class Text2SQLError(Exception):
    """Base class for all errors raised by text2sql."""


class SchemaExtractionError(Text2SQLError):
    """Raised when database schema/metadata could not be introspected."""


class SQLGenerationError(Text2SQLError):
    """Raised when the LLM failed to produce usable SQL after all retries.

    Carries the last SQL produced by the model and the original error
    that caused the generation/validation failure.
    """

    def __init__(
        self,
        message: str,
        sql: str = "",
        original_error: Optional[Exception] = None,
    ):
        super().__init__(message)

        self.sql = sql
        self.original_error = original_error


class SQLValidationError(Text2SQLError):
    """Raised when generated SQL fails safety or syntax validation."""


class SQLExecutionError(Text2SQLError):
    """Raised when valid SQL fails to execute against the database."""


class DatabaseConnectionError(Text2SQLError):
    """Raised when the database cannot be reached.

    Examples:
    - invalid connection string
    - authentication failure
    - network failure
    - database server unavailable

    This is distinct from SQLExecutionError, which means the database
    connection exists but a particular query failed.
    """


class EmptyDatabaseError(Text2SQLError):
    """Raised when the connection succeeds but no usable tables are found."""


class InvalidQuestionError(Text2SQLError):
    """Raised for structurally invalid questions.

    Examples:
    - empty/whitespace-only question
    - question exceeding the configured maximum length
    """