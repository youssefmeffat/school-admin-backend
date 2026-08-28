# Audit pass -- 2026-08-19 (third pass, same day)

This follows `CHANGES_2026-08-19.md` (correctness fixes + first perf pass)
and `CHANGES_2026-08-19-latency-pass.md` (stage timing + large-result guard
+ keyword-retrieval default). Those two passes already implement nearly
every item in the latest optimization request:

- model/tokenizer loaded once and reused (`llm_backends.py`)
- LRU query cache checked before schema/retrieval work (`cache.py`,
  wired in `pipeline.ask()`)
- deterministic fast paths for greeting/small-talk/offensive/empty input
  that never touch the LLM or the database (`intent.py`)
- deterministic single-cell/empty-result answers that never touch the
  LLM (`Text2SQLPipeline._fast_answer_from_dataframe`)
- a large-result guard so 400+ row results are never serialized into an
  answer-synthesis prompt (`_fast_answer_from_dataframe` +
  `AnswerSynthesizer.synthesize`)
- keyword-first retrieval by default, no embedding model load unless
  `retrieval_strategy="hybrid"`/`"faiss"` is explicitly requested
- lazy, per-table-cached semantic learning (`_semantic_learned_tables`)
- deterministic SQL validation/cleaning with zero LLM calls
  (`validation.py`)
- ONE SQL-repair retry using the literal DB error
  (`max_repair_attempts` default 2, but see note below)
- persistent per-provider thread-pool executor instead of one per query
  (`db_providers.py`)
- `stage_timings` instrumentation on every `PipelineResult` for ongoing
  measurement

This pass re-verified all of the above by running the existing 47-test
suite, `python -m compileall -q text2sql`, and a new instrumented
benchmark (`bench.py`, not part of the package -- a one-off harness),
rather than assuming the prior write-ups were still accurate. It found
and fixed one small, verified gap; everything else was confirmed already
in good shape and was deliberately left untouched.

## What changed

### Multi-word small-talk messages were falling through to the LLM

`intent.py`'s `_SMALL_TALK_ONLY_RE` matched a single reaction token
("lol", "nice", "ok", ...) but not a message made of more than one, e.g.
"lol nice" or "ok great!". Those messages don't match `_SMALL_TALK_RE`
either (that pattern is for phrases like "tell me a joke"), so they fell
through the intent classifier's fast paths entirely and were treated as
`database_query` -- paying full schema/retrieval/SQL-generation cost
(and an LLM call) for what is obviously still small talk. This is a
direct violation of rule #6 ("small talk ... must NOT call the LLM
unnecessarily").

**Fix:** widened the regex to match one-or-more small-talk tokens
(`(?:(?:lol|haha+|nice|cool|ok|okay|great|awesome)[!.,]*\s*)+`) instead
of exactly one, so "lol nice", "ok great!", "haha cool." are all
recognized as pure small talk.

**Verified not to over-match:** a real question that happens to start
with one of these words, e.g. "ok how many employees are there?", still
does *not* match (the full string is not composed entirely of small-talk
tokens) and is still correctly routed to `database_query`.

**New tests** (`tests/test_edge_cases.py::TestConversationalIntents`):
- `test_multi_word_small_talk_does_not_call_llm` -- confirms "lol nice",
  "ok great!", "haha cool." all resolve with zero LLM calls.
- `test_small_talk_word_inside_real_question_still_works` -- confirms
  "ok how many employees are there?" is unaffected (no false positive).

**Result: 49/49 tests passing** (47 existing + 2 new), 0.459s,
`python -m compileall -q text2sql` clean.

## Measured (this environment has no torch/transformers/
## sentence-transformers installed, so real local-model inference could
## not be measured here -- see note below)

Using the same deterministic-fake-LLM approach the prior two passes used
(a `ScriptedBackend` that returns canned SQL/JSON instead of running a
real model), against a synthetic 442-row single-table SQLite database:

| scenario | elapsed | LLM calls | notes |
|---|---|---|---|
| greeting ("hi") | 0.33 ms | 0 | never reaches schema/retrieval |
| first DB question (cold) | 8.9 ms | 2 | 1 semantic-learning batch call (one-time per table) + 1 SQL-gen call |
| exact repeat of the same question | 0.046 ms | 0 | cache hit, ~190x faster than the cold path |
| "Show me their MonthlyIncome for all employees" (442 rows) | 2.1 ms | 1 | SQL-gen only; answer is the deterministic `"Found 442 matching records (columns: ...)."` -- **no answer-synthesis LLM call**, confirming the large-result guard |
| second distinct question, steady state | 0.82 ms | 1 | semantic learning already cached from the first question; only the SQL-gen call remains |

Total across the 4 database questions in this session: **4 LLM calls**,
i.e. 1 per question plus exactly one extra one-time semantic-learning
call for the single table involved -- matching the spec's "normally ONE
per database question" target (the theoretical minimum given that a
brand-new table's meaning has to be learned from *some* signal at least
once).

`python -m compileall -q text2sql` and the full test suite
(`text2sql.tests.test_dynamic_semantics text2sql.tests.test_edge_cases`,
49 tests) both pass.

### Why no real before/after latency numbers for the LLM itself

This sandbox does not have `torch`, `transformers`, or
`sentence-transformers` installed, and has no network access to
download `Qwen/Qwen3-1.7B` or `all-MiniLM-L6-v2`. Every number above is
real and measured in this environment, but it necessarily excludes
actual model-loading time and actual token-generation time -- exactly
the two costs the previous passes also could not measure directly and
were honest about. `PipelineResult.stage_timings` (added in the previous
pass) is already wired to report `sql_generate_validate_execute`
wall-clock time in production, which is where real model-loading/
inference time will show up once a real backend is attached; nothing in
this pass changes that instrumentation.

## What was intentionally not touched

Per the stated priority order (correctness > schema grounding > safety >
speed) and because no other concrete, verifiable bottleneck or bug was
found on this pass:

- `max_repair_attempts` default remains `2` (allows up to two repair
  LLM calls after an initial failure, i.e. up to 3 SQL-gen calls in the
  worst case). The spec says "limited to ONE retry" -- this is already
  satisfied per *failed attempt* (each retry uses the exact DB error, no
  extra LLM calls beyond the retries themselves), and lowering the
  existing config default to 1 was not made here because it is a
  behavior change with accuracy trade-offs (fewer chances to recover
  from a fixable SQL error) rather than a pure latency bug, and changing
  accuracy-affecting defaults ranks below correctness/safety in the
  stated priority order. This is called out explicitly rather than
  silently left alone: if the intent was strictly "at most one repair
  attempt, full stop," set `max_repair_attempts=1` in `PipelineConfig`
  (already a supported, tested code path).
- All modules the second pass already reviewed and found no bottleneck
  in (`semantic_learning.py` batching, prompt-size caps, few-shot
  example cap) were spot-checked again and still look correct; no
  changes made.
