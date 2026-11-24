"""GhostBus reliability MCP server (official Python MCP SDK, FastMCP).

Read-only tools over the allowlisted reliability views: get_route_metrics, get_missed_trip_rates, compare_routes,
get_definition. Arguments are checked against fixed vocabularies (metric names, day types, time bands, route id and
month formats) and the SQL they build goes through the same sqlglot validator, read-only DuckDB connection,
500-row limit and 5 s timeout as the copilot. No tool writes, fetches URLs or reads files.

Run (stdio): uv run python -m gbcopilot.mcp_server
"""

from __future__ import annotations

import re
from typing import Literal

from gbcore.definitions import get_definition as _get_definition
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

from .validator import run_query

Metric = Literal["on_time_performance", "headway_adherence", "excess_wait_time_s", "bunching_rate", "missed_trip_rate"]
DayType = Literal["all", "weekday", "weekend"]
TimeBand = Literal["all", "am_peak", "midday", "pm_peak", "evening", "early_late"]
METRICS = ["on_time_performance", "headway_adherence", "excess_wait_time_s", "bunching_rate", "missed_trip_rate"]
ROUTE_RE = re.compile(r"^\d{1,3}$")
MONTH_RE = re.compile(r"^20\d\d-(0[1-9]|1[0-2])$")
DEF_ALIASES = {"excess_wait_time_s": "excess_wait_time"}


class ToolResult(BaseModel):
    columns: list[str]
    rows: list[list] = Field(description="Result rows, at most 500")
    sql: str = Field(description="The validated SQL that produced the rows")
    source: str = "GhostBus analytics views (MBTA Bus Arrival Departure Times 2025, CC0)"


class Definition(BaseModel):
    name: str
    text: str
    version: str


def _route(r: str) -> str:
    r = str(r).strip()
    if not ROUTE_RE.match(r):
        raise ValueError(f"invalid route id {r!r} (use e.g. '1', '23', '741' for SL1)")
    return str(int(r))


def _month(m: str) -> str:
    if not MONTH_RE.match(m):
        raise ValueError(f"invalid month {m!r} (use YYYY-MM)")
    return m


def _run(sql: str) -> ToolResult:
    res = run_query(sql)
    return ToolResult(columns=res.columns, rows=res.rows, sql=res.sql)


def _filters(day_type: str, time_band: str) -> str:
    if day_type not in ("all", "weekday", "weekend") or time_band not in (
            "all", "am_peak", "midday", "pm_peak", "evening", "early_late"):
        raise ValueError("invalid day_type or time_band")
    return f"day_type = '{day_type}' AND time_band = '{time_band}'"


# ---------------------------------------------------------------- plain functions (also used in-process)
def route_metrics(route_id: str, metric: str | None = None, month: str | None = None, day_type: str = "all",
                  time_band: str = "all") -> ToolResult:
    r = _route(route_id)
    cols = metric if metric else ", ".join(METRICS)
    if metric and metric not in METRICS:
        raise ValueError(f"unknown metric {metric!r}")
    where = f"route_id = '{r}' AND {_filters(day_type, time_band)}"
    if month:
        where += f" AND month = '{_month(month)}'"
    return _run(f"SELECT route_id, month, {cols} FROM v_route_timeband_metrics WHERE {where} ORDER BY month")


def missed_trip_rates(month: str, route_ids: list[str] | None = None, day_type: str = "all", time_band: str = "all",
                      order: str = "desc", limit: int = 10) -> ToolResult:
    where = f"month = '{_month(month)}' AND {_filters(day_type, time_band)} AND missed_trip_rate IS NOT NULL"
    if route_ids:
        where += " AND route_id IN (" + ", ".join(f"'{_route(r)}'" for r in route_ids) + ")"
    direction = "ASC" if str(order).lower() == "asc" else "DESC"
    n = max(1, min(int(limit), 50))
    return _run("SELECT route_id, month, missed_trip_rate, missed_trips, scheduled_trips FROM v_route_timeband_metrics "
                f"WHERE {where} ORDER BY missed_trip_rate {direction}, route_id LIMIT {n}")


def compare(route_ids: list[str], metric: str, month: str, day_type: str = "all", time_band: str = "all") -> ToolResult:
    if metric not in METRICS:
        raise ValueError(f"unknown metric {metric!r}")
    if len(route_ids) < 2:
        raise ValueError("compare_routes needs at least two routes")
    rl = ", ".join(f"'{_route(r)}'" for r in route_ids)
    return _run(f"SELECT route_id, month, {metric} FROM v_route_timeband_metrics WHERE month = '{_month(month)}' "
                f"AND {_filters(day_type, time_band)} AND route_id IN ({rl}) ORDER BY route_id")


def definition(name: str) -> Definition:
    key = DEF_ALIASES.get(name, name)
    d = _get_definition(key)
    if not d:
        raise ValueError(f"no published definition named {name!r}")
    return Definition(name=d["name"], text=d["text"], version=d["version"])


# ---------------------------------------------------------------- MCP server
mcp = FastMCP("ghostbus-reliability", log_level="WARNING", instructions=(
    "Read-only MBTA bus reliability metrics for GhostBus pilot routes (Jul-Dec 2025). Rates are fractions 0-1; "
    "excess wait is in seconds. Route ids are strings; SL1 = '741', SL3 = '743'."))
READ_ONLY = {"readOnlyHint": True, "idempotentHint": True, "openWorldHint": False}


@mcp.tool(annotations=READ_ONLY)
def get_route_metrics(route_id: str, metric: Metric | None = None, month: str | None = None,
                      day_type: DayType = "all", time_band: TimeBand = "all") -> ToolResult:
    """Reliability metrics for one route: one month, or every month (for trends) when month is omitted."""
    return route_metrics(route_id, metric, month, day_type, time_band)


@mcp.tool(annotations=READ_ONLY)
def get_missed_trip_rates(month: str, route_ids: list[str] | None = None, day_type: DayType = "all",
                          time_band: TimeBand = "all", order: Literal["desc", "asc"] = "desc",
                          limit: int = 10) -> ToolResult:
    """Missed-trip rates by route for a month, ranked (desc = worst first)."""
    return missed_trip_rates(month, route_ids, day_type, time_band, order, limit)


@mcp.tool(annotations=READ_ONLY)
def compare_routes(route_ids: list[str], metric: Metric, month: str, day_type: DayType = "all",
                   time_band: TimeBand = "all") -> ToolResult:
    """One metric for two or more routes in the same month, side by side."""
    return compare(route_ids, metric, month, day_type, time_band)


@mcp.tool(annotations=READ_ONLY)
def get_definition(name: str) -> Definition:
    """The published, versioned definition of a metric or ghost-bus rule (e.g. bunching_rate, high)."""
    return definition(name)


if __name__ == "__main__":
    mcp.run()
