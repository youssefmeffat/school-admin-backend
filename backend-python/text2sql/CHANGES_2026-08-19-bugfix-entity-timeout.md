# Bugfix pass — 2026-08-19

## Bug 1 — singular entity words wrongly rejected as "not in this database"

`Text2SQLPipeline._requested_entity_is_unavailable()` (pipeline.py) uses a
nested `concept_matches()` helper to compare words from the user's question
against `schema_words` derived from table/column names (plus learned
synonyms). It only singularized the *question's* word (stripping a trailing
`s`/`ies`) before checking `schema_words`. That covers "question word is
plural, schema word is singular" but never the reverse — "question word is
singular, schema word is plural" — e.g. question `"user"` vs. table
`"users"`.

Fix: `concept_matches()` now also tries pluralizing the question word
(`word + "s"`, `word + "es"`, and `word[:-1] + "ies"` for words ending in
`y`) before checking `schema_words`, making the match symmetric.

Added `tests/test_entity_word_matching.py`, which builds a mock schema with
`users` and `roles` tables and exercises `_requested_entity_is_unavailable()`
directly for both directions ("how many user/users are there",
"how many role/roles are there"), plus a regression check that a genuinely
unrelated entity ("restaurants") is still rejected.

## Bug 2 — CPU inference timeout on legitimate questions

A single question can trigger several sequential local LLM generations
(intent classification, on-demand semantic learning, SQL generation, up to
`max_repair_attempts` repair calls, answer synthesis) on a 1.5B–1.7B model.
On CPU-only hardware this routinely exceeded the 120s
`PythonService:TimeoutSeconds` in the .NET layer.

Applied:

1. `backend-dotnet/Text2SqlApi/appsettings.json`: raise
   `PythonService.TimeoutSeconds` from `120` to `300`.
   **Not included in this package** — the uploaded zip only contains the
   `text2sql/` Python package, no `backend-dotnet/` tree. Apply this change
   directly in the .NET repo/checkout.
2. `text2sql/config.py`: lowered `PipelineConfig.max_repair_attempts`
   default from `2` to `1`.
3. `text2sql/config.py`: `PipelineConfig.llm_model` is now overridable via
   the `T2S_LLM_MODEL` environment variable (previously no env var support
   existed anywhere in the package). `README_DYNAMIC.md` documents setting
   `T2S_LLM_MODEL=Qwen/Qwen2.5-0.5B-Instruct` for CPU-only deployments.

## Tests

`python -m unittest discover -s tests -v` — 54 tests, all passing (49
pre-existing + 5 new).
