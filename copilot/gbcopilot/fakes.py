"""Deterministic handlers behind FakeReliabilityChatModel, one per role. They make the graphs runnable and testable
without a network: intent templates write the SQL and choose tools, a formatter writes answers, and the brief and
judge roles use simple rules over the rows they are given."""

from __future__ import annotations

import json
import re

from . import offline
from .llm import fake_handler


def _intent(q: str):
    from .graph import _intent as gi

    return gi(q)


def _months():
    from .graph import months

    return months()


METHOD_TERMS = ("on time", "on-time", "headway", "wait", "bunch", "missed", "ghost", "flagged", "state", "elevated",
                "unknown", " low", "range", "prediction", "licen", "data", "feed", "rider", "store", "replay",
                "complete", "probability", "stop", "map", "tiles", "schedule", "position", "definition", "trip",
                "late", "early", "gap", "formula", "badge", "alert", "honest", "model")
ROUTE_MENTION = re.compile(r"\b(?:routes?|bus)\s*#?\d{1,3}\b|\bSL[13]\b|\bsilver line [13]\b", re.I)


@fake_handler("contextualize")
def contextualize(i: dict) -> str:
    q, prev = i["question"], i.get("previous_question") or ""
    it = _intent(q)
    if not it.reason or not prev:
        return json.dumps({"question": q})
    cur_routes = ROUTE_MENTION.findall(q)
    if cur_routes:  # "what about route 23?" → previous question with the route swapped
        base = ROUTE_MENTION.sub("", prev).replace("  ", " ").rstrip("?. ")
        merged = f"{base} for {' and '.join(cur_routes)}?"
    else:
        merged = f"{prev.rstrip('?. ')} {q}"
    return json.dumps({"question": merged})


@fake_handler("classify")
def classify(i: dict) -> str:
    from .graph import METHOD_RE

    q = i["question"]
    it = _intent(q)
    ql = q.lower()
    specific = bool(it.routes or it.months or re.search(r"\b(route|routes)\s*#?\d|\b20\d\d\b|\bsl[13]\b", ql))
    blocked = any(re.search(rf"\b{re.escape(w)}\b", ql) for w in offline.OUT_OF_SCOPE if w not in ("why", "will be"))
    conceptual = re.match(r"\s*(how|what|when|why|where|who|which|do|does|is|are|can)\b", ql) and \
        any(t in ql for t in METHOD_TERMS)
    if METHOD_RE.search(q) or (conceptual and not specific and not blocked):
        return json.dumps({"kind": "methodology", "reason": "asks how something is measured or defined"})
    if it.reason:
        return json.dumps({"kind": "out_of_scope", "reason": it.reason})
    return json.dumps({"kind": "data", "reason": "asks for a reliability metric"})


@fake_handler("plan")
def plan(i: dict) -> str:
    it = _intent(i["question"])
    month = it.months[0] if it.months else _months()[-1]
    base = {"day_type": it.day_type, "time_band": it.band}
    if it.kind == "comparison" and len(it.routes) >= 2 and len(it.months) <= 1:
        return json.dumps({"action": "tool", "tool": "compare_routes",
                           "args": {**base, "route_ids": it.routes, "metric": it.metric, "month": month}})
    if it.kind == "value" and len(it.routes) == 1:
        return json.dumps({"action": "tool", "tool": "get_route_metrics",
                           "args": {**base, "route_id": it.routes[0], "metric": it.metric, "month": month}})
    if it.kind == "trend" and len(it.routes) == 1 and len(it.months) < 2:
        return json.dumps({"action": "tool", "tool": "get_route_metrics",
                           "args": {**base, "route_id": it.routes[0], "metric": it.metric}})
    if it.kind == "ranking" and it.metric == "missed_trip_rate":
        return json.dumps({"action": "tool", "tool": "get_missed_trip_rates",
                           "args": {**base, "month": month, "order": "desc" if it.worst else "asc",
                                    "limit": it.n or 3}})
    return json.dumps({"action": "sql", "tool": None, "args": None})


@fake_handler("sql")
def sql(i: dict) -> str:
    it = _intent(i["question"])
    if it.reason:
        return "CANNOT_ANSWER"
    return offline.to_sql(it, _months()[-1])[0]


@fake_handler("answer")
def answer(i: dict) -> str:
    cols, rows = i["columns"], i["rows"]
    col = next((c for c, *_ in offline.METRICS if c in cols), None)
    if col is None:
        return f"{len(rows)} row(s) returned."
    label = next(m[3] for m in offline.METRICS if m[0] == col)
    ci = cols.index(col)
    ri = cols.index("route_id") if "route_id" in cols else None
    mi = cols.index("month") if "month" in cols else None
    parts = []
    for r in rows[:12]:
        lab = offline.route_label(str(r[ri])) if ri is not None else ""
        mon = r[mi] if mi is not None else ""
        parts.append(f"{lab} {mon} {offline.fmt(col, r[ci])}".strip())
    return f"{label.capitalize()}: " + "; ".join(parts) + "."


@fake_handler("methodology")
def methodology(i: dict) -> str:
    h = i["hits"][0]
    first = re.split(r"(?<=[.!?])\s", h["text"], maxsplit=1)[0]
    more = f" See also [{i['hits'][1]['id']}]." if len(i["hits"]) > 1 else ""
    return f"{first} [{h['id']}]{more}"


@fake_handler("brief_map")
def brief_map(i: dict) -> str:
    rows = i["rows"]
    cur = next((r for r in rows if r["month"] == i["month"]), rows[-1])
    prev = next((r for r in rows if r["month"] != i["month"]), None)
    parts = [f"{offline.route_label(i['route'])}: missed-trip rate {offline.fmt('missed_trip_rate', cur['missed_trip_rate'])} "
             f"[{cur['id']}:missed_trip_rate], on-time performance {offline.fmt('on_time_performance', cur['on_time_performance'])} "
             f"[{cur['id']}:on_time_performance]."]
    if prev and prev.get("missed_trip_rate") is not None and cur.get("missed_trip_rate") is not None:
        d = cur["missed_trip_rate"] - prev["missed_trip_rate"]
        parts.append(f"Missed trips {'rose' if d > 0 else 'fell'} from {offline.fmt('missed_trip_rate', prev['missed_trip_rate'])} "
                     f"[{prev['id']}:missed_trip_rate].")
    return " ".join(parts)


@fake_handler("brief_reduce")
def brief_reduce(i: dict) -> str:
    s = sorted(i["summaries"], key=lambda x: -(x.get("missed_trip_rate") or 0))
    worst = s[:3]
    best = s[-1]
    lines = [f"Missed trips were highest on {offline.route_label(worst[0]['route_id'])} in {i['month']}.", ""]
    lines += [f"- {w['summary']}" for w in worst]
    lines += ["", f"Improved or best: {best['summary']}"]
    return "\n".join(lines)


@fake_handler("judge")
def judge(i: dict) -> str:
    nums = re.findall(r"\d+(?:\.\d+)?", i["answer"])
    flat = json.dumps(i["rows"])
    vals = []
    for r in i["rows"]:
        for v in r:
            if isinstance(v, (int, float)):
                vals += [f"{v:.0f}", f"{100 * v:.1f}", str(v)]
    ok = all(n in flat or n in vals or len(n) == 4 for n in nums)  # 4-digit = a year
    return json.dumps({"faithful": ok, "answers_question": bool(i["rows"]), "rationale": "rule-based check"})
