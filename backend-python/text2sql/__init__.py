"""
General-purpose, database-agnostic Text-to-SQL package.

Point it at any SQLAlchemy-supported database and ask questions
in natural language. The pipeline returns:

- generated SQL
- executed query results
- a natural-language answer based on the actual data
"""

from .config import PipelineConfig
from .pipeline import Text2SQLPipeline, PipelineResult
from .cache import QueryCache
from .evaluation import EvalCase, Evaluator

from .exceptions import (
    Text2SQLError,
    SchemaExtractionError,
    SQLGenerationError,
    SQLValidationError,
    SQLExecutionError,
)

from .semantic_learning import (
    ColumnSemanticProfile,
    ColumnStatistics,
    SemanticSchemaLearner,
    TableSemanticProfile,
)

from .synthetic_sql import SyntheticSQLExampleGenerator
from .semantic_memory import SchemaScopedMemory, SemanticMemoryEntry
from .recommendations import RecommendationEngine
from .finetune import (
    FineTuneResult,
    FineTuneUnavailableError,
    SQLFineTuner,
)

__all__ = [
    "Text2SQLPipeline",
    "PipelineConfig",
    "PipelineResult",
    "QueryCache",
    "EvalCase",
    "Evaluator",
    "Text2SQLError",
    "SchemaExtractionError",
    "SQLGenerationError",
    "SQLValidationError",
    "SQLExecutionError",
    "SemanticSchemaLearner",
    "TableSemanticProfile",
    "ColumnSemanticProfile",
    "ColumnStatistics",
    "SyntheticSQLExampleGenerator",
    "SQLFineTuner",
    "FineTuneResult",
    "FineTuneUnavailableError",
    "SchemaScopedMemory",
    "SemanticMemoryEntry",
    "RecommendationEngine",
]

__version__ = "3.1.0"