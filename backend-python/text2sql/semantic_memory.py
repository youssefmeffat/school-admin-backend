"""Schema-scoped semantic memory and user teaching.

This module stores generic semantic feedback such as:
    "when I say workers, I mean employees"
    "column xx is salary"

Nothing is hardcoded to a particular dataset. Memory is keyed to a schema
fingerprint, so a mapping learned for one database is never silently applied to
another database.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from .schema_metadata import DatabaseMetadata


@dataclass
class SemanticMemoryEntry:
    phrase: str
    meaning: str
    table: Optional[str] = None
    column: Optional[str] = None
    confidence: float = 1.0
    source: str = "user"


class SchemaScopedMemory:
    def __init__(
        self,
        metadata: DatabaseMetadata,
        max_entries: int = 64,
        path: Optional[str] = None,
    ):
        self.schema_key = self.fingerprint(metadata)
        self.max_entries = max(1, int(max_entries))
        self.path = Path(path).expanduser() if path else None
        self.entries: List[SemanticMemoryEntry] = []
        self._load()

    @staticmethod
    def fingerprint(metadata: DatabaseMetadata) -> str:
        payload = {
            "dialect": metadata.dialect,
            "tables": [],
        }

        for table in metadata.tables.values():
            payload["tables"].append({
                "name": table.name,
                "columns": [
                    {
                        "name": c.name,
                        "type": c.data_type,
                        "nullable": c.nullable,
                        "pk": c.primary_key,
                    }
                    for c in table.columns
                ],
                "foreign_keys": [
                    {
                        "column": fk.column,
                        "table": fk.referenced_table,
                        "ref_column": fk.referenced_column,
                    }
                    for fk in table.foreign_keys
                ],
            })

        raw = json.dumps(
            payload,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()[:24]

    def _load(self) -> None:
        if self.path is None or not self.path.exists():
            return

        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if payload.get("schema_key") != self.schema_key:
                return

            self.entries = [
                SemanticMemoryEntry(**item)
                for item in payload.get("entries", [])
                if isinstance(item, dict) and item.get("phrase")
            ][: self.max_entries]
        except Exception:
            self.entries = []

    def _save(self) -> None:
        if self.path is None:
            return

        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "schema_key": self.schema_key,
                "entries": [
                    {
                        "phrase": e.phrase,
                        "meaning": e.meaning,
                        "table": e.table,
                        "column": e.column,
                        "confidence": e.confidence,
                        "source": e.source,
                    }
                    for e in self.entries[-self.max_entries:]
                ],
            }
            self.path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass

    def teach(
        self,
        phrase: str,
        meaning: str,
        table: Optional[str] = None,
        column: Optional[str] = None,
        source: str = "user",
    ) -> None:
        phrase = " ".join(str(phrase or "").strip().lower().split())
        meaning = " ".join(str(meaning or "").strip().split())

        if not phrase or not meaning:
            return

        # Replace the same phrase/target instead of growing duplicate memory.
        self.entries = [
            e for e in self.entries
            if not (
                e.phrase == phrase
                and e.table == table
                and e.column == column
            )
        ]

        self.entries.append(
            SemanticMemoryEntry(
                phrase=phrase,
                meaning=meaning,
                table=table,
                column=column,
                confidence=1.0,
                source=source,
            )
        )
        self.entries = self.entries[-self.max_entries:]
        self._save()

    def matching(self, question: str) -> List[SemanticMemoryEntry]:
        q = " ".join(str(question or "").lower().split())
        matches = []

        for entry in self.entries:
            if entry.phrase in q:
                matches.append(entry)
                continue

            # Token overlap fallback for short user aliases.
            q_tokens = set(re.findall(r"[a-z0-9]+", q))
            p_tokens = set(re.findall(r"[a-z0-9]+", entry.phrase))
            if p_tokens and p_tokens.issubset(q_tokens):
                matches.append(entry)

        return matches

    def build_hint(self, question: str) -> str:
        matches = self.matching(question)
        if not matches:
            return ""

        lines = [
            "User-taught semantic mappings for this database:"
        ]

        for entry in matches[:8]:
            target = entry.meaning
            if entry.table:
                target += f" -> table {entry.table}"
            if entry.column:
                target += f" -> column {entry.column}"
            lines.append(f'- "{entry.phrase}" means {target}')

        return "\n".join(lines)

    def __len__(self) -> int:
        return len(self.entries)
