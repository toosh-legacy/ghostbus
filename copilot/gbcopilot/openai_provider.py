"""OpenAI provider (developer's choice; optional). The model only writes SQL; the same sqlglot validator, read-only
connection, LIMIT 500 and 5 s timeout apply. Only the view schemas and the user's question are sent to OpenAI —
never data rows, never rider data. No tool can write, fetch URLs or read files.

Config (environment or the git-ignored .env): OPENAI_API_KEY (required for this provider), OPENAI_MODEL,
OPENAI_BASE_URL. Without a key the copilot falls back to the offline provider; tests never need a key.
"""

from __future__ import annotations

import httpx
from gbcore.settings import get_settings

from .ollama import build_prompt, extract_sql

SYSTEM = ("You translate questions into a single read-only DuckDB SELECT over the listed views. "
          "Output only SQL, or CANNOT_ANSWER.")


def available() -> bool:
    return bool(get_settings().openai_api_key)


def generate_sql(question: str, months: list[str], timeout: float = 30.0) -> str | None:
    s = get_settings()
    r = httpx.post(
        f"{s.openai_base_url.rstrip('/')}/chat/completions",
        headers={"Authorization": f"Bearer {s.openai_api_key}"},
        timeout=timeout,
        json={"model": s.openai_model, "temperature": 0, "seed": 7,  # seed: best-effort repeatability
              "messages": [{"role": "system", "content": SYSTEM},
                           {"role": "user", "content": build_prompt(question, months)}]},
    )
    r.raise_for_status()
    return extract_sql(r.json()["choices"][0]["message"]["content"] or "")
