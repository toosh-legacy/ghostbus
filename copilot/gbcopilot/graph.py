"""The copilot as a LangGraph graph.

    contextualize → classify ─┬─ data ────────→ plan ─┬─ tool → call MCP tool ─────────────┐
                              │                        └─ sql  → generate SQL → validate → execute
                              │                                       ↑   (retry ≤ 2)        │
                              │                                       └──── check result ◄───┘
                              │                                                  │
                              ├─ methodology → retrieve (hybrid + rerank) → answer with citations
                              └─ out of scope → refuse                         answer (SQL, dates, definitions)

- Structured outputs are Pydantic models; cheap tier for contextualize/classify/plan/SQL, strong tier for answers.
- Tools come from the GhostBus MCP server through langchain-mcp-adapters (mcp_client.py).
- Every SQL string, model-written or tool-built, passes the sqlglot validator and runs read-only.
- A SqliteSaver checkpointer keeps per-thread conversation memory (var/copilot_memory.sqlite).
"""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from collections.abc import Iterator
from functools import lru_cache
from typing import Annotated, Literal, TypedDict

from gbcore.definitions import get_definition
from gbcore.settings import get_settings
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field

from . import fakes, llm, offline, rag, telemetry  # noqa: F401  (fakes registers the offline handlers)
from .validator import ValidationError, list_views, run_query, validate

MAX_RETRIES = 2
REFUSAL = "I can't answer that from this data."
SUGGESTIONS = [
    "Which 3 pilot routes had the highest missed-trip rate on weekday PM peaks last month?",
    "Compare on-time performance for route 1 and route 111 in November 2025.",
    "How did bunching on route 66 change by month?",
    "How is excess wait time measured?",
]
FUTURE_RE = re.compile(r"\b(next (month|week|year|quarter)|predict\w*|forecast\w*|will be|going to|tomorrow|"
                       r"upcoming|future)\b", re.I)
NOT_IN_DATA_RE = re.compile(r"(red|orange|blue|green|mattapan) line|subway|commuter rail|ferry|"
                            r"ridership|passengers?|fares?|weather", re.I)
METHOD_RE = re.compile(r"\b(how (is|are|do you|does ghostbus)|what counts|what is an? |what does .* mean|defin\w*|"
                       r"measur\w*|calculat\w*|where does the data|data sources?|licen[cs]e|threshold|methodology|"
                       r"privacy|what happens when|why is .* unknown)\b", re.I)


# --------------------------------------------------------------------------- structured outputs
class Classification(BaseModel):
    kind: Literal["data", "methodology", "out_of_scope"]
    reason: str = Field(description="one sentence")


class ToolArgs(BaseModel):
    route_id: str | None = None
    route_ids: list[str] | None = None
    metric: str | None = None
    month: str | None = None
    day_type: Literal["all", "weekday", "weekend"] = "all"
    time_band: Literal["all", "am_peak", "midday", "pm_peak", "evening", "early_late"] = "all"
    order: Literal["desc", "asc"] = "desc"
    limit: int | None = None


class Plan(BaseModel):
    action: Literal["tool", "sql"]
    tool: Literal["get_route_metrics", "get_missed_trip_rates", "compare_routes"] | None = None
    args: ToolArgs | None = None


class Standalone(BaseModel):
    question: str


class State(TypedDict, total=False):
    messages: Annotated[list[BaseMessage], add_messages]
    question: str
    standalone: str
    kind: str
    reason: str
    plan: dict | None
    sql: str | None
    attempts: int
    error: str | None
    result: dict | None
    hits: list[dict]
    response: dict | None


# --------------------------------------------------------------------------- vocab + helpers
@lru_cache
def vocab() -> tuple[frozenset, tuple]:
    r = run_query("SELECT DISTINCT route_id, month FROM v_route_month_metrics")
    return frozenset(x[0] for x in r.rows), tuple(sorted({x[1] for x in r.rows}))


def months() -> tuple[str, ...]:
    return vocab()[1]


@lru_cache
def schema_text() -> str:
    return "\n".join(f"- {v}: {i['description']} Columns: {', '.join(i['columns'])}" for v, i in list_views().items())


def default_band_filters(sql: str) -> str:
    """Add day_type/time_band = 'all' when a query on v_route_timeband_metrics never mentions them (a common LLM
    slip that returns one row per band). The validator still checks the result."""
    import sqlglot
    from sqlglot import exp

    try:
        tree = sqlglot.parse_one(sql, read="duckdb")
    except sqlglot.errors.ParseError:
        return sql
    for sel in tree.find_all(exp.Select):
        tables = [t.name.lower() for t in sel.find_all(exp.Table)]
        if "v_route_timeband_metrics" not in tables or sel.find(exp.Subquery):
            continue
        where = sel.args.get("where")
        used = {c.name.lower() for c in where.find_all(exp.Column)} if where else set()
        for col in ("day_type", "time_band"):
            if col not in used:
                sel.where(exp.EQ(this=exp.column(col), expression=exp.Literal.string("all")), copy=False)
    return tree.sql(dialect="duckdb")


def _intent(q: str) -> offline.Intent:
    routes, ms = vocab()
    return offline.parse(q, set(routes), list(ms))


def _span(name: str):
    return telemetry.tracer().start_as_current_span(f"copilot.{name}")


def refusal(reason: str) -> dict:
    return {"answer": f"{REFUSAL} ({reason}.)", "refused": True, "table": None, "sql": None, "date_range": None,
            "definitions": [], "citations": [], "caveat": None, "suggestions": SUGGESTIONS}


# --------------------------------------------------------------------------- nodes
def contextualize(s: State) -> dict:
    with _span("contextualize"):
        prior = [m for m in s.get("messages", [])[:-1]]
        q = s["question"]
        if not prior:
            return {"standalone": q}
        hist = "\n".join(f"{'User' if isinstance(m, HumanMessage) else 'Copilot'}: {m.content}" for m in prior[-6:])
        prev_q = next((str(m.content) for m in reversed(prior) if isinstance(m, HumanMessage)), "")
        out = llm.structured("contextualize", "contextualize", Standalone,
                             inputs={"question": q, "previous_question": prev_q}, history=hist, question=q)
        return {"standalone": out.question}


def classify(s: State) -> dict:
    with _span("classify"):
        q = s["standalone"]
        # deterministic guards first: models misjudge which months are "future" and invent route mappings
        if FUTURE_RE.search(q):
            return {"kind": "out_of_scope", "reason": "the data is historical; it does not contain forecasts"}
        if NOT_IN_DATA_RE.search(q):
            return {"kind": "out_of_scope", "reason": "the data covers MBTA bus reliability only (no subway, rail, "
                                                      "ridership, fares or weather)"}
        ms = months()
        c = llm.structured("classify", "classify", Classification, inputs={"question": q},
                           question=q, first_month=ms[0], last_month=ms[-1])
        if c.kind == "out_of_scope" and _intent(q).reason is None:
            # the question fits a supported template exactly (metric, routes, months in range): not out of scope
            return {"kind": "data", "reason": "matches a supported reliability question (classifier overridden)"}
        return {"kind": c.kind, "reason": c.reason}


def plan(s: State) -> dict:
    with _span("plan"):
        q = s["standalone"]
        p = llm.structured("plan", "plan", Plan, inputs={"question": q}, question=q, last_month=months()[-1])
        return {"plan": p.model_dump(), "attempts": 0, "error": None}


TOOL_PARAMS = {
    "get_route_metrics": {"route_id", "metric", "month", "day_type", "time_band"},
    "get_missed_trip_rates": {"month", "route_ids", "day_type", "time_band", "order", "limit"},
    "compare_routes": {"route_ids", "metric", "month", "day_type", "time_band"},
}


def normalize_tool_call(tool: str, args: dict) -> tuple[str, dict]:
    """Deterministic repair of common model slips before the MCP call (the server still checks everything)."""
    a = {k: v for k, v in args.items() if v is not None}
    if tool == "compare_routes" and a.get("route_id"):
        a["route_ids"] = [a.pop("route_id"), *[r for r in a.get("route_ids") or [] if r != a.get("route_id")]]
    if tool == "compare_routes" and len(a.get("route_ids") or []) == 1:
        tool, a["route_id"] = "get_route_metrics", a.pop("route_ids")[0]
    if tool == "get_missed_trip_rates" and not a.get("month"):
        rid = a.pop("route_id", None) or (a.get("route_ids") or [None])[0]
        if rid:  # missed-trip trend for one route
            tool, a = "get_route_metrics", {"route_id": rid, "metric": "missed_trip_rate",
                                           "day_type": a.get("day_type", "all"), "time_band": a.get("time_band", "all")}
        else:
            a["month"] = months()[-1]
    if tool == "get_missed_trip_rates" and a.get("route_id"):
        a["route_ids"] = [a.pop("route_id")]
    return tool, {k: v for k, v in a.items() if k in TOOL_PARAMS[tool]}


def call_tool(s: State) -> dict:
    from .mcp_client import McpToolError, host

    p = dict(s["plan"] or {})
    tool, args = normalize_tool_call(p["tool"], dict(p.get("args") or {}))
    p["tool"] = tool
    with _span("call_tool") as span:
        span.set_attribute("tool", p.get("tool") or "")
        try:
            out = host().call(tool, args)
        except McpToolError as e:  # tool rejected the arguments → fall back to SQL generation
            return {"error": f"tool {p.get('tool')} failed: {e}", "plan": {**p, "action": "sql"}}
        return {"result": {"columns": out["columns"], "rows": out["rows"], "sql": out["sql"],
                           "via": f"mcp:{p['tool']}"}, "sql": out["sql"], "error": None}


def generate_sql(s: State) -> dict:
    with _span("generate_sql"):
        q = s["standalone"]
        ms = months()
        fb = f"\nThe previous attempt failed: {s['error']}\nWrite a corrected query.\n" if s.get("error") else ""
        raw = llm.text("sql", "sql", inputs={"question": q}, question=q, schema=schema_text(),
                       first_month=ms[0], last_month=ms[-1], feedback=fb)
        sql = extract_sql(raw)
        if sql is None:
            return {"sql": None, "error": "the model did not produce a query"}
        return {"sql": default_band_filters(sql), "error": None}


def validate_sql(s: State) -> dict:
    with _span("validate"):
        if not s.get("sql"):
            return {"attempts": s.get("attempts", 0) + 1}
        try:
            validate(s["sql"])
        except ValidationError as e:
            return {"error": f"validator rejected the query: {e}", "attempts": s.get("attempts", 0) + 1}
        return {"error": None}


def execute(s: State) -> dict:
    with _span("execute"):
        try:
            res = run_query(s["sql"])
        except (ValidationError, TimeoutError) as e:
            return {"error": f"query failed: {e}", "attempts": s.get("attempts", 0) + 1}
        return {"result": {"columns": res.columns, "rows": res.rows, "sql": res.sql, "via": "sql"}, "error": None}


def check_result(s: State) -> dict:
    with _span("check_result"):
        r = s.get("result") or {}
        if not r.get("rows"):
            return {"error": "the query returned no rows; check the filters and month",
                    "attempts": s.get("attempts", 0) + 1, "result": None}
        return {}


def answer(s: State) -> dict:
    with _span("answer"):
        r = s["result"]
        q = s["standalone"]
        rows = r["rows"][:50]
        text = llm.text("answer", "answer", inputs={"question": q, "columns": r["columns"], "rows": rows},
                        question=q, columns=", ".join(r["columns"]), rows=json.dumps(rows, default=str))
        col = next((c for c, *_ in offline.METRICS if c in r["columns"]), None)
        d = get_definition(offline.DEF_NAME[col]) if col else None
        mi = r["columns"].index("month") if "month" in r["columns"] else None
        found = sorted({row[mi] for row in r["rows"]}) if mi is not None else []
        ms = months()
        rng = {"start_month": found[0], "end_month": found[-1]} if found else \
            {"start_month": ms[0], "end_month": ms[-1]}
        caveat = None
        try:
            uk = run_query("SELECT sum(unknown_route_days) FROM v_route_month_metrics WHERE month BETWEEN "
                           f"'{rng['start_month']}' AND '{rng['end_month']}'").rows[0][0]
            if uk:
                caveat = f"{int(uk)} route-days in this range had incomplete data and were treated as unknown."
        except Exception:  # noqa: BLE001
            pass
        resp = {"answer": text.strip(), "refused": False, "table": {"columns": r["columns"], "rows": r["rows"]},
                "sql": r["sql"], "via": r["via"], "date_range": rng,
                "definitions": [{**d, "link": f"/methodology#{d['name']}"}] if d else [], "citations": [],
                "caveat": caveat, "suggestions": []}
        return {"response": resp, "messages": [AIMessage(resp["answer"])]}


def retrieve(s: State) -> dict:
    with _span("retrieve"):
        hits = rag.retrieve(s["standalone"])
        return {"hits": [{"id": h.id, "text": h.doc.page_content, "score": h.score} for h in hits]}


def answer_methodology(s: State) -> dict:
    with _span("answer_methodology"):
        hits = s.get("hits") or []
        if not hits:
            resp = refusal("no published methodology covers that")
            return {"response": resp, "messages": [AIMessage(resp["answer"])]}
        passages = "\n".join(f"[{h['id']}] {h['text']}" for h in hits)
        q = s["standalone"]
        text = llm.text("methodology", "methodology", inputs={"question": q, "hits": hits}, question=q,
                        passages=passages)
        cited = [h for h in hits if f"[{h['id']}]" in text] or hits[:1]
        resp = {"answer": text.strip(), "refused": False, "table": None, "sql": None, "via": "rag",
                "date_range": None, "definitions": [], "citations": cited, "caveat": None, "suggestions": []}
        return {"response": resp, "messages": [AIMessage(resp["answer"])]}


def refuse(s: State) -> dict:
    with _span("refuse"):
        reason = s.get("reason") or "the question is outside the reliability views"
        if s.get("error"):
            reason = f"no valid query after {MAX_RETRIES} retries ({s['error']})"
        resp = refusal(reason.rstrip("."))
        if s.get("sql") and s.get("error"):
            resp["rejected_sql"] = s["sql"]
        return {"response": resp, "messages": [AIMessage(resp["answer"])]}


# --------------------------------------------------------------------------- edges
def after_classify(s: State) -> str:
    return {"data": "plan", "methodology": "retrieve"}.get(s["kind"], "refuse")


def after_plan(s: State) -> str:
    return "call_tool" if (s.get("plan") or {}).get("action") == "tool" else "generate_sql"


def after_tool(s: State) -> str:
    return "generate_sql" if s.get("error") else "check_result"


def retry_or(next_node: str):
    def edge(s: State) -> str:
        if not s.get("error"):
            return next_node
        return "generate_sql" if s.get("attempts", 0) <= MAX_RETRIES else "refuse"
    return edge


def after_check(s: State) -> str:
    if not s.get("error"):
        return "answer"
    return "generate_sql" if s.get("attempts", 0) <= MAX_RETRIES else "refuse"


def build(checkpointer=None):
    g = StateGraph(State)
    for name, fn in [("contextualize", contextualize), ("classify", classify), ("plan", plan),
                     ("call_tool", call_tool), ("generate_sql", generate_sql), ("validate", validate_sql),
                     ("execute", execute), ("check_result", check_result), ("answer", answer),
                     ("retrieve", retrieve), ("answer_methodology", answer_methodology), ("refuse", refuse)]:
        g.add_node(name, fn)
    g.add_edge(START, "contextualize")
    g.add_edge("contextualize", "classify")
    g.add_conditional_edges("classify", after_classify, ["plan", "retrieve", "refuse"])
    g.add_conditional_edges("plan", after_plan, ["call_tool", "generate_sql"])
    g.add_conditional_edges("call_tool", after_tool, ["generate_sql", "check_result"])
    g.add_conditional_edges("generate_sql", lambda s: "validate", ["validate"])
    g.add_conditional_edges("validate", retry_or("execute"), ["execute", "generate_sql", "refuse"])
    g.add_conditional_edges("execute", retry_or("check_result"), ["check_result", "generate_sql", "refuse"])
    g.add_conditional_edges("check_result", after_check, ["answer", "generate_sql", "refuse"])
    g.add_edge("retrieve", "answer_methodology")
    for n in ("answer", "answer_methodology", "refuse"):
        g.add_edge(n, END)
    return g.compile(checkpointer=checkpointer)


@lru_cache
def checkpointer() -> SqliteSaver:
    path = get_settings().var_dir / "copilot_memory.sqlite"
    path.parent.mkdir(parents=True, exist_ok=True)
    return SqliteSaver(sqlite3.connect(str(path), check_same_thread=False))


@lru_cache
def graph():
    return build(checkpointer())


def _turn(question: str) -> dict:
    return {"messages": [HumanMessage(question)], "question": question, "standalone": "", "kind": "", "reason": "",
            "plan": None, "sql": None, "attempts": 0, "error": None, "result": None, "hits": [], "response": None}


def ask(question: str, thread_id: str | None = None) -> dict:
    tid = thread_id or uuid.uuid4().hex
    with telemetry.tracer().start_as_current_span("copilot.ask"):
        out = graph().invoke(_turn(question), config={"configurable": {"thread_id": tid}, "recursion_limit": 40})
    return {**out["response"], "thread_id": tid, "mode": "live" if llm.live() else "fake"}


def stream(question: str, thread_id: str | None = None) -> Iterator[dict]:
    """Yields {"type": "progress", "node": ...} per finished node, then {"type": "answer", ...}."""
    tid = thread_id or uuid.uuid4().hex
    final = None
    with telemetry.tracer().start_as_current_span("copilot.stream"):
        for upd in graph().stream(_turn(question), config={"configurable": {"thread_id": tid}, "recursion_limit": 40},
                                  stream_mode="updates"):
            for node, val in upd.items():
                yield {"type": "progress", "node": node}
                if isinstance(val, dict) and val.get("response"):
                    final = val["response"]
    yield {"type": "answer", **(final or refusal("no answer was produced")), "thread_id": tid,
           "mode": "live" if llm.live() else "fake"}


def extract_sql(text: str) -> str | None:
    t = text.strip()
    if "CANNOT_ANSWER" in t:
        return None
    m = re.search(r"```(?:sql)?\s*(.*?)```", t, re.S | re.I)
    if m:
        t = m.group(1).strip()
    m = re.search(r"\b(SELECT|WITH)\b.*", t, re.S | re.I)
    if not m:
        return None
    return m.group(0).strip().rstrip(";").strip().split(";")[0]
