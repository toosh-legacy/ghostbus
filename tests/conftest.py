"""Tests never call paid or network LLMs, whatever the developer's .env says: the copilot graphs run on the
deterministic fake chat model. Real model calls happen only in `make eval-live`."""

import os

os.environ["GB_LLM"] = "fake"
os.environ["OPENAI_API_KEY"] = ""
os.environ.pop("LANGSMITH_API_KEY", None)
os.environ["GB_SESSION"] = "pytest"
os.environ.setdefault("GB_RERANKER", "none")  # the cross-encoder has its own test; keep the rest fast
os.environ["MLFLOW_DISABLE_AGENT_HINT"] = "1"
