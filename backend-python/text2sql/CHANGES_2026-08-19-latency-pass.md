# Latency pass -- 2026-08-19 (second pass, same day)

This follows directly on `CHANGES_2026-08-19.md`, which already covered
correctness fixes and a first round of performance work. That pass left a
note: *"Modules not touched ... were read and spot-checked but no concrete
bug or measurable bottleneck was found in them during this pass."* This
pass re-reads the pipeline end-to-end specifically looking for latency,
against the priority order **correctness > schema grounding > safety >
speed**, and makes two verified changes plus adds the timing
instrumentation needed to keep measuring instead of guessing going
forward.

No files were redesigned. No database-agnostic behavior, employee/
customer/restaurant-specific logic, or accuracy-affecting behavior was
touched. `tests/test_dynamic_semantics.py` and `tests/test_edge_cases.py`
(47 tests) pass unmodified, before and after this pass, in ~0.45s.

## 1. Measurement: per-stage wall-clock timing (`PipelineResult.stage_timings`)

Before this pass the pipeline tracked *token* usage per stage
(`TokenTracker`) but not *wall-clock time* per stage -- there was no way to
answer "is this slow because of retrieval, semantic learning, or
generation?" without guessing or attaching a profiler.

Added a lightweight `_checkpoint(name)` closure inside `ask()` (a single
`time.perf_counter()` call per checkpoint -- effectively free) that
records elapsed time since the previous checkpoint into a `timings` dict,
attached to the returned `PipelineResult.stage_timings`:

- `intent_classification`
- `cache_lookup`
- `schema_load` (near-zero after the first question against a given
  pipeline instance -- the existing `_indexed` guard already prevents
  re-discovery; this timing makes that visible instead of assumed)
- `retrieval`
- `semantic_learning` (near-zero after the first question against a given
  table -- see `_semantic_learned_tables`; only genuinely new tables pay
  an LLM call here)
- `semantic_retrieval_and_prompt_build`
- `sql_generate_validate_execute` (covers the full generate -> validate ->
  execute -> repair loop, i.e. the local-LLM-inference-dominated span)
- `answer_synthesis`

Fast paths (greeting/small-talk/offensive/unavailable-entity/ambiguous/
cache-hit) return before most of these checkpoints are reached, which is
itself the expected/desired signal -- those paths are already near-zero
and don't need a stage breakdown. A cache-hit still reports
`intent_classification` + `cache_lookup` so the "how much did the cache
save" number is visible without extra work.

This is pure instrumentation: no control flow, ordering, or behavior
changed. Verified with a manual run against a 442-row synthetic table (see
item 2 below) -- output:

```
stage_timings: {'intent_classification': 9.3e-05, 'cache_lookup': 8.3e-06,
 'schema_load': 0.0039, 'retrieval': 0.0001, 'semantic_learning': 0.0029,
 'semantic_retrieval_and_prompt_build': 6.0e-05,
 'sql_generate_validate_execute': 0.0115, 'answer_synthesis': 2.3e-05}
```

With the `ScriptedBackend`/`EchoBackend` test doubles this confirms the
non-LLM stages (intent, cache, retrieval, prompt build) are all
sub-millisecond; with a real local model, `sql_generate_validate_execute`
(and, for first-time tables, `semantic_learning`) will dominate, which is
exactly what the instrumentation is meant to make visible rather than
assumed.

## 2. Large results are no longer sent to the answer-synthesis LLM

**This was the literal example in the spec** ("Show me their
MonthlyIncome" over 442 rows) and it was a real gap: the answer path
checked for an empty result and a single-cell result (both handled
without an LLM), but a wide/tall non-superlative result fell through
straight to `AnswerSynthesizer.synthesize()`, which serializes up to
`max_result_rows_for_answer` (20) rows into a prompt and makes a local-LLM
call just to produce a sentence like "According to the data, the count is
442" -- work with no payoff, since the full dataframe is already returned
to the caller/frontend regardless of what the answer text says.

**Fix:** added a large-result guard in two places:

- `Text2SQLPipeline._fast_answer_from_dataframe()` -- checked *before*
  `answer_synthesizer.synthesize()` is even called, so for this case the
  LLM is skipped entirely, not just given a truncated view. Threshold is
  `len(dataframe) > config.max_result_rows_for_answer` (existing config
  field, no new default introduced) producing:
  `"Found {N} matching records (columns: {...})."`
- `AnswerSynthesizer.synthesize()` -- the same guard, as defense-in-depth
  for anyone using `AnswerSynthesizer` directly without going through the
  pipeline's fast-answer check.

Ordering matters and was preserved: the existing empty-result check, the
existing single-cell deterministic check, and the existing highest/lowest
deterministic check (`_deterministic_extreme_answer`, which needs the
*full* un-truncated dataframe to find the true max/min row) all still run
**before** the large-result guard. A "who has the highest X" question that
happens to return many rows (e.g. from a GROUP BY) is still answered
correctly from the full data, not short-circuited into a row count.

**Verified** with a 442-row synthetic SQLite table and a scripted backend
that always selects the full column (worst case: no LIMIT in the
generated SQL):

```
rows: 442
answer: Found 442 matching records (columns: id, monthly_income).
llm calls (sql-gen backend): 2   # 1 semantic-learning call + 1 SQL-gen call
answer_synthesis stage time: 2.3e-05s   # no LLM call happened
```

For comparison, the same scenario before this fix would have made a third
LLM call (the answer-synthesis one) with a ~6000-char serialized-table
prompt.

Also re-ran the existing `TestLargeResults` test
(`test_multi_row_multi_column_result_is_summarized_safely`, a 6-row
result) to confirm the new guard does not fire below the threshold and
existing small-result behavior is untouched -- still passes.

## 3. Default `retrieval_strategy` changed from `"hybrid"` to `"keyword"`

Per the spec: *"Do not initialize embedding models unnecessarily... For
small and medium schemas, benchmark keyword retrieval against hybrid
retrieval. If keyword retrieval is faster and accuracy remains acceptable,
prefer it."*

The previous default, `"hybrid"`, causes `build_retriever()` to construct
a `FaissRetriever`, which loads a `sentence-transformers` embedding model
(`all-MiniLM-L6-v2`) during `learn_schema()` and runs it on every
`retrieve()` call. `"keyword"` has zero model-loading cost and no
per-query embedding inference.

**Benchmarked** against this project's own test schemas (the two-table
company DB in `tests/test_edge_cases.py` and the schemas in
`tests/test_dynamic_semantics.py`): keyword retrieval selects the same
table set as hybrid for every case in both suites -- both test files
already pinned `retrieval_strategy="keyword"` explicitly, which was itself
a signal that the previous default didn't match what the maintainers
actually exercised. With `"keyword"` as the new default, both suites now
also pass using the pipeline's *default* config with no `PipelineConfig`
override, which was not true before this change.

This is a default-value change only, not a removal: `"hybrid"` and
`"faiss"` remain fully implemented and documented (see the updated
comment in `config.py`) as the right choice for large schemas with many
tables that have overlapping/ambiguous natural-language names, where
semantic similarity meaningfully beats exact keyword overlap. Set
`retrieval_strategy="hybrid"` explicitly for that case.

## What was intentionally not touched

Consistent with the previous pass's stated priority order, and because no
concrete bottleneck was found:

- `semantic_learning.py`'s batching (`semantic_llm_max_columns_per_call =
  12`) -- already batches columns per table instead of one LLM call per
  column, and is already lazy/cached per table.
- `db_providers.py`'s persistent-executor change from the previous pass --
  still in place, not revisited.
- Local model choice (`Qwen/Qwen3-1.7B`) -- already matches the spec's
  suggested starting point; swapping to Qwen3-4B/8B is a real-hardware
  benchmarking decision this pass has no way to make offline (no GPU/
  model weights available in this environment) and was left as
  configuration (`config.llm_model`) rather than guessed at.
- Prompt size, few-shot example cap (3), and conversation-context cap
  (1800 chars) -- already bounded from the previous pass; re-checked, no
  regression found, left as-is.

## How to see the new timings

```python
result = pipeline.ask("your question")
print(result.stage_timings)
# {'intent_classification': ..., 'cache_lookup': ..., 'schema_load': ...,
#  'retrieval': ..., 'semantic_learning': ...,
#  'semantic_retrieval_and_prompt_build': ...,
#  'sql_generate_validate_execute': ..., 'answer_synthesis': ...}
```

## Test suite

```bash
cd text2sql_project
PYTHONPATH=. python3 -m unittest text2sql.tests.test_dynamic_semantics text2sql.tests.test_edge_cases -v
```

**Result: 47/47 passing, ~0.45s, unchanged from before this pass.**
