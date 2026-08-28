"""Semantic Schema Learning — the "understand what this database *means*"
step, on top of the raw introspection (schema_metadata.py) and the
structural profiler (schema_profiler.py).

Why this exists: schema_profiler.py already classifies a column as
identifier/date_time/categorical/measure/free_text from *structure*
(SQL type, naming idioms, cardinality). That tells the model a column is
"a measure" but not *what it measures*. On a database with meaningless or
abbreviated column names (`xx`, `amt`, `dob`, `cust`) that gap is exactly
where a naive text-to-SQL pipeline fails: "average salary" never matches a
column literally named `xx`.

This module closes that gap automatically, for *any* schema, using no
per-column hardcoded name->meaning table:

  1. ColumnStatisticsCollector -- pulls real statistics per column (distinct
     count, min/max/avg for numeric columns, sample values) directly from
     the live database via safe, identifier-quoted aggregate queries.
  2. SemanticInferenceEngine -- for each table, sends the LLM already
     loaded for SQL generation a single batched prompt containing every
     column's name, SQL type, structural role, nearby columns, sample
     values and statistics, and asks it to infer: a semantic name, a list
     of synonyms/aliases, a business meaning, a unit (if any), a data
     category, and a confidence score -- purely from that context, the same
     way a human analyst would read an unfamiliar schema. Falls back to a
     conservative, low-confidence heuristic guess (never a domain-specific
     lookup table) if no LLM is available or its output can't be parsed.
  3. SemanticSchemaLearner -- orchestrates 1+2 across every table/column,
     producing a `TableSemanticProfile`/`ColumnSemanticProfile` per table,
     and builds a column-level *and* table-level embedding index (reusing
     retrieval.py's existing FAISS/BM25/keyword retrievers, so it degrades
     gracefully exactly like table retrieval already does) so a question
     like "employees earning more than 5000" can be matched, at the
     embedding level, to a column literally named `xx`.

Regenerating this is exactly as cheap as re-running `learn_schema()`: if the
underlying database changes (new columns, a different dataset entirely),
calling `pipeline.learn_schema(force=True)` throws every semantic profile
away and relearns from scratch -- nothing here is cached across schemas.
"""

import json
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .llm_backends import LLMBackend
from .retrieval import HybridRetriever, RetrievalDocument, build_retriever
from .schema_metadata import DatabaseMetadata
from .schema_profiler import TableProfile

_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_NUMERIC_TYPE_HINT = re.compile(r"(INT|DECIMAL|NUMERIC|FLOAT|DOUBLE|REAL|MONEY)", re.IGNORECASE)


def _quote_identifier(name: str, dialect: str) -> str:
    if not _SAFE_IDENTIFIER.match(name):
        raise ValueError(f"Unsafe identifier rejected: {name!r}")
    if dialect == "mysql":
        return f"`{name}`"
    return f'"{name}"'


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class ColumnStatistics:
    distinct_count: Optional[int] = None
    min_value: object = None
    max_value: object = None
    avg_value: Optional[float] = None
    sample_values: List = field(default_factory=list)

    def as_prompt_lines(self) -> List[str]:
        lines = []
        if self.sample_values:
            lines.append(f"Sample values: {self.sample_values}")
        if self.distinct_count is not None:
            lines.append(f"Distinct count: {self.distinct_count}")
        if self.min_value is not None or self.max_value is not None:
            lines.append(f"Min: {self.min_value}, Max: {self.max_value}")
        if self.avg_value is not None:
            lines.append(f"Average: {round(self.avg_value, 4)}")
        return lines


@dataclass
class ColumnSemanticProfile:
    table: str
    original_name: str
    sql_type: str
    structural_type: str  # identifier | date_time | categorical | measure | free_text
    nearby_columns: List[str] = field(default_factory=list)
    statistics: ColumnStatistics = field(default_factory=ColumnStatistics)

    # -- learned --
    semantic_name: str = ""
    synonyms: List[str] = field(default_factory=list)
    business_meaning: str = ""
    units: Optional[str] = None
    data_category: str = "Unknown"
    confidence: float = 0.0  # 0.0-1.0
    inferred_by: str = "heuristic"  # "llm" | "heuristic"

    @property
    def all_names(self) -> List[str]:
        """Every string a question might use to refer to this column."""
        names = [self.original_name, self.semantic_name] + list(self.synonyms)
        return [n for n in dict.fromkeys(n.strip() for n in names if n and n.strip())]

    @property
    def embedding_text(self) -> str:
        bits = [
            f"Column '{self.original_name}' in table '{self.table}'",
            f"Meaning: {self.semantic_name or self.original_name}",
        ]
        if self.synonyms:
            bits.append("Also called: " + ", ".join(self.synonyms))
        if self.business_meaning:
            bits.append(self.business_meaning)
        if self.units:
            bits.append(f"Unit: {self.units}")
        bits.append(f"Category: {self.data_category}")
        bits.append(f"Type: {self.structural_type}")
        return ". ".join(bits)

    def as_report_lines(self) -> List[str]:
        """Human-readable block matching the "Semantic Meaning / Possible
        Synonyms / Units / Data Category / Confidence" format used to spec
        this module -- handy for `pipeline.describe_column()` and docs."""
        lines = [
            f"Column Name: {self.original_name}",
        ]
        if self.statistics.sample_values:
            lines.append("Sample Values:")
            lines.extend(f"  {v}" for v in self.statistics.sample_values)
        if self.nearby_columns:
            lines.append("Nearby Columns:")
            lines.extend(f"  {c}" for c in self.nearby_columns)
        lines.append(f"Semantic Meaning: {self.semantic_name or '(unknown)'}")
        lines.append("Possible Synonyms: " + (", ".join(self.synonyms) if self.synonyms else "(none)"))
        lines.append(f"Units: {self.units or 'N/A'}")
        lines.append(f"Data Category: {self.data_category}")
        lines.append(f"Confidence: {round(self.confidence * 100)}%")
        return lines


@dataclass
class TableSemanticProfile:
    name: str
    role: str
    columns: Dict[str, ColumnSemanticProfile] = field(default_factory=dict)
    semantic_name: str = ""
    synonyms: List[str] = field(default_factory=list)
    business_meaning: str = ""
    confidence: float = 0.0
    inferred_by: str = "heuristic"

    @property
    def all_names(self) -> List[str]:
        names = [self.name, self.semantic_name] + list(self.synonyms)
        return [n for n in dict.fromkeys(
            n.strip() for n in names if n and n.strip()
        )]

    @property
    def embedding_text(self) -> str:
        table_label = self.semantic_name or self.name
        bits = [
            f"Table '{self.name}' ({self.role})",
            f"Meaning: {table_label}",
        ]
        if self.synonyms:
            bits.append("Also called: " + ", ".join(self.synonyms[:6]))
        if self.business_meaning:
            bits.append(self.business_meaning)

        for col in self.columns.values():
            fragment = col.semantic_name or col.original_name
            if col.synonyms:
                fragment += " (" + ", ".join(col.synonyms[:4]) + ")"
            bits.append(fragment)

        return ". ".join(bits)


# ---------------------------------------------------------------------------
# 1. Statistics collection
# ---------------------------------------------------------------------------

class ColumnStatisticsCollector:
    """Collect compact real-data evidence with a bounded number of queries.

    The original implementation executed up to two SQL statements per column.
    That becomes a serious latency problem on wide/unknown databases. This
    implementation uses one sample query per table plus one aggregate query for
    all numeric columns in that table, while preserving the same public
    statistics model.
    """

    def __init__(self, provider, max_sample_values: int = 5):
        self.provider = provider
        self.max_sample_values = max(1, min(int(max_sample_values), 10))

    def collect(
        self,
        metadata: DatabaseMetadata,
        include_tables: Optional[List[str]] = None,
    ) -> Dict[str, Dict[str, ColumnStatistics]]:
        allowed = set(include_tables) if include_tables else None
        out: Dict[str, Dict[str, ColumnStatistics]] = {}

        for table in metadata.tables.values():
            if allowed is not None and table.name not in allowed:
                continue

            out[table.name] = {}
            dialect = metadata.dialect

            try:
                qtable = _quote_identifier(table.name, dialect)
            except ValueError:
                for column in table.columns:
                    out[table.name][column.name] = ColumnStatistics()
                continue

            # ----------------------------------------------------------
            # One bounded sample query per table.
            # ----------------------------------------------------------
            try:
                if dialect == "mssql":
                    sample_sql = (
                        f"SELECT TOP ({self.max_sample_values}) * "
                        f"FROM {qtable}"
                    )
                else:
                    sample_sql = (
                        f"SELECT * FROM {qtable} "
                        f"LIMIT {self.max_sample_values}"
                    )

                sample_df = self.provider.execute(sample_sql)
            except Exception:
                sample_df = None

            for column in table.columns:
                stats = ColumnStatistics()

                if sample_df is not None and column.name in sample_df.columns:
                    try:
                        values = sample_df[column.name].tolist()
                        cleaned = []
                        seen = set()
                        for value in values:
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
                            if len(cleaned) >= self.max_sample_values:
                                break
                        stats.sample_values = cleaned
                    except Exception:
                        stats.sample_values = []

                out[table.name][column.name] = stats

            # ----------------------------------------------------------
            # One aggregate query for all numeric columns.
            # ----------------------------------------------------------
            numeric_columns = [
                c for c in table.columns
                if _NUMERIC_TYPE_HINT.search(c.data_type or "")
            ]

            if numeric_columns:
                expressions = []
                aliases = []
                for idx, column in enumerate(numeric_columns):
                    try:
                        qcol = _quote_identifier(column.name, dialect)
                    except ValueError:
                        continue
                    dc = f"dc_{idx}"
                    mn = f"mn_{idx}"
                    mx = f"mx_{idx}"
                    av = f"av_{idx}"
                    expressions.extend([
                        f"COUNT(DISTINCT {qcol}) AS {dc}",
                        f"MIN({qcol}) AS {mn}",
                        f"MAX({qcol}) AS {mx}",
                        f"AVG({qcol}) AS {av}",
                    ])
                    aliases.append((column.name, dc, mn, mx, av))

                if expressions:
                    try:
                        aggregate_sql = (
                            "SELECT " + ", ".join(expressions)
                            + f" FROM {qtable}"
                        )
                        aggregate_df = self.provider.execute(aggregate_sql)
                        row = aggregate_df.iloc[0] if aggregate_df is not None and not aggregate_df.empty else None

                        if row is not None:
                            for column_name, dc, mn, mx, av in aliases:
                                stats = out[table.name][column_name]
                                stats.distinct_count = _safe_int(row.get(dc))
                                stats.min_value = row.get(mn)
                                stats.max_value = row.get(mx)
                                stats.avg_value = _safe_float(row.get(av))
                    except Exception:
                        # Sample values are still useful even if the backend
                        # rejects a complex aggregate query.
                        pass

        return out


def _safe_int(value) -> Optional[int]:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _safe_float(value) -> Optional[float]:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# 2. Semantic inference (LLM-driven, with a conservative structural fallback)
# ---------------------------------------------------------------------------

_JSON_OBJECT_RE = re.compile(r"\{.*?\}", re.DOTALL)

_INFERENCE_INSTRUCTIONS = """You are learning an unfamiliar database for a generic Text-to-SQL system.

Infer the table's real-world concept and the meaning of each listed column ONLY from
the table name, table role, SQL types, nearby columns, and actual sample/statistical
evidence. Names may be abbreviated or meaningless. Never assume a fixed domain.

The result will be used to resolve natural language such as:
- a user saying "workers" when the database's table is named something else;
- a user saying "salary" when a monetary column has an opaque name such as "xx";
- a user asking about price, revenue, cost, age, dates, status, etc. when those
  concepts are not literal schema identifiers.

Respond with ONLY one JSON object:
{{
  "table": {{
    "semantic_name": "short business concept",
    "synonyms": ["3-8 useful natural-language aliases"],
    "business_meaning": "one short sentence",
    "confidence": 0-100
  }},
  "columns": [
    {{
      "column": "exact original column name",
      "semantic_name": "short business meaning",
      "synonyms": ["3-8 aliases"],
      "business_meaning": "one short sentence",
      "units": "unit or null",
      "data_category": "short category",
      "confidence": 0-100
    }}
  ]
}}

Return one column object for every listed column, in the same order.

TABLE
Name: {table_name}
Role: {table_role}

COLUMNS
{columns_block}

JSON:"""



class SemanticInferenceEngine:
    """Infers semantic meaning for a table's columns in one batched LLM call
    (bounded by `max_columns_per_call` so prompts/compute stay predictable
    on wide tables), with a structural fallback if the LLM is unavailable or
    its output can't be parsed. Nothing here is a per-column lookup table --
    every inference is derived fresh from the context given.
    """

    def __init__(
        self,
        llm_backend: Optional[LLMBackend],
        max_columns_per_call: int = 12,
        max_new_tokens: int = 384,
    ):
        self.llm_backend = llm_backend
        self.max_columns_per_call = max_columns_per_call
        self.max_new_tokens = max(160, int(max_new_tokens))

    def infer_table(
        self,
        table_name: str,
        columns: List,
        structural_profile: Optional[TableProfile],
        statistics: Dict[str, ColumnStatistics],
    ) -> Dict[str, ColumnSemanticProfile]:
        column_names = [c.name for c in columns]
        profiles: Dict[str, ColumnSemanticProfile] = {}
        self._last_table_semantic = {
            "semantic_name": "",
            "synonyms": [],
            "business_meaning": "",
            "confidence": 0.0,
            "inferred_by": "heuristic",
        }

        batch_size = max(1, int(self.max_columns_per_call))
        for start in range(0, len(columns), batch_size):
            batch = columns[start:start + batch_size]
            batch_profiles, table_semantic = self._infer_batch(
                table_name,
                batch,
                column_names,
                structural_profile,
                statistics,
            )
            profiles.update(batch_profiles)
            if table_semantic.get("semantic_name"):
                self._last_table_semantic = table_semantic

        return profiles

    # -- internals ----------------------------------------------------
    def _nearby(self, column_names: List[str], name: str, radius: int = 2) -> List[str]:
        idx = column_names.index(name)
        lo, hi = max(0, idx - radius), min(len(column_names), idx + radius + 1)
        return [c for c in column_names[lo:hi] if c != name]

    def _infer_batch(
        self,
        table_name,
        batch,
        column_names,
        structural_profile,
        statistics,
    ):
        skeletons: Dict[str, ColumnSemanticProfile] = {}
        blocks = []

        table_role = (
            structural_profile.role
            if structural_profile is not None
            else "unknown role"
        )

        for column in batch:
            nearby = self._nearby(column_names, column.name)
            col_stats = statistics.get(column.name, ColumnStatistics())
            structural_type = "unknown"

            if structural_profile and column.name in structural_profile.columns:
                structural_type = structural_profile.columns[column.name].semantic_type

            skeletons[column.name] = ColumnSemanticProfile(
                table=table_name,
                original_name=column.name,
                sql_type=column.data_type,
                structural_type=structural_type,
                nearby_columns=nearby,
                statistics=col_stats,
            )

            block_lines = [
                f"- Column Name: {column.name}",
                f"  SQL Type: {column.data_type}",
                f"  Structural Role: {structural_type}",
                f"  Nearby Columns: {', '.join(nearby) if nearby else '(none)'}",
            ]
            block_lines.extend(f"  {line}" for line in col_stats.as_prompt_lines())
            blocks.append("\n".join(block_lines))

        table_info = {
            "semantic_name": "",
            "synonyms": [],
            "business_meaning": "",
            "confidence": 0.0,
            "inferred_by": "heuristic",
        }

        if self.llm_backend is not None:
            try:
                parsed = self._call_llm(
                    table_name,
                    table_role,
                    blocks,
                )

                # New format: {table: {...}, columns: [...]}
                if isinstance(parsed, dict):
                    table_entry = parsed.get("table") or {}
                    if isinstance(table_entry, dict):
                        table_info["semantic_name"] = str(
                            table_entry.get("semantic_name") or ""
                        ).strip()
                        synonyms = table_entry.get("synonyms") or []
                        if isinstance(synonyms, list):
                            table_info["synonyms"] = [
                                str(s).strip() for s in synonyms
                                if str(s).strip()
                            ][:8]
                        table_info["business_meaning"] = str(
                            table_entry.get("business_meaning") or ""
                        ).strip()
                        try:
                            table_info["confidence"] = max(
                                0.0,
                                min(
                                    float(table_entry.get("confidence", 0)) / 100.0,
                                    1.0,
                                ),
                            )
                        except (TypeError, ValueError):
                            table_info["confidence"] = 0.0

                    parsed_columns = parsed.get("columns") or []
                else:
                    # Backward compatibility with the previous list format.
                    parsed_columns = parsed

                for entry in parsed_columns:
                    if not isinstance(entry, dict):
                        continue
                    name = str(entry.get("column", "")).strip()
                    if name in skeletons:
                        self._apply_llm_entry(skeletons[name], entry)

                if table_info["semantic_name"]:
                    table_info["inferred_by"] = "llm"

            except Exception:
                pass

        for name, profile in skeletons.items():
            if not profile.semantic_name:
                self._apply_heuristic(profile)

        return skeletons, table_info

    def _call_llm(
        self,
        table_name: str,
        table_role: str,
        blocks: List[str],
    ):
        prompt = _INFERENCE_INSTRUCTIONS.format(
            table_name=table_name,
            table_role=table_role,
            columns_block="\n\n".join(blocks),
        )

        # Keep the semantic call bounded. It is only performed for a small
        # candidate table on demand, then cached for future questions.
        token_budget = min(
            self.max_new_tokens,
            max(160, 24 * len(blocks)),
        )

        raw = self.llm_backend.generate(
            prompt,
            max_new_tokens=token_budget,
            temperature=0.0,
        )
        return _parse_json_object_or_array(raw)


    @staticmethod
    def _apply_llm_entry(profile: ColumnSemanticProfile, entry: dict) -> None:
        semantic_name = str(entry.get("semantic_name") or "").strip()
        if not semantic_name:
            return
        profile.semantic_name = semantic_name
        synonyms = entry.get("synonyms") or []
        if isinstance(synonyms, list):
            profile.synonyms = [str(s).strip() for s in synonyms if str(s).strip()][:8]
        profile.business_meaning = str(entry.get("business_meaning") or "").strip()
        units = entry.get("units")
        profile.units = str(units).strip() if units not in (None, "", "null") else None
        profile.data_category = str(entry.get("data_category") or "Unknown").strip() or "Unknown"
        try:
            confidence = float(entry.get("confidence", 0))
        except (TypeError, ValueError):
            confidence = 0.0
        profile.confidence = max(0.0, min(confidence, 100.0)) / 100.0
        profile.inferred_by = "llm"

    @staticmethod
    def _apply_heuristic(profile: ColumnSemanticProfile) -> None:
        """Conservative, generic fallback used only when the LLM is
        unavailable or unparsable -- never a domain-specific name->meaning
        table. It just turns the raw name into a readable label and defers
        everything else (synonyms, units, category) to "unknown" with a low
        confidence, so downstream code can tell a real inference from a
        guess."""
        readable = re.sub(r"[_\-]+", " ", profile.original_name).strip()
        readable = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", readable)  # camelCase -> spaced
        profile.semantic_name = readable.title() if readable else profile.original_name
        profile.synonyms = []
        profile.business_meaning = (
            f"Column '{profile.original_name}' ({profile.structural_type}); "
            "no LLM-based semantic inference available."
        )
        category_by_structural_type = {
            "identifier": "Identifier",
            "date_time": "Temporal",
            "measure": "Numeric",
            "categorical": "Categorical",
            "free_text": "Descriptive",
        }
        profile.data_category = category_by_structural_type.get(profile.structural_type, "Unknown")
        profile.units = None
        profile.confidence = 0.35
        profile.inferred_by = "heuristic"


def _parse_json_object_or_array(raw: str):
    """Tolerant JSON extraction for semantic-learning output."""
    text = (raw or "").strip()
    text = re.sub(r"^```(?:json)?", "", text.strip(), flags=re.IGNORECASE)
    text = re.sub(r"```$", "", text.strip()).strip()

    try:
        parsed = json.loads(text)
        if isinstance(parsed, (dict, list)):
            return parsed
    except (json.JSONDecodeError, ValueError):
        pass

    # Try an outer object first.
    first = text.find("{")
    last = text.rfind("}")
    if first != -1 and last > first:
        try:
            parsed = json.loads(text[first:last + 1])
            if isinstance(parsed, (dict, list)):
                return parsed
        except (json.JSONDecodeError, ValueError):
            pass

    objects = []
    for match in _JSON_OBJECT_RE.finditer(text):
        try:
            obj = json.loads(match.group(0))
            if isinstance(obj, dict):
                objects.append(obj)
        except (json.JSONDecodeError, ValueError):
            continue

    return objects



# ---------------------------------------------------------------------------
# 3. Orchestration + embedding index
# ---------------------------------------------------------------------------

@dataclass
class SemanticLearningResult:
    table_profiles: Dict[str, TableSemanticProfile]
    column_documents: List[RetrievalDocument]
    column_retriever: HybridRetriever

    def get_column(self, table: str, column: str) -> Optional[ColumnSemanticProfile]:
        table_profile = self.table_profiles.get(table)
        return table_profile.columns.get(column) if table_profile else None

    def describe(self) -> str:
        """Full human-readable semantic report for the whole database, in
        the same "Column Name / Sample Values / Nearby Columns / Semantic
        Meaning / Possible Synonyms / Units / Data Category / Confidence"
        format this module was specified with."""
        lines = []
        for table_name, table_profile in self.table_profiles.items():
            lines.append(f"Table: {table_name} ({table_profile.role})")
            lines.append("=" * 60)
            for col in table_profile.columns.values():
                lines.extend(col.as_report_lines())
                lines.append("-" * 50)
            lines.append("")
        return "\n".join(lines)


class SemanticSchemaLearner:
    """Top-level entry point: scans every table/column, infers meaning, and
    builds a searchable (embedding-backed, when FAISS/sentence-transformers
    are installed -- keyword otherwise, exactly like table-level retrieval)
    index over columns so a question can be matched to a column by meaning
    instead of by literal name.
    """

    def __init__(
        self,
        provider,
        llm_backend: Optional[LLMBackend] = None,
        max_sample_values: int = 5,
        max_columns_per_llm_call: int = 12,
        max_new_tokens: int = 384,
        retrieval_strategy: str = "hybrid",
        embedding_model: Optional[str] = None,
    ):
        self.stats_collector = ColumnStatisticsCollector(provider, max_sample_values=max_sample_values)
        self.inference_engine = SemanticInferenceEngine(
            llm_backend,
            max_columns_per_call=max_columns_per_llm_call,
            max_new_tokens=max_new_tokens,
        )
        self.retrieval_strategy = retrieval_strategy
        self.embedding_model = embedding_model

    def learn(
        self,
        metadata: DatabaseMetadata,
        structural_profiles: Dict[str, TableProfile],
        verbose: bool = True,
        include_tables: Optional[List[str]] = None,
        include_columns: Optional[Dict[str, List[str]]] = None,
    ) -> SemanticLearningResult:
        """Learn semantics for all tables or only a targeted subset.

        ``include_tables`` is the key latency optimization for interactive
        systems: the first database build can remain fast, while a question
        that needs semantic resolution learns only the most relevant table(s).
        """

        selected = set(include_tables) if include_tables else set(metadata.tables)
        selected_tables = [
            table for table in metadata.tables.values()
            if table.name in selected
        ]

        statistics = self.stats_collector.collect(
            metadata,
            include_tables=[table.name for table in selected_tables],
        )

        table_profiles: Dict[str, TableSemanticProfile] = {}
        column_documents: List[RetrievalDocument] = []

        total = len(selected_tables)

        for i, table in enumerate(selected_tables, start=1):
            if verbose:
                print(
                    f"  Learning column semantics: table {i}/{total} "
                    f"('{table.name}')..."
                )

            structural = structural_profiles.get(table.name)
            role = structural.role if structural else "unknown role"

            columns_to_infer = table.columns
            if include_columns and table.name in include_columns:
                wanted = set(include_columns[table.name])
                columns_to_infer = [
                    column for column in table.columns
                    if column.name in wanted
                ]

            column_profiles = self.inference_engine.infer_table(
                table.name,
                columns_to_infer,
                structural,
                statistics.get(table.name, {}),
            )

            table_semantic = getattr(
                self.inference_engine,
                "_last_table_semantic",
                {},
            )
            table_profiles[table.name] = TableSemanticProfile(
                name=table.name,
                role=role,
                columns=column_profiles,
                semantic_name=str(
                    table_semantic.get("semantic_name") or ""
                ).strip(),
                synonyms=list(
                    table_semantic.get("synonyms") or []
                )[:8],
                business_meaning=str(
                    table_semantic.get("business_meaning") or ""
                ).strip(),
                confidence=float(
                    table_semantic.get("confidence") or 0.0
                ),
                inferred_by=str(
                    table_semantic.get("inferred_by") or "heuristic"
                ),
            )

            for col_name, col_profile in column_profiles.items():
                column_documents.append(
                    RetrievalDocument(
                        document_id=f"{table.name}.{col_name}",
                        table=table.name,
                        text=col_profile.embedding_text,
                        metadata={
                            "table": table.name,
                            "column": col_name,
                            "profile": col_profile,
                        },
                    )
                )

        column_retriever = build_retriever(
            self.retrieval_strategy,
            self.embedding_model,
        )

        if column_documents:
            column_retriever.build(column_documents)

        return SemanticLearningResult(
            table_profiles=table_profiles,
            column_documents=column_documents,
            column_retriever=column_retriever,
        )


def build_semantic_hint_block(
    matches: List[dict], min_confidence: float = 0.0, max_items: int = 8,
) -> str:
    """Build a compact semantic-column mapping block.

    The caller controls min_confidence. We never silently enable semantic
    learning or change the project's config-level feature flag.
    """
    """Turns column-retriever hits into a short prompt block the SQL-writing
    LLM can use to resolve a business term ("salary") to the actual column
    ("xx") -- this is what lets the pipeline answer "average salary" even
    when no column is literally named that."""
    lines = []
    seen = set()
    for hit in matches[:max_items]:
        doc = hit["document"]
        profile: ColumnSemanticProfile = doc.metadata.get("profile")
        if profile is None or profile.confidence < min_confidence:
            continue
        key = (profile.table, profile.original_name)
        if key in seen:
            continue
        seen.add(key)
        syn = ", ".join(profile.synonyms[:5]) if profile.synonyms else "(none)"
        lines.append(
            f"- \"{profile.semantic_name}\" likely refers to {profile.table}.{profile.original_name} "
            f"(synonyms: {syn}; confidence: {round(profile.confidence * 100)}%)"
        )
    if not lines:
        return ""
    return (
        "Semantic Column Mapping (learned from this database's actual data -- "
        "use these to resolve business terms in the question to real columns):\n"
        + "\n".join(lines)
    )

def build_semantic_table_hint_block(
    table_profiles: Dict[str, TableSemanticProfile],
    min_confidence: float = 0.0,
    max_items: int = 4,
) -> str:
    """Build compact table-level semantic mappings for SQL generation.

    This is what lets "workers" resolve to an Employee-like table without
    hardcoding any particular dataset's table name.
    """
    lines = []
    for profile in list(table_profiles.values())[:max_items]:
        if not profile.semantic_name:
            continue
        if profile.confidence < min_confidence:
            continue

        synonyms = ", ".join(profile.synonyms[:6]) if profile.synonyms else "(none)"
        lines.append(
            f'- "{profile.semantic_name}" likely refers to table '
            f'{profile.name} (synonyms: {synonyms}; '
            f'confidence: {round(profile.confidence * 100)}%)'
        )

    if not lines:
        return ""

    return (
        "Semantic Table Mapping (learned from this database):\n"
        + "\n".join(lines)
    )
