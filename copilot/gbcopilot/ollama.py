"""Optional local-LLM provider (Ollama). The model only writes SQL; everything it writes goes through the same
validator, read-only connection, row limit and timeout as the offline provider. It sees view schemas, never data
rows, and has no tool that writes, fetches URLs or reads files.

Config: COPILOT_PROVIDER=ollama, OLLAMA_URL (default http://127.0.0.1:11434), OLLAMA_MODEL (default llama3.2:3b).
"""

from __future__ import annotations

import os
import re

import httpx

CANNOT = "CANNOT_ANSWER"


def _schema_text() -> str:
    from .validator import list_views

    lines = []
    for v, info in list_views().items():
        cols = ", ".join(info["columns"])
        lines.append(f"- {v}: {info['description']} Columns: {cols}")
    return "\n".join(lines)


def build_prompt(question: str, months: list[str]) -> str:
    return f"""You write one DuckDB SQL SELECT query that answers a question about MBTA bus reliability.
Use ONLY these tables:
{_schema_text()}

Rules:
- Output only the SQL, no explanation, no markdown.
- Prefer v_route_timeband_metrics: it has day_type and time_band columns (v_route_month_metrics does not).
- Use the rate column for rates (missed_trip_rate, not missed_trips). No subqueries are needed.
- Always select route_id and month (when the table has them) plus the metric column, using the exact column names.
- Rates are fractions 0-1. Use day_type 'all' and time_band 'all' unless the question names a day type or time band
  (time_band values: am_peak, midday, pm_peak, evening, early_late; day_type values: weekday, weekend).
- Data covers months {months[0]} to {months[-1]}; "last month" means {months[-1]}. Months are strings like '2025-12'.
- Route ids are strings: '1','23','28','39','66','77','109','111','741' (= SL1),'743' (= SL3).
- For "highest"/"worst" missed-trip, bunching or excess wait, ORDER BY the metric DESC; for on-time performance or
  headway adherence, "worst" means lowest (ASC). Add LIMIT N when the question asks for N routes.
- If the question cannot be answered from these tables, output exactly {CANNOT}.

Question: {question}
SQL:"""


def extract_sql(text: str) -> str | None:
    t = text.strip()
    if CANNOT in t:
        return None
    m = re.search(r"```(?:sql)?\s*(.*?)```", t, re.S | re.I)
    if m:
        t = m.group(1).strip()
    m = re.search(r"\b(SELECT|WITH)\b.*", t, re.S | re.I)
    if not m:
        return None
    sql = m.group(0).strip().rstrip(";").strip()
    return sql.split(";")[0]


def generate_sql(question: str, months: list[str], timeout: float = 90.0) -> str | None:
    url = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
    model = os.environ.get("OLLAMA_MODEL", "llama3.2:3b")
    r = httpx.post(f"{url}/api/generate", timeout=timeout, json={
        "model": model, "prompt": build_prompt(question, months), "stream": False,
        "options": {"temperature": 0, "seed": 7, "num_predict": 256}})
    r.raise_for_status()
    return extract_sql(r.json().get("response", ""))


def available() -> bool:
    try:
        url = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
        return httpx.get(f"{url}/api/tags", timeout=2).status_code == 200
    except httpx.HTTPError:
        return False
