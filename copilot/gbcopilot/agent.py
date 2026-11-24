"""Copilot entry points used by the API, evals and tests. The work happens in the LangGraph graph (graph.py).

answer(question, provider=None, thread_id=None):
  provider "fake"/"offline" forces the deterministic fake chat model, "live"/"openai" forces OpenAI, None uses the
  environment (OpenAI when OPENAI_API_KEY is set, unless GB_LLM=fake).

Answer contract: short answer, result table, the SQL, the data date range, definitions used, citations (for
methodology answers), a caveat when route-days were unknown, and `refused` + suggestions when the question can't be
answered from the data. There is no tool that writes, fetches URLs or reads files.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from . import graph as _graph
from .graph import FUTURE_RE, REFUSAL, SUGGESTIONS, default_band_filters, refusal  # noqa: F401
from .llm import MODE, SYSTEM

SYSTEM_PROMPT = SYSTEM


@contextmanager
def _mode(provider: str | None):
    m = {"fake": "fake", "offline": "fake", "live": "live", "openai": "live"}.get(provider or "")
    tok = MODE.set(m)
    try:
        yield
    finally:
        MODE.reset(tok)


def answer(question: str, provider: str | None = None, thread_id: str | None = None) -> dict:
    with _mode(provider):
        return _graph.ask(question, thread_id)


def stream(question: str, provider: str | None = None, thread_id: str | None = None) -> Iterator[dict]:
    with _mode(provider):
        yield from _graph.stream(question, thread_id)
