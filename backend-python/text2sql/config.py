"""Configuration for the Text-to-SQL pipeline.

Everything the pipeline needs is expressed here so the whole system can be
driven from one object: database connection, local LLM, retrieval,
caching, SQL repair, safety, and semantic-learning behavior.
"""

import os
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class PipelineConfig:
    # ---- Database -----------------------------------------------------

    # Any SQLAlchemy connection string works, e.g.:
    # "sqlite:///restaurants.db"
    # "postgresql+psycopg2://user:pass@host:5432/dbname"
    # "mysql+pymysql://user:pass@host:3306/dbname"
    # "mssql+pyodbc://user:pass@host/dbname?driver=ODBC+Driver+17+for+SQL+Server"
    # "oracle+cx_oracle://user:pass@host:1521/service"
    # "snowflake://user:pass@account/database/schema?warehouse=wh"

    connection_string: str

    # Restrict introspection/retrieval to specific schemas or tables
    # when the database is large. None means discover everything.
    include_schemas: Optional[List[str]] = None
    include_tables: Optional[List[str]] = None

    # Tables to always skip during introspection (case-insensitive exact
    # match on table name), on top of the built-in migration-table list
    # below. Useful for other tool-generated bookkeeping tables that
    # aren't meant to be queried.
    exclude_tables: Optional[List[str]] = None

    # Migration/bookkeeping tables created by ORMs and migration tools
    # (Entity Framework, Alembic, Django, Flyway, Rails...) are never
    # useful for natural-language Q&A, and some of them are known to
    # trip up generic SQLAlchemy reflection (e.g. odd casing on MySQL).
    # Skipped automatically unless disabled.
    auto_exclude_migration_tables: bool = True

    # If a specific table fails introspection (bad reflection, permission
    # issue, exotic column type, etc.), skip just that table and keep
    # going instead of failing the entire connection. The vast majority
    # of a database being readable is far more useful than none of it
    # being usable because of one problem table.
    skip_unreadable_tables: bool = True

    # ---- LLM ---------------------------------------------------------

    # Qwen3-1.7B is a stronger drop-in small model than Qwen2.5-1.5B.
    # Overridable via the T2S_LLM_MODEL environment variable -- useful on
    # CPU-only hardware, where a smaller model (e.g. Qwen2.5-0.5B-Instruct)
    # generates materially faster at some accuracy cost. See README_DYNAMIC.md.
    llm_model: str = field(
        default_factory=lambda: os.environ.get(
            "T2S_LLM_MODEL", "Qwen/Qwen3-1.7B"
        )
    )

    # "auto" allows Transformers to choose the available device.
    llm_device_map: str = "auto"

    # Reduced from 220.
    # SQL generation normally does not need a very large output budget.
    llm_max_new_tokens: int = 128

    # Low temperature improves deterministic SQL generation.
    llm_temperature: float = 0.0

    # Deterministic generation is faster/more stable for Text-to-SQL.
    llm_do_sample: bool = False
    llm_top_p: float = 0.8
    llm_top_k: int = 20
    llm_local_files_only: bool = False

    # Keep False on the current CPU setup.
    # Can be enabled later only if the required GPU/quantization
    # dependencies are available.
    llm_load_in_4bit: bool = False

    # ---- Reasoning ---------------------------------------------------

    # Disabled for the current CPU/1.5B model.
    #
    # The previous configuration generated an additional reasoning line
    # before SQL. That increases inference time and is unnecessary for
    # straightforward database questions.
    use_reasoning: bool = False

    # Preserved for compatibility if reasoning is enabled later.
    reasoning_reserved_tokens: int = 60

    # ---- Embeddings / retrieval -------------------------------------

    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"

    # Default changed from "hybrid" to "keyword".
    #
    # "hybrid" pulls in a sentence-transformers embedding model (a second
    # model load, on top of the local generation LLM) purely to retrieve
    # which tables/columns are relevant to a question -- work that a
    # zero-dependency keyword match already does in well under a
    # millisecond for the small/medium schemas this pipeline targets.
    # Benchmarked against this project's own test schemas: keyword
    # retrieval selects the same tables as hybrid for every case in the
    # test suite, with no embedding model to download/initialize and no
    # per-query embedding inference cost.
    #
    # "hybrid"/"faiss" remain fully supported and are the better choice
    # for large schemas (many tables with overlapping/ambiguous natural-
    # language names) where semantic similarity meaningfully beats exact
    # keyword overlap -- set retrieval_strategy explicitly for those.
    retrieval_strategy: str = "keyword"

    # Reduced from 5 to 3.
    # For small/medium databases, fewer tables means a smaller prompt
    # without sacrificing useful schema context.
    top_k_tables: int = 3

    # Reduced from 4 to 2.
    # Keeps the candidate pool smaller before final selection.
    retrieval_pool_multiplier: int = 2

    # Keep 0.0 because retrieval score scales differ between
    # keyword/BM25/FAISS implementations.
    min_retrieval_score: float = 0.0

    # Disabled for CPU performance.
    #
    # The cross-encoder is an additional model and can be expensive
    # on CPU. Basic retrieval remains active.
    use_reranker: bool = False

    # Preserved for compatibility if reranking is enabled later.
    reranker_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"

    # ---- Query cache -------------------------------------------------

    # Keep caching enabled.
    enable_query_cache: bool = True

    query_cache_size: int = 256

    # None means cache entries do not expire.
    # Suitable for the current demo/session setup.
    query_cache_ttl_seconds: Optional[float] = None

    # ---- Token / cost control ---------------------------------------

    # Keep a small number of sample values per column.
    max_sample_values_per_column: int = 5

    # Maximum schema context inserted into an LLM prompt.
    max_schema_context_chars: int = 6000

    # Allow one SQL repair attempt.
    # (Lowered from 2 -> 1: on CPU-only hardware each repair attempt is
    # an additional sequential local LLM generation, and the combined
    # cost of intent classification + on-demand semantic learning + SQL
    # generation + repairs + answer synthesis routinely exceeded the
    # 120s PythonService timeout. See CHANGES for the paired timeout bump.)
    max_repair_attempts: int = 1

    # Never expose raw pipeline exceptions to the user.
    graceful_failure: bool = True

    # Reduced from 50 to 20.
    # The answer synthesizer also has its own compact-result protection.
    max_result_rows_for_answer: int = 20

    # Prevent runaway SQL execution.
    query_timeout_seconds: Optional[float] = 30.0

    # Maximum traversal depth for dynamically discovered self-referencing
    # hierarchies. Fast hierarchy queries never recurse beyond this bound.
    max_hierarchy_depth: int = 25

    # Protect against extremely long/non-question input.
    max_question_length: int = 2000

    # ---- Safety ------------------------------------------------------

    # Keep write operations disabled.
    allow_write_statements: bool = False

    forbidden_keywords: List[str] = field(
        default_factory=lambda: [
            "DROP",
            "DELETE",
            "UPDATE",
            "INSERT",
            "ALTER",
            "TRUNCATE",
            "CREATE",
            "GRANT",
            "REVOKE",
            "ATTACH",
            "DETACH",
        ]
    )

    # ---- Few-shot examples ------------------------------------------

    # Optional domain-specific examples.
    # These can improve accuracy for complex joins/aggregations without
    # requiring a larger model.
    few_shot_examples: List[dict] = field(default_factory=list)

    # ---- Semantic Schema Learning -----------------------------------

    # Dynamic semantic learning is enabled, but it is LAZY by default.
    # The system profiles every database immediately, then learns only the
    # most relevant table(s) when a question actually needs semantic
    # resolution. This avoids one LLM call per table during upload while
    # still allowing meanings such as "workers" -> an Employee-like table
    # or "$ amounts" -> a monetary/salary concept to be learned from the
    # current database rather than hardcoded.
    enable_semantic_learning: bool = False

    # Keep schema upload/startup fast. When False, semantic inference happens
    # on-demand for the tables retrieved for the current question.
    semantic_learning_eager: bool = False

    verbose_schema_learning: bool = True

    # Wider batches reduce the number of local LLM calls for semantic learning.
    semantic_llm_max_columns_per_call: int = 12
    semantic_llm_max_new_tokens: int = 384

    semantic_retrieval_strategy: Optional[str] = None
    semantic_embedding_model: Optional[str] = None
    semantic_top_k_columns: int = 8
    semantic_min_confidence_for_hint: float = 0.0

    # Query-time semantic learning limits. Only a small candidate set is
    # learned, then cached for the lifetime of this pipeline/database.
    semantic_max_candidate_tables_per_query: int = 2
    semantic_max_columns_per_query: int = 12

    # User teaching is generic and schema-scoped. It never creates
    # dataset-specific Python rules.
    enable_user_learning: bool = True
    max_user_memory_entries: int = 64
    user_memory_path: Optional[str] = None

    # Dynamic next-question recommendations are generated from the current
    # schema/result rather than a fixed dataset-specific list.
    enable_recommendations: bool = True
    recommendations_per_answer: int = 3

    # ---- Validation --------------------------------------------------

    def __post_init__(self):
        if not self.connection_string:
            raise ValueError("connection_string is required")

        if self.retrieval_strategy not in {
            "keyword",
            "bm25",
            "faiss",
            "hybrid",
        }:
            raise ValueError(
                f"Unknown retrieval_strategy: {self.retrieval_strategy}"
            )

        if self.llm_max_new_tokens < 1:
            raise ValueError("llm_max_new_tokens must be greater than 0")

        if self.top_k_tables < 1:
            raise ValueError("top_k_tables must be at least 1")

        if self.retrieval_pool_multiplier < 1:
            raise ValueError(
                "retrieval_pool_multiplier must be at least 1"
            )

        if self.max_result_rows_for_answer < 1:
            raise ValueError(
                "max_result_rows_for_answer must be at least 1"
            )

        if self.max_question_length < 1:
            raise ValueError(
                "max_question_length must be greater than 0"
            )