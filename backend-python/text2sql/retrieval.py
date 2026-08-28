"""Schema retrieval for dynamic Text-to-SQL.

Optimized for small local LLMs:
- compact table documents;
- fast lexical retrieval always available;
- optional BM25/FAISS retrieval;
- bounded candidate pools;
- lightweight query normalization;
- relationship expansion kept separate from semantic retrieval;
- optional cross-encoder reranking only on a small pool.

Important:
semantic schema learning is orchestrated by the pipeline. This module remains
LLM-free: it only retrieves from documents/profiles that the pipeline has
already learned.
"""

import re
from dataclasses import dataclass
from typing import Dict, List, Optional

from .relationships import RelationshipGraph
from .schema_metadata import DatabaseMetadata
from .schema_profiler import TableProfile


@dataclass
class RetrievalDocument:
    document_id: str
    table: str
    text: str
    metadata: dict


_WORD_RE = re.compile(r"[a-zA-Z0-9_]+")


def _tokens(text: str) -> List[str]:
    return _WORD_RE.findall((text or "").lower())


def _normalize_query(text: str) -> str:
    """Normalize common natural-language wording without changing meaning."""
    text = (text or "").lower()
    text = re.sub(r"[^\w\s]", " ", text)
    return " ".join(text.split())


class SchemaDocumentBuilder:
    """Build one compact retrieval document per database table."""

    def __init__(self, max_sample_values: int = 5):
        self.max_sample_values = max(1, min(int(max_sample_values), 10))

    def build(
        self,
        metadata: DatabaseMetadata,
        statistics: dict,
        profiles: Optional[Dict[str, TableProfile]] = None,
        semantic_profiles: Optional[Dict] = None,
    ) -> List[RetrievalDocument]:
        profiles = profiles or {}
        semantic_profiles = semantic_profiles or {}
        documents = []

        for table in metadata.tables.values():
            table_profile = profiles.get(table.name)
            table_semantic = semantic_profiles.get(table.name)

            lines = [f"Table {table.name}"]

            if table_profile:
                lines.append(f"Role: {table_profile.role}")

            lines.append("Columns:")

            for column in table.columns:
                flags = []

                if column.primary_key:
                    flags.append("PK")

                if not column.nullable:
                    flags.append("NOT NULL")

                col_profile = (
                    table_profile.columns.get(column.name)
                    if table_profile
                    else None
                )

                if col_profile:
                    flags.append(col_profile.semantic_type)

                flag_str = (
                    f" [{', '.join(flags)}]"
                    if flags
                    else ""
                )

                semantic_suffix = ""

                col_semantic = (
                    table_semantic.columns.get(column.name)
                    if table_semantic
                    else None
                )

                if col_semantic and col_semantic.semantic_name:
                    syn = ", ".join(
                        col_semantic.synonyms[:3]
                    )
                    semantic_suffix = (
                        f" -> {col_semantic.semantic_name}"
                    )

                    if syn:
                        semantic_suffix += f" ({syn})"

                    if col_semantic.units:
                        semantic_suffix += (
                            f" [{col_semantic.units}]"
                        )

                lines.append(
                    f"- {column.name} ({column.data_type})"
                    f"{flag_str}{semantic_suffix}"
                )

            if table.foreign_keys:
                lines.append("Foreign Keys:")

                for fk in table.foreign_keys:
                    is_self_ref = (
                        fk.referenced_table.lower()
                        == table.name.lower()
                    )
                    self_ref_note = (
                        " (SELF-REFERENCING -- this table stores a "
                        "hierarchy/tree; e.g. a manager/parent chain. "
                        "Use a recursive CTE for multi-level questions.)"
                        if is_self_ref
                        else ""
                    )
                    lines.append(
                        f"- {fk.column} -> "
                        f"{fk.referenced_table}."
                        f"{fk.referenced_column}"
                        f"{self_ref_note}"
                    )

            table_stats = statistics.get(table.name, {})

            if table_stats:
                lines.append("Sample Values:")

                for column, values in table_stats.items():
                    trimmed = values[: self.max_sample_values]

                    if trimmed:
                        lines.append(
                            f"- {column}: {trimmed}"
                        )

            text = "\n".join(lines)

            documents.append(
                RetrievalDocument(
                    document_id=table.name,
                    table=table.name,
                    text=text,
                    metadata={"table": table.name},
                )
            )

        return documents


class KeywordRetriever:
    """Fast zero-dependency lexical retriever.

    Table/column names receive extra weight because exact schema-name
    mentions are highly reliable signals for Text-to-SQL.
    """

    def __init__(self):
        self.documents: List[RetrievalDocument] = []
        self._tokenized: List[set] = []
        self._name_tokens: List[set] = []

    def build(
        self,
        documents: List[RetrievalDocument],
    ) -> None:
        self.documents = documents

        self._tokenized = [
            set(_tokens(d.text))
            for d in documents
        ]

        self._name_tokens = [
            set(_tokens(d.table))
            for d in documents
        ]

    def search(
        self,
        question: str,
        top_k: int = 5,
    ) -> List[dict]:
        if not self.documents:
            return []

        top_k = max(1, int(top_k))
        q = _normalize_query(question)
        q_tokens = set(_tokens(q))

        scored = []

        for doc, tokens, name_tokens in zip(
            self.documents,
            self._tokenized,
            self._name_tokens,
        ):
            overlap = len(q_tokens & tokens)

            # Exact table-name token matches are strong evidence.
            name_overlap = len(
                q_tokens & name_tokens
            )

            # Direct table-name phrase match is stronger still.
            phrase_bonus = (
                4.0
                if doc.table.lower() in q
                else 0.0
            )

            score = (
                float(overlap)
                + 3.0 * name_overlap
                + phrase_bonus
            )

            scored.append((score, doc))

        scored.sort(
            key=lambda item: item[0],
            reverse=True,
        )

        positive = [
            {"score": score, "document": doc}
            for score, doc in scored[:top_k]
            if score > 0
        ]

        if positive:
            return positive

        # Never return an empty retrieval set when the DB is valid.
        return [
            {"score": 0.0, "document": doc}
            for doc in self.documents[:top_k]
        ]


class BM25Retriever:
    """Optional BM25 lexical retriever."""

    def __init__(self):
        self.documents: List[RetrievalDocument] = []
        self.bm25 = None

    @staticmethod
    def available() -> bool:
        try:
            import rank_bm25  # noqa: F401
            return True
        except ImportError:
            return False

    def build(
        self,
        documents: List[RetrievalDocument],
    ) -> None:
        from rank_bm25 import BM25Okapi

        self.documents = documents

        corpus = [
            _tokens(doc.text)
            for doc in documents
        ]

        self.bm25 = BM25Okapi(corpus)

    def search(
        self,
        question: str,
        top_k: int = 5,
    ) -> List[dict]:
        if not self.documents or self.bm25 is None:
            return []

        import numpy as np

        top_k = max(1, int(top_k))
        scores = self.bm25.get_scores(
            _tokens(question)
        )

        order = np.argsort(scores)[::-1][:top_k]

        return [
            {
                "score": float(scores[i]),
                "document": self.documents[i],
            }
            for i in order
        ]


class FaissRetriever:
    """Optional embedding retriever.

    The embedding model is loaded once and reused for all questions.
    """

    def __init__(
        self,
        embedding_model: str,
    ):
        self.embedding_model_name = embedding_model
        self._model = None
        self.index = None
        self.documents: List[RetrievalDocument] = []

    @staticmethod
    def available() -> bool:
        try:
            import faiss  # noqa: F401
            import sentence_transformers  # noqa: F401
            return True
        except ImportError:
            return False

    def _load_model(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(
                self.embedding_model_name
            )

        return self._model

    def build(
        self,
        documents: List[RetrievalDocument],
    ) -> None:
        import faiss

        self.documents = documents

        if not documents:
            self.index = None
            return

        model = self._load_model()

        embeddings = model.encode(
            [d.text for d in documents],
            convert_to_numpy=True,
            normalize_embeddings=True,
            show_progress_bar=False,
            batch_size=32,
        ).astype("float32")

        self.index = faiss.IndexFlatIP(
            embeddings.shape[1]
        )
        self.index.add(embeddings)

    def search(
        self,
        question: str,
        top_k: int = 5,
    ) -> List[dict]:
        if self.index is None or not self.documents:
            return []

        model = self._load_model()

        vector = model.encode(
            [_normalize_query(question)],
            convert_to_numpy=True,
            normalize_embeddings=True,
            show_progress_bar=False,
        )[0].astype("float32")

        scores, indices = self.index.search(
            vector.reshape(1, -1),
            max(1, int(top_k)),
        )

        return [
            {
                "score": float(score),
                "document": self.documents[index],
            }
            for score, index in zip(
                scores[0],
                indices[0],
            )
            if index != -1
        ]


class HybridRetriever:
    """Combines available retrievers and deduplicates by table.

    We use rank-based fusion instead of comparing raw BM25/cosine scores,
    because those score scales are not directly comparable.
    """

    def __init__(self, retrievers: List):
        self.retrievers = retrievers
        self.documents: List[RetrievalDocument] = []

    def build(
        self,
        documents: List[RetrievalDocument],
    ) -> None:
        self.documents = documents

        working = []

        for retriever in self.retrievers:
            try:
                retriever.build(documents)
                working.append(retriever)
            except Exception as exc:
                print(
                    "[text2sql] Warning: "
                    f"{type(retriever).__name__} failed to build "
                    f"({exc}); skipping it."
                )

        if not working:
            fallback = KeywordRetriever()
            fallback.build(documents)
            working.append(fallback)

        self.retrievers = working

    def retrieve(
        self,
        question: str,
        top_k: int = 5,
        candidate_k: Optional[int] = None,
    ) -> List[dict]:
        if not self.documents:
            return []

        top_k = max(1, int(top_k))
        candidate_k = max(
            top_k,
            int(candidate_k or top_k * 2),
        )

        ranked_by_table: Dict[str, dict] = {}
        any_succeeded = False

        # Retrieve a modest candidate pool from each backend.
        for retriever in self.retrievers:
            try:
                results = retriever.search(
                    question,
                    candidate_k,
                )
            except Exception as exc:
                print(
                    "[text2sql] Warning: "
                    f"{type(retriever).__name__} search failed "
                    f"({exc}); skipping it for this query."
                )
                continue

            any_succeeded = True

            # Reciprocal-rank style contribution. This avoids comparing
            # incompatible BM25 and cosine score magnitudes.
            for rank, item in enumerate(results, start=1):
                document = item["document"]
                key = document.document_id

                contribution = 1.0 / (
                    10.0 + rank
                )

                if key not in ranked_by_table:
                    ranked_by_table[key] = {
                        "document": document,
                        "score": contribution,
                    }
                else:
                    ranked_by_table[key]["score"] += contribution

        if not any_succeeded:
            return [
                {
                    "score": 0.0,
                    "document": doc,
                }
                for doc in self.documents[:top_k]
            ]

        ranked = sorted(
            ranked_by_table.values(),
            key=lambda item: item["score"],
            reverse=True,
        )

        return ranked[:top_k]

    def expand_with_relationships(
        self,
        results: List[dict],
        all_documents: List[RetrievalDocument],
        graph: RelationshipGraph,
        hops: int = 1,
        max_total_tables: Optional[int] = None,
    ) -> List[dict]:
        """Add FK-linked tables while keeping the prompt bounded."""

        if not results:
            return []

        tables = [
            result["document"].table
            for result in results
        ]

        expanded_tables = graph.related_tables(
            tables,
            hops=hops,
        )

        by_table = {
            document.table: document
            for document in all_documents
        }

        existing = {
            result["document"].table
            for result in results
        }

        out = list(results)

        for table in expanded_tables:
            if table in existing:
                continue

            if table not in by_table:
                continue

            out.append(
                {
                    "score": 0.0,
                    "document": by_table[table],
                }
            )

            existing.add(table)

            if (
                max_total_tables is not None
                and len(out) >= max_total_tables
            ):
                break

        return out


def build_retriever(
    strategy: str,
    embedding_model: Optional[str] = None,
) -> HybridRetriever:
    """Create the requested retriever with graceful degradation."""

    strategy = (strategy or "keyword").lower()
    retrievers = []

    if strategy in ("bm25", "hybrid"):
        if BM25Retriever.available():
            retrievers.append(
                BM25Retriever()
            )

    if strategy in ("faiss", "hybrid"):
        if (
            embedding_model
            and FaissRetriever.available()
        ):
            retrievers.append(
                FaissRetriever(embedding_model)
            )

    # Keyword retrieval remains the mandatory fast fallback.
    if not retrievers:
        retrievers.append(
            KeywordRetriever()
        )

    return HybridRetriever(retrievers)


class CrossEncoderReranker:
    """Optional precise reranker over a very small candidate pool."""

    def __init__(
        self,
        model_name: str,
    ):
        self.model_name = model_name
        self._model = None

    @staticmethod
    def available() -> bool:
        try:
            from sentence_transformers import CrossEncoder  # noqa: F401
            return True
        except ImportError:
            return False

    def _load(self):
        if self._model is None:
            from sentence_transformers import CrossEncoder

            self._model = CrossEncoder(
                self.model_name
            )

        return self._model

    def rerank(
        self,
        question: str,
        candidates: List[dict],
        top_k: int,
        max_candidates: int = 12,
    ) -> List[dict]:
        if not candidates:
            return candidates

        # Critical CPU optimization:
        # never run a cross-encoder over a large schema.
        candidates = candidates[
            : max(1, int(max_candidates))
        ]

        model = self._load()

        pairs = [
            (
                question,
                candidate["document"].text,
            )
            for candidate in candidates
        ]

        scores = model.predict(
            pairs,
            show_progress_bar=False,
        )

        reranked = sorted(
            zip(scores, candidates),
            key=lambda item: item[0],
            reverse=True,
        )

        return [
            {
                "score": float(score),
                "document": candidate["document"],
            }
            for score, candidate in reranked[
                : max(1, int(top_k))
            ]
        ]


def build_reranker(
    use_reranker: bool,
    reranker_model: Optional[str],
):
    """Build the optional reranker or return None."""

    if not use_reranker or not reranker_model:
        return None

    if not CrossEncoderReranker.available():
        return None

    return CrossEncoderReranker(
        reranker_model
    )