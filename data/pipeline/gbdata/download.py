"""Streaming downloads with hashing and the committed data/manifest.json."""

from __future__ import annotations

import hashlib
import json
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
from gbcore.settings import REPO_ROOT, get_settings

MANIFEST = REPO_ROOT / "data" / "manifest.json"


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while b := f.read(chunk):
            h.update(b)
    return h.hexdigest()


def load_manifest() -> dict:
    if MANIFEST.exists():
        return json.loads(MANIFEST.read_text(encoding="utf-8"))
    return {"sources": {}}


def update_manifest(name: str, **fields) -> None:
    m = load_manifest()
    entry = m["sources"].get(name, {})
    entry.update(fields)
    m["sources"][name] = entry
    m["updated_utc"] = datetime.now(UTC).isoformat()
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(m, indent=2, sort_keys=True), encoding="utf-8")


def download(url: str, dest: Path, retries: int = 3, timeout: float = 120.0) -> Path:
    """Download url to dest (streamed). Idempotent: skips if dest exists. Exponential backoff."""
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    ua = get_settings().recorder_user_agent
    last: Exception | None = None
    for attempt in range(retries):
        try:
            with httpx.stream("GET", url, headers={"User-Agent": ua}, timeout=timeout,
                              follow_redirects=True) as r:
                r.raise_for_status()
                with tmp.open("wb") as f:
                    for chunk in r.iter_bytes(1 << 20):
                        f.write(chunk)
            tmp.replace(dest)
            return dest
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(2 ** attempt)
    raise RuntimeError(f"download failed after {retries} attempts: {url}: {last}")
