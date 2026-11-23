"""SQL validator + read-only query tool for the copilot (spec 11).

Checks: parse with sqlglot (DuckDB dialect); exactly one statement; root must be a SELECT; every table reference must
be an allowlisted view (or a CTE defined in the same query); no schema/catalog qualifiers; no table functions or file
access; only allowlisted scalar/aggregate functions; no PRAGMA/ATTACH/COPY/SET/DDL/DML anywhere. The query that runs
is regenerated from the AST (comments dropped) and wrapped with LIMIT 500. A 5 s timeout interrupts the connection,
which is opened read-only with external access disabled.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

import sqlglot
from gbdata.analytics import ALLOWLISTED_VIEWS, read_only_connection
from sqlglot import exp

ROW_LIMIT = 500
TIMEOUT_S = 5.0

SAFE_FUNCS = {
    "sum", "avg", "count", "min", "max", "round", "coalesce", "abs", "median", "quantile_cont", "quantile",
    "strftime", "lower", "upper", "nullif", "greatest", "least", "stddev", "stddev_samp", "count_if", "first",
    "last", "any_value", "length", "substr", "substring", "concat", "trim", "date_trunc", "year", "month", "floor",
    "ceil", "ceiling", "sqrt", "ln", "log", "power", "pow", "row_number", "rank", "dense_rank", "lag", "lead",
    "percent_rank", "ntile", "cast", "trycast", "if", "ifnull", "case", "variance", "corr", "arg_max", "arg_min",
}
BLOCKED_NODES = (exp.Insert, exp.Update, exp.Delete, exp.Drop, exp.Create, exp.Alter, exp.Command, exp.Copy,
                 exp.Set, exp.Use, exp.Pragma, exp.Attach, exp.Detach, exp.Merge, exp.TruncateTable, exp.Transaction,
                 exp.Commit, exp.Rollback, exp.Grant, exp.Describe, exp.Into, exp.LoadData, exp.Show)
FILE_FUNCS = {"read_csv", "read_csv_auto", "read_parquet", "read_json", "read_json_auto", "read_text", "read_blob",
              "glob", "parquet_scan", "csv_scan", "sniff_csv", "query_table", "query", "getenv", "sql_auto_complete"}


class ValidationError(ValueError):
    pass


@dataclass
class QueryResult:
    sql: str
    columns: list[str]
    rows: list[list]
    elapsed_ms: float


def _func_name(node: exp.Expression) -> str:
    if isinstance(node, exp.Anonymous):
        return str(node.this).lower()
    return (node.sql_name() if hasattr(node, "sql_name") else type(node).__name__).lower()


def validate(sql: str) -> str:
    """Return the safe, regenerated SQL (with the row limit) or raise ValidationError."""
    if not sql or len(sql) > 5000:
        raise ValidationError("empty or too long query")
    try:
        stmts = sqlglot.parse(sql, read="duckdb")
    except Exception as e:  # noqa: BLE001
        raise ValidationError(f"could not parse SQL: {e}") from None
    stmts = [s for s in stmts if s is not None]
    if len(stmts) != 1:
        raise ValidationError("exactly one statement is allowed")
    root = stmts[0]
    if not isinstance(root, exp.Select):
        raise ValidationError(f"only SELECT is allowed (got {type(root).__name__})")
    for node in root.walk():
        if isinstance(node, BLOCKED_NODES):
            raise ValidationError(f"{type(node).__name__} is not allowed")
    ctes = {c.alias_or_name.lower() for c in root.find_all(exp.CTE)}
    for t in root.find_all(exp.Table):
        if t.args.get("db") or t.args.get("catalog"):
            raise ValidationError("schema/catalog-qualified names are not allowed")
        if not isinstance(t.this, exp.Identifier):
            raise ValidationError("table functions are not allowed")
        name = t.name.lower()
        if name not in ALLOWLISTED_VIEWS and name not in ctes:
            raise ValidationError(f"table '{t.name}' is not an allowlisted view")
    for f in root.find_all(exp.Func):
        name = _func_name(f)
        if name in FILE_FUNCS or isinstance(f, exp.ReadCSV if hasattr(exp, "ReadCSV") else ()):
            raise ValidationError(f"function {name} is not allowed")
        if isinstance(f, exp.Anonymous) and name not in SAFE_FUNCS:
            raise ValidationError(f"function {name} is not allowed")
    for lit in root.find_all(exp.Literal):
        s = str(lit.this).lower()
        if lit.is_string and ("/" in s or "\\" in s) and any(x in s for x in (".csv", ".parquet", ".json", "etc", "passwd", "http")):
            raise ValidationError("file paths / URLs are not allowed")
    inner = root.sql(dialect="duckdb", comments=False)  # regenerated: comments dropped
    return f"SELECT * FROM ({inner}) AS q LIMIT {ROW_LIMIT}"


def run_query(sql: str, timeout_s: float = TIMEOUT_S) -> QueryResult:
    safe = validate(sql)
    con = read_only_connection()
    timer = threading.Timer(timeout_s, con.interrupt)
    t0 = time.perf_counter()
    timer.start()
    try:
        cur = con.execute(safe)
        rows = cur.fetchall()
        cols = [d[0] for d in cur.description]
    except Exception as e:  # noqa: BLE001
        el = time.perf_counter() - t0
        if el >= timeout_s * 0.95 or "interrupt" in str(e).lower():
            raise TimeoutError(f"query exceeded {timeout_s:.0f} s and was cancelled") from None
        raise ValidationError(f"query failed: {str(e)[:200]}") from None
    finally:
        timer.cancel()
        con.close()
    return QueryResult(safe, cols, [list(r) for r in rows], (time.perf_counter() - t0) * 1000)


def list_views() -> dict:
    from gbdata.analytics import COLUMN_DOCS

    con = read_only_connection()
    try:
        out = {}
        for v, desc in ALLOWLISTED_VIEWS.items():
            cols = [r[0] for r in con.execute(f"DESCRIBE {v}").fetchall()]
            out[v] = {"description": desc, "columns": {c: COLUMN_DOCS.get(c, "") for c in cols}}
        return out
    finally:
        con.close()
