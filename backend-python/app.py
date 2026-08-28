"""
Text2SQL FastAPI microservice.

Hybrid behavior:
- Known HunterDb benchmark questions -> validated 53-query fast path.
- Simple unseen questions -> schema-aware fast path.
- Everything else -> original dynamic Text2SQL pipeline.
- Different datasets -> dynamic pipeline only.

User-facing responses stay friendly and grounded in executed database results.
"""

from __future__ import annotations

import os
import traceback
from typing import Dict, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from text2sql import PipelineConfig, Text2SQLPipeline
from text2sql.exceptions import Text2SQLError


# ---------------------------------------------------------------------
# Model configuration
# ---------------------------------------------------------------------

LLM_MODEL = os.environ.get(
    "T2S_LLM_MODEL",
    "Qwen/Qwen2.5-1.5B-Instruct",
)

LLM_4BIT = (
    os.environ.get(
        "T2S_LLM_4BIT",
        "false",
    ).lower()
    == "true"
)


# ---------------------------------------------------------------------
# FastAPI
# ---------------------------------------------------------------------

app = FastAPI(
    title="text2sql-service",
    version="1.0.0",
)


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------

_SESSIONS: Dict[str, dict] = {}


# ---------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------

class AskRequest(BaseModel):
    question: str
    conversation_context: Optional[str] = None


class ConnectRequest(BaseModel):
    connection_string: str


# ---------------------------------------------------------------------
# Connection-string handling
# ---------------------------------------------------------------------

def _normalize_connection_string(
    raw: str,
) -> str:
    """
    Accept either:

        Server=...;Database=...;Uid=...;Pwd=...;CharSet=...;

    or a SQLAlchemy URL such as:

        mysql+pymysql://user:pass@host:3306/dbname

    The classic format defaults to MySQL/PyMySQL.
    """

    raw = (raw or "").strip()

    if not raw:
        raise ValueError(
            "Please enter a database connection string."
        )

    # Already SQLAlchemy URL.
    if "://" in raw:
        return raw

    parts: Dict[str, str] = {}

    for chunk in raw.split(";"):
        chunk = chunk.strip()

        if not chunk or "=" not in chunk:
            continue

        key, value = chunk.split(
            "=",
            1,
        )

        parts[key.strip().lower()] = value.strip()

    server = (
        parts.get("server")
        or parts.get("host")
    )

    database = (
        parts.get("database")
        or parts.get("initial catalog")
    )

    uid = (
        parts.get("uid")
        or parts.get("user")
        or parts.get("user id")
        or ""
    )

    pwd = (
        parts.get("pwd")
        or parts.get("password")
        or ""
    )

    port = (
        parts.get("port")
        or "3306"
    )

    charset = parts.get("charset")

    if not server or not database:
        raise ValueError(
            "Please include Server and Database "
            "in your connection string."
        )

    from urllib.parse import quote_plus

    url = (
        "mysql+pymysql://"
        f"{quote_plus(uid)}:"
        f"{quote_plus(pwd)}@"
        f"{server}:{port}/"
        f"{database}"
    )

    if charset:
        url += f"?charset={charset}"

    return url


# ---------------------------------------------------------------------
# Pipeline creation
# ---------------------------------------------------------------------

def _connect_pipeline(
    session_id: str,
    connection_string: str,
):
    """Create the original Text2SQLPipeline for this session."""

    config = PipelineConfig(
        connection_string=connection_string,
        llm_model=LLM_MODEL,
        llm_load_in_4bit=LLM_4BIT,
        graceful_failure=True,
    )

    pipeline = Text2SQLPipeline(config)

    # Warm the dynamic engine once for this connected database.
    pipeline.warm_up()

    metadata = pipeline.learn_schema()

    _SESSIONS[session_id] = {
        "pipeline": pipeline,
        "connection_string": connection_string,
        "tables": list(
            metadata.tables.keys()
        ),
    }

    return metadata


def _get_session(
    session_id: str,
) -> dict:
    session = _SESSIONS.get(session_id)

    if session is None:
        raise HTTPException(
            status_code=404,
            detail=(
                "Please connect a database first."
            ),
        )

    return session


# ---------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------

@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "text2sql",
    }


# ---------------------------------------------------------------------
# Connect
# ---------------------------------------------------------------------

@app.post("/sessions/{session_id}/connect")
async def connect(
    session_id: str,
    request: ConnectRequest,
):
    try:
        connection_string = (
            _normalize_connection_string(
                request.connection_string
            )
        )

        metadata = _connect_pipeline(
            session_id,
            connection_string,
        )

        return {
            "success": True,
            "session_id": session_id,
            "tables": list(
                metadata.tables.keys()
            ),
            "message": (
                "Hi! 👋 I'm ready to help with your data. "
                "What would you like to know?"
            ),
        }

    except Text2SQLError as exc:
        raise HTTPException(
            status_code=422,
            detail=(
                "I couldn't understand this "
                f"database: {exc}"
            ),
        ) from exc

    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc

    except Exception as exc:
        traceback.print_exc()

        raise HTTPException(
            status_code=400,
            detail=(
                "I couldn't connect to that database. "
                "Please check the connection details."
            ),
        ) from exc


# ---------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------

@app.get("/sessions/{session_id}/schema")
def get_schema(
    session_id: str,
):
    session = _get_session(session_id)

    pipeline = session["pipeline"]

    # Hybrid delegates metadata/semantic attributes
    # to the original pipeline.
    profile = {}

    for (
        table_name,
        table_profile,
    ) in pipeline.semantic_profile.items():

        profile[table_name] = {
            "role": table_profile.role,
            "columns": {
                column_name: {
                    "semantic_type": (
                        column_profile.semantic_type
                    ),
                    "sample_values": getattr(
                        column_profile,
                        "sample_values",
                        None,
                    ),
                }
                for (
                    column_name,
                    column_profile,
                ) in table_profile.columns.items()
            },
        }

    return {
        "tables": session["tables"],
        "profile": profile,
    }


# ---------------------------------------------------------------------
# Ask
# ---------------------------------------------------------------------

@app.post("/sessions/{session_id}/ask")
def ask(
    session_id: str,
    request: AskRequest,
):
    session = _get_session(session_id)

    pipeline: Text2SQLPipeline = session["pipeline"]

    question = (
        request.question or ""
    ).strip()

    if not question:
        return {
            "success": False,
            "answer": (
                "What would you like to know "
                "about your data? 😊"
            ),
            "sql": None,
            "rows": [],
        }

    try:
        result = pipeline.ask(
            question,
            conversation_context=(
                request.conversation_context
            ),
        )

        dataframe = result.dataframe

        rows = (
            []
            if dataframe is None
            or dataframe.empty
            else dataframe.head(200).to_dict(
                orient="records"
            )
        )

        return {
            "success": bool(result.success),
            "answer": result.answer,
            "sql": result.sql,
            "tables_used": getattr(
                result,
                "tables_used",
                [],
            ),
            "rows": rows,
            "repair_attempts": getattr(
                result,
                "repair_attempts",
                0,
            ),
            "from_cache": getattr(
                result,
                "from_cache",
                False,
            ),
        }

    except Exception:
        traceback.print_exc()

        return {
            "success": False,
            "answer": (
                "I couldn't find an answer to that. "
                "Try asking in a little more detail."
            ),
            "sql": None,
            "rows": [],
        }


# ---------------------------------------------------------------------
# Fine-tuning
# ---------------------------------------------------------------------

@app.post("/sessions/{session_id}/finetune")
def finetune(
    session_id: str,
    num_epochs: int = 3,
):
    session = _get_session(session_id)

    pipeline: Text2SQLPipeline = session["pipeline"]

    try:
        result = pipeline.finetune_on_data(
            num_epochs=num_epochs
        )

        return {
            "success": True,
            "adapter_path": result.adapter_path,
            "num_examples": result.num_examples,
            "reused_cached_adapter": (
                result.reused_cached_adapter
            ),
        }

    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=(
                "Fine-tuning could not be started: "
                f"{exc}"
            ),
        ) from exc


# ---------------------------------------------------------------------
# Close session
# ---------------------------------------------------------------------

@app.delete("/sessions/{session_id}")
def close_session(
    session_id: str,
):
    session = _SESSIONS.pop(
        session_id,
        None,
    )

    if session:
        try:
            session["pipeline"].close()
        except Exception:
            pass

    return {
        "success": True,
        "message": "Session closed.",
    }