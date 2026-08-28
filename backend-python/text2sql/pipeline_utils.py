"""Restart-proof pipeline builder.

This module is written to disk so it survives a Colab/Jupyter restart.

The builder recreates the Text2SQL pipeline from the database stored on disk
and keeps the important performance settings consistent with the production
configuration.

Important performance decision:

    enable_semantic_learning=False

Semantic schema learning makes an additional LLM call per table. It is
therefore disabled by default for the CPU-based runtime used by this project.
"""

import os


def get_or_build_pipeline(
    existing_pipeline=None,
    db_path="demo/uploaded_dataset.db",
    llm_model="Qwen/Qwen3-1.7B",
    llm_load_in_4bit=False,
    enable_semantic_learning=False,
    llm_max_new_tokens=128,
    use_reasoning=False,
):
    """Reuse an existing pipeline or rebuild it from the database on disk.

    Returns:

        (pipeline, config, metadata)

    Performance defaults are intentionally conservative for CPU inference.
    """

    # ---------------------------------------------------------------
    # Reuse existing pipeline
    # ---------------------------------------------------------------

    if existing_pipeline is not None:
        return existing_pipeline, None, None

    # ---------------------------------------------------------------
    # Imports
    # ---------------------------------------------------------------

    from text2sql.pipeline import Text2SQLPipeline
    from text2sql.config import PipelineConfig

    # ---------------------------------------------------------------
    # Validate database
    # ---------------------------------------------------------------

    if not os.path.exists(db_path):
        raise RuntimeError(
            f"No database found at {db_path}. "
            "Run the upload step first so the database is created, "
            "then run this builder again."
        )

    absolute_db_path = os.path.abspath(
        db_path
    )

    print(
        f"Rebuilding pipeline from existing database at "
        f"{absolute_db_path} ..."
    )

    # ---------------------------------------------------------------
    # Build configuration
    # ---------------------------------------------------------------

    config = PipelineConfig(
        connection_string=(
            f"sqlite:///{absolute_db_path}"
        ),

        # -----------------------------------------------------------
        # Local LLM
        # -----------------------------------------------------------

        llm_model=llm_model,

        llm_load_in_4bit=llm_load_in_4bit,

        # Shorter generation = faster CPU inference.
        llm_max_new_tokens=llm_max_new_tokens,

        # SQL does not need a long reasoning continuation.
        # This saves generated tokens.
        use_reasoning=use_reasoning,

        # -----------------------------------------------------------
        # Semantic learning
        # -----------------------------------------------------------

        # Semantic learning is disabled on the interactive fast path. It can
        # still be explicitly enabled when a workload genuinely needs it.
        enable_semantic_learning=enable_semantic_learning,

        # -----------------------------------------------------------
        # Query cache
        # -----------------------------------------------------------

        enable_query_cache=True,
    )

    # ---------------------------------------------------------------
    # Create pipeline
    # ---------------------------------------------------------------

    pipeline = Text2SQLPipeline(
        config
    )

    # ---------------------------------------------------------------
    # Learn structural database schema
    # ---------------------------------------------------------------

    print(
        "Discovering database schema..."
    )

    metadata = pipeline.learn_schema()

    # ---------------------------------------------------------------
    # Load the local model once
    # ---------------------------------------------------------------

    print(
        "Loading local LLM..."
    )

    pipeline.warm_up()

    # ---------------------------------------------------------------
    # Ready
    # ---------------------------------------------------------------

    print(
        "Pipeline ready."
    )

    print(
        "Tables discovered:",
        list(metadata.tables.keys()),
    )

    print(
        "Semantic learning:",
        "ON"
        if config.enable_semantic_learning
        else "OFF",
    )

    print(
        "LLM max new tokens:",
        config.llm_max_new_tokens,
    )

    print(
        "Reasoning:",
        "ON"
        if config.use_reasoning
        else "OFF",
    )

    return (
        pipeline,
        config,
        metadata,
    )