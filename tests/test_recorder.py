from __future__ import annotations

import gzip
import json
from datetime import UTC, datetime

import httpx
from gbstream.recorder import FeedState, Recorder, backoff_seconds, snapshot_path


def test_snapshot_path_partitioning(tmp_path):
    ts = datetime(2026, 11, 23, 21, 5, 7, 123456, tzinfo=UTC)
    p = snapshot_path(tmp_path, "VehiclePositions", ts)
    assert p.relative_to(tmp_path).as_posix() == (
        "VehiclePositions/date=2026-11-23/hour=21/20261123T210507123456Z.pb.gz"
    )


def test_backoff_exponential_and_capped():
    assert backoff_seconds(1, 20) == 20
    assert backoff_seconds(2, 20) == 40
    assert backoff_seconds(3, 20) == 80
    assert backoff_seconds(30, 20) == 600


def test_interval_never_below_15s(tmp_path):
    r = Recorder(tmp_path / "raw", tmp_path / "var", 1.0, "GhostBus-test", feeds=[])
    assert r.interval_s == 15.0


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_fetch_stores_snapshot_manifest_and_heartbeat(tmp_path):
    seen_headers = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen_headers.append(dict(req.headers))
        if "if-modified-since" in req.headers:
            return httpx.Response(304)
        return httpx.Response(200, content=b"\x0a\x00", headers={"Last-Modified": "Mon, 23 Nov 2026 20:59:43 GMT"})

    fs = FeedState("VehiclePositions", "https://example.invalid/vp.pb")
    r = Recorder(tmp_path / "raw", tmp_path / "var", 20, "GhostBus-test/0.1", feeds=[fs])
    with _client(handler) as c:
        r.fetch_once(c, fs)
        r.fetch_once(c, fs)
    r.write_heartbeat()
    files = list((tmp_path / "raw" / "VehiclePositions").rglob("*.pb.gz"))
    assert len(files) == 1
    assert gzip.decompress(files[0].read_bytes()) == b"\x0a\x00"
    assert seen_headers[0]["user-agent"].startswith("GhostBus")
    assert seen_headers[1]["if-modified-since"] == "Mon, 23 Nov 2026 20:59:43 GMT"
    lines = [json.loads(x) for x in (tmp_path / "raw" / "manifest.jsonl").read_text().splitlines()]
    assert [x["status"] for x in lines] == [200, 304]
    hb = json.loads((tmp_path / "var" / "recorder_heartbeat.json").read_text())
    assert hb["feeds"]["VehiclePositions"]["stored"] == 1
    assert hb["feeds"]["VehiclePositions"]["not_modified"] == 1


def test_fetch_error_backs_off_and_survives(tmp_path):
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    fs = FeedState("TripUpdates", "https://example.invalid/tu.pb")
    r = Recorder(tmp_path / "raw", tmp_path / "var", 20, "GhostBus-test", feeds=[fs])
    with _client(handler) as c:
        r.fetch_once(c, fs)
        r.fetch_once(c, fs)
    assert fs.errors == 2 and fs.consecutive_errors == 2
    lines = [json.loads(x) for x in (tmp_path / "raw" / "manifest.jsonl").read_text().splitlines()]
    assert lines[-1]["backoff_s"] == 40
