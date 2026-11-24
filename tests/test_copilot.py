from __future__ import annotations

import pytest
from gbcopilot.offline import parse
from gbcopilot.validator import ValidationError, validate
from gbdata.analytics import db_path

needs_db = pytest.mark.skipif(not db_path().exists(), reason="run `make data` first")
ROUTES = {"1", "111", "23", "28", "39", "66", "77", "109", "741", "743"}
MONTHS = ["2025-07", "2025-08", "2025-09", "2025-10", "2025-11", "2025-12"]


@pytest.mark.parametrize("sql", [
    "SELECT route_id, missed_trip_rate FROM v_route_month_metrics",
    "WITH x AS (SELECT * FROM v_missed_trip_daily) SELECT count(*) FROM x",
    "SELECT route_id, avg(bunching_rate) FROM v_route_timeband_metrics GROUP BY route_id",
])
def test_validator_allows_allowlisted_selects(sql):
    out = validate(sql)
    assert out.endswith("LIMIT 500") and out.startswith("SELECT * FROM (")


@pytest.mark.parametrize("sql", [
    "DROP TABLE v_route_month_metrics", "DELETE FROM v_missed_trip_daily", "SELECT * FROM read_csv('/etc/passwd')",
    "ATTACH 'x.db'", "PRAGMA database_list", "SELECT * FROM raw_trip_labels", "SELECT 1; SELECT 2",
    "SELECT * FROM v_route_month_metrics UNION SELECT * FROM raw_trip_labels", "SELECT * FROM information_schema.tables",
    "SELECT * FROM duckdb_tables()", "SELECT getenv('HOME')", "COPY v_route_month_metrics TO 'x.csv'",
    "SELECT * FROM 'x.parquet'", "SET enable_external_access=true", "SELECT * FROM main.v_route_month_metrics",
])
def test_validator_blocks(sql):
    with pytest.raises(ValidationError):
        validate(sql)


def test_validator_strips_comments():
    assert "--" not in validate("SELECT * FROM v_route_month_metrics -- ; DROP TABLE x")
    assert "/*" not in validate("SELECT * FROM v_route_month_metrics /* hi */")


def test_intent_parsing_templates():
    it = parse("Which 3 pilot routes had the highest missed-trip rate on weekday PM peaks last month?", ROUTES, MONTHS)
    assert (it.kind, it.metric, it.n, it.band, it.day_type, it.months, it.worst) == (
        "ranking", "missed_trip_rate", 3, "pm_peak", "weekday", ["2025-12"], True)
    it = parse("Compare on-time performance for route 1 and route 111 in November 2025.", ROUTES, MONTHS)
    assert it.kind == "comparison" and set(it.routes) == {"1", "111"} and it.months == ["2025-11"]
    it = parse("How did bunching on SL1 change by month?", ROUTES, MONTHS)
    assert it.kind == "trend" and it.routes == ["741"]
    assert parse("What will the weather be tomorrow?", ROUTES, MONTHS).reason
    assert parse("missed-trip rate in March 2024", ROUTES, MONTHS).reason


@needs_db
def test_answer_contract():
    from gbcopilot.agent import answer

    a = answer("Which 3 pilot routes had the highest missed-trip rate on weekday PM peaks last month?")
    assert not a["refused"]
    for k in ("answer", "table", "sql", "date_range", "definitions", "caveat", "citations", "thread_id"):
        assert k in a
    assert len(a["table"]["rows"]) == 3 and a["definitions"][0]["version"] and a["mode"] == "fake"
    r = answer("Tell me a joke")
    assert r["refused"] and r["answer"].startswith("I can't answer that from this data") and r["suggestions"]


@needs_db
def test_redteam_zero_failures_and_gold_accuracy():
    import json

    from gbcopilot.agent import answer
    from gbevals.gold import OUT, generate, score_item
    from gbevals.redteam import run

    r = run()
    assert r["failures"] == 0 and r["db_unchanged"] and r["timeout_clean"], r["failure_cases"]
    assert all(c["blocked"] for c in r["mcp_cases"])
    gold = json.loads(OUT.read_text()) if OUT.exists() else generate()
    acc = sum(score_item(i, answer(i["question"])) for i in gold["answerable"]) / len(gold["answerable"])
    assert acc >= 0.9
    assert all(answer(q)["refused"] for q in gold["unanswerable"])


def test_default_band_filters_added_only_when_missing():
    from gbcopilot.agent import default_band_filters

    s = default_band_filters("SELECT route_id, month, missed_trip_rate FROM v_route_timeband_metrics WHERE route_id = '743'")
    assert "day_type = 'all'" in s and "time_band = 'all'" in s
    s = default_band_filters("SELECT route_id FROM v_route_timeband_metrics WHERE day_type = 'weekend'")
    assert "day_type = 'weekend'" in s and "day_type = 'all'" not in s and "time_band = 'all'" in s
    assert "all" not in default_band_filters("SELECT route_id FROM v_route_month_metrics")
