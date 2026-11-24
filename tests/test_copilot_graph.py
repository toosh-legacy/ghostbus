"""LangGraph copilot, MCP server contracts, cost guard, prompt versioning, RAG and the daily brief.
All run on the fake chat model (conftest sets GB_LLM=fake); no network."""

from __future__ import annotations

import asyncio
import json

import pytest
from gbdata.analytics import db_path

needs_db = pytest.mark.skipif(not db_path().exists(), reason="run `make data` first")


# --------------------------------------------------------------------------- MCP contract tests
def _mcp(coro_fn):
    from gbcopilot.mcp_server import mcp
    from mcp.shared.memory import create_connected_server_and_client_session

    async def go():
        async with create_connected_server_and_client_session(mcp) as session:
            return await coro_fn(session)

    return asyncio.run(go())


def _payload(res) -> dict:
    assert not res.isError, res.content
    return res.structuredContent if res.structuredContent else json.loads(res.content[0].text)


@needs_db
def test_mcp_lists_exactly_the_read_only_tools():
    async def f(s):
        return (await s.list_tools()).tools

    tools = {t.name: t for t in _mcp(f)}
    assert set(tools) == {"get_route_metrics", "get_missed_trip_rates", "compare_routes", "get_definition"}
    for t in tools.values():
        assert t.annotations.readOnlyHint is True and t.annotations.openWorldHint is False
        assert t.description and t.inputSchema["type"] == "object"
    assert set(tools["compare_routes"].inputSchema["required"]) == {"route_ids", "metric", "month"}
    metric_enum = tools["compare_routes"].inputSchema["properties"]["metric"]
    assert "missed_trip_rate" in json.dumps(metric_enum)


@needs_db
@pytest.mark.parametrize("tool,args,cols,n_min", [
    ("get_route_metrics", {"route_id": "1", "metric": "bunching_rate", "month": "2025-12"},
     ["route_id", "month", "bunching_rate"], 1),
    ("get_route_metrics", {"route_id": "741", "metric": "on_time_performance"}, ["route_id", "month",
                                                                                 "on_time_performance"], 6),
    ("get_missed_trip_rates", {"month": "2025-12", "limit": 3}, ["route_id", "month", "missed_trip_rate",
                                                                "missed_trips", "scheduled_trips"], 3),
    ("compare_routes", {"route_ids": ["1", "111"], "metric": "on_time_performance", "month": "2025-11",
                        "day_type": "weekday", "time_band": "pm_peak"}, ["route_id", "month", "on_time_performance"], 2),
])
def test_mcp_tool_happy_paths(tool, args, cols, n_min):
    async def f(s):
        return await s.call_tool(tool, args)

    out = _payload(_mcp(f))
    assert out["columns"] == cols and len(out["rows"]) >= n_min
    assert out["sql"].startswith("SELECT * FROM (") and out["sql"].endswith("LIMIT 500")  # validator-regenerated
    assert "CC0" in out["source"]


@needs_db
def test_mcp_missed_trip_ranking_matches_direct_sql():
    from gbcopilot.validator import run_query

    async def f(s):
        return await s.call_tool("get_missed_trip_rates", {"month": "2025-12", "limit": 3})

    out = _payload(_mcp(f))
    ref = run_query("SELECT route_id FROM v_route_timeband_metrics WHERE month='2025-12' AND day_type='all' AND "
                    "time_band='all' ORDER BY missed_trip_rate DESC, route_id LIMIT 3").rows
    assert [r[0] for r in out["rows"]] == [r[0] for r in ref]


@needs_db
def test_mcp_get_definition():
    async def f(s):
        return await s.call_tool("get_definition", {"name": "excess_wait_time_s"})

    out = _payload(_mcp(f))
    assert out["name"] == "excess_wait_time" and out["version"].startswith("metrics-def")


@needs_db
@pytest.mark.parametrize("tool,args", [
    ("get_route_metrics", {"route_id": "1'; DROP TABLE x; --"}),
    ("get_route_metrics", {"route_id": "1", "month": "2025-13"}),
    ("get_route_metrics", {"route_id": "1", "metric": "raw_trip_labels"}),
    ("get_missed_trip_rates", {"month": "2025-12", "route_ids": ["1) OR (1=1"]}),
    ("compare_routes", {"route_ids": ["1"], "metric": "bunching_rate", "month": "2025-12"}),
    ("compare_routes", {"route_ids": ["1", "23"], "metric": "getenv('HOME')", "month": "2025-12"}),
    ("get_definition", {"name": "../../etc/passwd"}),
])
def test_mcp_tools_reject_bad_arguments(tool, args):
    async def f(s):
        return await s.call_tool(tool, args)

    assert _mcp(f).isError


@needs_db
def test_tool_call_normalizer_repairs_model_slips():
    from gbcopilot.graph import normalize_tool_call

    assert normalize_tool_call("compare_routes", {"route_id": "111", "route_ids": ["743"], "metric": "bunching_rate",
                                                  "month": "2025-10"})[1]["route_ids"] == ["111", "743"]
    tool, args = normalize_tool_call("get_missed_trip_rates", {"route_id": "743"})
    assert tool == "get_route_metrics" and args["metric"] == "missed_trip_rate"
    assert normalize_tool_call("compare_routes", {"route_ids": ["1"], "metric": "bunching_rate"})[0] == "get_route_metrics"


@needs_db
def test_langchain_mcp_adapter_host_round_trip():
    from gbcopilot.mcp_client import McpToolError, host

    h = host()
    assert set(h.tools) >= {"get_route_metrics", "compare_routes"}
    out = h.call("compare_routes", {"route_ids": ["111", "743"], "metric": "excess_wait_time_s", "month": "2025-10"})
    assert [r[0] for r in out["rows"]] == ["111", "743"]
    with pytest.raises(McpToolError):
        h.call("get_route_metrics", {"route_id": "x"})


# --------------------------------------------------------------------------- graph behaviour
@needs_db
def test_graph_routes_to_mcp_tool_sql_rag_and_refusal():
    from gbcopilot.agent import answer

    assert answer("Compare on-time performance for route 1 and route 111 in November 2025.")["via"] == "mcp:compare_routes"
    assert answer("Which 2 pilot routes had the highest headway adherence on weekends in September 2025?")["via"] == "sql"
    m = answer("How do you work out excess wait time?")
    assert m["via"] == "rag" and any(c["id"] == "def:excess_wait_time" for c in m["citations"])
    assert "[def:excess_wait_time]" in m["answer"]
    assert answer("Predict the missed-trip rate for route 1 next month.")["refused"]


@needs_db
def test_graph_retries_after_validator_rejection(monkeypatch):
    from gbcopilot import llm

    calls = []
    good = llm.FAKE_HANDLERS["sql"]

    def flaky(inputs):
        calls.append(1)
        return "SELECT * FROM raw_trip_labels" if len(calls) == 1 else good(inputs)

    monkeypatch.setitem(llm.FAKE_HANDLERS, "sql", flaky)
    from gbcopilot.agent import answer

    a = answer("Which 2 pilot routes had the highest headway adherence on weekends in September 2025?")
    assert not a["refused"] and len(calls) == 2


@needs_db
def test_graph_refuses_after_two_retries(monkeypatch):
    from gbcopilot import llm

    calls = []
    monkeypatch.setitem(llm.FAKE_HANDLERS, "sql", lambda i: calls.append(1) or "SELECT * FROM read_csv('/etc/passwd')")
    from gbcopilot.agent import answer

    a = answer("Which 2 pilot routes had the highest headway adherence on weekends in September 2025?")
    assert a["refused"] and len(calls) == 3 and "read_csv" in a["rejected_sql"].lower()


@needs_db
def test_tool_failure_falls_back_to_sql(monkeypatch):
    from gbcopilot import llm

    monkeypatch.setitem(llm.FAKE_HANDLERS, "plan", lambda i: json.dumps(
        {"action": "tool", "tool": "compare_routes", "args": {"route_ids": ["1", "111"], "metric": "bunching_rate",
                                                               "month": "2025-13"}}))
    from gbcopilot.agent import answer

    a = answer("Compare on-time performance for route 1 and route 111 in November 2025.")
    assert not a["refused"] and a["via"] == "sql"


@needs_db
def test_sqlite_checkpointer_keeps_thread_memory():
    import uuid

    from gbcopilot.agent import answer
    from gbcopilot.graph import graph

    tid = f"test-thread-{uuid.uuid4().hex}"
    first = answer("What was the bunching rate of route 39 in the evening in December 2025?", thread_id=tid)
    follow = answer("what about route 23?", thread_id=tid)
    assert follow["table"]["rows"][0][0] == "23" and first["table"]["rows"][0][0] == "39"
    state = graph().get_state({"configurable": {"thread_id": tid}})
    assert len(state.values["messages"]) == 4  # two questions, two answers


@needs_db
def test_stream_yields_node_progress_then_answer():
    from gbcopilot.agent import stream

    evs = list(stream("How did bunching on route 66 change by month?"))
    nodes = [e["node"] for e in evs if e["type"] == "progress"]
    assert nodes[:3] == ["contextualize", "classify", "plan"] and "call_tool" in nodes
    assert evs[-1]["type"] == "answer" and not evs[-1]["refused"]


# --------------------------------------------------------------------------- cost guard, call log, models
def test_cost_guard_hard_stop(tmp_path, monkeypatch):
    from gbcopilot import llm

    monkeypatch.setenv("GB_SESSION", "cost-test")
    ledger = llm.Ledger(tmp_path)
    ledger.charge(2.99, {"model": "gpt-4o"})
    ledger.check()  # under the $3 limit
    ledger.charge(0.02, {"model": "gpt-4o"})
    with pytest.raises(llm.CostLimitExceeded):
        ledger.check()
    cb = llm.GuardCallback(ledger)
    with pytest.raises(llm.CostLimitExceeded):
        cb.on_chat_model_start({}, [[]], run_id="r1", metadata={"prompt": "sql"})


def test_call_log_records_prompt_version_model_and_cost(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from gbcopilot import llm

    monkeypatch.setenv("GB_SESSION", "log-test")
    cb = llm.GuardCallback(llm.Ledger(tmp_path))
    cb.on_chat_model_start({}, [[]], run_id="r1", invocation_params={"model": "gpt-4o-mini"},
                           metadata={"role": "sql", "prompt": "sql", "prompt_version": 1, "prompt_sha": "abc"})
    cb.on_llm_end(SimpleNamespace(llm_output={"token_usage": {"prompt_tokens": 1000, "completion_tokens": 200},
                                              "model_name": "gpt-4o-mini"}, generations=[]), run_id="r1")
    rec = json.loads((tmp_path / "llm" / "calls.jsonl").read_text().splitlines()[-1])
    assert rec["prompt"] == "sql" and rec["prompt_version"] == 1 and rec["model"] == "gpt-4o-mini"
    assert rec["usd"] == pytest.approx((1000 * 0.15 + 200 * 0.60) / 1e6)


def test_model_ids_checked_against_models_list(monkeypatch):
    import openai
    from gbcopilot import llm

    class FakeClient:
        def __init__(self, **kw):
            self.models = self

        def list(self):
            from types import SimpleNamespace

            return [SimpleNamespace(id=i) for i in ("gpt-4o-mini", "text-embedding-3-small")]

    monkeypatch.setattr(openai, "OpenAI", FakeClient)
    llm.verify_models.cache_clear()
    with pytest.raises(llm.ModelConfigError, match="gpt-4o"):
        llm.verify_models()
    llm.verify_models.cache_clear()


def test_tiers_cheap_for_routing_strong_for_answers():
    from gbcopilot import llm

    assert llm.model_for("sql") == llm.model_for("classify") == llm.config()["tiers"]["cheap"]["model"]
    assert llm.model_for("answer") == llm.model_for("judge") == llm.config()["tiers"]["strong"]["model"]


def test_prompts_are_versioned_and_eval_locked():
    from gbcopilot.prompting import all_prompts, unlocked

    ps = all_prompts()
    assert {"classify", "plan", "sql", "answer", "methodology", "rerank", "brief_map", "brief_reduce", "judge"} <= set(ps)
    assert all(p.version >= 1 and len(p.sha) == 16 for p in ps.values())
    assert unlocked() == [], ("prompts changed without passing the eval suite; run "
                              "`uv run python -m gbevals.run --fast --lock-prompts`")


# --------------------------------------------------------------------------- RAG + brief
def test_retrieval_metrics_math():
    from gbevals.retrieval_ablation import metrics

    m = metrics([["a", "b", "c"], ["x", "y", "a"]], [["a"], ["a"]])
    assert m["recall@1"] == 0.5 and m["recall@3"] == 1.0 and m["mrr@10"] == pytest.approx((1 + 1 / 3) / 2)


def test_hybrid_retrieval_finds_definitions():
    from gbcopilot import rag
    from gbevals.rag_gold import GOLD
    from gbevals.retrieval_ablation import metrics

    r = rag.retriever()
    m = metrics([[h.id for h in r.hybrid_search(q)] for q, _ in GOLD], [g for _, g in GOLD])
    assert m["recall@3"] >= 0.75


@pytest.mark.skipif(not __import__("gbcopilot.rag", fromlist=["x"]).cross_encoder_available(),
                    reason="install the rerank extra (sentence-transformers)")
@pytest.mark.slow
def test_cross_encoder_rerank_improves_recall_at_1():
    from gbcopilot import rag
    from gbevals.rag_gold import GOLD
    from gbevals.retrieval_ablation import metrics

    r = rag.retriever()
    gold = [g for _, g in GOLD]
    base = metrics([[h.id for h in r.hybrid_search(q)] for q, _ in GOLD], gold)
    rr = metrics([[h.id for h in rag.cross_encoder_rerank(q, r.hybrid_search(q))] for q, _ in GOLD], gold)
    assert rr["recall@1"] > base["recall@1"]


@needs_db
def test_daily_brief_map_reduce_with_citations():
    from gbcopilot.brief import daily_brief

    b = daily_brief("2025-12")
    assert len(b["route_summaries"]) == len(b["routes"]) >= 5  # one Send branch per route
    cited = set(__import__("re").findall(r"\[(\d+:20\d\d-\d\d):", b["brief"]))
    assert cited and cited <= set(b["sources"])


@needs_db
def test_api_copilot_job_never_blocks_and_streams_progress():
    from fastapi.testclient import TestClient
    from gbapi.app import create_app

    with TestClient(create_app(pipeline_mode="off")) as c:
        r = c.post("/v1/copilot/jobs", json={"question": "How did bunching on route 66 change by month?"})
        assert r.status_code == 202 and r.json()["data"]["status"] == "running"
        jid = r.json()["data"]["job_id"]
        with c.stream("GET", f"/v1/jobs/{jid}/events") as s:
            events = [line.split(":", 1)[1].strip() for line in s.iter_lines() if line.startswith("event:")]
        assert events[0] == "progress" and events[-2:] == ["answer", "end"]
        assert c.get(f"/v1/jobs/{jid}").json()["data"]["result"]["via"] == "mcp:get_route_metrics"
        assert c.post("/v1/copilot/jobs", json={"question": " "}).status_code == 422
        assert c.get("/v1/jobs/nope").status_code == 404
