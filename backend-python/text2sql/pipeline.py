"""Text2SQLPipeline: the single object applications interact with.

This module coordinates:

1. Schema discovery
2. Statistics and semantic profiling
3. Retrieval
4. Prompt construction
5. Local LLM SQL generation
6. SQL validation
7. Semantic SQL alignment checks
8. Self-repair
9. Database execution
10. Grounded answer synthesis
"""

import re
import time
from dataclasses import dataclass, field, replace
from typing import Optional

import pandas as pd

from .answer import AnswerSynthesizer
from .cache import QueryCache
from .config import PipelineConfig
from .db_providers import make_provider
from .exceptions import (
    DatabaseConnectionError,
    EmptyDatabaseError,
    InvalidQuestionError,
    SchemaExtractionError,
    SQLExecutionError,
    SQLGenerationError,
    SQLValidationError,
    Text2SQLError,
)
from .intent import (
    DATABASE_QUERY,
    GREETING,
    OFFENSIVE,
    SMALL_TALK,
    UNKNOWN,
    IntentClassifier,
    is_ambiguous,
)
from .llm_backends import HuggingFaceBackend, LLMBackend
from .prompting import PromptBuilder, SQLCleaner
from .relationships import RelationshipGraph, StatisticsService
from .synthetic_sql import SyntheticSQLExampleGenerator
from .finetune import SQLFineTuner, FineTuneResult
from .retrieval import RetrievalDocument, SchemaDocumentBuilder, build_reranker, build_retriever
from .schema_metadata import DatabaseMetadata, SchemaIntrospector
from .schema_profiler import SchemaProfiler
from .semantic_learning import (
    ColumnSemanticProfile,
    SemanticSchemaLearner,
    TableSemanticProfile,
    build_semantic_hint_block,
    build_semantic_table_hint_block,
)
from .semantic_memory import SchemaScopedMemory
from .recommendations import RecommendationEngine
from .token_tracker import TokenTracker
from .validation import SQLValidator


@dataclass
class PipelineResult:
    question: str
    sql: str
    dataframe: pd.DataFrame
    answer: str
    repair_attempts: int
    token_usage: TokenTracker
    tables_used: list = field(default_factory=list)
    success: bool = True
    error: Optional[str] = None
    elapsed_seconds: float = 0.0
    from_cache: bool = False
    recommendations: list = field(default_factory=list)
    # Lightweight per-stage wall-clock timings (seconds), e.g.
    # {"intent": 0.0002, "schema_load": 0.0001, "retrieval": 0.0009,
    #  "semantic_learning": 0.0, "prompt_build": 0.0001,
    #  "sql_generate_validate_execute": 0.41, "answer_synthesis": 0.0}.
    # Populated on the normal database-query path only; fast paths
    # (greeting/small talk/cache-hit/etc.) are already near-zero and are
    # not individually broken down. Intended for measuring where time
    # actually goes -- not guessing -- per request.
    stage_timings: dict = field(default_factory=dict)


class Text2SQLPipeline:

    def __init__(
        self,
        config: PipelineConfig,
        llm_backend: Optional[LLMBackend] = None,
    ):
        self.config = config

        self.llm_backend = llm_backend or HuggingFaceBackend(
            model_name=config.llm_model,
            device_map=config.llm_device_map,
            load_in_4bit=config.llm_load_in_4bit,
            do_sample=config.llm_do_sample,
            top_p=config.llm_top_p,
            top_k=config.llm_top_k,
            local_files_only=config.llm_local_files_only,
        )

        self.provider = make_provider(
            config.connection_string,
            query_timeout_seconds=config.query_timeout_seconds,
        )

        # Database connection is lazy. This keeps greeting/small-talk/offensive
        # requests from opening a database connection before we know that a
        # database query is actually needed. The existing explicit
        # learn_schema()/warm_up() workflow still initializes everything when
        # the application wants a fully warmed pipeline.
        self._provider_connected = False

        self.metadata: Optional[DatabaseMetadata] = None
        self.statistics: dict = {}
        self.semantic_profile: dict = {}
        self.semantic_schema: dict = {}

        self.column_retriever = None
        self.retriever = None
        self.documents = []

        self.graph = RelationshipGraph()

        self.prompt_builder = PromptBuilder(
            max_schema_context_chars=config.max_schema_context_chars,
            few_shot_examples=config.few_shot_examples,
        )

        self.cleaner = SQLCleaner()

        self.intent_classifier = IntentClassifier()

        self.answer_synthesizer = AnswerSynthesizer(
            self.llm_backend,
            max_rows=config.max_result_rows_for_answer,
        )

        self.reranker = build_reranker(
            config.use_reranker,
            config.reranker_model,
        )

        self.cache: Optional[QueryCache] = (
            QueryCache(
                max_size=config.query_cache_size,
                ttl_seconds=config.query_cache_ttl_seconds,
            )
            if config.enable_query_cache
            else None
        )

        self._indexed = False

        # Lazy semantic learner: initialized only after schema discovery.
        # It is deliberately not run during upload unless eager learning is
        # explicitly requested.
        self._semantic_learner = None
        self._semantic_learned_tables = set()

        # Schema-scoped user teaching. No mapping is valid outside this
        # database fingerprint.
        self.user_memory = None

        self.recommendation_engine = RecommendationEngine(
            max_recommendations=config.recommendations_per_answer,
        )

    # ------------------------------------------------------------------
    # Resource initialization
    # ------------------------------------------------------------------

    def _ensure_provider_connected(self) -> None:
        """Connect to the database exactly once, on first DB-dependent use."""
        if self._provider_connected:
            return

        self.provider.connect()
        self._provider_connected = True

    # ------------------------------------------------------------------
    # Warm up
    # ------------------------------------------------------------------

    def warm_up(self) -> None:
        self.learn_schema()
        self.llm_backend.warm_up()

    # ------------------------------------------------------------------
    # Schema learning
    # ------------------------------------------------------------------

    def learn_schema(self) -> DatabaseMetadata:

        if self._indexed:
            return self.metadata

        self._ensure_provider_connected()

        introspector = SchemaIntrospector(
            self.provider,
            include_tables=self.config.include_tables,
            exclude_tables=self.config.exclude_tables,
            auto_exclude_migration_tables=self.config.auto_exclude_migration_tables,
            skip_unreadable_tables=self.config.skip_unreadable_tables,
        )

        self.metadata = introspector.extract()

        if self.metadata.skipped_tables and self.config.verbose_schema_learning:
            names = ", ".join(
                f"'{name}' ({reason})"
                for name, reason in self.metadata.skipped_tables
            )
            print(
                f"[text2sql] Skipped {len(self.metadata.skipped_tables)} "
                f"table(s) during schema learning: {names}"
            )

        if not self.metadata.tables:
            raise EmptyDatabaseError(
                "Connected to the database, but it has no tables "
                "(or include_tables/include_schemas filtered everything out). "
                "Nothing to query."
            )

        # --------------------------------------------------------------
        # Statistics
        # --------------------------------------------------------------

        stats_service = StatisticsService(
            self.provider,
            max_values=self.config.max_sample_values_per_column,
        )

        statistics = stats_service.analyze(self.metadata)

        self.statistics = statistics

        # --------------------------------------------------------------
        # Structural semantic profiling
        # --------------------------------------------------------------

        profiler = SchemaProfiler(
            max_sample_values=self.config.max_sample_values_per_column
        )

        self.semantic_profile = profiler.profile(
            self.metadata,
            statistics,
        )

        # Build cheap structural semantic profiles immediately. These are
        # heuristic-only labels derived from the schema profiler and require
        # no LLM call. They provide a useful semantic_schema for exact
        # schema-grounded questions while keeping expensive semantic
        # inference as a true fallback. The LLM learner can later replace
        # these profiles for synonym-heavy questions.
        self.semantic_schema = {}
        for table_name, table_meta in self.metadata.tables.items():
            table_profile = self.semantic_profile.get(table_name)
            semantic_table = TableSemanticProfile(
                name=table_name,
                role=(
                    table_profile.role
                    if table_profile is not None
                    else "unknown role"
                ),
                semantic_name=table_name,
                confidence=0.35,
                inferred_by="heuristic",
            )

            for column in table_meta.columns:
                structural_type = "unknown"
                if (
                    table_profile is not None
                    and column.name in table_profile.columns
                ):
                    structural_type = table_profile.columns[
                        column.name
                    ].semantic_type

                column_profile = ColumnSemanticProfile(
                    table=table_name,
                    original_name=column.name,
                    sql_type=column.data_type,
                    structural_type=structural_type,
                )
                # Generic, domain-agnostic heuristic only: turn the raw
                # identifier into a readable label and preserve the
                # structural category. No domain-specific mapping is used.
                readable = re.sub(r"[_\-]+", " ", column.name).strip()
                readable = re.sub(
                    r"(?<=[a-z])(?=[A-Z])", " ", readable
                )
                column_profile.semantic_name = (
                    readable.title() if readable else column.name
                )
                column_profile.business_meaning = (
                    f"Column '{column.name}' ({structural_type})."
                )
                column_profile.data_category = {
                    "identifier": "Identifier",
                    "date_time": "Temporal",
                    "measure": "Numeric",
                    "categorical": "Categorical",
                    "free_text": "Descriptive",
                }.get(structural_type, "Unknown")
                column_profile.confidence = 0.35
                column_profile.inferred_by = "heuristic"
                semantic_table.columns[column.name] = column_profile

            self.semantic_schema[table_name] = semantic_table

        # --------------------------------------------------------------
        # Semantic learning
        # --------------------------------------------------------------
        #
        # Fast default: structural profiling happens during schema build,
        # while expensive LLM semantic inference is deferred until a question
        # actually needs it. This preserves dynamic semantic learning without
        # adding one LLM call per table to every upload.
        self.column_retriever = None
        self._semantic_learned_tables = set()

        if self.config.enable_user_learning:
            self.user_memory = SchemaScopedMemory(
                self.metadata,
                max_entries=self.config.max_user_memory_entries,
                path=self.config.user_memory_path,
            )

        if (
            self.config.enable_semantic_learning
            and self.config.semantic_learning_eager
        ):
            self._learn_semantics_for_tables(
                list(self.metadata.tables.keys())
            )

        # --------------------------------------------------------------
        # Retrieval documents
        # --------------------------------------------------------------

        self.documents = SchemaDocumentBuilder(
            max_sample_values=self.config.max_sample_values_per_column
        ).build(
            self.metadata,
            statistics,
            profiles=self.semantic_profile,
            semantic_profiles=self.semantic_schema,
        )

        # --------------------------------------------------------------
        # Relationship graph
        # --------------------------------------------------------------

        self.graph.build(self.metadata)

        # --------------------------------------------------------------
        # Retriever
        # --------------------------------------------------------------

        self.retriever = build_retriever(
            self.config.retrieval_strategy,
            self.config.embedding_model,
        )

        self.retriever.build(self.documents)

        # --------------------------------------------------------------
        # Validator
        # --------------------------------------------------------------

        self.validator = SQLValidator(
            forbidden_keywords=self.config.forbidden_keywords,
            allow_write_statements=self.config.allow_write_statements,
            metadata=self.metadata,
        )

        # --------------------------------------------------------------
        # Automatic few-shot examples
        # --------------------------------------------------------------

        auto_examples = self._auto_few_shot_examples(
            self.metadata,
            self.semantic_profile,
            statistics,
        )

        existing_questions = {
            e["question"]
            for e in self.config.few_shot_examples
        }

        combined = list(self.config.few_shot_examples) + [
            ex
            for ex in auto_examples
            if ex["question"] not in existing_questions
        ]

        self.prompt_builder.few_shot_examples = combined

        self._indexed = True

        return self.metadata

    # ------------------------------------------------------------------
    # Dynamic semantic learning
    # ------------------------------------------------------------------

    @staticmethod
    def _semantic_candidate_columns(
        table,
        question: str,
        limit: int,
        statistics: Optional[dict] = None,
    ) -> list:
        """Select the most informative columns for one semantic-learning call.

        This keeps the LLM prompt small while still surfacing opaque monetary,
        date, categorical and identifier evidence.
        """
        q = set(re.findall(r"[a-z0-9]+", (question or "").lower()))
        scored = []

        for column in table.columns:
            name_tokens = set(
                re.findall(
                    r"[a-z0-9]+",
                    re.sub(
                        r"(?<=[a-z])(?=[A-Z])",
                        " ",
                        column.name,
                    ).lower(),
                )
            )
            score = 0.0
            score += 4.0 * len(q & name_tokens)

            structural = getattr(
                getattr(column, "profile", None),
                "semantic_type",
                "",
            )

            if column.primary_key:
                score += 1.0

            if re.search(
                r"(INT|DECIMAL|NUMERIC|FLOAT|DOUBLE|REAL|MONEY|NUMBER)",
                column.data_type or "",
                re.I,
            ):
                score += 2.0

            if re.search(
                r"(date|time|year|month|day)",
                column.name or "",
                re.I,
            ):
                score += 1.5

            if re.search(
                r"(name|title|description|address|text|status|type|category)",
                column.name or "",
                re.I,
            ):
                score += 1.0

            samples = (statistics or {}).get(column.name, []) or []
            sample_text = " ".join(str(v) for v in samples[:5])

            # Generic data-pattern evidence. These are not domain-specific
            # mappings; they only describe the observed value format.
            if re.search(r"[$€£¥]\s*[-+]?\d", sample_text):
                score += 4.0
            if re.search(r"\b\d+(?:\.\d+)?\s*%", sample_text):
                score += 2.0
            if re.search(
                r"\b\d{4}[-/]\d{1,2}[-/]\d{1,2}\b",
                sample_text,
            ):
                score += 2.0

            scored.append((score, column))

        scored.sort(key=lambda item: item[0], reverse=True)
        return [column.name for _, column in scored[:max(1, int(limit))]]

    def _learn_semantics_for_tables(self, table_names, question: str = "") -> None:
        """Learn and cache semantics only for the requested database tables."""
        if not self.metadata or not self.config.enable_semantic_learning:
            return

        names = [
            name for name in dict.fromkeys(table_names or [])
            if name in self.metadata.tables
            and name not in self._semantic_learned_tables
        ]
        if not names:
            return

        if self._semantic_learner is None:
            self._semantic_learner = SemanticSchemaLearner(
                self.provider,
                llm_backend=self.llm_backend,
                max_sample_values=self.config.max_sample_values_per_column,
                max_columns_per_llm_call=self.config.semantic_llm_max_columns_per_call,
                max_new_tokens=self.config.semantic_llm_max_new_tokens,
                retrieval_strategy=(
                    self.config.semantic_retrieval_strategy
                    or self.config.retrieval_strategy
                ),
                embedding_model=(
                    self.config.semantic_embedding_model
                    or self.config.embedding_model
                ),
            )

        selected_metadata = DatabaseMetadata(
            dialect=self.metadata.dialect,
            tables={
                name: self.metadata.tables[name]
                for name in names
            },
        )
        selected_profiles = {
            name: self.semantic_profile[name]
            for name in names
            if name in self.semantic_profile
        }

        include_columns = {
            name: self._semantic_candidate_columns(
                self.metadata.tables[name],
                question,
                self.config.semantic_max_columns_per_query,
                statistics=self.statistics.get(name, {}),
            )
            for name in names
        }

        result = self._semantic_learner.learn(
            selected_metadata,
            selected_profiles,
            verbose=self.config.verbose_schema_learning,
            include_tables=names,
            include_columns=include_columns,
        )

        self.semantic_schema.update(result.table_profiles)
        self._semantic_learned_tables.update(names)

        # Rebuild the column semantic retriever over only what has actually
        # been learned. The embedding model itself is loaded once and reused.
        all_documents = []
        for table_profile in self.semantic_schema.values():
            for col_name, col_profile in table_profile.columns.items():
                all_documents.append(
                    RetrievalDocument(
                        document_id=f"{table_profile.name}.{col_name}",
                        table=table_profile.name,
                        text=col_profile.embedding_text,
                        metadata={
                            "table": table_profile.name,
                            "column": col_name,
                            "profile": col_profile,
                        },
                    )
                )

        from .retrieval import build_retriever
        self.column_retriever = build_retriever(
            self.config.semantic_retrieval_strategy
            or self.config.retrieval_strategy,
            self.config.semantic_embedding_model
            or self.config.embedding_model,
        )
        if all_documents:
            self.column_retriever.build(all_documents)

    def _learn_semantics_for_question(self, question: str, retrieved) -> None:
        """Trigger targeted semantic learning for the current question."""
        if (
            not self.config.enable_semantic_learning
            or not self.metadata
        ):
            return

        candidates = [
            item["document"].table
            for item in (retrieved or [])
            if item.get("document") is not None
        ]

        candidates = list(dict.fromkeys(candidates))
        max_tables = max(
            1,
            int(self.config.semantic_max_candidate_tables_per_query),
        )

        if len(candidates) < max_tables:
            # For small databases, learning all tables is still bounded and
            # gives the semantic model enough context to resolve concepts.
            if len(self.metadata.tables) <= max_tables:
                candidates = list(self.metadata.tables.keys())

        candidates = candidates[:max_tables]

        if candidates:
            self._learn_semantics_for_tables(
                candidates,
                question=question,
            )

    # ------------------------------------------------------------------
    # User teaching
    # ------------------------------------------------------------------

    _TEACH_PATTERNS = (
        re.compile(
            r"^\s*when\s+i\s+say\s+(.+?)\s*,?\s*i\s+mean\s+(.+?)\s*\.?\s*$",
            re.IGNORECASE,
        ),
        re.compile(
            r"^\s*(?:remember|learn|note)\s+(?:that\s+)?(.+?)\s+(?:means|is)\s+(.+?)\s*\.?\s*$",
            re.IGNORECASE,
        ),
        re.compile(
            r"^\s*column\s+([A-Za-z_][A-Za-z0-9_]*)\s+(?:means|is)\s+(.+?)\s*\.?\s*$",
            re.IGNORECASE,
        ),
    )

    def _maybe_learn_user_instruction(self, question: str):
        if not self.config.enable_user_learning:
            return None

        match = None
        for pattern in self._TEACH_PATTERNS:
            match = pattern.match(question or "")
            if match:
                break

        if not match:
            return None

        self._ensure_provider_connected()
        if not self._indexed:
            self.learn_schema()

        phrase = " ".join(match.group(1).strip().split())
        meaning = " ".join(match.group(2).strip().split())

        # If the user explicitly names a real column/table, keep that
        # grounding. Otherwise the phrase/meaning is stored as a generic
        # concept that semantic retrieval can resolve later.
        table = None
        column = None
        for table_meta in self.metadata.tables.values():
            for col in table_meta.columns:
                if col.name.casefold() == phrase.casefold():
                    table = table_meta.name
                    column = col.name
                    break
            if table:
                break

        self.user_memory.teach(
            phrase,
            meaning,
            table=table,
            column=column,
        )

        # A semantic correction changes SQL resolution, so cached answers
        # generated before the teaching event are no longer authoritative.
        if self.cache is not None:
            self.cache.clear()

        return PipelineResult(
            question=question,
            sql="-- semantic teaching instruction; no SQL executed --",
            dataframe=pd.DataFrame(),
            answer=(
                f"Learned that \"{phrase}\" means \"{meaning}\" "
                "for this database. I'll use that mapping in future questions."
            ),
            repair_attempts=0,
            token_usage=TokenTracker(),
            tables_used=[],
            success=True,
        )

    # ------------------------------------------------------------------
    # Automatic few-shot examples
    # ------------------------------------------------------------------

    @staticmethod
    def _auto_few_shot_examples(
        metadata: DatabaseMetadata,
        profiles: dict,
        statistics: dict,
    ) -> list:

        examples = []

        for table_name, profile in profiles.items():

            if len(examples) >= 3:
                break

            cat_col = next(
                (
                    c
                    for c, p in profile.columns.items()
                    if p.semantic_type == "categorical"
                ),
                None,
            )

            measure_col = next(
                (
                    c
                    for c, p in profile.columns.items()
                    if p.semantic_type == "measure"
                ),
                None,
            )

            if cat_col:

                values = (
                    statistics.get(table_name, {}) or {}
                ).get(cat_col) or []

                values = [
                    v for v in values
                    if v not in (None, "")
                ]

                if values:

                    sample_value = values[0]

                    examples.append({
                        "question": (
                            f"Show all {table_name} "
                            f"where {cat_col} is {sample_value}"
                        ),
                        "sql": (
                            f"SELECT * FROM {table_name} "
                            f"WHERE {cat_col} = '{sample_value}';"
                        ),
                    })

            if measure_col and cat_col:

                examples.append({
                    "question": (
                        f"What is the total {measure_col} "
                        f"for each {cat_col} in {table_name}?"
                    ),
                    "sql": (
                        f"SELECT {cat_col}, "
                        f"SUM({measure_col}) AS Total{measure_col} "
                        f"FROM {table_name} "
                        f"GROUP BY {cat_col};"
                    ),
                }  )

            elif measure_col:

                examples.append({
                    "question": (
                        f"What is the average {measure_col} "
                        f"across all {table_name}?"
                    ),
                    "sql": (
                        f"SELECT AVG({measure_col}) "
                        f"AS Avg{measure_col} "
                        f"FROM {table_name};"
                    ),
                })

        # Join example
        for table in metadata.tables.values():

            if table.foreign_keys:

                fk = table.foreign_keys[0]

                examples.append({
                    "question": (
                        f"List each {table.name} row together "
                        f"with its related {fk.referenced_table} details"
                    ),
                    "sql": (
                        f"SELECT * FROM {table.name} t "
                        f"JOIN {fk.referenced_table} r "
                        f"ON t.{fk.column} = "
                        f"r.{fk.referenced_column};"
                    ),
                })

                break

        return examples[:5]

    # ------------------------------------------------------------------
    # Schema metadata question detection
    # ------------------------------------------------------------------

    _SCHEMA_META_RE = re.compile(
        r"^\s*(what|which|list|show( me)?|describe|tell me about|"
        r"how many)\b.{0,40}\b(tables?|columns?|schema)\b",
        re.IGNORECASE,
    )

    _ROW_COUNT_RE = re.compile(
        r"how many rows|row count|rows (does|do) "
        r"(each|the) table",
        re.IGNORECASE,
    )

    # ------------------------------------------------------------------
    # Semantic SQL alignment guards
    # ------------------------------------------------------------------

    _TOP_K_RE = re.compile(
        r"\b(highest|lowest|top|bottom|most|least|"
        r"greatest|smallest)\b",
        re.IGNORECASE,
    )

    _TOP_K_PERSON_RE = re.compile(
        r"\b(who|which|what)\b.{0,100}\b"
        r"(highest|lowest|top|bottom|most|least|"
        r"greatest|smallest)\b",
        re.IGNORECASE,
    )

    _HOW_MANY_ENTITY_RE = re.compile(
        r"^\s*how many\s+"
        r"([A-Za-z][A-Za-z0-9_ -]*?)"
        r"(?=\s+(?:are|is|have|has|do|does|were|was|"
        r"with|where|that)\b|\?|$)",
        re.IGNORECASE,
    )

    _GENERIC_COUNT_WORDS = {
        "rows",
        "records",
        "entries",
        "items",
    }

    # A BARE row-count question ("how many roles", "how many users are
    # there?", "how many roles exist") has exactly one possible correct
    # query. Anything with extra filter language after the entity word
    # (e.g. "how many users are active") must NOT match this -- it needs
    # real SQL generation -- so the trailing group only accepts generic,
    # non-filtering phrasing.
    # Built as fully anchored (^...$) alternatives -- NOT a single pattern
    # with an optional trailing group -- so a lazy entity capture cannot
    # backtrack into swallowing filter words (e.g. "are active") just
    # because the trailing phrase is "allowed to be absent". Each
    # alternative pins its own trailing literal, so the entity group can
    # only ever stop exactly where each alternative says it must.
    _SIMPLE_COUNT_RE = re.compile(
        r"^\s*how many\s+([A-Za-z][A-Za-z0-9_ -]*?)\s*"
        r"(?:are|is)\s+there\s*\??\s*$"
        r"|"
        r"^\s*how many\s+([A-Za-z][A-Za-z0-9_ -]*?)\s*"
        r"(?:are|is)\s+in\s+(?:the|this)\s+"
        r"(?:database|table|system)\s*\??\s*$"
        r"|"
        r"^\s*how many\s+([A-Za-z][A-Za-z0-9_ -]*?)\s*"
        r"exist(?:s)?\s*\??\s*$"
        r"|"
        r"^\s*how many\s+([A-Za-z][A-Za-z0-9_ -]*?)\s*"
        r"do\s+(?:we|you)\s+have\s*\??\s*$"
        r"|"
        r"^\s*how many\s+([A-Za-z][A-Za-z0-9_ -]*?)\s*\??\s*$",
        re.IGNORECASE,
    )

    # ------------------------------------------------------------------
    # Direct intent answers
    # ------------------------------------------------------------------

    def _answer_small_talk(
        self,
        question: str,
    ) -> PipelineResult:

        answer = (
            "I'm a text-to-SQL assistant -- I turn plain-English "
            "questions into queries against this database. "
            "Ask me something about the data whenever you're ready!"
        )

        return PipelineResult(
            question=question,
            sql="-- small talk, no query needed --",
            dataframe=pd.DataFrame(),
            answer=answer,
            repair_attempts=0,
            token_usage=TokenTracker(),
            tables_used=[],
            success=True,
        )

    def _answer_offensive(
        self,
        question: str,
    ) -> PipelineResult:

        answer = (
            "I'd like to keep things constructive. "
            "I'm happy to help as soon as you have a question "
            "about the data."
        )

        return PipelineResult(
            question=question,
            sql="-- offensive language, no query needed --",
            dataframe=pd.DataFrame(),
            answer=answer,
            repair_attempts=0,
            token_usage=TokenTracker(),
            tables_used=[],
            success=True,
        )

    def _answer_unknown_intent(
        self,
        question: str,
    ) -> PipelineResult:

        table_names = (
            ", ".join(self.metadata.tables.keys())
            if self.metadata
            else ""
        )

        answer = (
            "I'm not sure what you're asking -- could you rephrase "
            "that as a question about the data"
        )

        answer += (
            f" ({table_names})?"
            if table_names
            else "?"
        )

        return PipelineResult(
            question=question,
            sql="-- unrecognized intent, no query needed --",
            dataframe=pd.DataFrame(),
            answer=answer,
            repair_attempts=0,
            token_usage=TokenTracker(),
            tables_used=[],
            success=True,
        )

    def _answer_ambiguous(
        self,
        question: str,
    ) -> PipelineResult:

        table_names = (
            ", ".join(self.metadata.tables.keys())
            if self.metadata
            else "the data"
        )

        answer = (
            "That's a bit ambiguous -- which specific record "
            f"are you asking about (e.g. by name, ID, or date)? "
            f"I can look it up across {table_names}."
        )

        return PipelineResult(
            question=question,
            sql="-- ambiguous question, clarification requested --",
            dataframe=pd.DataFrame(),
            answer=answer,
            repair_attempts=0,
            token_usage=TokenTracker(),
            tables_used=[],
            success=True,
        )

    def _answer_unavailable_entity(
        self,
        question: str,
        entity_text: str,
    ) -> PipelineResult:
        """Reject a question about an entity absent from the schema.

        This is the deterministic guard for rule #7: the pipeline must
        never silently substitute an unrelated table for a concept that
        does not exist anywhere in the connected database.
        """

        table_names = (
            ", ".join(sorted(self.metadata.tables.keys()))
            if self.metadata
            else "this database"
        )

        answer = (
            f"I couldn't find anything about \"{entity_text}\" in this "
            f"database. The available data covers: {table_names}. "
            "Could you ask about one of those instead?"
        )

        return PipelineResult(
            question=question,
            sql="-- unsupported entity, no matching table/column --",
            dataframe=pd.DataFrame(),
            answer=answer,
            repair_attempts=0,
            token_usage=TokenTracker(),
            tables_used=[],
            success=False,
            error=f"unsupported_entity: {entity_text}",
        )

    def _answer_greeting(
        self,
        question: str,
    ) -> PipelineResult:

        table_names = (
            ", ".join(self.metadata.tables.keys())
            if self.metadata
            else ""
        )

        answer = "Hi! What's your question about the data"

        answer += (
            f" ({table_names})?"
            if table_names
            else "?"
        )

        return PipelineResult(
            question=question,
            sql="-- greeting, no query needed --",
            dataframe=pd.DataFrame(),
            answer=answer,
            repair_attempts=0,
            token_usage=TokenTracker(),
            tables_used=[],
            success=True,
        )

    # ------------------------------------------------------------------
    # Schema metadata answer
    # ------------------------------------------------------------------

    def _answer_schema_meta(
        self,
        question: str,
    ) -> PipelineResult:

        want_row_counts = bool(
            self._ROW_COUNT_RE.search(question)
        )

        row_counts = {}

        if want_row_counts:

            for name in self.metadata.tables:

                try:
                    df = self.provider.execute(
                        f"SELECT COUNT(*) AS n FROM {name};"
                    )

                    row_counts[name] = int(
                        df.iloc[0, 0]
                    )

                except Exception:
                    row_counts[name] = None

        lines = []

        for name, profile in self.semantic_profile.items():

            semantic_table = (
                self.semantic_schema.get(name)
            )

            col_bits = []

            for col_name in profile.columns:

                col_semantic = (
                    semantic_table.columns.get(col_name)
                    if semantic_table
                    else None
                )

                if (
                    col_semantic
                    and col_semantic.semantic_name
                    and col_semantic.semantic_name != col_name
                ):
                    col_bits.append(
                        f"{col_name} "
                        f"({col_semantic.semantic_name})"
                    )
                else:
                    col_bits.append(col_name)

            count_bit = ""

            if want_row_counts:

                n = row_counts.get(name)

                count_bit = (
                    f" -- {n} row(s)"
                    if n is not None
                    else " -- row count unavailable"
                )

            lines.append(
                f"- **{name}** "
                f"({profile.role})"
                f"{count_bit}: "
                f"{', '.join(col_bits)}"
            )

        num_tables = len(self.metadata.tables)

        intro = (
            f"This database has {num_tables} table(s). "
            "Here's what I learned about each:\n"
        )

        answer = intro + "\n".join(lines)

        return PipelineResult(
            question=question,
            sql=(
                "-- answered directly from the learned schema"
                + (
                    " + COUNT(*) per table"
                    if want_row_counts
                    else ""
                )
                + "; no generated query needed --"
            ),
            dataframe=pd.DataFrame(),
            answer=answer,
            repair_attempts=0,
            token_usage=TokenTracker(),
            tables_used=list(
                self.metadata.tables.keys()
            ),
            success=True,
        )

    # ------------------------------------------------------------------
    # Input failure
    # ------------------------------------------------------------------

    def _invalid_input_result(
        self,
        question: str,
        message: str,
        error: str,
    ) -> PipelineResult:

        return PipelineResult(
            question=question,
            sql="",
            dataframe=pd.DataFrame(),
            answer=message,
            repair_attempts=0,
            token_usage=TokenTracker(),
            tables_used=[],
            success=False,
            error=error,
        )

    # ------------------------------------------------------------------
    # Entity validation
    # ------------------------------------------------------------------

    _MISSING_COLUMN_RE = re.compile(
        r"no such column:\s*"
        r"([A-Za-z_][A-Za-z0-9_.]*)"
        r"|column\s+\"?"
        r"([A-Za-z_][A-Za-z0-9_.]*)\"?"
        r"\s+does not exist",
        re.IGNORECASE,
    )

    @staticmethod
    def _normalized_words(
        value: str,
    ) -> set:

        return set(
            re.findall(
                r"[a-z0-9]+",
                (value or "").lower(),
            )
        )

    @staticmethod
    def _word_forms(word: str) -> set:
        """Cheap singular/plural forms of a lowercase word for matching."""

        forms = {word}

        if word.endswith("ies") and len(word) > 3:
            forms.add(word[:-3] + "y")
        elif (
            word.endswith("s")
            and not word.endswith("ss")
            and len(word) > 1
        ):
            forms.add(word[:-1])

        if word.endswith("y") and not word.endswith(
            ("ay", "ey", "oy", "uy")
        ):
            forms.add(word[:-1] + "ies")

        forms.add(word + "s")
        forms.add(word + "es")

        return forms

    # ------------------------------------------------------------------
    # Deterministic entity / hierarchy fast paths
    # ------------------------------------------------------------------

    @staticmethod
    def _sql_literal(value: str) -> str:
        return "'" + str(value).replace("'", "''") + "'"

    def _find_entity_table(self, entity_text: str):
        if not self.metadata:
            return None
        words = self._normalized_words(entity_text)
        if not words:
            return None
        matches = []
        for name, table in self.metadata.tables.items():
            table_words = self._normalized_words(name)
            if words == table_words:
                matches.append(table)
                continue
            expanded = set()
            for word in table_words:
                expanded |= self._word_forms(word)
            if all(any(form in expanded for form in self._word_forms(w)) for w in words):
                matches.append(table)
        return matches[0] if len(matches) == 1 else None

    def _column_by_role(self, table, role: str):
        cols = list(getattr(table, "columns", []) or [])
        if role == "id":
            pk = [c for c in cols if getattr(c, "primary_key", False)]
            exact = [c for c in pk if c.name.casefold() == "id"]
            if len(exact) == 1:
                return exact[0]
            if len(pk) == 1:
                return pk[0]
            exact = [c for c in cols if c.name.casefold() == "id"]
            return exact[0] if len(exact) == 1 else None
        if role == "email":
            hits = [c for c in cols if "email" in c.name.casefold()]
            return hits[0] if len(hits) == 1 else None
        if role == "title":
            hits = [c for c in cols if self._normalized_words(c.name) & {"title", "position", "jobtitle"}]
            return hits[0] if len(hits) == 1 else None
        return None

    def _find_self_hierarchy(self):
        if not self.metadata:
            return None
        for table in self.metadata.tables.values():
            for fk in getattr(table, "foreign_keys", []) or []:
                if str(fk.referenced_table).casefold() == str(table.name).casefold():
                    child_col = next((c for c in table.columns if c.name.casefold() == fk.column.casefold()), None)
                    parent_col = next((c for c in table.columns if c.name.casefold() == fk.referenced_column.casefold()), None)
                    if child_col is not None and parent_col is not None:
                        return table, child_col, parent_col
        return None

    @staticmethod
    def _extract_email(question: str) -> Optional[str]:
        match = re.search(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", question or "", re.I)
        return match.group(0) if match else None

    @staticmethod
    def _extract_id_value(question: str) -> Optional[str]:
        for pattern in (
            r"\b(?:id|identifier)\s*(?:(?:is|=|:)\s*)?([A-Za-z0-9_-]+)",
            r"\bwhose\s+(?:id|identifier)\s*(?:(?:is|=|:)\s*)?([A-Za-z0-9_-]+)",
        ):
            match = re.search(pattern, question or "", re.I)
            if match:
                return match.group(1)
        return None

    def _fast_entity_lookup(self, question: str) -> Optional["PipelineResult"]:
        if not self.metadata:
            return None
        q = question.strip()
        from .relationships import _quote_identifier

        id_value = self._extract_id_value(q)
        if id_value is not None:
            match = re.search(
                r"\b(?:which|what|show\s+(?:me\s+)?)?\s*([A-Za-z][A-Za-z0-9_ ]{1,50}?)\s+(?:has|whose)\s+(?:id|identifier)\b",
                q, re.I,
            )
            entity = match.group(1).strip() if match else ""
            table = self._find_entity_table(entity)
            if table is None:
                return None
            id_col = self._column_by_role(table, "id")
            if id_col is None:
                return None
            qt = _quote_identifier(table.name, self.metadata.dialect)
            qc = _quote_identifier(id_col.name, self.metadata.dialect)
            sql = f"SELECT * FROM {qt} WHERE {qc} = {self._sql_literal(id_value)};"
            try:
                self.validator.validate(sql, dialect=self.metadata.dialect)
                df = self.provider.execute(sql)
            except Exception:
                return None
            return PipelineResult(question=question, sql=sql, dataframe=df,
                                  answer=self._fast_answer_from_dataframe(df) or "No matching records were found.",
                                  repair_attempts=0, token_usage=TokenTracker(), tables_used=[table.name], success=True)

        # Exact title lookup: structurally identify a title/position column.
        match = re.match(r"^\s*(?:who|which)\s+is\s+(?:the\s+)?(.+?)\s*\??\s*$", q, re.I)
        if match:
            value = match.group(1).strip()
            candidates = []
            for table in self.metadata.tables.values():
                title_col = self._column_by_role(table, "title")
                if title_col is not None:
                    candidates.append((table, title_col))
            if len(candidates) != 1:
                return None
            table, title_col = candidates[0]
            qt = _quote_identifier(table.name, self.metadata.dialect)
            qc = _quote_identifier(title_col.name, self.metadata.dialect)
            sql = f"SELECT * FROM {qt} WHERE LOWER({qc}) = LOWER({self._sql_literal(value)});"
            try:
                self.validator.validate(sql, dialect=self.metadata.dialect)
                df = self.provider.execute(sql)
            except Exception:
                return None
            return PipelineResult(question=question, sql=sql, dataframe=df,
                                  answer=self._fast_answer_from_dataframe(df) or "No matching records were found.",
                                  repair_attempts=0, token_usage=TokenTracker(), tables_used=[table.name], success=True)
        return None

    def _fast_hierarchy_lookup(self, question: str) -> Optional["PipelineResult"]:
        if not self.metadata:
            return None
        relation = self._find_self_hierarchy()
        if relation is None:
            return None
        table, child_fk, parent_pk = relation
        ql = question.casefold()
        if not any(term in ql for term in ("manager", "direct report", "direct reporter", "everyone under", "all under", "hierarchy", "subordinates")):
            return None

        email = self._extract_email(question)
        id_value = self._extract_id_value(question)
        id_col = self._column_by_role(table, "id")
        email_col = self._column_by_role(table, "email")
        if email and email_col is None:
            return None
        if not email and not id_value:
            return None

        from .relationships import _quote_identifier
        dialect = self.metadata.dialect
        qt = _quote_identifier(table.name, dialect)
        qfk = _quote_identifier(child_fk.name, dialect)
        qpk = _quote_identifier(parent_pk.name, dialect)
        if "direct report" in ql or "direct reporter" in ql:
            if email:
                qe = _quote_identifier(email_col.name, dialect)
                sql = (
                    f"SELECT child.* FROM {qt} child JOIN {qt} manager "
                    f"ON child.{qfk} = manager.{qpk} "
                    f"WHERE LOWER(manager.{qe}) = LOWER({self._sql_literal(email)});"
                )
            else:
                if id_col is None:
                    return None
                qid = _quote_identifier(id_col.name, dialect)
                sql = f"SELECT child.* FROM {qt} child JOIN {qt} manager ON child.{qfk} = manager.{qpk} WHERE manager.{qid} = {self._sql_literal(id_value)};"
        elif "manager" in ql and not any(x in ql for x in ("under", "hierarchy", "subordinate")):
            if email:
                qe = _quote_identifier(email_col.name, dialect)
                sql = (
                    f"SELECT manager.* FROM {qt} child JOIN {qt} manager "
                    f"ON child.{qfk} = manager.{qpk} "
                    f"WHERE LOWER(child.{qe}) = LOWER({self._sql_literal(email)});"
                )
            else:
                if id_col is None:
                    return None
                qid = _quote_identifier(id_col.name, dialect)
                sql = f"SELECT manager.* FROM {qt} child JOIN {qt} manager ON child.{qfk} = manager.{qpk} WHERE child.{qid} = {self._sql_literal(id_value)};"
        else:
            depth = max(1, int(getattr(self.config, "max_hierarchy_depth", 25)))
            # Internal CTE name is a fixed safe identifier; keeping it
            # unquoted also makes deterministic alias validation portable.
            cte = "_text2sql_hierarchy"
            lvl = _quote_identifier("Level", dialect)
            if email:
                qe = _quote_identifier(email_col.name, dialect)
                # The anchor is the manager's direct children, not the
                # manager itself. Level 1 therefore means direct report.
                anchor_from = f"FROM {qt} u JOIN {qt} root ON root.{qpk} = u.{qfk} WHERE LOWER(root.{qe}) = LOWER({self._sql_literal(email)})"
                anchor_start = f"h.{qfk} IN (SELECT root.{qpk} FROM {qt} root WHERE LOWER(root.{qe}) = LOWER({self._sql_literal(email)}))"
            else:
                qid = _quote_identifier(id_col.name, dialect)
                anchor_from = f"FROM {qt} u JOIN {qt} root ON root.{qpk} = u.{qfk} WHERE root.{qid} = {self._sql_literal(id_value)}"
                anchor_start = f"h.{qfk} IN (SELECT root.{qpk} FROM {qt} root WHERE root.{qid} = {self._sql_literal(id_value)})"

            if dialect == "oracle":
                sql = f"SELECT h.*, LEVEL AS {lvl} FROM {qt} h START WITH {anchor_start} CONNECT BY NOCYCLE PRIOR {qpk} = {qfk} AND LEVEL <= {depth}"
            elif dialect == "mssql":
                anchor_q = f"SELECT u.*, 1 AS {lvl}, CAST(u.{qpk} AS NVARCHAR(MAX)) AS _path {anchor_from}"
                rec_q = f"SELECT u.*, h.{lvl} + 1, CAST(h._path + '|' + CAST(u.{qpk} AS NVARCHAR(MAX)) AS NVARCHAR(MAX)) FROM {qt} u JOIN {cte} h ON u.{qfk} = h.{qpk} WHERE h.{lvl} < {depth} AND CHARINDEX('|' + CAST(u.{qpk} AS NVARCHAR(MAX)) + '|', '|' + h._path + '|') = 0"
                sql = f"WITH {cte} AS ({anchor_q} UNION ALL {rec_q}) SELECT * FROM {cte};"
            elif dialect == "postgresql":
                anchor_q = f"SELECT u.*, 1 AS {lvl}, ARRAY[u.{qpk}] AS _path {anchor_from}"
                rec_q = f"SELECT u.*, h.{lvl} + 1, h._path || u.{qpk} FROM {qt} u JOIN {cte} h ON u.{qfk} = h.{qpk} WHERE h.{lvl} < {depth} AND NOT u.{qpk} = ANY(h._path)"
                sql = f"WITH RECURSIVE {cte} AS ({anchor_q} UNION ALL {rec_q}) SELECT * FROM {cte} ORDER BY {lvl};"
            else:
                if dialect == "mysql":
                    cast = f"CAST(u.{qpk} AS CHAR)"
                    child_cast = f"CAST(u.{qpk} AS CHAR)"
                    path_anchor = f"CONCAT('|', {cast}, '|')"
                    path_next = f"CONCAT(h._path, {child_cast}, '|')"
                    no_cycle = f"INSTR(h._path, CONCAT('|', {child_cast}, '|')) = 0"
                else:
                    cast = f"CAST(u.{qpk} AS TEXT)"
                    child_cast = f"CAST(u.{qpk} AS TEXT)"
                    path_anchor = f"('|' || {cast} || '|')"
                    path_next = f"(h._path || {child_cast} || '|')"
                    no_cycle = f"INSTR(h._path, '|' || {child_cast} || '|') = 0"
                anchor_q = f"SELECT u.*, 1 AS {lvl}, {path_anchor} AS _path {anchor_from}"
                rec_q = f"SELECT u.*, h.{lvl} + 1, {path_next} FROM {qt} u JOIN {cte} h ON u.{qfk} = h.{qpk} WHERE h.{lvl} < {depth} AND {no_cycle}"
                sql = f"WITH RECURSIVE {cte} AS ({anchor_q} UNION ALL {rec_q}) SELECT * FROM {cte} ORDER BY {lvl};"

        try:
            self.validator.validate(sql, dialect=dialect)
            df = self.provider.execute(sql)
        except Exception:
            return None
        return PipelineResult(question=question, sql=sql, dataframe=df,
                              answer=self._fast_answer_from_dataframe(df) or "No matching records were found.",
                              repair_attempts=0, token_usage=TokenTracker(), tables_used=[table.name], success=True)

    def _try_simple_count_fast_path(
        self,
        question: str,
    ) -> Optional["PipelineResult"]:
        """Answer a bare "how many X (are there)?" question directly.

        A bare row-count question has exactly one possible correct query
        (``SELECT COUNT(*) FROM X``), so paying for retrieval, targeted
        semantic learning, LLM SQL generation, validation/repair, and LLM
        answer synthesis buys zero additional accuracy here -- only
        latency. On CPU that chain of several sequential model calls is
        also the actual mechanism behind "That question is taking too
        long to answer" for exactly this kind of question.

        Deliberately conservative -- returns None (falls through to the
        full pipeline, unchanged) unless:
        - the question is a bare count with no extra filter language, and
        - the entity word matches exactly one table name via simple
          singular/plural normalization (no semantic/synonym guessing).

        A filtered count ("how many users are active"), a multi-word or
        camelCase entity that doesn't match a table name directly, or an
        ambiguous match across multiple tables, all fall through to the
        real pipeline exactly as before this fast path existed.
        """

        if not self.metadata or not self.metadata.tables:
            return None

        match = self._SIMPLE_COUNT_RE.match(question or "")

        if not match:
            return None

        captured = next(
            (g for g in match.groups() if g is not None),
            None,
        )

        if not captured:
            return None

        entity_text = re.sub(
            r"\s+",
            " ",
            captured.strip(" _-"),
        )
        entity_key = entity_text.lower().replace(" ", "")

        if (
            not entity_key
            or entity_key in self._GENERIC_COUNT_WORDS
        ):
            return None

        entity_forms = self._word_forms(entity_key)

        matches = [
            table_name
            for table_name in self.metadata.tables
            if self._word_forms(table_name.lower())
            & entity_forms
        ]

        if len(matches) != 1:
            # No confident, unambiguous structural match -- let the full
            # pipeline (which also understands learned synonyms) decide.
            return None

        table_name = matches[0]

        try:
            from .relationships import _quote_identifier

            quoted = _quote_identifier(
                table_name,
                self.metadata.dialect,
            )
            df = self.provider.execute(
                f"SELECT COUNT(*) AS n FROM {quoted};"
            )
            count = int(df.iloc[0, 0])
        except Exception:
            # If the direct count fails for any reason (permissions,
            # odd table name, etc.), fall back to the full pipeline
            # rather than surfacing a raw error from this shortcut.
            return None

        return PipelineResult(
            question=question,
            sql=f"SELECT COUNT(*) AS n FROM {quoted};",
            dataframe=df,
            answer=f"There are {count} row(s) in {table_name}.",
            repair_attempts=0,
            token_usage=TokenTracker(),
            tables_used=[table_name],
            success=True,
        )

    def _requested_entity_is_unavailable(
        self,
        question: str,
    ) -> Optional[str]:

        match = self._HOW_MANY_ENTITY_RE.match(
            question or ""
        )

        if not match or not self.metadata:
            return None

        entity_text = re.sub(
            r"\s+",
            " ",
            match.group(1).strip(" _-"),
        )

        entity_words = self._normalized_words(
            entity_text
        )

        if (
            not entity_words
            or entity_text.lower()
            in self._GENERIC_COUNT_WORDS
        ):
            return None

        schema_words = set()

        for table_name in self.metadata.tables:

            schema_words |= self._normalized_words(
                table_name
            )

        for table in self.metadata.tables.values():

            for column in table.columns:

                schema_words |= self._normalized_words(
                    column.name
                )

        # --------------------------------------------------------------
        # Include dynamically learned synonyms (rule #3).
        #
        # A word such as "workers" must NOT be rejected once the schema
        # has taught this pipeline (via LLM semantic learning or explicit
        # user teaching) that it maps to a real table/column, e.g.
        # "workers" -> Employees. Only concepts with no schema evidence
        # at all -- structural or learned -- are treated as unavailable.
        # --------------------------------------------------------------

        for profile in (self.semantic_schema or {}).values():

            for synonym in getattr(profile, "synonyms", None) or []:
                schema_words |= self._normalized_words(synonym)

            schema_words |= self._normalized_words(
                getattr(profile, "semantic_name", "") or ""
            )

            for col_profile in (
                getattr(profile, "columns", None) or {}
            ).values():

                for synonym in (
                    getattr(col_profile, "synonyms", None) or []
                ):
                    schema_words |= self._normalized_words(synonym)

                schema_words |= self._normalized_words(
                    getattr(col_profile, "semantic_name", "") or ""
                )

        if self.user_memory is not None:

            try:
                schema_words |= self._normalized_words(
                    self.user_memory.build_hint(entity_text) or ""
                )
            except Exception:
                pass

        def concept_matches(word: str) -> bool:

            candidates = {word}

            if word.endswith("ies"):
                candidates.add(
                    word[:-3] + "y"
                )

            elif (
                word.endswith("s")
                and not word.endswith("ss")
            ):
                candidates.add(
                    word[:-1]
                )

            # Reverse direction: the question word may be singular
            # while the schema uses the plural form (e.g. question
            # "user" vs. table "users"). Try pluralizing too.
            if word.endswith("y") and not word.endswith(
                ("ay", "ey", "oy", "uy")
            ):
                candidates.add(
                    word[:-1] + "ies"
                )

            candidates.add(word + "s")
            candidates.add(word + "es")

            return any(
                candidate in schema_words
                for candidate in candidates
            )

        meaningful = [
            w for w in entity_words
            if len(w) > 1
        ]

        if meaningful and all(
            concept_matches(w)
            for w in meaningful
        ):
            return None

        if (
            len(meaningful) == 1
            and len(meaningful[0]) >= 3
        ):
            return entity_text

        return None

    # ------------------------------------------------------------------
    # IMPORTANT FIX:
    # Semantic SQL alignment validation
    # ------------------------------------------------------------------

    def _question_sql_alignment_error(
        self,
        question: str,
        sql: str,
    ) -> Optional[str]:
        """Catch common semantic SQL mistakes before DB execution.

        In particular, prevent:

            SELECT EmployeeNumber, MAX(MonthlyRate)
            FROM Employee
            GROUP BY EmployeeNumber;

        from being accepted for:

            "Who has the highest MonthlyRate?"

        The query above is syntactically valid but semantically wrong
        because it returns one row for every employee.
        """

        q = (question or "").strip()
        upper = (sql or "").upper()

        # No highest/lowest/top/bottom language -> no special check.
        if not self._TOP_K_RE.search(q):
            return None

        # --------------------------------------------------------------
        # Questions asking for an actual record/person.
        # --------------------------------------------------------------

        if self._TOP_K_PERSON_RE.search(q):

            has_order = bool(
                re.search(
                    r"\bORDER\s+BY\b",
                    upper,
                )
            )

            has_row_limit = bool(
                re.search(
                    r"\bLIMIT\s+\d+",
                    upper,
                )
                or re.search(
                    r"\bTOP\s*\(\s*\d+\s*\)",
                    upper,
                )
                or re.search(
                    r"\bFETCH\s+(?:FIRST|NEXT)\s+\d+",
                    upper,
                )
            )

            has_group_by = bool(
                re.search(
                    r"\bGROUP\s+BY\b",
                    upper,
                )
            )

            has_extreme_aggregate = bool(
                re.search(
                    r"\b(?:MAX|MIN)\s*\(",
                    upper,
                )
            )

            # ----------------------------------------------------------
            # CRITICAL:
            #
            # GROUP BY + MAX/MIN is wrong for:
            # "Who has the highest salary?"
            #
            # because it produces one row per group.
            # ----------------------------------------------------------

            if (
                has_group_by
                and has_extreme_aggregate
            ):
                return (
                    "The generated SQL incorrectly uses GROUP BY "
                    "with MAX/MIN for a highest/lowest record "
                    "question. This returns one row per group "
                    "instead of the single record with the extreme "
                    "value. Use ORDER BY the requested measure "
                    "DESC for highest/top or ASC for lowest/bottom "
                    "with a dialect-appropriate row limit."
                )

            # ----------------------------------------------------------
            # A record/person question should normally use:
            #
            # ORDER BY column DESC LIMIT 1
            #
            # or:
            #
            # ORDER BY column ASC LIMIT 1
            # ----------------------------------------------------------

            if not has_order or not has_row_limit:

                # MAX/MIN alone is acceptable for:
                #
                # "What is the maximum salary?"
                #
                # but not:
                #
                # "Who has the highest salary?"
                #
                # Therefore reject it here.
                return (
                    "The question asks for a single highest/lowest/"
                    "top/bottom record. The generated SQL must "
                    "select the actual record using ORDER BY on "
                    "the requested column and a dialect-appropriate "
                    "row limit."
                )

            # --------------------------------------------------------
            # NULL-safety for highest/lowest (rule #5).
            #
            # In standard SQL, NULL sorts before every real value in an
            # ASC ordering (and after every value in most dialects'
            # DESC ordering), so "ORDER BY col ASC LIMIT 1" on a
            # nullable column can silently return the row with a
            # missing value instead of the true minimum -- and the
            # reverse risk exists for DESC on some dialects. Require an
            # explicit NULL-exclusion whenever the ordered column is
            # known to be nullable.
            # --------------------------------------------------------

            null_guard_error = self._missing_null_guard_error(sql)

            if null_guard_error:
                return null_guard_error

        return None

    def _missing_null_guard_error(self, sql: str) -> Optional[str]:
        """Detect ORDER BY ... LIMIT on a nullable column with no NULL guard."""

        if not self.metadata:
            return None

        order_match = re.search(
            r"ORDER\s+BY\s+"
            r"([A-Za-z_][A-Za-z0-9_]*(?:\s*\.\s*[A-Za-z_][A-Za-z0-9_]*)?)"
            r"\s*(ASC|DESC)?",
            sql,
            re.IGNORECASE,
        )

        if not order_match:
            return None

        raw_ref = order_match.group(1)
        bare_column = raw_ref.split(".")[-1].strip().strip('"`[]').lower()

        # Only flag columns that actually exist and are nullable somewhere
        # in the known schema -- never guess about unknown identifiers.
        nullable_hit = False

        for table in self.metadata.tables.values():
            for column in table.columns:
                if column.name.lower() == bare_column:
                    if column.nullable and not column.primary_key:
                        nullable_hit = True
                    else:
                        # A non-nullable definition for this column name
                        # anywhere is enough evidence to skip the guard;
                        # avoid false positives on common column names.
                        pass

        if not nullable_hit:
            return None

        masked = self._mask_quoted_literals(sql)
        upper = masked.upper()
        bare_upper = bare_column.upper()

        has_not_null_filter = bool(
            re.search(
                rf"\b{re.escape(bare_upper)}\b\s+IS\s+NOT\s+NULL",
                upper,
            )
        )

        has_nulls_ordering = bool(
            re.search(r"\bNULLS\s+(LAST|FIRST)\b", upper)
        )

        if has_not_null_filter or has_nulls_ordering:
            return None

        return (
            f"The ORDER BY column '{bare_column}' can contain NULL "
            "values in this schema. NULL sorts ambiguously across "
            "SQL dialects, so a highest/lowest query must exclude "
            f"NULLs explicitly, e.g. add 'WHERE {bare_column} IS NOT "
            "NULL' (or an equivalent NULLS LAST/FIRST clause) so the "
            "row with a missing value is never returned as the "
            "extreme result."
        )

    @staticmethod
    def _mask_quoted_literals(sql: str) -> str:
        """Blank out quoted string contents so keyword scans ignore them."""

        chars = list(sql)
        i = 0
        n = len(chars)

        while i < n:
            if chars[i] in ("'", '"'):
                quote = chars[i]
                i += 1
                while i < n:
                    if chars[i] == quote:
                        i += 1
                        break
                    chars[i] = " "
                    i += 1
            else:
                i += 1

        return "".join(chars)

    # ------------------------------------------------------------------
    # Graceful SQL failure
    # ------------------------------------------------------------------

    def _graceful_failure_message(
        self,
        exc: SQLGenerationError,
        tables_used: list,
    ) -> str:

        original = (
            str(exc.original_error)
            if exc.original_error
            else str(exc)
        )

        match = self._MISSING_COLUMN_RE.search(
            original
        )

        if match:

            bad_col = (
                match.group(1)
                or match.group(2)
                or ""
            ).split(".")[-1]

            available_cols = []

            for t in (
                tables_used
                or list(self.metadata.tables.keys())
            ):

                table_meta = (
                    self.metadata.tables.get(t)
                )

                if table_meta:

                    available_cols.extend(
                        c.name
                        for c in table_meta.columns
                    )

            near = [
                c
                for c in available_cols
                if (
                    bad_col.lower() in c.lower()
                    or c.lower() in bad_col.lower()
                )
            ]

            lines = [
                f'This dataset doesn\'t have a column '
                f'called "{bad_col}", so I can\'t answer '
                "that directly."
            ]

            if near:

                lines.append(
                    "Closest column(s) available: "
                    + ", ".join(
                        sorted(set(near))
                    )
                    + "."
                )

            elif available_cols:

                unique_cols = sorted(
                    set(available_cols)
                )

                lines.append(
                    "Columns available here: "
                    + ", ".join(
                        unique_cols[:15]
                    )
                    + (
                        ", ..."
                        if len(unique_cols) > 15
                        else "."
                    )
                )

            return " ".join(lines)

        return (
            "I couldn't turn that into a working query "
            "against this database "
            f"after {self.config.max_repair_attempts + 1} "
            "attempt(s). It's possible the question refers "
            "to something this dataset doesn't actually contain "
            '-- try asking "what tables/columns are available?" '
            "to see what can be queried, or rephrase using a "
            "column name you see there."
        )

    # ------------------------------------------------------------------
    # Fast dataframe answers
    # ------------------------------------------------------------------

    def _fast_answer_from_dataframe(
        self,
        dataframe: pd.DataFrame,
    ) -> Optional[str]:
        """Format query results without invoking a second LLM.

        SQL generation is the only model call normally needed by the
        pipeline. Result narration is deliberately deterministic because
        the database has already produced the authoritative values. This
        keeps the request on the fast path and prevents a second local-model
        generation from consuming the majority of the latency budget.
        """

        if dataframe.empty:
            return "No matching records were found."

        columns = [str(c) for c in dataframe.columns]

        # Large results: do not serialize rows into an answer-generation
        # prompt. The structured dataframe remains available to the caller.
        if len(dataframe) > self.config.max_result_rows_for_answer:
            return (
                f"Found {len(dataframe)} matching records "
                f"(columns: {', '.join(columns)})."
            )

        # Scalar result.
        if len(dataframe) == 1 and len(columns) == 1:
            value = dataframe.iloc[0, 0]
            if pd.isna(value):
                value_text = "no value"
            else:
                if hasattr(value, "item"):
                    try:
                        value = value.item()
                    except Exception:
                        pass
                value_text = str(value)
            return f"According to the data, {columns[0]} is {value_text}."

        # One row: give the complete row because it is small and
        # unambiguous.
        if len(dataframe) == 1:
            row = dataframe.iloc[0]
            parts = []
            for column in dataframe.columns:
                value = row[column]
                if pd.isna(value):
                    value_text = "no value"
                else:
                    if hasattr(value, "item"):
                        try:
                            value = value.item()
                        except Exception:
                            pass
                    value_text = str(value)
                parts.append(f"{column}: {value_text}")
            return "According to the data, " + "; ".join(parts) + "."

        # Small multi-row results: return a compact deterministic preview.
        # The frontend still receives the full dataframe. Keeping only a
        # handful of rows here prevents giant response strings while avoiding
        # any answer-LLM call.
        preview_rows = min(len(dataframe), 5)
        preview = dataframe.head(preview_rows).copy()
        preview = preview.where(pd.notna(preview), "no value")
        records = []
        for _, row in preview.iterrows():
            values = []
            for column in dataframe.columns:
                value = row[column]
                if hasattr(value, "item"):
                    try:
                        value = value.item()
                    except Exception:
                        pass
                values.append(f"{column}={value}")
            records.append("{" + ", ".join(values) + "}")

        suffix = ""
        if len(dataframe) > preview_rows:
            suffix = f" Showing the first {preview_rows}."
        return (
            f"Found {len(dataframe)} matching records."
            + suffix
            + " "
            + " ".join(records)
        )

    def _semantic_learning_needed(
        self,
        question: str,
        retrieved,
    ) -> bool:
        """Return True only when structural grounding is insufficient.

        Semantic learning is expensive because it uses the same local LLM
        used for SQL generation. Exact schema matches and structural
        hierarchy evidence are therefore preferred. Synonym-heavy questions
        such as "workers" or "salary" can still trigger targeted
        semantic learning, preserving the dynamic behavior without making it
        mandatory on every request.
        """
        if not self.config.enable_semantic_learning or not self.metadata:
            return False

        q_tokens = self._normalized_words(question)
        if not q_tokens:
            return False

        def _identifier_tokens(value: str) -> set:
            raw = str(value or "")
            # Match natural-language forms against snake_case and camelCase
            # identifiers without adding domain-specific synonyms.
            expanded = {raw.lower(), raw.lower().replace("_", "")}
            spaced = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", raw)
            expanded.add(spaced.lower())
            tokens = set()
            for item in expanded:
                tokens |= self._normalized_words(item)
                tokens.add(item.replace(" ", "").replace("_", ""))
            return {t for t in tokens if t}

        # Exact table/column/relationship tokens are strong enough to ground
        # the SQL prompt without another model call.
        schema_tokens = set()
        for table in self.metadata.tables.values():
            schema_tokens |= _identifier_tokens(table.name)
            for column in table.columns:
                schema_tokens |= _identifier_tokens(column.name)
            for fk in getattr(table, "foreign_keys", []) or []:
                schema_tokens |= _identifier_tokens(fk.column)
                schema_tokens |= _identifier_tokens(fk.referenced_table)
                schema_tokens |= _identifier_tokens(fk.referenced_column)

        if q_tokens & schema_tokens:
            return False

        # Hierarchy intent can be grounded structurally from a self-FK even
        # when the user uses natural-language words like "reports" that do
        # not literally occur in the schema.
        hierarchy_words = {
            "manager", "managers", "report", "reports",
            "reporter", "reporters", "under", "hierarchy",
            "subordinate", "subordinates", "direct",
            "parent", "parents", "child", "children",
        }
        if q_tokens & hierarchy_words:
            for table in self.metadata.tables.values():
                for fk in getattr(table, "foreign_keys", []) or []:
                    if str(fk.referenced_table).casefold() == str(table.name).casefold():
                        return False

        # A positive retrieval result is useful structural evidence. If the
        # question already shares words with the selected document, avoid
        # paying for semantic learning.
        for item in retrieved or []:
            document = item.get("document")
            if document is not None:
                doc_tokens = self._normalized_words(getattr(document, "text", ""))
                if q_tokens & doc_tokens:
                    return False

        return True

    # ------------------------------------------------------------------
    # Main ask()
    # ------------------------------------------------------------------

    def ask(
        self,
        question: str,
        conversation_context: Optional[str] = None,
    ) -> PipelineResult:

        start_time = time.perf_counter()
        timings: dict = {}
        _checkpoint_state = {"t": start_time}

        def _checkpoint(name: str) -> None:
            """Record elapsed time since the previous checkpoint.

            Lightweight (a single perf_counter() call), used to answer
            "where did the time go" for a request without guessing --
            see PipelineResult.stage_timings.
            """
            now = time.perf_counter()
            timings[name] = now - _checkpoint_state["t"]
            _checkpoint_state["t"] = now

        # --------------------------------------------------------------
        # Input validation
        # --------------------------------------------------------------

        if (
            not isinstance(question, str)
            or not question.strip()
        ):

            if not self.config.graceful_failure:
                raise InvalidQuestionError(
                    "Question is empty."
                )

            return self._invalid_input_result(
                question or "",
                "I didn't receive a question -- "
                "what would you like to know about the data?",
                "empty_question",
            )

        if (
            len(question)
            > self.config.max_question_length
        ):

            if not self.config.graceful_failure:
                raise InvalidQuestionError(
                    f"Question exceeds max_question_length "
                    f"({self.config.max_question_length} chars)."
                )

            return self._invalid_input_result(
                question,
                "That question is quite long -- "
                "could you ask something more specific, "
                "in a sentence or two?",
                "question_too_long",
            )

        # --------------------------------------------------------------
        # Explicit user teaching FIRST
        # --------------------------------------------------------------
        # A user can teach this database without creating dataset-specific
        # Python rules, e.g. "when I say workers, I mean employees" or
        # "column xx is salary".
        taught = self._maybe_learn_user_instruction(question)
        if taught is not None:
            return taught

        # --------------------------------------------------------------
        # Intent FIRST
        # --------------------------------------------------------------
        #
        # This must happen before schema discovery. Greetings, small talk,
        # offensive input, and clearly unusable messages are deterministic
        # fast paths and must not pay the database/retrieval cost.

        intent = self.intent_classifier.classify(
            question
        )

        _checkpoint("intent_classification")

        if intent.intent == GREETING:
            return self._answer_greeting(
                question
            )

        if intent.intent == SMALL_TALK:
            return self._answer_small_talk(
                question
            )

        if intent.intent == OFFENSIVE:
            return self._answer_offensive(
                question
            )

        if intent.intent == UNKNOWN:
            return self._answer_unknown_intent(
                question
            )

        assert intent.intent == DATABASE_QUERY

        # --------------------------------------------------------------
        # Cache BEFORE schema/retrieval work
        # --------------------------------------------------------------
        #
        # A repeated successful query should be able to return without
        # rediscovering the schema or touching the LLM. The cache is scoped
        # to this pipeline instance/database, so the existing cache key is
        # sufficient for this lifecycle.

        cache_key = None

        if self.cache is not None:

            cache_key = QueryCache.make_key(
                question,
                conversation_context,
                self.config.top_k_tables,
                self.config.retrieval_strategy,
                self.config.use_reranker,
                self.config.enable_semantic_learning,
                self.config.llm_model,
            )

            cached = self.cache.get(
                cache_key
            )

            if cached is not None:
                return replace(
                    cached,
                    from_cache=True,
                    elapsed_seconds=(
                        time.perf_counter()
                        - start_time
                    ),
                    stage_timings={
                        "intent_classification": timings.get(
                            "intent_classification", 0.0
                        ),
                        "cache_lookup": (
                            time.perf_counter()
                            - _checkpoint_state["t"]
                        ),
                    },
                )

        _checkpoint("cache_lookup")

        # --------------------------------------------------------------
        # Learn schema only for actual database work
        # --------------------------------------------------------------

        try:
            self.learn_schema()

        except (
            DatabaseConnectionError,
            EmptyDatabaseError,
            SchemaExtractionError,
        ) as exc:

            if not self.config.graceful_failure:
                raise

            return self._invalid_input_result(
                question,
                "I couldn't access the database schema right now, "
                "so I can't answer that database question.",
                str(exc),
            )

        # learn_schema() is a cheap no-op after the first call for this
        # pipeline instance (self._indexed guard), so this timing reflects
        # true schema-discovery cost only on the first database question.
        _checkpoint("schema_load")

        # --------------------------------------------------------------
        # Deterministic entity/hierarchy fast paths -- no LLM calls
        # --------------------------------------------------------------
        for fast_result in (
            self._fast_hierarchy_lookup(question),
            self._fast_entity_lookup(question),
        ):
            if fast_result is not None:
                fast_result.elapsed_seconds = time.perf_counter() - start_time
                fast_result.stage_timings = {
                    "intent_classification": timings.get("intent_classification", 0.0),
                    "cache_lookup": timings.get("cache_lookup", 0.0),
                    "schema_load": timings.get("schema_load", 0.0),
                    "deterministic_fast_path": time.perf_counter() - _checkpoint_state["t"],
                }
                if self.cache is not None and cache_key is not None:
                    self.cache.set(cache_key, fast_result)
                return fast_result

        # --------------------------------------------------------------
        # Simple count fast path -- no LLM calls at all
        # --------------------------------------------------------------
        # Must run before retrieval/semantic-learning/SQL-generation so a
        # bare "how many <table>" question never pays for any of that.

        fast_count = self._try_simple_count_fast_path(question)

        if fast_count is not None:

            fast_count.elapsed_seconds = (
                time.perf_counter() - start_time
            )
            fast_count.stage_timings = {
                "intent_classification": timings.get(
                    "intent_classification", 0.0
                ),
                "cache_lookup": timings.get("cache_lookup", 0.0),
                "schema_load": timings.get("schema_load", 0.0),
                "simple_count_fast_path": (
                    time.perf_counter() - _checkpoint_state["t"]
                ),
            }

            if self.cache is not None and cache_key is not None:
                self.cache.set(cache_key, fast_count)

            return fast_count

        # --------------------------------------------------------------
        # Schema metadata questions
        # --------------------------------------------------------------

        if self._SCHEMA_META_RE.search(question):
            return self._answer_schema_meta(
                question
            )

        # --------------------------------------------------------------
        # Ambiguity
        # --------------------------------------------------------------

        if is_ambiguous(
            question,
            has_conversation_context=bool(
                conversation_context
            ),
        ):

            return self._answer_ambiguous(
                question
            )

        tracker = TokenTracker()

        # --------------------------------------------------------------
        # Retrieval
        # --------------------------------------------------------------

        pool_k = max(
            self.config.top_k_tables
            * self.config.retrieval_pool_multiplier,
            self.config.top_k_tables,
        )

        retrieved = self.retriever.retrieve(
            question,
            top_k=pool_k,
        )

        if (
            self.config.min_retrieval_score
            > 0
        ):

            filtered = [
                r
                for r in retrieved
                if r["score"]
                >= self.config.min_retrieval_score
            ]

            retrieved = (
                filtered
                or retrieved[:1]
            )

        if self.reranker is not None:

            retrieved = self.reranker.rerank(
                question,
                retrieved,
                top_k=self.config.top_k_tables,
            )

        else:

            retrieved = retrieved[
                : self.config.top_k_tables
            ]

        # --------------------------------------------------------------
        # Relationship expansion
        # --------------------------------------------------------------

        retrieved = (
            self.retriever.expand_with_relationships(
                retrieved,
                self.documents,
                self.graph,
                hops=1,
            )
        )

        _checkpoint("retrieval")

        # --------------------------------------------------------------
        # Targeted semantic learning -- FALLBACK ONLY
        # --------------------------------------------------------------
        # Do not spend a local-LLM generation on every request. Exact schema
        # and relationship evidence are handled deterministically first.
        # Semantic learning remains available for genuinely synonym-heavy or
        # otherwise weakly grounded questions.
        if self._semantic_learning_needed(question, retrieved):
            self._learn_semantics_for_question(
                question,
                retrieved,
            )

        _checkpoint("semantic_learning")

        # --------------------------------------------------------------
        # Reject unsupported entities (rule #7)
        # --------------------------------------------------------------
        #
        # "How many restaurants are there?" against a schema that has no
        # restaurant-like table/column must be rejected rather than
        # silently counting some unrelated table. This runs AFTER
        # targeted semantic learning so dynamically-learned synonyms
        # ("workers" -> Employees) and user-taught mappings are honored
        # (rule #3) -- only genuinely absent concepts are rejected.

        unavailable_entity = self._requested_entity_is_unavailable(
            question
        )

        if unavailable_entity is not None:

            return self._answer_unavailable_entity(
                question,
                unavailable_entity,
            )

        # --------------------------------------------------------------
        # Semantic column retrieval
        # --------------------------------------------------------------

        semantic_hints = None

        # Table-level semantic mappings are essential for entity concepts
        # such as "workers" -> a learned employee/staff/personnel table.
        semantic_table_hints = build_semantic_table_hint_block(
            self.semantic_schema,
            min_confidence=self.config.semantic_min_confidence_for_hint,
            max_items=self.config.semantic_max_candidate_tables_per_query,
        )

        if self.column_retriever is not None:

            column_hits = (
                self.column_retriever.retrieve(
                    question,
                    top_k=self.config.semantic_top_k_columns,
                )
            )

            semantic_hints = (
                build_semantic_hint_block(
                    column_hits,
                    min_confidence=(
                        self.config
                        .semantic_min_confidence_for_hint
                    ),
                    max_items=(
                        self.config
                        .semantic_top_k_columns
                    ),
                )
            )

            existing_tables = {
                r["document"].table
                for r in retrieved
            }

            by_table = {
                d.table: d
                for d in self.documents
            }

            for hit in column_hits:

                table = hit["document"].table

                if (
                    table not in existing_tables
                    and table in by_table
                ):

                    retrieved.append({
                        "score": 0.0,
                        "document": by_table[table],
                    })

                    existing_tables.add(table)

        tables_used = [
            r["document"].table
            for r in retrieved
        ]

        # --------------------------------------------------------------
        # Prompt
        # --------------------------------------------------------------

        dialect = self.provider.dialect

        user_hints = (
            self.user_memory.build_hint(question)
            if self.user_memory is not None
            else ""
        )

        combined_semantic_hints = "\n\n".join(
            part for part in (
                semantic_table_hints,
                semantic_hints or "",
                user_hints,
            )
            if part
        ) or None

        prompt = self.prompt_builder.build(
            question,
            retrieved,
            dialect=dialect,
            conversation_context=conversation_context,
            use_reasoning=self.config.use_reasoning,
            semantic_hints=combined_semantic_hints,
        )

        # Covers semantic column retrieval + prompt assembly (both pure
        # Python/string work -- no LLM call in this span).
        _checkpoint("semantic_retrieval_and_prompt_build")

        # --------------------------------------------------------------
        # Generate + validate + execute + repair
        # --------------------------------------------------------------

        try:

            sql, dataframe, repair_attempts = (
                self._generate_and_execute(
                    question,
                    prompt,
                    retrieved,
                    dialect,
                    tracker,
                )
            )

        except SQLGenerationError as exc:

            if not self.config.graceful_failure:
                raise

            _checkpoint("sql_generate_validate_execute")

            result = PipelineResult(
                question=question,
                sql=exc.sql,
                dataframe=pd.DataFrame(),
                answer=self._graceful_failure_message(
                    exc,
                    tables_used,
                ),
                repair_attempts=(
                    self.config.max_repair_attempts
                ),
                token_usage=tracker,
                tables_used=tables_used,
                success=False,
                error=(
                    str(exc.original_error)
                    if exc.original_error
                    else str(exc)
                ),
                elapsed_seconds=(
                    time.perf_counter()
                    - start_time
                ),
                stage_timings=timings,
            )

            if (
                self.cache is not None
                and cache_key is not None
            ):
                self.cache.set(
                    cache_key,
                    result,
                )

            return result

        _checkpoint("sql_generate_validate_execute")

        # --------------------------------------------------------------
        # Answer
        # --------------------------------------------------------------

        answer = (
            self._fast_answer_from_dataframe(
                dataframe
            )
        )

        if answer is not None:

            tracker.record(
                "answer_synthesis_fast",
                question + sql,
                answer,
            )

        else:
            # The database result is authoritative; avoid a second local LLM
            # call merely to paraphrase it. The deterministic formatter above
            # always handles non-empty results, so this branch is defensive.
            answer = self._fast_answer_from_dataframe(dataframe) or (
                "The query completed successfully, but there were no "
                "displayable results."
            )
            tracker.record(
                "answer_synthesis_deterministic",
                question + sql,
                answer,
            )

        _checkpoint("answer_synthesis")

        recommendations = []
        if self.config.enable_recommendations:
            try:
                recommendations = self.recommendation_engine.suggest(
                    question,
                    self.metadata,
                    semantic_schema=self.semantic_schema,
                    result_columns=list(dataframe.columns),
                )
            except Exception:
                recommendations = []

        if recommendations:
            answer = (
                answer
                + "\n\nYou might also ask:\n"
                + "\n".join(f"- {item}" for item in recommendations)
            )

        result = PipelineResult(
            question=question,
            sql=sql,
            dataframe=dataframe,
            answer=answer,
            repair_attempts=repair_attempts,
            token_usage=tracker,
            tables_used=tables_used,
            elapsed_seconds=(
                time.perf_counter()
                - start_time
            ),
            recommendations=recommendations,
            stage_timings=timings,
        )

        if (
            self.cache is not None
            and cache_key is not None
        ):
            self.cache.set(
                cache_key,
                result,
            )

        return result

    # ------------------------------------------------------------------
    # SQL generation / validation / execution / repair
    # ------------------------------------------------------------------

    def _generate_and_execute(
        self,
        question,
        prompt,
        retrieved,
        dialect,
        tracker: TokenTracker,
    ):

        attempts = 0
        last_error: Optional[Exception] = None
        last_sql = ""
        current_prompt = prompt

        max_new_tokens = (
            self.config.llm_max_new_tokens
            + (
                self.config.reasoning_reserved_tokens
                if self.config.use_reasoning
                else 0
            )
        )

        while (
            attempts
            <= self.config.max_repair_attempts
        ):

            raw = self.llm_backend.generate(
                current_prompt,
                max_new_tokens=max_new_tokens,
                temperature=(
                    self.config.llm_temperature
                    if self.config.llm_do_sample
                    else 0.0
                ),
            )

            stage = (
                "sql_generation"
                if attempts == 0
                else f"sql_repair_{attempts}"
            )

            tracker.record(
                stage,
                current_prompt,
                raw,
            )

            sql = self.cleaner.clean(raw)

            last_sql = sql

            try:

                # ------------------------------------------------------
                # 1. Safety + syntax + schema validation
                # ------------------------------------------------------

                self.validator.validate(
                    sql,
                    dialect=dialect,
                )

                # ------------------------------------------------------
                # 2. Semantic question/SQL alignment
                # ------------------------------------------------------

                alignment_error = (
                    self._question_sql_alignment_error(
                        question,
                        sql,
                    )
                )

                if alignment_error:

                    raise SQLValidationError(
                        alignment_error
                    )

                # ------------------------------------------------------
                # 3. Execute only after ALL validation passes
                # ------------------------------------------------------

                dataframe = self.provider.execute(
                    sql
                )

                return (
                    sql,
                    dataframe,
                    attempts,
                )

            except (
                SQLValidationError,
                SQLExecutionError,
            ) as exc:

                last_error = exc
                attempts += 1

                if (
                    attempts
                    > self.config.max_repair_attempts
                ):
                    break

                # ------------------------------------------------------
                # Feed exact failure back to LLM
                # ------------------------------------------------------

                current_prompt = (
                    self.prompt_builder.build_repair(
                        question,
                        retrieved,
                        dialect,
                        sql,
                        str(exc),
                    )
                )

        raise SQLGenerationError(
            f"Failed to produce a working query "
            f"after {attempts} attempt(s). "
            f"Last error: {last_error}",
            sql=last_sql,
            original_error=last_error,
        )

    # ------------------------------------------------------------------
    # Semantic learning reports
    # ------------------------------------------------------------------

    def describe_semantics(self) -> str:

        self.learn_schema()

        if not self.semantic_schema:

            if self.config.enable_semantic_learning:
                return (
                    "Semantic learning is enabled in lazy mode; "
                    "no table has been semantically learned yet. "
                    "Ask a database question to trigger targeted learning."
                )

            return (
                "Semantic learning is disabled "
                "(config.enable_semantic_learning=False) "
                "or produced no profiles."
            )

        lines = []

        for (
            table_name,
            table_profile,
        ) in self.semantic_schema.items():

            lines.append(
                f"Table: {table_name} "
                f"({table_profile.role})"
            )

            lines.append("=" * 60)

            for col in (
                table_profile.columns.values()
            ):

                lines.extend(
                    col.as_report_lines()
                )

                lines.append(
                    "-" * 50
                )

            lines.append("")

        return "\n".join(lines)

    def describe_column(
        self,
        table: str,
        column: str,
    ) -> Optional[
        ColumnSemanticProfile
    ]:

        self.learn_schema()

        table_profile = (
            self.semantic_schema.get(table)
        )

        return (
            table_profile.columns.get(column)
            if table_profile
            else None
        )

    # ------------------------------------------------------------------
    # Fine tuning
    # ------------------------------------------------------------------

    def finetune_on_data(
        self,
        num_epochs: int = 3,
        max_examples_per_table: int = 12,
        lora_r: int = 8,
        force_retrain: bool = False,
    ) -> "FineTuneResult":

        self.learn_schema()

        # Fine-tuning is an explicit/offline operation, so unlike normal
        # interactive queries it may intentionally learn the whole database
        # before generating semantic training pairs.
        if (
            self.config.enable_semantic_learning
            and self.metadata
        ):
            self._learn_semantics_for_tables(
                list(self.metadata.tables.keys())
            )

        generator = SyntheticSQLExampleGenerator(
            max_examples_per_table=max_examples_per_table
        )

        synthetic_examples = generator.generate(
            self.metadata,
            self.semantic_profile,
            self.statistics,
            self.graph,
        )

        # If semantic learning has been performed, add natural-language
        # examples using the learned meanings/synonyms. This is the bridge
        # between "the database taught us what xx means" and optional LoRA
        # training: the adapter learns to map those concepts to real columns.
        if self.semantic_schema:
            synthetic_examples.extend(
                generator.semantic_examples(
                    self.semantic_schema,
                    self.metadata,
                    max_examples=max(
                        16,
                        max_examples_per_table * max(
                            1,
                            len(self.metadata.tables),
                        ),
                    ),
                )
            )

        dialect = self.provider.dialect

        training_pairs = []

        for ex in synthetic_examples:

            retrieved = self.retriever.retrieve(
                ex["question"],
                top_k=self.config.top_k_tables,
            )

            retrieved = (
                self.retriever.expand_with_relationships(
                    retrieved,
                    self.documents,
                    self.graph,
                    hops=1,
                )
            )

            prompt = self.prompt_builder.build(
                ex["question"],
                retrieved,
                dialect=dialect,
                use_reasoning=self.config.use_reasoning,
            )

            if self.config.use_reasoning:

                completion = (
                    f"Reasoning: {ex['reasoning']}\n"
                    f"SQL: {ex['sql']}"
                )

            else:

                completion = ex["sql"]

            training_pairs.append({
                "prompt": prompt,
                "completion": completion,
            })

        tuner = SQLFineTuner(
            num_epochs=num_epochs,
            lora_r=lora_r,
        )

        return tuner.train(
            self.llm_backend,
            self.metadata,
            training_pairs,
            force_retrain=force_retrain,
        )

    # ------------------------------------------------------------------
    # Close
    # ------------------------------------------------------------------

    def close(self) -> None:
        self.provider.close()