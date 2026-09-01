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
import time
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


# The traceparent for the MCP call currently in flight.
#
# WHY THIS EXISTS: the MCP SDK does not POST from your task. The streamable-HTTP
# transport owns a long-lived writer task, created when the session was opened.
# OpenTelemetry context lives in contextvars, which are copied per TASK -- so
# inside the httpx hook we are in the transport's task and see the context from
# session-open time, not the span around the tool call. `headers()` there
# returns either nothing or the wrong parent, which is why MCP spans landed in
# their own traces while the LLM spans were correctly parented.
#
# mcp 2.x solves this properly by stamping `_meta` per operation. On 1.x we set
# this immediately before each call instead. Tool calls are awaited one at a
# time, so a single slot is safe.
_pending: dict = {}
_pending_at: float = 0.0
# How long a captured context stays usable. A tool call sends its POST within
# milliseconds; anything later is a different event that happens to find the
# slot still full.
_PENDING_TTL = 30.0


def set_pending(carrier: dict) -> None:
    """Record the trace context for the call about to be sent."""
    global _pending_at
    _pending.clear()
    _pending.update(carrier or {})
    _pending_at = time.monotonic()


def _take_pending() -> dict:
    """Consume the slot. Once, and only while it is fresh.

    Both halves matter. The slot used to be read without clearing, so the MCP
    transport's long-lived `GET /mcp` stream -- which reconnects on its own
    schedule, minutes after any tool call -- picked up whatever context was
    left in it. That is how a 15-minute idle SSE stream appeared in Jaeger as
    a child of `tool:publish_publish_video`, a span that had finished twelve
    minutes earlier.
    """
    if not _pending:
        return {}
    fresh = (time.monotonic() - _pending_at) <= _PENDING_TTL
    carrier = dict(_pending)
    _pending.clear()
    return carrier if fresh else {}


async def _mirror_mcp_trace_headers(request) -> None:
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
    if "traceparent" in request.headers:
        return

    # The streamable-HTTP transport also opens a long-lived GET for the
    # server->client stream, and sends a DELETE to close the session. Those are
    # transport lifecycle, not part of any tool call: give them no parent, so
    # they start their own trace instead of hanging a 15-minute idle span off
    # whatever ran last.
    if request.method != "POST":
        return

    # Fallback order matters. `_pending` is the context captured in the CALLER's
    # task just before the call; the live context here belongs to the transport
    # task and is usually wrong. So prefer _meta, then _pending, then whatever
    # this task happens to hold.
    for k, v in (_take_pending() or headers()).items():
        request.headers[k] = v


def mcp_httpx_factory(extra_request_hooks=()):
    """httpx client factory for streamablehttp_client, with the mirror hook.

    extra_request_hooks run after the trace mirror on every request -- this is
    how the Keycloak bearer token is attached per REQUEST, so a login mid-demo
    takes effect on the next call rather than needing a restart.
    """
    import httpx

    hooks = [_mirror_mcp_trace_headers, *extra_request_hooks]

    def factory(headers=None, timeout=None, auth=None):
        return httpx.AsyncClient(
            headers=headers, timeout=timeout, auth=auth, follow_redirects=True,
            event_hooks={"request": hooks},
        )

    return factory
