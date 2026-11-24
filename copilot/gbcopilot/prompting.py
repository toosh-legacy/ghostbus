"""Versioned prompt files (copilot/gbcopilot/prompts/<name>.md with a name/version header).

Every model call logs the prompt name, version and content hash. `eval_lock.json` records the hash of each prompt
that last passed the eval suite; a test fails when a prompt changes without the suite being re-run
(`uv run python -m gbevals.run --fast --lock-prompts`), so a prompt change can't merge without passing evals.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from string import Template

PROMPT_DIR = Path(__file__).parent / "prompts"
LOCK_FILE = PROMPT_DIR / "eval_lock.json"


@dataclass(frozen=True)
class Prompt:
    name: str
    version: int
    body: str
    sha: str

    @property
    def tag(self) -> str:
        return f"{self.name}.v{self.version}"

    def render(self, **kw) -> str:
        return Template(self.body).safe_substitute(**kw)


def _parse(path: Path) -> Prompt:
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    if not text.startswith("---\n"):
        raise ValueError(f"{path.name}: missing header")
    head, body = text[4:].split("\n---\n", 1)
    meta = dict(line.split(":", 1) for line in head.strip().splitlines())
    meta = {k.strip(): v.strip() for k, v in meta.items()}
    sha = hashlib.sha256(text.encode()).hexdigest()[:16]
    return Prompt(meta["name"], int(meta["version"]), body.strip() + "\n", sha)


@lru_cache
def load(name: str) -> Prompt:
    p = _parse(PROMPT_DIR / f"{name}.md")
    if p.name != name:
        raise ValueError(f"{name}.md declares name {p.name}")
    return p


def all_prompts() -> dict[str, Prompt]:
    return {p.stem: load(p.stem) for p in sorted(PROMPT_DIR.glob("*.md"))}


def current_hashes() -> dict[str, dict]:
    return {n: {"version": p.version, "sha": p.sha} for n, p in all_prompts().items()}


def write_lock() -> None:
    LOCK_FILE.write_text(json.dumps(current_hashes(), indent=2, sort_keys=True) + "\n", encoding="utf-8")


def unlocked() -> list[str]:
    """Prompts whose current content has not passed the eval suite."""
    lock = json.loads(LOCK_FILE.read_text()) if LOCK_FILE.exists() else {}
    return [n for n, h in current_hashes().items() if lock.get(n) != h]
