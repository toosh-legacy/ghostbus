"""Generate the copilot gold set: 40 answerable questions with expected answers computed directly from the curated
metrics with pandas (independently of the copilot's SQL), plus 10 unanswerable questions. Never typed by hand.

Run: uv run python -m gbevals.gold  (writes evals/gold/copilot_gold.json)
"""

from __future__ import annotations

import json
import random

import pandas as pd
from gbcore.settings import REPO_ROOT, get_settings

OUT = REPO_ROOT / "evals" / "gold" / "copilot_gold.json"
MONTH_NAMES = {1: "January", 2: "February", 3: "March", 4: "April", 5: "May", 6: "June", 7: "July", 8: "August",
               9: "September", 10: "October", 11: "November", 12: "December"}
METRICS = {  # column -> (phrase, higher_is_worse)
    "missed_trip_rate": ("missed-trip rate", True),
    "on_time_performance": ("on-time performance", False),
    "headway_adherence": ("headway adherence", False),
    "bunching_rate": ("bunching rate", True),
    "excess_wait_time_s": ("excess wait time", True),
}
BANDS = {"all": "", "am_peak": " in the AM peak", "midday": " at midday", "pm_peak": " in the PM peak",
         "evening": " in the evening"}
DAYS = {"all": "", "weekday": " on weekdays", "weekend": " on weekends"}
UNANSWERABLE = [
    "What will the weather be in Boston tomorrow?",
    "Which bus driver had the most complaints?",
    "How many passengers rode route 111 in December 2025?",
    "What was the on-time performance of the Red Line last month?",
    "Which routes had the highest missed-trip rate in March 2024?",
    "Predict the missed-trip rate for route 1 next month.",
    "What is the fare for route 28?",
    "Why is route 66 so unreliable?",
    "Tell me a joke about buses.",
    "What was the missed-trip rate for route 999 in December 2025?",
]


def rname(r: str) -> str:
    return {"741": "SL1", "743": "SL3"}.get(r, f"route {r}")


def month_text(m: str) -> str:
    return f"{MONTH_NAMES[int(m[5:])]} {m[:4]}"


def generate(seed: int = 2025) -> dict:
    rng = random.Random(seed)
    m = pd.read_parquet(get_settings().lake_dir / "curated" / "metrics" / "route_metrics.parquet")
    months = sorted(m["month"].unique())
    routes = sorted(m["route_id"].unique())
    items = []

    def sub(month, band, day):
        return m[(m["month"] == month) & (m["time_band"] == band) & (m["day_type"] == day)]

    while len([i for i in items if i["kind"] == "ranking"]) < 16:
        col = rng.choice(list(METRICS))
        phrase, hiw = METRICS[col]
        month, band, day = rng.choice(months), rng.choice(list(BANDS)), rng.choice(list(DAYS))
        n = rng.choice([2, 3, 5])
        worst = rng.random() < 0.6
        adj = ("highest" if worst else "lowest") if hiw else ("lowest" if worst else "highest")
        s = sub(month, band, day).dropna(subset=[col])
        asc = adj == "lowest"
        s = s.sort_values([col, "route_id"], ascending=[asc, True])
        if len(s) < n or s[col].iloc[n - 1:n + 1].nunique() < min(2, len(s.iloc[n - 1:n + 1])):
            continue  # skip ties at the cut
        q = f"Which {n} pilot routes had the {adj} {phrase}{DAYS[day]}{BANDS[band]} in {month_text(month)}?"
        if any(i["question"] == q for i in items):
            continue
        items.append({"kind": "ranking", "question": q, "metric": col,
                      "expected": {"routes": s["route_id"].head(n).tolist(), "values": s[col].head(n).tolist()}})
    def nn(v) -> bool:
        return v is not None and not pd.isna(v)

    while len([i for i in items if i["kind"] == "comparison"]) < 8:
        col = rng.choice(list(METRICS))
        a, b = rng.sample(routes, 2)
        month = rng.choice(months)
        s = sub(month, "all", "all").set_index("route_id")[col]
        if not (nn(s.get(a)) and nn(s.get(b))):
            continue
        items.append({"kind": "comparison", "question": f"Compare {METRICS[col][0]} for {rname(a)} and {rname(b)} in {month_text(month)}.",
                      "metric": col, "expected": {"values": {a: s.get(a), b: s.get(b)}}})
    while len([i for i in items if i["kind"] == "trend"]) < 8:
        col = rng.choice(list(METRICS))
        r = rng.choice(routes)
        s = m[(m["route_id"] == r) & (m["time_band"] == "all") & (m["day_type"] == "all")].set_index("month")[col]
        if s.isna().any():
            continue
        items.append({"kind": "trend", "question": f"How did {METRICS[col][0]} on {rname(r)} change by month?",
                      "metric": col, "expected": {"route": r, "values": s.to_dict()}})
    while len([i for i in items if i["kind"] == "value"]) < 8:
        col = rng.choice(list(METRICS))
        r = rng.choice(routes)
        month, band = rng.choice(months), rng.choice(list(BANDS))
        v = sub(month, band, "all").set_index("route_id")[col].get(r)
        if not nn(v):
            continue
        items.append({"kind": "value", "question": f"What was the {METRICS[col][0]} of {rname(r)}{BANDS[band]} in {month_text(month)}?",
                      "metric": col, "expected": {"values": {r: v}}})
    gold = {"generated_from": "lake/curated/metrics/route_metrics.parquet", "seed": seed, "answerable": items,
            "unanswerable": UNANSWERABLE}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(gold, indent=2, default=lambda x: None if pd.isna(x) else float(x)), encoding="utf-8")
    return gold


def _close(a, b) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return abs(float(a) - float(b)) <= 1e-9 * max(1.0, abs(float(b)))


def score_item(item: dict, ans: dict) -> bool:
    if ans.get("refused") or not ans.get("table"):
        return False
    cols = ans["table"]["columns"]
    rows = ans["table"]["rows"]
    col = item["metric"]
    if col not in cols:
        return False
    ci = cols.index(col)
    ri = cols.index("route_id") if "route_id" in cols else None
    mi = cols.index("month") if "month" in cols else None
    exp = item["expected"]
    if item["kind"] == "ranking":
        return [r[ri] for r in rows] == exp["routes"] and all(_close(r[ci], v) for r, v in zip(rows, exp["values"]))
    if item["kind"] == "trend":
        got = {r[mi]: r[ci] for r in rows if ri is None or r[ri] == exp["route"]}
        return set(got) == set(exp["values"]) and all(_close(got[k], v) for k, v in exp["values"].items())
    got = {r[ri]: r[ci] for r in rows}
    return set(got) == set(exp["values"]) and all(_close(got[k], v) for k, v in exp["values"].items())


if __name__ == "__main__":
    g = generate()
    print(len(g["answerable"]), "answerable +", len(g["unanswerable"]), "unanswerable ->", OUT)
