"""Synthesize grounded natural-language answers from executed SQL results.

The answer layer is deliberately conservative:

- Empty results are handled without an LLM.
- Single-cell aggregate results are answered deterministically.
- "highest/top" and "lowest/bottom" questions can be summarized directly
  from the full executed dataframe when the result contains the requested
  measure, avoiding small-LLM mistakes.
- Normal multi-row results use a compact LLM prompt.
- The LLM is never allowed to invent facts that are absent from the result.
"""

import re
from typing import Optional

import pandas as pd

from .llm_backends import LLMBackend


_HASHTAG_RE = re.compile(r"#\w+")

_NEW_TURN_MARKERS = (
    "\nQuestion:",
    "\nQ:",
    "\nUser:",
    "\nFriendly answer:",
)


class AnswerSynthesizer:
    _FRIENDLY_SYSTEM_INSTRUCTION = (
        "You are a friendly data assistant in a website chat. "
        "Answer the user naturally and clearly. "
        "Do not mention tables, schemas, SQL, queries, pipelines, models, "
        "retrieval, caching, or implementation details unless the user asks. "
        "Use only facts present in the executed result. "
        "For lists, present the most useful information clearly. "
        "Be concise and conversational. "
        "Do not start with technical phrases such as 'According to the data' "
        "or 'Found N matching records' unless needed for clarity."
    )

    def __init__(
        self,
        llm_backend: LLMBackend,
        max_rows: int = 20,
        max_data_chars: int = 6000,
    ):
        self.llm_backend = llm_backend
        self.max_rows = max_rows
        self.max_data_chars = max_data_chars

    # -----------------------------------------------------------------
    # Result serialization
    # -----------------------------------------------------------------

    def _serialize(self, dataframe: pd.DataFrame) -> str:
        """Serialize only a compact preview of the actual result."""

        if dataframe.empty:
            return "(query returned no rows)"

        trimmed = dataframe.head(self.max_rows)
        table_text = trimmed.to_csv(index=False)

        if len(dataframe) > self.max_rows:
            table_text += (
                f"\n... ({len(dataframe) - self.max_rows} "
                "more rows not shown)"
            )

        if len(table_text) > self.max_data_chars:
            table_text = (
                table_text[: self.max_data_chars]
                + "\n... (result preview truncated)"
            )

        return table_text

    # -----------------------------------------------------------------
    # Cleanup
    # -----------------------------------------------------------------

    def _clean(self, text: str) -> str:
        """Remove common small-model continuation/degradation patterns."""

        text = (text or "").strip()

        hashtags = list(_HASHTAG_RE.finditer(text))
        if len(hashtags) >= 3:
            text = text[: hashtags[0].start()].strip()

        for marker in _NEW_TURN_MARKERS:
            idx = text.find(marker)
            if idx != -1:
                text = text[:idx].strip()

        # Remove accidental markdown fences.
        text = text.replace("```text", "").replace("```", "").strip()

        if not text:
            return (
                "I found the data, but had trouble summarizing it. "
                "Check the SQL/result preview below."
            )

        return text

    # -----------------------------------------------------------------
    # Empty-result response
    # -----------------------------------------------------------------

    def _not_found_message(
        self,
        tables_checked: Optional[list] = None,
        available_tables: Optional[list] = None,
    ) -> str:
        """Explain empty results without inventing information."""

        lines = [
            "I couldn’t find anything matching your question."
        ]

        if tables_checked:
            lines.append(
                f"I checked: {', '.join(tables_checked)} — "
                "no rows matched."
            )

        others = [
            table
            for table in (available_tables or [])
            if table not in (tables_checked or [])
        ]

        if others:
            lines.append(
                "You might find related data in: "
                + ", ".join(others)
                + "."
            )

        lines.append(
            "Try rephrasing the question or giving me a little more detail."
        )

        return "\n".join(lines)

    # -----------------------------------------------------------------
    # Single-cell deterministic answers
    # -----------------------------------------------------------------

    def _deterministic_single_value_answer(
        self,
        question: str,
        dataframe: pd.DataFrame,
    ) -> Optional[str]:
        """Answer one-cell results without using the LLM."""

        if dataframe.shape != (1, 1):
            return None

        value = dataframe.iloc[0, 0]

        if pd.isna(value):
            return None

        if hasattr(value, "item"):
            try:
                value = value.item()
            except Exception:
                pass

        column = str(dataframe.columns[0])

        return f"Here’s what I found: {column} is {value}."

    # -----------------------------------------------------------------
    # Top/highest/bottom/lowest deterministic handling
    # -----------------------------------------------------------------

    @staticmethod
    def _normalized(text: str) -> str:
        return re.sub(r"[^a-z0-9]+", "", str(text).lower())

    def _find_requested_measure(
        self,
        question: str,
        dataframe: pd.DataFrame,
    ) -> Optional[str]:
        """Find a result column that appears to be the requested measure.

        Example:
            question = "Who has the highest MonthlyRate?"
            columns = ["EmployeeNumber", "MonthlyRate"]

        -> "MonthlyRate"
        """

        q = self._normalized(question)

        # Prefer the longest matching column name so that a column such as
        # "MonthlyRate" wins over a shorter related name.
        candidates = []
        for column in dataframe.columns:
            normalized_column = self._normalized(column)
            if normalized_column and normalized_column in q:
                candidates.append((len(normalized_column), str(column)))

        if candidates:
            candidates.sort(reverse=True)
            return candidates[0][1]

        return None

    @staticmethod
    def _is_numeric_series(series: pd.Series) -> bool:
        return pd.api.types.is_numeric_dtype(series)

    def _deterministic_extreme_answer(
        self,
        question: str,
        dataframe: pd.DataFrame,
    ) -> Optional[str]:
        """Answer common highest/lowest questions directly from the result.

        This is useful when a small LLM generated a grouped result such as:

            SELECT EmployeeNumber, MAX(MonthlyRate)
            FROM ...
            GROUP BY EmployeeNumber

        The executed dataframe still contains enough information to answer
        "Who has the highest MonthlyRate?" correctly. We select the maximum
        value from the full dataframe rather than trusting an LLM to inspect
        every row.

        This is only used when the requested measure can be identified from
        the question and is numeric.
        """

        if dataframe.empty or len(dataframe.columns) < 1:
            return None

        q = question.lower()

        highest = bool(
            re.search(
                r"\b(highest|max(?:imum)?|top|most|largest)\b",
                q,
            )
        )

        lowest = bool(
            re.search(
                r"\b(lowest|min(?:imum)?|bottom|least|smallest)\b",
                q,
            )
        )

        if not highest and not lowest:
            return None

        measure = self._find_requested_measure(question, dataframe)

        # If the question explicitly names a measure, use it.
        # Otherwise, choose the last numeric column as a conservative
        # fallback, because SQL result sets commonly place the aggregate
        # measure after the identifying column.
        if measure is None:
            numeric_columns = [
                str(column)
                for column in dataframe.columns
                if self._is_numeric_series(dataframe[column])
            ]
            if not numeric_columns:
                return None
            measure = numeric_columns[-1]

        if measure not in dataframe.columns:
            return None

        series = pd.to_numeric(dataframe[measure], errors="coerce")
        valid = series.dropna()

        if valid.empty:
            return None

        target_value = (
            valid.max()
            if highest
            else valid.min()
        )

        matching_rows = dataframe.loc[series == target_value]

        if matching_rows.empty:
            return None

        row = matching_rows.iloc[0]

        # Prefer an identifier-like column for "Who/which employee/etc."
        # questions, while avoiding the measure itself.
        identifier_column = None

        for column in dataframe.columns:
            name = str(column).lower()
            if str(column) == measure:
                continue

            if any(
                hint in name
                for hint in (
                    "employee",
                    "customer",
                    "user",
                    "person",
                    "id",
                    "name",
                )
            ):
                identifier_column = str(column)
                break

        # If no obvious identifier exists, use the first non-measure column.
        if identifier_column is None:
            non_measure = [
                str(column)
                for column in dataframe.columns
                if str(column) != measure
            ]
            if non_measure:
                identifier_column = non_measure[0]

        value = row[measure]

        if identifier_column is not None:
            identifier = row[identifier_column]

            if highest:
                return (
                    f"The highest {measure} is {value}, "
                    f"for {identifier_column} {identifier}."
                )

            return (
                f"The lowest {measure} is {value}, "
                f"for {identifier_column} {identifier}."
            )

        if highest:
            return f"The highest {measure} is {value}."

        return f"The lowest {measure} is {value}."

    # -----------------------------------------------------------------
    # Large-result deterministic answer
    # -----------------------------------------------------------------

    def _large_result_message(self, dataframe: pd.DataFrame) -> str:
        """Deterministic message for results too large to hand to the LLM.

        Serializing hundreds/thousands of rows into a prompt is slow (large
        prompt -> more tokens to process) and pointless: the row preview is
        truncated to `max_rows` before the model ever sees it, so the model
        cannot describe rows it was never shown anyway. The full dataframe
        is already returned to the caller/frontend via PipelineResult, so a
        grounded count + column summary is all the "answer" text needs to
        say. This never fabricates a total -- `len(dataframe)` is the exact
        executed row count.
        """
        columns = ", ".join(str(c) for c in dataframe.columns)
        return (
            f"Found {len(dataframe)} matching records "
            f"(columns: {columns})."
        )

    # -----------------------------------------------------------------
    # Main synthesis
    # -----------------------------------------------------------------

    def synthesize(
        self,
        question: str,
        sql: str,
        dataframe: pd.DataFrame,
        max_new_tokens: int = 80,
        tables_checked: Optional[list] = None,
        available_tables: Optional[list] = None,
    ) -> str:

        # -------------------------------------------------------------
        # 1. Empty result: no LLM.
        # -------------------------------------------------------------
        if dataframe.empty:
            return self._not_found_message(
                tables_checked,
                available_tables,
            )

        # -------------------------------------------------------------
        # 2. One-cell result: deterministic.
        # -------------------------------------------------------------
        deterministic = self._deterministic_single_value_answer(
            question,
            dataframe,
        )

        if deterministic is not None:
            return deterministic

        # -------------------------------------------------------------
        # 3. Extreme-value questions: deterministic when possible.
        #
        # This avoids hallucinating "highest" from a large result preview.
        # -------------------------------------------------------------
        extreme = self._deterministic_extreme_answer(
            question,
            dataframe,
        )

        if extreme is not None:
            return extreme

        # -------------------------------------------------------------
        # 3.5 Large result: no LLM.
        #
        # Only reached for genuinely large, non-scalar, non-superlative
        # results (e.g. "show me their MonthlyIncome" over hundreds of
        # rows). The dataframe itself is still returned to the caller in
        # full; only the free-text "answer" is deterministic here.
        # -------------------------------------------------------------
        if len(dataframe) > self.max_rows:
            return self._large_result_message(dataframe)

        # -------------------------------------------------------------
        # 4. Normal multi-row result: compact LLM summary.
        # -------------------------------------------------------------
        data_block = self._serialize(dataframe)

        columns_note = ", ".join(
            str(column)
            for column in dataframe.columns
        )

        prompt = f"""You are a careful data analyst.

Answer the user's question using ONLY the query result below.

Rules:
- Answer directly in 1-3 sentences.
- Use only information visible in the result.
- Do not invent numbers, names, units, dates, totals, or explanations.
- Do not extrapolate beyond the displayed rows.
- Keep the original meaning of column names.
- If the result only partly answers the question, say so.
- Do not mention SQL or the query.
- Do not add hashtags, greetings, disclaimers, or a sign-off.
- Stop immediately after answering.

Question:
{question}

Columns:
{columns_note}

Result:
{data_block}

Answer:"""

        raw = self.llm_backend.generate(
            prompt,
            max_new_tokens=max_new_tokens,
            temperature=0.1,
        )

        return self._clean(raw)