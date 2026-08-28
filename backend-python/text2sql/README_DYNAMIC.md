# Dynamic Fast Text-to-SQL

This version is designed around a database-agnostic semantic-learning architecture.

## Core behavior

1. Discover an unknown database schema dynamically.
2. Profile real sample data without hardcoded domain mappings.
3. Use lazy semantic learning: only the relevant table(s) are sent to the local model.
4. Learn table concepts and column meanings from names, types, relationships, neighboring columns, and real values.
5. Allow user teaching such as:
   - `when I say workers, I mean employees`
   - `column xx is salary`
6. Scope learned mappings to the current schema fingerprint.
7. Cache learned semantics and completed queries.
8. Generate dynamic next-question recommendations from the current schema/result.
9. Keep LoRA fine-tuning optional/offline; the generated training set includes learned semantic language.
10. Keep SQL validation/safety active.

## Model

Default local model: `Qwen/Qwen3-1.7B`.

Qwen3 supports a non-thinking mode, which is used here for low-latency SQL generation. The backend keeps the model loaded once and reuses it.

### CPU-only deployments

A single question can trigger several sequential local LLM generations (intent
classification, on-demand semantic learning for the touched table(s), SQL
generation, up to `max_repair_attempts` repair calls, and answer synthesis).
On CPU-only hardware this adds up and can be slow with the default 1.7B model.

For CPU-only deployments, set:

```bash
export T2S_LLM_MODEL=Qwen/Qwen2.5-0.5B-Instruct
```

This overrides `PipelineConfig.llm_model` and generates materially faster on
CPU, at some accuracy cost versus the 1.5B/1.7B default. If you're running
behind the .NET API layer, also make sure `PythonService:TimeoutSeconds` in
`appsettings.json` is generous enough for CPU inference (300s is a reasonable
starting point).

## Performance strategy

- model warm-up at startup
- Qwen3 non-thinking mode
- deterministic SQL generation by default
- 128-token SQL output budget
- one sample query per table instead of one query per column
- one numeric aggregate query per table during semantic profiling
- lazy semantic inference
- at most two candidate tables per semantic-learning request
- at most twelve columns in one semantic-learning prompt
- query result cache
- no cross-encoder reranker by default
- compact schema prompts
- CPU thread limiting

The 30-second target is an engineering target, not a guarantee: final latency must be benchmarked on the user's actual CPU and database size.

## Explicit training

`Text2SQLPipeline.finetune_on_data()` now adds semantic training examples such as:

- "How many workers are there?" -> real current table
- "What is the average salary?" -> real current salary column
- "Who has the highest salary?" -> real current salary column

Fine-tuning should be treated as an explicit/offline operation. Retraining the 1.7B model on every upload would conflict with the 30-second response requirement.

## Tests

Run:

```bash
python -m unittest discover -s tests -v
```
