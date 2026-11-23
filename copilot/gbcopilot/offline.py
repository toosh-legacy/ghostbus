"""Offline provider: deterministic intent parsing into parameterized SQL templates over the allowlisted views.

Slots: intent (ranking | comparison | trend | value | count), metric, routes, month(s), time band, day type,
N, direction (worst/best). Slot values come only from fixed vocabularies (route ids, months present in the data),
so no user text ever reaches SQL verbatim; the generated SQL still goes through the validator.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

MONTHS = {"january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3, "april": 4, "apr": 4, "may": 5,
          "june": 6, "jun": 6, "july": 7, "jul": 7, "august": 8, "aug": 8, "september": 9, "sep": 9, "sept": 9,
          "october": 10, "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12}

METRICS = [  # (column, keywords, higher_is_worse, label)
    ("missed_trip_rate", ["missed-trip rate", "missed trip rate", "missed trips", "missed-trip", "missed trip",
                          "missed", "ghost"], True, "missed-trip rate"),
    ("on_time_performance", ["on-time performance", "on time performance", "on-time", "on time", "otp", "punctual"],
     False, "on-time performance"),
    ("headway_adherence", ["headway adherence", "headway"], False, "headway adherence"),
    ("bunching_rate", ["bunching", "bunched", "bunch"], True, "bunching rate"),
    ("excess_wait_time_s", ["excess wait time", "excess wait", "ewt", "wait time", "waiting"], True, "excess wait time"),
]
HIGHER_IS_WORSE = {m[0]: m[2] for m in METRICS}
DEF_NAME = {"missed_trip_rate": "missed_trip_rate", "on_time_performance": "on_time_performance",
            "headway_adherence": "headway_adherence", "bunching_rate": "bunching_rate",
            "excess_wait_time_s": "excess_wait_time"}
BANDS = [("am_peak", ["am peak", "morning peak", "morning rush", "am rush", "morning"]),
         ("pm_peak", ["pm peak", "evening rush", "afternoon rush", "pm rush", "afternoon peak", "evening peak"]),
         ("midday", ["midday", "mid-day", "middle of the day"]),
         ("evening", ["evening", "night"]),
         ("early_late", ["early morning", "late night", "overnight", "early_late"])]
ROUTE_ALIASES = {"sl1": "741", "sl3": "743", "silver line 1": "741", "silver line 3": "743", "silver line": None}
OUT_OF_SCOPE = ["weather", "rain", "snow", "crime", "driver", "operator", "salary", "fare", "ticket", "ridership",
                "passenger count", "crowd", "subway", "red line", "orange line", "green line", "blue line",
                "commuter rail", "ferry", "tomorrow", "next week", "next month", "forecast", "predict", "will be",
                "accident", "covid", "budget", "electric", "emissions", "why", "cause", "opinion", "should i",
                "stock", "population", "2023", "2024", "2026", "2027", "uber", "parking", "bike", "address", "phone"]


@dataclass
class Intent:
    kind: str | None = None
    metric: str | None = None
    routes: list[str] = field(default_factory=list)
    months: list[str] = field(default_factory=list)
    band: str = "all"
    day_type: str = "all"
    n: int | None = None
    worst: bool = True
    reason: str | None = None  # why it can't be answered


def _find_routes(q: str, valid: set[str]) -> list[str]:
    out: list[str] = []
    ql = q.lower()
    for k, v in ROUTE_ALIASES.items():
        if k in ql and v:
            out.append(v)
    for m in re.finditer(r"\b(?:route|routes|bus|the|and|vs\.?|versus|,)\s*#?(\d{1,3})\b", ql):
        r = str(int(m.group(1)))
        if r in valid:
            out.append(r)
    for m in re.finditer(r"\b(\d{1,3})\b", ql):
        r = str(int(m.group(1)))
        if r in valid and r not in out and not re.search(rf"\b(top|worst|best|bottom|first)\s+{m.group(1)}\b", ql) \
                and not re.search(rf"\b{m.group(1)}\s+(routes|pilot)", ql):
            out.append(r)
    seen = set()
    return [r for r in out if not (r in seen or seen.add(r))]


def parse(q: str, valid_routes: set[str], months_available: list[str]) -> Intent:
    ql = " " + q.lower().strip() + " "
    it = Intent()
    for col, kws, worse, _ in METRICS:
        if any(k in ql for k in kws):
            it.metric, it.worst = col, worse
            break
    for band, kws in BANDS:
        if any(k in ql for k in kws):
            it.band = band
            break
    if "weekday" in ql or "work day" in ql or "workday" in ql:
        it.day_type = "weekday"
    elif "weekend" in ql or "saturday" in ql or "sunday" in ql:
        it.day_type = "weekend"
    latest = months_available[-1] if months_available else None
    years = re.findall(r"\b(20\d\d)\b", ql)
    for name, num in MONTHS.items():
        if re.search(rf"\b{name}\b", ql) and not (name == "may" and re.search(r"\bmay (i|you|we)\b", ql)):
            y = years[0] if years else (latest[:4] if latest else "2025")
            m = f"{y}-{num:02d}"
            if m not in it.months:
                it.months.append(m)
    if "last month" in ql or "latest month" in ql or "most recent month" in ql:
        if latest:
            it.months = [latest]
    it.months.sort()
    it.routes = _find_routes(q, valid_routes)
    m = re.search(r"\b(?:top|worst|best|bottom|first)\s+(\d{1,2})\b|\b(\d{1,2})\s+(?:pilot\s+)?routes\b", ql)
    words = {"three": 3, "five": 5, "two": 2, "four": 4, "ten": 10}
    if m:
        it.n = int(m.group(1) or m.group(2))
    else:
        for w, n in words.items():
            if re.search(rf"\b(top|worst|best|bottom)\s+{w}\b|\b{w}\s+(pilot\s+)?routes\b", ql):
                it.n = n
    best_words = ["best", "lowest" if it.worst else "highest", "most reliable", "least"]
    worst_words = ["worst", "highest" if it.worst else "lowest", "most", "least reliable", "poorest"]
    if any(re.search(rf"\b{w}\b", ql) for w in ["best", "most reliable", "fewest", "least"]):
        it.worst = False if any(w in ql for w in ["best", "most reliable"]) else it.worst
    if it.metric in ("missed_trip_rate", "bunching_rate", "excess_wait_time_s"):
        if re.search(r"\b(lowest|fewest|least|best)\b", ql):
            it.worst = False
        elif re.search(r"\b(highest|most|worst)\b", ql):
            it.worst = True
    else:
        if re.search(r"\b(highest|best|most)\b", ql):
            it.worst = False
        elif re.search(r"\b(lowest|worst|least|poorest)\b", ql):
            it.worst = True
    del best_words, worst_words
    # ---- intent
    if any(w in ql for w in ["trend", "over time", "by month", "month by month", "each month", "every month",
                             "monthly", "changed", "change from", "over the months"]):
        it.kind = "trend"
    elif any(w in ql for w in [" compare", " vs", "versus", " or route", "difference between", "better:"]) or \
            (len(it.routes) >= 2 and " and " in ql and not it.n):
        it.kind = "comparison"
    elif re.search(r"\bhow many\b", ql) and it.metric == "missed_trip_rate":
        it.kind = "count"
    elif re.search(r"\b(which|what|rank|top|worst|best|highest|lowest|list)\b", ql) and not it.routes:
        it.kind = "ranking"
    elif it.routes:
        it.kind = "value"
    # ---- unanswerable checks
    if any(re.search(rf"\b{re.escape(w)}\b", ql) for w in OUT_OF_SCOPE):
        it.reason = "that question needs data this copilot does not have (it only covers bus reliability aggregates)"
    elif it.metric is None:
        it.reason = "no reliability metric was recognised in the question"
    elif any(m not in months_available for m in it.months):
        it.reason = (f"the data only covers {months_available[0]} to {months_available[-1]}" if months_available
                     else "no months loaded")
    elif re.search(r"\broute\s+#?\d+\b", ql) and not it.routes:
        it.reason = "that route is not one of the pilot routes"
    elif it.kind is None:
        it.reason = "the question did not match a ranking, comparison, trend or value template"
    return it


def to_sql(it: Intent, latest_month: str) -> tuple[str, dict]:
    col = it.metric
    month = it.months[0] if it.months else latest_month
    base = f"FROM v_route_timeband_metrics WHERE day_type = '{it.day_type}' AND time_band = '{it.band}'"
    rl = ", ".join(f"'{r}'" for r in it.routes)
    if it.kind == "ranking":
        hiw = HIGHER_IS_WORSE[col]
        order = "DESC" if it.worst == hiw else "ASC"
        n = it.n or 3
        sql = (f"SELECT route_id, month, {col} {base} AND month = '{month}' AND {col} IS NOT NULL "
               f"ORDER BY {col} {order}, route_id LIMIT {n}")
        rng = (month, month)
    elif it.kind == "comparison":
        months = it.months or [month]
        ml = ", ".join(f"'{m}'" for m in months)
        sql = f"SELECT route_id, month, {col} {base} AND month IN ({ml}) AND route_id IN ({rl}) ORDER BY month, route_id"
        rng = (months[0], months[-1])
    elif it.kind == "trend":
        cond = f" AND route_id IN ({rl})" if it.routes else ""
        mcond = f" AND month BETWEEN '{it.months[0]}' AND '{it.months[-1]}'" if len(it.months) >= 2 else ""
        if it.routes:
            sql = f"SELECT route_id, month, {col} {base}{cond}{mcond} ORDER BY route_id, month"
        else:
            agg = ("sum(missed_trips) / sum(scheduled_trips)" if col == "missed_trip_rate" else f"avg({col})")
            sql = f"SELECT month, {agg} AS {col} {base}{mcond} GROUP BY month ORDER BY month"
        rng = (it.months[0], it.months[-1]) if len(it.months) >= 2 else ("2025-07", latest_month)
    elif it.kind == "count":
        cond = f" AND route_id IN ({rl})" if it.routes else ""
        sql = (f"SELECT route_id, month, missed_trips, scheduled_trips, unknown_route_days {base} AND month = '{month}'"
               f"{cond} ORDER BY missed_trips DESC, route_id")
        rng = (month, month)
    else:  # value
        sql = f"SELECT route_id, month, {col} {base} AND month = '{month}' AND route_id IN ({rl}) ORDER BY route_id"
        rng = (month, month)
    return sql, {"start_month": rng[0], "end_month": rng[1]}


def fmt(col: str, v) -> str:
    if v is None:
        return "n/a"
    if col in ("missed_trip_rate", "on_time_performance", "headway_adherence", "bunching_rate"):
        return f"{100 * float(v):.1f}%"
    if col == "excess_wait_time_s":
        return f"{float(v):.0f} s"
    return str(v)


def route_label(r: str) -> str:
    return {"741": "SL1 (741)", "743": "SL3 (743)"}.get(r, f"Route {r}")
