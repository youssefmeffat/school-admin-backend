"""Benchmark harness for the text2sql pipeline (non-LLM stages).

No torch/transformers/sentence-transformers are installed in this
environment, so real local-model inference cannot be measured here. This
harness measures everything that CAN be measured honestly without a model:
intent classification, cache lookups, schema load/indexing, retrieval,
semantic-learning orchestration overhead (using a deterministic fake LLM
for the semantic-labeling call itself), SQL validation/execution, and
answer synthesis fast paths -- plus LLM CALL COUNTS, which don't require
a real model to verify.
"""
import os
import sqlite3
import tempfile
import time
import json

from text2sql.config import PipelineConfig
from text2sql.pipeline import Text2SQLPipeline


class ScriptedBackend:
    def __init__(self):
        self.calls = 0

    def warm_up(self):
        pass

    def generate(self, prompt, max_new_tokens=220, temperature=0.1):
        self.calls += 1
        full = prompt.lower()
        if "respond with only one json object" in full:
            return (
                '{"table": {"semantic_name": "Employees", '
                '"synonyms": ["workers", "staff", "personnel"], '
                '"business_meaning": "people employed by the company", '
                '"confidence": 95}, "columns": ['
                '{"column": "monthly_income", "semantic_name": "Salary", '
                '"synonyms": ["pay", "income", "compensation"], '
                '"business_meaning": "employee compensation", '
                '"units": "USD", "data_category": "Financial", '
                '"confidence": 95}]}'
            )
        q = full.rsplit("user question", 1)[-1] if "user question" in full else full
        if "average" in q and "monthlyincome" in q.replace(" ", ""):
            return "SELECT AVG(monthly_income) AS avg_income FROM employees;"
        if "monthlyincome" in q.replace(" ", "") and (
            "show" in q or "their" in q
        ):
            return "SELECT id, monthly_income FROM employees;"
        if "how many" in q and "employee" in q:
            return "SELECT COUNT(*) AS row_count FROM employees;"
        return "SELECT COUNT(*) AS row_count FROM employees;"


def build_hr_db(path, n=442):
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE employees (id INTEGER PRIMARY KEY, full_name TEXT, "
        "monthly_income REAL, job_satisfaction INTEGER)"
    )
    rows = [
        (i, f"Employee {i}", 3000 + (i * 37) % 9000, 1 + (i % 4))
        for i in range(1, n + 1)
    ]
    conn.executemany(
        "INSERT INTO employees VALUES (?, ?, ?, ?)", rows
    )
    conn.commit()
    conn.close()


def main():
    tmpdir = tempfile.mkdtemp()
    db_path = os.path.join(tmpdir, "hr.db")
    build_hr_db(db_path, n=442)

    backend = ScriptedBackend()
    config = PipelineConfig(
        connection_string=f"sqlite:///{db_path}",
        retrieval_strategy="keyword",
        enable_recommendations=False,
        verbose_schema_learning=False,
    )
    pipeline = Text2SQLPipeline(config, llm_backend=backend)

    results = {}

    # 1. Greeting fast path -- must not touch schema/LLM at all.
    t0 = time.perf_counter()
    r = pipeline.ask("hi")
    results["greeting"] = {
        "elapsed_s": time.perf_counter() - t0,
        "llm_calls_total": backend.calls,
        "stage_timings": r.stage_timings,
    }

    # 2. Cold database question (first real question -- pays schema load +
    #    semantic learning + SQL generation).
    calls_before = backend.calls
    t0 = time.perf_counter()
    r = pipeline.ask("How many employees are there?")
    results["cold_count_question"] = {
        "elapsed_s": time.perf_counter() - t0,
        "llm_calls_this_question": backend.calls - calls_before,
        "answer": r.answer,
        "stage_timings": r.stage_timings,
    }

    # 3. Repeat the exact same question -- must be a cache hit, zero LLM
    #    calls, near-zero latency.
    calls_before = backend.calls
    t0 = time.perf_counter()
    r = pipeline.ask("How many employees are there?")
    results["repeat_cache_hit"] = {
        "elapsed_s": time.perf_counter() - t0,
        "llm_calls_this_question": backend.calls - calls_before,
        "from_cache": r.from_cache,
        "stage_timings": r.stage_timings,
    }

    # 4. Large result (442 rows) -- must NOT trigger an answer-synthesis
    #    LLM call (ScriptedBackend has no branch that would make a sane
    #    answer-synthesis prompt succeed anyway -- this checks call count).
    calls_before = backend.calls
    t0 = time.perf_counter()
    r = pipeline.ask("Show me their MonthlyIncome for all employees")
    results["large_result_442_rows"] = {
        "elapsed_s": time.perf_counter() - t0,
        "llm_calls_this_question": backend.calls - calls_before,
        "row_count": len(r.dataframe),
        "answer": r.answer,
        "stage_timings": r.stage_timings,
    }

    # 5. Second distinct table-touching question on an already-indexed,
    #    already-semantically-learned pipeline (steady state).
    calls_before = backend.calls
    t0 = time.perf_counter()
    r = pipeline.ask("What is the average MonthlyIncome?")
    results["steady_state_new_question"] = {
        "elapsed_s": time.perf_counter() - t0,
        "llm_calls_this_question": backend.calls - calls_before,
        "answer": r.answer,
        "stage_timings": r.stage_timings,
    }

    results["cache_stats"] = pipeline.cache.stats if pipeline.cache else None
    results["total_llm_calls"] = backend.calls

    print(json.dumps(results, indent=2, default=str))


if __name__ == "__main__":
    main()
