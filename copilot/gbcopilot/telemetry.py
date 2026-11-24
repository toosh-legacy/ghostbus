"""OpenTelemetry tracing, always on. Spans go to var/otel/spans.jsonl (and to an OTLP collector when
OTEL_EXPORTER_OTLP_ENDPOINT is set and the OTLP exporter package is installed). LangSmith tracing is added on top
only when LANGSMITH_API_KEY is set."""

from __future__ import annotations

import json
import os
from collections.abc import Sequence
from functools import lru_cache

from gbcore.settings import get_settings
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter, SpanExportResult


class JsonlSpanExporter(SpanExporter):
    def __init__(self) -> None:
        self.dir = get_settings().var_dir / "otel"
        self.dir.mkdir(parents=True, exist_ok=True)

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        with (self.dir / "spans.jsonl").open("a", encoding="utf-8") as f:
            for s in spans:
                f.write(json.dumps({
                    "name": s.name, "trace_id": f"{s.context.trace_id:032x}", "span_id": f"{s.context.span_id:016x}",
                    "parent": f"{s.parent.span_id:016x}" if s.parent else None,
                    "start_ns": s.start_time, "end_ns": s.end_time, "attributes": dict(s.attributes or {}),
                }, default=str) + "\n")
        return SpanExportResult.SUCCESS


@lru_cache
def setup() -> TracerProvider:
    provider = TracerProvider(resource=Resource.create({"service.name": "ghostbus"}))
    provider.add_span_processor(BatchSpanProcessor(JsonlSpanExporter()))
    if os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

            provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
        except ImportError:
            pass
    trace.set_tracer_provider(provider)
    if os.environ.get("LANGSMITH_API_KEY"):
        os.environ.setdefault("LANGSMITH_TRACING", "true")
        os.environ.setdefault("LANGSMITH_PROJECT", "ghostbus")
    else:
        os.environ["LANGSMITH_TRACING"] = "false"
    return provider


def tracer() -> trace.Tracer:
    setup()
    return trace.get_tracer("ghostbus.copilot")
