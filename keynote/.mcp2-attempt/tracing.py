"""One trace for the whole film.

Without this you get a scatter of unrelated spans in Jaeger -- each LLM call,
each MCP tool call, each A2A hop its own tiny trace. The story ("one request
fanning out across five backends through one gateway") is invisible.

Three things are needed, and the third is the one that catches people out:

1. A root span per turn, so everything has a common ancestor.
2. W3C `traceparent` on outbound HTTP: the LLM calls and the A2A calls.
3. **MCP is different.** The MCP SDK puts trace context in the JSON-RPC body,
   under `params._meta`, NOT in HTTP headers -- and agentgateway reads headers.
   So we hook httpx on the way out and mirror `_meta` into the headers.
   (Same fix as linsun/agentgateway-workshop@272af0c.)

Degrades to a no-op if the OpenTelemetry packages are missing.
"""

import json
import os
from contextlib import contextmanager

OTLP = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")
SERVICE = os.environ.get("OTEL_SERVICE_NAME", "imagine-director")
ENABLED = os.environ.get("TRACING", "1") not in ("", "0", "false")

TRACING = False
_tracer = None
_inject = None

if ENABLED:
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.trace.propagation.tracecontext import (
            TraceContextTextMapPropagator,
        )

        provider = TracerProvider(resource=Resource.create({"service.name": SERVICE}))
        provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=OTLP, insecure=True))
        )
        trace.set_tracer_provider(provider)
        _tracer = trace.get_tracer(SERVICE)
        _inject = TraceContextTextMapPropagator().inject
        TRACING = True
    except ImportError:
        pass


@contextmanager
def span(name: str, **attrs):
    """A span, or nothing at all if tracing is unavailable."""
    if not TRACING:
        yield None
        return
    with _tracer.start_as_current_span(name) as sp:
        for k, v in attrs.items():
            if v is not None:
                sp.set_attribute(k, v)
        yield sp


def headers() -> dict:
    """W3C traceparent/tracestate for the active span, or {} when tracing is off."""
    if not TRACING or _inject is None:
        return {}
    carrier: dict = {}
    _inject(carrier)
    return carrier


async def mirror_mcp_trace_headers(request) -> None:
    """Copy the MCP SDK's per-operation trace context from `_meta` to HTTP headers.

    The SDK stamps traceparent into the JSON-RPC body per operation, which is
    invisible to a gateway reading headers. Static headers on the client are not
    enough either -- they would all carry the SAME span. This runs per request,
    so each tool call keeps its own parent.
    """
    try:
        body = json.loads(request.content)
    except Exception:  # noqa: BLE001
        body = None
    meta = {}
    if isinstance(body, dict):
        meta = (body.get("params") or {}).get("_meta") or {}
    for name in ("traceparent", "tracestate", "baggage"):
        value = meta.get(name)
        if isinstance(value, str):
            request.headers[name] = value
    # No fallback to the live context here: this hook runs in the transport's
    # task, whose contextvars were copied when the session opened, so
    # `headers()` would return the wrong parent. `_meta` is authoritative --
    # mcp 2.x stamps it in the caller's task, where the tool span is active.
