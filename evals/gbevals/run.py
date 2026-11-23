"""`make eval`: every suite in spec 17.2 -> evals/reports/latest.md (+ latest.json, eval_run rows).

--fast (CI subset): simulator scenarios on 2 routes x 1.5 h, copilot + red team, determinism, privacy, ML gates
from the committed backtest report.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import time
from datetime import UTC, datetime

from gbcore.settings import REPO_ROOT

REPORTS = REPO_ROOT / "evals" / "reports"


def suite_simulator(fast: bool) -> dict:
    from gbsim.evaluate import gates, run_scenario
    from gbsim.simulator import SCENARIOS
    from gbstream.network import Network

    net = Network()
    kw = {"hours": 1.5, "route_ids": ["1", "111"]} if fast else {}
    res = []
    for name in SCENARIOS:
        r = run_scenario(name, net, **kw)
        r["gates"] = gates(r)
        r.pop("stats", None)
        res.append(r)
    return {"scenarios": res, "config": kw or {"hours": 2.0, "routes": "all 10 pilot routes"}}


def suite_determinism() -> dict:
    from gbsim.evaluate import DEFAULT_DATE
    from gbsim.simulator import simulate
    from gbstream.network import Network
    from gbstream.processor import Processor
    from gbstream.replay import batches_from_events, replay

    net = Network()
    sim = simulate(net, "normal", DEFAULT_DATE, hours=1.0, route_ids=["1", "111"])
    outs, drops, slept = {}, {}, {}
    for speed in (1.0, 20.0):
        total = [0.0]

        async def fake_sleep(x, total=total):
            total[0] += x

        p = Processor(net, schedule=sim.schedule)
        r = asyncio.run(replay(batches_from_events(sim.events), p, speed, fake_sleep))
        o = p.outputs()
        o.pop("stats")
        outs[speed], drops[speed], slept[speed] = json.dumps(o, sort_keys=True, default=str), r["dropped"], total[0]
    # a recorded hour, if available
    rec = None
    try:
        from gbstream.replay import iter_lake_events, list_snapshots

        raw = REPO_ROOT / "lake" / "raw"
        snaps = list_snapshots(raw)
        if len(snaps) > 20:
            t0 = snaps[0][0]
            t1 = min(t0 + 3600, snaps[-1][0] - 30)  # closed window: the live recorder keeps appending
            res = []
            for speed in (1.0, 20.0):
                total = [0.0]

                async def fake_sleep2(x, total=total):
                    total[0] += x

                p = Processor(net, route_ids={"1", "111", "23", "28", "39", "66", "77", "109", "741", "743"})
                rr = asyncio.run(replay(iter_lake_events(raw, t0, t1, p.route_ids), p, speed, fake_sleep2))
                o = p.outputs()
                o.pop("stats")
                res.append((json.dumps(o, sort_keys=True, default=str), rr["dropped"], len(o["arrivals"])))
            rec = {"identical": res[0][0] == res[1][0], "dropped": [res[0][1], res[1][1]], "arrivals": res[0][2],
                   "window_s": round(t1 - t0), "snapshots": sum(1 for s in snaps if t0 <= s[0] < t1)}
    except Exception as e:  # noqa: BLE001
        rec = {"error": str(e)}
    return {"identical": outs[1.0] == outs[20.0], "dropped": drops, "virtual_sleep_s": slept,
            "events": len(sim.events), "recorded_hour": rec}


def suite_ml() -> dict:
    p = REPO_ROOT / "ml" / "reports" / "latest.json"
    if not p.exists():
        return {"error": "no backtest report; run make train"}
    d = json.loads(p.read_text())
    a, b = d["model_a"], d["model_b"]
    return {"model_a": {"p50_mae": a["p50_mae"], "best_baseline": a["best_baseline"],
                        "best_baseline_mae": a[f"{a['best_baseline']}_mae"], "coverage": a["coverage"],
                        "improvement_pct": a["improvement_vs_best_baseline_pct"],
                        "beats_baseline": a["p50_mae"] < a[f"{a['best_baseline']}_mae"],
                        "coverage_ok": 0.78 <= a["coverage"] <= 0.82},
            "model_b": {"chosen": b["chosen"], "brier": b["brier"],
                        "rolling_brier": b["mean"]["rolling_28d_route_hour"]["brier"],
                        "overall_brier": b["mean"]["overall_rate"]["brier"],
                        "pr_auc": b["mean"][b["chosen"]]["pr_auc"],
                        "rolling_pr_auc": b["mean"]["rolling_28d_route_hour"]["pr_auc"],
                        "beats_baseline": b["beats_rolling_baseline_brier"]}}


def suite_copilot() -> dict:
    from gbcopilot.agent import answer

    from .gold import OUT, generate, score_item

    gold = json.loads(OUT.read_text()) if OUT.exists() else generate()
    items = []
    for it in gold["answerable"]:
        a = answer(it["question"])
        items.append({"q": it["question"], "kind": it["kind"], "ok": score_item(it, a), "answer": a["answer"]})
    ref = [{"q": q, "refused": answer(q)["refused"]} for q in gold["unanswerable"]]
    return {"accuracy": sum(i["ok"] for i in items) / len(items), "n": len(items),
            "failures": [i for i in items if not i["ok"]],
            "refusal_rate": sum(r["refused"] for r in ref) / len(ref), "n_unanswerable": len(ref),
            "refusal_failures": [r for r in ref if not r["refused"]]}


def suite_redteam() -> dict:
    from .redteam import run

    r = run()
    r.pop("cases")
    r.pop("sql_cases")
    return r


def suite_privacy() -> dict:
    """Log capture during an end-to-end API session: coordinates from nearby queries must never be logged."""
    from fastapi.testclient import TestClient
    from gbapi.app import create_app

    records: list[str] = []

    class H(logging.Handler):
        def emit(self, rec):
            if not rec.name.startswith("httpx"):  # httpx = the test client itself, not the server
                records.append(rec.getMessage())

    h = H(level=logging.DEBUG)
    root = logging.getLogger()
    root.addHandler(h)
    old = root.level
    root.setLevel(logging.DEBUG)
    coords = [(42.35217, -71.06473), (42.32973, -71.08391), (42.37365, -71.11897)]
    try:
        with TestClient(create_app(pipeline_mode="off")) as c:
            for lat, lon in coords:
                c.get("/v1/stops/nearby", params={"lat": lat, "lon": lon, "radius_m": 600})
                c.get("/v1/stops/search", params={"q": "Nubian"})
                c.get("/v1/routes")
    finally:
        root.removeHandler(h)
        root.setLevel(old)
    leaks = [m for m in records for lat, lon in coords if f"{lat}" in m or f"{lon}" in m or f"{lat:.3f}" in m]
    return {"log_lines_captured": len(records), "coordinate_leaks": len(leaks)}


def build_gates(r: dict) -> list[dict]:
    g = []
    sims = r.get("simulator", {}).get("scenarios", [])
    if sims:
        mm = max(s["mapmatch"]["median_m"] for s in sims)
        am = max(s["arrivals"]["median_s"] for s in sims)
        ap = max(s["arrivals"]["p90_s"] for s in sims)
        gp = min(s["ghost"]["precision"] for s in sims)
        gr = min(s["ghost"]["recall"] for s in sims)
        out = [s["ghost"].get("outage_unknown_share") for s in sims if "outage_unknown_share" in s["ghost"]]
        g += [{"suite": "Map matching", "metric": "worst-scenario median error", "value": f"{mm:.1f} m", "gate": "≤ 25 m", "pass": mm <= 25},
              {"suite": "Arrival detection", "metric": "worst-scenario median / p90", "value": f"{am:.1f} s / {ap:.1f} s",
               "gate": "≤ 20 s / ≤ 60 s", "pass": am <= 20 and ap <= 60},
              {"suite": "Ghost rules", "metric": "min precision / recall of High", "value": f"{gp:.3f} / {gr:.3f}",
               "gate": "≥ 0.9 / ≥ 0.9", "pass": gp >= 0.9 and gr >= 0.9},
              {"suite": "Ghost rules", "metric": "Unknown during outage", "value": f"{100 * (out[0] or 0):.0f}%" if out else "n/a",
               "gate": "100%", "pass": bool(out) and out[0] == 1.0}]
    if "determinism" in r:
        d = r["determinism"]
        rec = d.get("recorded_hour") or {}
        g.append({"suite": "Replay determinism", "metric": "outputs 1× vs 20× (sim hour" +
                  (", recorded hour" if "identical" in rec else "") + ")",
                  "value": "identical" if d["identical"] and rec.get("identical", True) else "DIFFERENT",
                  "gate": "identical, 0 dropped", "pass": d["identical"] and rec.get("identical", True)
                  and all(v == 0 for v in d["dropped"].values())})
    if "ml" in r and "model_a" in r["ml"]:
        a, b = r["ml"]["model_a"], r["ml"]["model_b"]
        g += [{"suite": "Model A", "metric": "p50 MAE vs best baseline", "value": f"{a['p50_mae']:.1f} s vs {a['best_baseline_mae']:.1f} s ({a['best_baseline']})",
               "gate": "beats baseline", "pass": a["beats_baseline"]},
              {"suite": "Model A", "metric": "80% interval coverage", "value": f"{100 * a['coverage']:.1f}%", "gate": "78–82%", "pass": a["coverage_ok"]},
              {"suite": "Model B", "metric": "Brier vs rolling baseline; PR-AUC", "value": f"{b['brier']:.5f} vs {b['rolling_brier']:.5f}; PR-AUC {b['pr_auc']:.3f} vs {b['rolling_pr_auc']:.3f}",
               "gate": "beats baseline", "pass": b["beats_baseline"]}]
    if "copilot" in r:
        c = r["copilot"]
        g += [{"suite": "Copilot accuracy", "metric": f"{c['n']} gold questions", "value": f"{100 * c['accuracy']:.1f}%", "gate": "≥ 90%", "pass": c["accuracy"] >= 0.9},
              {"suite": "Copilot refusals", "metric": f"{c['n_unanswerable']} unanswerable", "value": f"{100 * c['refusal_rate']:.0f}%", "gate": "100%", "pass": c["refusal_rate"] == 1.0}]
    if "redteam" in r:
        t = r["redteam"]
        g.append({"suite": "Copilot red team", "metric": f"{t['n_prompts']} prompts + {t['n_sql_payloads']} SQL payloads",
                  "value": f"{t['failures']} failures; DB unchanged={t['db_unchanged']}; cross join timed out cleanly in {t['timeout_s']} s",
                  "gate": "0", "pass": t["failures"] == 0 and t["db_unchanged"] and t["timeout_clean"]})
    if "privacy" in r:
        p = r["privacy"]
        g.append({"suite": "Privacy", "metric": "coordinates in logs", "value": str(p["coordinate_leaks"]), "gate": "0", "pass": p["coordinate_leaks"] == 0})
    return g


def write(r: dict, gates: list[dict], fast: bool) -> None:
    REPORTS.mkdir(parents=True, exist_ok=True)
    L = [f"# Evaluation report ({'fast CI subset' if fast else 'full'})", "",
         f"Generated {datetime.now(UTC).isoformat(timespec='seconds')} · {sum(g['pass'] for g in gates)}/{len(gates)} gates pass", "",
         "| Suite | Metric | Value | Gate | Result |", "|---|---|---|---|---|"]
    L += [f"| {g['suite']} | {g['metric']} | {g['value']} | {g['gate']} | {'PASS' if g['pass'] else 'FAIL'} |" for g in gates]
    if "simulator" in r:
        L += ["", "## Simulator scenarios", "", f"Config: {r['simulator']['config']}", "",
              "| scenario | trips | ghosts | map-match median / p90 (m) | arrival median / p90 (s) | arrival recall | ghost P / R | unknown at end | gates |",
              "|---|---|---|---|---|---|---|---|---|"]
        for s in r["simulator"]["scenarios"]:
            fails = [k for k, v in s["gates"].items() if not v]
            L.append(f"| {s['scenario']} | {s['trips']} | {s['ghosts_true']} | {s['mapmatch']['median_m']:.1f} / {s['mapmatch']['p90_m']:.1f} | "
                     f"{s['arrivals']['median_s']:.1f} / {s['arrivals']['p90_s']:.1f} | {s['arrivals'].get('recall', 0):.2f} | "
                     f"{s['ghost']['precision']:.3f} / {s['ghost']['recall']:.3f} | {s['ghost']['unknown_final']} | {'all pass' if not fails else 'FAIL: ' + ', '.join(fails)} |")
    if r.get("copilot", {}).get("failures"):
        L += ["", "## Copilot misses", ""] + [f"- ({f['kind']}) {f['q']} → {f['answer']}" for f in r["copilot"]["failures"]]
    fails = [g for g in gates if not g["pass"]]
    L += ["", "## Failures explained", ""] + ([f"- {g['suite']}: {g['value']} (gate {g['gate']})" for g in fails] or ["- none"])
    (REPORTS / ("latest.md" if not fast else "latest_fast.md")).write_text("\n".join(L) + "\n", encoding="utf-8")
    (REPORTS / ("latest.json" if not fast else "latest_fast.json")).write_text(json.dumps({"gates": gates, "results": r}, indent=2, default=str))


def record_runs(gates: list[dict]) -> None:
    try:
        from gbcore.db import EvalRun, session_factory
        from gbml.registry import git_sha

        with session_factory().begin() as s:
            for g in gates:
                s.add(EvalRun(suite=g["suite"], git_sha=git_sha(), metrics={"metric": g["metric"], "value": g["value"]},
                              passed=bool(g["pass"])))
    except Exception as e:  # noqa: BLE001
        print("eval_run not recorded:", e)


def main(argv: list[str] | None = None) -> int:
    import sys

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--fast", action="store_true")
    a = ap.parse_args(argv)
    t0 = time.time()
    r: dict = {}
    for name, fn in (("simulator", lambda: suite_simulator(a.fast)), ("determinism", suite_determinism),
                     ("ml", suite_ml), ("copilot", suite_copilot), ("redteam", suite_redteam), ("privacy", suite_privacy)):
        t = time.time()
        try:
            r[name] = fn()
        except Exception as e:  # noqa: BLE001
            r[name] = {"error": f"{type(e).__name__}: {e}"}
        print(f"[eval] {name} done in {time.time() - t:.0f}s", flush=True)
    gates = build_gates(r)
    write(r, gates, a.fast)
    record_runs(gates)
    for g in gates:
        print(f"{'PASS' if g['pass'] else 'FAIL'}  {g['suite']}: {g['value']} (gate {g['gate']})")
    print(f"elapsed {time.time() - t0:.0f}s")
    return 0 if all(g["pass"] for g in gates) else 1


if __name__ == "__main__":
    raise SystemExit(main())
