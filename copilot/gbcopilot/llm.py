"""LLM access for the copilot graphs: OpenAI via LangChain, with a cost guard, a call log and a fake model for tests.

- Model IDs and prices live in config/models.yaml; live mode checks the IDs with `client.models.list()` once.
- Cheap tier for routing and SQL drafts, strong tier for answers and the eval judge (`roles` in the config).
- Every call logs prompt name, version, hash and model ID to var/llm/calls.jsonl (no keys, no rider data).
- Cost guard: tokens x list price accumulate per session (GB_SESSION, default = today's date); a call that would
  start past the limit (default $3 per overnight session) raises CostLimitExceeded.
- Without OPENAI_API_KEY (or with GB_LLM=fake) a deterministic fake chat model answers instead, so tests and CI
  never make network calls. Real calls run in the app when a key is set and in `make eval-live`.
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections.abc import Callable
from contextvars import ContextVar
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from gbcore.settings import REPO_ROOT, get_settings
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import BaseModel

from . import telemetry
from .prompting import Prompt, load

CONFIG_PATH = REPO_ROOT / "config" / "models.yaml"
SYSTEM = "You are GhostBus, a careful read-only assistant for MBTA bus reliability data."


class CostLimitExceeded(RuntimeError):
    pass


class ModelConfigError(RuntimeError):
    pass


@lru_cache
def config() -> dict:
    return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))


def model_for(role: str) -> str:
    c = config()
    return c["tiers"][c["roles"][role]]["model"]


MODE: ContextVar[str | None] = ContextVar("gb_llm_mode", default=None)  # "fake" | "live" override per call


def live() -> bool:
    if MODE.get() is not None:
        return MODE.get() == "live"
    return bool(get_settings().openai_api_key or os.environ.get("OPENAI_API_KEY")) and \
        os.environ.get("GB_LLM", "auto") != "fake"


def _api_key() -> str:
    return os.environ.get("OPENAI_API_KEY") or get_settings().openai_api_key


@lru_cache
def verify_models() -> list[str]:
    """Check every configured model ID exists for this key. Raises ModelConfigError listing the missing ones."""
    import openai

    ids = {m.id for m in openai.OpenAI(api_key=_api_key()).models.list()}
    c = config()
    wanted = {t["model"] for t in c["tiers"].values()} | {c["embeddings"]["model"]}
    missing = sorted(wanted - ids)
    if missing:
        raise ModelConfigError(f"configured models not available for this key: {missing}")
    return sorted(wanted)


def price(model: str) -> tuple[float, float]:
    c = config()
    for t in list(c["tiers"].values()) + [c["embeddings"]]:
        if model.startswith(t["model"]):
            return t["input_per_mtok"], t.get("output_per_mtok", 0.0)
    return 0.0, 0.0


# --------------------------------------------------------------------------- cost ledger + call log
class Ledger:
    """Session spend, persisted so separate processes in one overnight session share the limit."""

    _lock = threading.Lock()

    def __init__(self, var_dir: Path | None = None):
        self.dir = (var_dir or get_settings().var_dir) / "llm"
        self.dir.mkdir(parents=True, exist_ok=True)

    @property
    def session(self) -> str:
        return os.environ.get("GB_SESSION") or date.today().isoformat()

    @property
    def path(self) -> Path:
        return self.dir / f"cost_{self.session}.json"

    @property
    def limit(self) -> float:
        return float(os.environ.get("GB_COST_LIMIT_USD", config()["cost_limit_usd_per_session"]))

    def spent(self) -> float:
        try:
            return float(json.loads(self.path.read_text())["usd"])
        except (FileNotFoundError, ValueError, KeyError):
            return 0.0

    def check(self) -> None:
        if self.spent() >= self.limit:
            raise CostLimitExceeded(f"session {self.session} spent ${self.spent():.4f} of ${self.limit:.2f}; "
                                    "no more model calls this session")

    def charge(self, usd: float, record: dict) -> None:
        with self._lock:
            total = self.spent() + usd
            self.path.write_text(json.dumps({"session": self.session, "usd": round(total, 6)}))
            with (self.dir / "calls.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps({**record, "usd": round(usd, 6), "session_usd": round(total, 6)}) + "\n")


class GuardCallback(BaseCallbackHandler):
    """Cost guard + call log + OpenTelemetry span per chat-model call."""

    raise_error = True

    def __init__(self, ledger: Ledger | None = None):
        self.ledger = ledger or Ledger()
        self._runs: dict[Any, dict] = {}

    def on_chat_model_start(self, serialized, messages, *, run_id, metadata=None, invocation_params=None, **kw):
        self.ledger.check()
        md = metadata or {}
        model = (invocation_params or {}).get("model") or (invocation_params or {}).get("model_name") or "fake"
        span = telemetry.tracer().start_span(f"llm {md.get('prompt', '?')}")
        span.set_attribute("gen_ai.request.model", model)
        span.set_attribute("ghostbus.prompt", md.get("prompt_tag", "?"))
        self._runs[run_id] = {"t0": time.perf_counter(), "model": model, "span": span, **md}

    def on_llm_end(self, response, *, run_id, **kw):
        info = self._runs.pop(run_id, {})
        usage: dict = {}
        if response.llm_output:
            usage = response.llm_output.get("token_usage") or {}
            info["model"] = response.llm_output.get("model_name") or info.get("model")
        if not usage and response.generations and response.generations[0]:
            msg = getattr(response.generations[0][0], "message", None)
            um = getattr(msg, "usage_metadata", None) or {}
            usage = {"prompt_tokens": um.get("input_tokens", 0), "completion_tokens": um.get("output_tokens", 0)}
        tin, tout = int(usage.get("prompt_tokens", 0) or 0), int(usage.get("completion_tokens", 0) or 0)
        pin, pout = price(info.get("model") or "")
        usd = (tin * pin + tout * pout) / 1e6
        span = info.pop("span", None)
        if span is not None:
            span.set_attribute("gen_ai.usage.input_tokens", tin)
            span.set_attribute("gen_ai.usage.output_tokens", tout)
            span.end()
        self.ledger.charge(usd, {"ts": time.time(), "model": info.get("model"), "role": info.get("role"),
                                 "prompt": info.get("prompt"), "prompt_version": info.get("prompt_version"),
                                 "prompt_sha": info.get("prompt_sha"), "input_tokens": tin, "output_tokens": tout,
                                 "ms": round((time.perf_counter() - info.get("t0", time.perf_counter())) * 1000)})

    def on_llm_error(self, error, *, run_id, **kw):
        info = self._runs.pop(run_id, {})
        if (span := info.get("span")) is not None:
            span.set_attribute("error", str(error)[:200])
            span.end()


@lru_cache
def guard() -> GuardCallback:
    return GuardCallback()


# --------------------------------------------------------------------------- fake model
FAKE_HANDLERS: dict[str, Callable[[dict], str]] = {}


def fake_handler(role: str):
    def deco(fn: Callable[[dict], str]):
        FAKE_HANDLERS[role] = fn
        return fn
    return deco


class FakeReliabilityChatModel(BaseChatModel):
    """Deterministic stand-in for the OpenAI chat model in tests and offline runs.

    It reads the structured inputs the graph attaches to the prompt and answers with the rule-based handler for
    the role (intent templates for SQL, a formatter for answers), so the whole graph runs without a network.
    """

    role: str

    @property
    def _llm_type(self) -> str:
        return "ghostbus-fake"

    def _generate(self, messages: list[BaseMessage], stop=None, run_manager=None, **kw) -> ChatResult:
        text = str(messages[-1].content)
        inputs = json.loads(text.rsplit("<inputs>", 1)[1].split("</inputs>", 1)[0]) if "<inputs>" in text else {}
        out = FAKE_HANDLERS[self.role](inputs)
        msg = AIMessage(content=out, usage_metadata={"input_tokens": 0, "output_tokens": 0, "total_tokens": 0})
        return ChatResult(generations=[ChatGeneration(message=msg)], llm_output={"model_name": "fake"})


# --------------------------------------------------------------------------- calls
def chat(role: str) -> BaseChatModel:
    if live():
        from langchain_openai import ChatOpenAI

        verify_models()
        return ChatOpenAI(model=model_for(role), temperature=0, seed=7, timeout=30, max_retries=2,
                          api_key=_api_key(), callbacks=[guard()])
    return FakeReliabilityChatModel(role=role, callbacks=[guard()])


def _messages(p: Prompt, inputs: dict, vars: dict) -> list[BaseMessage]:
    human = p.render(**vars)
    if not live():
        human += "\n<inputs>" + json.dumps(inputs, default=str) + "</inputs>"
    return [SystemMessage(SYSTEM), HumanMessage(human)]


def _config(role: str, p: Prompt) -> dict:
    return {"run_name": p.tag, "metadata": {"role": role, "prompt": p.name, "prompt_version": p.version,
                                            "prompt_tag": p.tag, "prompt_sha": p.sha}}


def structured[T: BaseModel](role: str, prompt: str, schema: type[T], inputs: dict | None = None, **vars) -> T:
    """Prompt -> Pydantic object (OpenAI json_schema structured output, or the fake model's JSON)."""
    p = load(prompt)
    msgs = _messages(p, inputs or vars, vars)
    if live():
        return chat(role).with_structured_output(schema, method="json_schema").invoke(msgs, config=_config(role, p))
    out = chat(role).invoke(msgs, config=_config(role, p))
    return schema.model_validate_json(str(out.content))


def text(role: str, prompt: str, inputs: dict | None = None, **vars) -> str:
    p = load(prompt)
    out = chat(role).invoke(_messages(p, inputs or vars, vars), config=_config(role, p))
    return str(out.content)
