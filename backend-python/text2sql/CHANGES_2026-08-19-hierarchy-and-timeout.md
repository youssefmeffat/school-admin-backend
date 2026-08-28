# Hierarchy support + timeout follow-up — 2026-08-19

## Context

Reported: "how many roles" timed out ("That question is taking too long to
answer"), and the project needs to cope with manager/organizational
hierarchy queries (self-referencing `users.ManagerReferenceId ->
users.UserReferenceId`, resolved with a recursive CTE).

## 1. The timeout

The previous bugfix pass (`CHANGES_2026-08-19-bugfix-entity-timeout.md`)
already addresses the mechanism behind this:

- `PipelineConfig.max_repair_attempts` lowered from `2` to `1` (fewer
  sequential CPU LLM calls per question) — **present in this package**
  (`config.py`, currently `1`).
- `T2S_LLM_MODEL` env var to swap in a smaller/faster model on CPU-only
  hardware — **present in this package** (`config.py`).
- Raising `PythonService:TimeoutSeconds` from 120 to 300 in
  `backend-dotnet/Text2SqlApi/appsettings.json` — **NOT present in this
  package** (the zip only ships the `text2sql/` Python package, not the
  `backend-dotnet/` tree). This still needs to be applied directly in the
  .NET project. A trivial COUNT(*) query on `roles` shouldn't need 120s+ of
  wall time on its own — if it's still timing out after that change, the
  bottleneck is very likely raw model load/inference speed. Check:
  1. Is `T2S_LLM_MODEL` set to a small model (e.g.
     `Qwen/Qwen2.5-0.5B-Instruct`) on CPU-only deployments? A 1.7B model
     doing 2-4 sequential generations per question (intent, semantic
     learning, SQL gen, answer synthesis) on CPU is the likely root cause.
  2. Is the model being reloaded per-request instead of once at process
     startup? Reloading a 1.5-7B model per request costs far more than
     inference itself.
  3. Confirm `backend-dotnet` actually picked up the raised timeout — a
     stale build/deploy would still show the old 120s ceiling.

## 2. Hierarchy / recursive-CTE support (new in this pass)

The pipeline had no concept of hierarchical/self-referencing relationships
at all, so a question like "show me everyone under this manager, every
level" had no grounding to reliably produce a recursive CTE on a small
model, even though the SQL validator already accepted `WITH RECURSIVE`
correctly.

- `retrieval.py` (`SchemaDocumentBuilder`): a foreign key where
  `referenced_table == table.name` is now annotated in the schema text fed
  to the LLM: `... (SELF-REFERENCING -- this table stores a hierarchy/tree;
  ... Use a recursive CTE for multi-level questions.)`.
- `prompting.py`: `PromptBuilder.build()` and `.build_repair()` now inject
  a compact, dialect-correct "HIERARCHY / RECURSIVE RULE" block — **only**
  when the retrieved schema text actually contains a `SELF-REFERENCING`
  flag, so prompt size/latency is unaffected for schemas without a
  hierarchy. Dialect syntax notes:
  - sqlite / postgresql / mysql (8.0+) / snowflake: `WITH RECURSIVE`
  - mssql: `WITH cte AS (...)` — no `RECURSIVE` keyword; T-SQL treats a
    self-referencing CTE as recursive automatically
  - oracle: `WITH cte(cols) AS (...)` (no `RECURSIVE` keyword) or
    `CONNECT BY PRIOR parent = child`

No change was needed in `validation.py` — `WITH RECURSIVE ...` already
validates correctly (single statement, CTE name recognized in
known-table/alias checks); confirmed with a direct unit test.

## 3. Simple-count fast path (new — actually fixes the reported timeout)

Traced the real cause of "how many roles" / "how many users" timing out:
these questions still went through the *entire* pipeline — retrieval,
targeted semantic learning (1 LLM call), SQL generation (1 LLM call), up to
`max_repair_attempts` repair calls, and LLM answer synthesis (1 more call).
That's several sequential CPU generations for a question that has exactly
one possible correct query.

Added `Text2SQLPipeline._try_simple_count_fast_path()`, wired into `ask()`
right after schema load and before retrieval/semantic learning/SQL
generation. It:

- only fires on a bare `"how many X"` / `"how many X are there"` /
  `"...exist"` / `"...do we have"` question with **no extra filter
  language** (a question like `"how many users are active"` does not
  structurally match any table name once "active" is folded in, so it
  correctly falls through to the real pipeline instead of silently
  answering the unfiltered count);
- resolves the entity word to **exactly one** table via simple
  singular/plural normalization (reusing the same kind of matching as
  `_requested_entity_is_unavailable`) — zero or multiple matches fall
  through to the full pipeline unchanged;
- executes `SELECT COUNT(*) FROM <table>` directly against the DB and
  returns the answer — **no LLM call at all**.

This is unconditionally correct (there is no other valid SQL for "how many
X are there") and eliminates the entire latency chain — and therefore the
timeout — for this class of question, regardless of model speed. It also
composes with the existing cache (`ask()` still checks/populates the cache
around it).

Several pre-existing tests in `tests/test_edge_cases.py` used
`"how many employees are there"` purely as a stand-in "generic question" to
exercise repair/rejection/malicious-backend-defense mechanics. Those were
updated to use a filtered phrasing (`"...with a salary above 0"`) so they
still reach real SQL generation; this is a one-line question-text change
per test, not a behavior change to what's being tested.

## Tests

- Added `tests/test_hierarchy_recursive_cte.py` (5 tests): self-reference
  flagging (positive/negative), dialect-specific prompt guidance appears
  only when relevant, and a `WITH RECURSIVE` manager-hierarchy query
  validates against a mock `users` schema.
- Added `TestSimpleCountFastPath` in `tests/test_edge_cases.py` (5 tests):
  proves the LLM backend is never called for bare count questions (via a
  backend that raises if invoked), several phrasing variants, that a
  filtered count still reaches the LLM, that filter words are never
  silently dropped into an unfiltered count, and that an ambiguous entity
  ("how many rows") doesn't guess a table.

`PYTHONPATH=. python -m unittest discover -s text2sql/tests -v` — 64 tests,
all passing (54 pre-existing + 10 new).
