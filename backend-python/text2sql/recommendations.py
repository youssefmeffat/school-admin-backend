"""Generic, schema-driven next-question recommendations.

Recommendations are generated without a fixed dataset vocabulary. They use the
current question, learned semantic profiles, and actual schema roles/columns.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional


class RecommendationEngine:
    def __init__(self, max_recommendations: int = 3):
        self.max_recommendations = max(1, int(max_recommendations))

    @staticmethod
    def _label(column_name: str, semantic_schema: Optional[Dict], table: str) -> str:
        profile = semantic_schema.get(table) if semantic_schema else None
        if profile:
            col = profile.columns.get(column_name)
            if col and col.semantic_name:
                return col.semantic_name
        return re.sub(
            r"(?<=[a-z])(?=[A-Z])|[_-]+",
            " ",
            str(column_name),
        ).strip().lower()

    def suggest(
        self,
        question: str,
        metadata,
        semantic_schema: Optional[Dict] = None,
        result_columns=None,
    ) -> List[str]:
        semantic_schema = semantic_schema or {}
        result_columns = [str(c) for c in (result_columns or [])]
        q = str(question or "").lower()

        numeric = []
        categorical = []
        identifiers = []

        for table in metadata.tables.values():
            profile = semantic_schema.get(table.name)
            for column in table.columns:
                structural = None
                if profile and column.name in profile.columns:
                    structural = profile.columns[column.name].structural_type

                label = self._label(column.name, semantic_schema, table.name)

                if structural == "measure" or re.search(
                    r"(amount|price|cost|rate|income|revenue|salary|score|total|value|quantity)",
                    label,
                    re.I,
                ):
                    numeric.append((table.name, column.name, label))

                if (
                    structural == "categorical"
                    and not re.search(
                        r"(amount|price|cost|rate|income|revenue|salary|score|total|value|quantity|compensation|pay)",
                        label,
                        re.I,
                    )
                ):
                    categorical.append((table.name, column.name, label))

                if structural == "identifier" or column.primary_key:
                    identifiers.append((table.name, column.name, label))

        suggestions = []

        # Use the result's columns first when possible.
        result_numeric = []
        for c in result_columns:
            for table in metadata.tables.values():
                if c in {col.name for col in table.columns}:
                    label = self._label(c, semantic_schema, table.name)
                    if any(
                        c == item[1] and label == item[2]
                        for item in numeric
                    ):
                        result_numeric.append(
                            (table.name, c, label)
                        )
                    break

        chosen_numeric = result_numeric + numeric

        if any(word in q for word in ("highest", "top", "largest", "most")):
            for _, _, label in chosen_numeric[:1]:
                suggestions.append(f"What is the lowest {label}?")

        if any(word in q for word in ("lowest", "bottom", "smallest", "least")):
            for _, _, label in chosen_numeric[:1]:
                suggestions.append(f"Who has the highest {label}?")

        if chosen_numeric and categorical:
            measure_label = chosen_numeric[0][2]
            category_label = categorical[0][2]
            suggestions.append(
                f"What is the average {measure_label} by {category_label}?"
            )

        if categorical and chosen_numeric:
            category_label = categorical[0][2]
            suggestions.append(
                f"How many records are there for each {category_label}?"
            )

        if identifiers:
            suggestions.append(
                f"How many records are in the main {metadata.tables[identifiers[0][0]].name} data?"
            )

        # De-duplicate while preserving relevance order.
        out = []
        seen = set()
        for suggestion in suggestions:
            key = suggestion.lower()
            if key == str(question or "").strip().lower() or key in seen:
                continue
            seen.add(key)
            out.append(suggestion)
            if len(out) >= self.max_recommendations:
                break

        return out
