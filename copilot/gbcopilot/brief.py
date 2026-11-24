"""Daily reliability brief: a LangGraph map-reduce over pilot routes using the Send API.

    START ─(Send per route)→ summarize_route (cheap model, metrics via MCP tool) ─→ write_brief (strong model)

Each route summary cites the metric rows it used as [route:month:metric]; the reduce step keeps those citations, and
the response returns the cited rows so every number in the brief can be traced back.

Run: uv run python -m gbcopilot.brief --month 2025-12
"""

from __future__ import annotations

import argparse
import operator
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from . import fakes, llm, telemetry  # noqa: F401  (fakes registers the offline handlers)
from .graph import months, vocab


class BriefState(TypedDict, total=False):
    month: str
    routes: list[str]
    summaries: Annotated[list[dict], operator.add]
    brief: str
    sources: dict


class RouteTask(TypedDict):
    route_id: str
    month: str


def fan_out(s: BriefState) -> list[Send]:
    return [Send("summarize_route", {"route_id": r, "month": s["month"]}) for r in s["routes"]]


def summarize_route(t: RouteTask) -> dict:
    from .mcp_client import host

    with telemetry.tracer().start_as_current_span("brief.summarize_route") as span:
        span.set_attribute("route_id", t["route_id"])
        res = host().call("get_route_metrics", {"route_id": t["route_id"]})
        cols = res["columns"]
        ms = list(months())
        keep = {t["month"], ms[ms.index(t["month"]) - 1] if ms.index(t["month"]) > 0 else t["month"]}
        rows = []
        for r in res["rows"]:
            d = dict(zip(cols, r))
            if d["month"] in keep:
                rows.append({"id": f"{t['route_id']}:{d['month']}", **d})
        rows.sort(key=lambda d: d["month"], reverse=True)
        text = llm.text("brief_map", "brief_map", inputs={"route": t["route_id"], "month": t["month"], "rows": rows},
                        route=t["route_id"], month=t["month"], rows=_json(rows))
        cur = next((r for r in rows if r["month"] == t["month"]), {})
        return {"summaries": [{"route_id": t["route_id"], "summary": text.strip(),
                               "missed_trip_rate": cur.get("missed_trip_rate"), "rows": rows}]}


def write_brief(s: BriefState) -> dict:
    with telemetry.tracer().start_as_current_span("brief.write_brief"):
        summaries = sorted(s["summaries"], key=lambda x: x["route_id"])
        text = llm.text("brief_reduce", "brief_reduce", inputs={"month": s["month"], "summaries": [
            {k: v for k, v in x.items() if k != "rows"} for x in summaries]}, month=s["month"],
            summaries="\n".join(f"- {x['summary']}" for x in summaries))
        sources = {r["id"]: {k: v for k, v in r.items() if k != "id"} for x in summaries for r in x["rows"]}
        return {"brief": text.strip(), "sources": sources}


def _json(v) -> str:
    import json

    return json.dumps(v, default=str)


def build():
    g = StateGraph(BriefState)
    g.add_node("summarize_route", summarize_route)
    g.add_node("write_brief", write_brief)
    g.add_conditional_edges(START, fan_out, ["summarize_route"])
    g.add_edge("summarize_route", "write_brief")
    g.add_edge("write_brief", END)
    return g.compile()


def daily_brief(month: str | None = None, routes: list[str] | None = None) -> dict:
    m = month or months()[-1]
    rs = routes or sorted(vocab()[0], key=lambda r: int(r))
    with telemetry.tracer().start_as_current_span("brief.daily"):
        out = build().invoke({"month": m, "routes": rs, "summaries": []})
    return {"month": m, "routes": rs, "brief": out["brief"], "route_summaries": [
        {"route_id": x["route_id"], "summary": x["summary"]} for x in sorted(out["summaries"], key=lambda x: x["route_id"])],
        "sources": out["sources"], "mode": "live" if llm.live() else "fake"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--month")
    a = ap.parse_args()
    print(daily_brief(a.month)["brief"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
