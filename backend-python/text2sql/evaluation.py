"""Evaluation harness for the Text-to-SQL pipeline.

Measures:

- retrieval precision / recall
- success rate
- latency
- median / p95 latency
- SQL repair attempts
- cache hit rate
- grounding rate
- hallucination proxy
- before/after optimization comparisons

The evaluator does not modify the pipeline. It only measures its behavior.
"""

import time
from dataclasses import dataclass
from typing import List, Optional

import pandas as pd


@dataclass
class EvalCase:
    question: str

    # Ground-truth table set for retrieval precision/recall.
    # Optional: cases without it skip these metrics.
    expected_tables: Optional[List[str]] = None

    # Substrings that must appear in the final answer.
    # Used as a lightweight grounding/hallucination proxy.
    expected_answer_contains: Optional[List[str]] = None

    # True when the question is expected to return no data.
    expect_not_found: bool = False


class Evaluator:
    def __init__(self, pipeline):
        self.pipeline = pipeline

    def run(self, cases: List[EvalCase]) -> pd.DataFrame:
        """Run all evaluation cases.

        Never raises because of one failed case. A failed case is recorded
        and evaluation continues with the remaining cases.
        """

        rows = []

        for case in cases:
            start = time.perf_counter()

            try:
                result = self.pipeline.ask(case.question)

                measured_elapsed = time.perf_counter() - start

                elapsed = (
                    getattr(result, "elapsed_seconds", None)
                    or measured_elapsed
                )

                # -----------------------------------------------------
                # Retrieval precision / recall
                # -----------------------------------------------------

                precision = None
                recall = None

                if case.expected_tables:
                    used = set(
                        getattr(result, "tables_used", [])
                    )

                    expected = set(
                        case.expected_tables
                    )

                    intersection = used & expected

                    precision = (
                        len(intersection) / len(used)
                        if used
                        else 0.0
                    )

                    recall = (
                        len(intersection) / len(expected)
                        if expected
                        else None
                    )

                # -----------------------------------------------------
                # Grounding check
                # -----------------------------------------------------

                grounded = None

                answer = getattr(
                    result,
                    "answer",
                    "",
                ) or ""

                if case.expect_not_found:
                    grounded = (
                        "couldn't find" in answer.lower()
                        or "could not find" in answer.lower()
                        or not result.success
                    )

                elif case.expected_answer_contains:
                    grounded = all(
                        keyword.lower() in answer.lower()
                        for keyword in case.expected_answer_contains
                    )

                # -----------------------------------------------------
                # Cache information
                # -----------------------------------------------------

                from_cache = bool(
                    getattr(
                        result,
                        "from_cache",
                        False,
                    )
                )

                # -----------------------------------------------------
                # Store result
                # -----------------------------------------------------

                rows.append(
                    {
                        "question": case.question,
                        "tables_used": getattr(
                            result,
                            "tables_used",
                            [],
                        ),
                        "expected_tables": (
                            case.expected_tables or []
                        ),
                        "retrieval_precision": precision,
                        "retrieval_recall": recall,
                        "latency_seconds": round(
                            elapsed,
                            3,
                        ),
                        "success": bool(
                            getattr(
                                result,
                                "success",
                                False,
                            )
                        ),
                        "grounded": grounded,
                        "repair_attempts": getattr(
                            result,
                            "repair_attempts",
                            0,
                        ),
                        "from_cache": from_cache,
                        "answer": answer,
                        "sql": getattr(
                            result,
                            "sql",
                            "",
                        ),
                        "error": None,
                    }
                )

            except Exception as exc:
                rows.append(
                    {
                        "question": case.question,
                        "tables_used": [],
                        "expected_tables": (
                            case.expected_tables or []
                        ),
                        "retrieval_precision": None,
                        "retrieval_recall": None,
                        "latency_seconds": round(
                            time.perf_counter() - start,
                            3,
                        ),
                        "success": False,
                        "grounded": False,
                        "repair_attempts": 0,
                        "from_cache": False,
                        "answer": "",
                        "sql": "",
                        "error": str(exc),
                    }
                )

        return pd.DataFrame(rows)

    @staticmethod
    def summarize(df: pd.DataFrame) -> dict:
        """Convert per-question results into headline metrics."""

        if df.empty:
            return {
                "num_cases": 0,
            }

        latency = df["latency_seconds"]

        summary = {
            # Overall volume
            "num_cases": len(df),

            # Success/failure
            "success_rate": float(
                df["success"].mean()
            ),
            "successful_cases": int(
                df["success"].sum()
            ),
            "failed_cases": int(
                (~df["success"]).sum()
            ),

            # Speed
            "avg_latency_seconds": float(
                latency.mean()
            ),
            "median_latency_seconds": float(
                latency.median()
            ),
            "p95_latency_seconds": float(
                latency.quantile(0.95)
            ),

            # SQL repair
            "avg_repair_attempts": float(
                df["repair_attempts"].mean()
            ),

            # Cache
            "cache_hit_rate": float(
                df["from_cache"].mean()
            ),
            "cache_hits": int(
                df["from_cache"].sum()
            ),
        }

        # -------------------------------------------------------------
        # Retrieval metrics
        # -------------------------------------------------------------

        precision = df[
            "retrieval_precision"
        ].dropna()

        recall = df[
            "retrieval_recall"
        ].dropna()

        if len(precision):
            summary[
                "avg_retrieval_precision"
            ] = float(
                precision.mean()
            )

        if len(recall):
            summary[
                "avg_retrieval_recall"
            ] = float(
                recall.mean()
            )

        # -------------------------------------------------------------
        # Grounding metrics
        # -------------------------------------------------------------

        grounded = df[
            "grounded"
        ].dropna()

        if len(grounded):
            grounded_rate = float(
                grounded.mean()
            )

            summary[
                "grounded_rate"
            ] = grounded_rate

            summary[
                "hallucination_rate"
            ] = 1.0 - grounded_rate

        return summary

    def summarize_run(
        self,
        cases: List[EvalCase],
    ) -> dict:
        """Run evaluation and immediately summarize it."""

        return self.summarize(
            self.run(cases)
        )

    @staticmethod
    def compare(
        before: dict,
        after: dict,
    ) -> pd.DataFrame:
        """Compare two evaluation summaries.

        Positive delta is not automatically better:
        - higher success/grounding/precision/recall is better
        - lower latency/repair/hallucination is better

        The caller should interpret the metric direction accordingly.
        """

        keys = sorted(
            set(before) | set(after)
        )

        rows = []

        for key in keys:
            before_value = before.get(key)
            after_value = after.get(key)

            if (
                isinstance(
                    before_value,
                    (int, float),
                )
                and isinstance(
                    after_value,
                    (int, float),
                )
            ):
                delta = (
                    after_value
                    - before_value
                )
            else:
                delta = None

            rows.append(
                {
                    "metric": key,
                    "before": before_value,
                    "after": after_value,
                    "delta": delta,
                }
            )

        return pd.DataFrame(rows)

    @staticmethod
    def slowest_cases(
        df: pd.DataFrame,
        n: int = 5,
    ) -> pd.DataFrame:
        """Return the slowest questions from an evaluation run."""

        if df.empty:
            return df.copy()

        n = max(1, int(n))

        return (
            df.sort_values(
                "latency_seconds",
                ascending=False,
            )
            .head(n)
            .reset_index(drop=True)
        )