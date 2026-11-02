"""Director, Scout and DP as kagent BYO agents -- how they run in k8s/kagent/.

kagent runs each Agent as an Agent Substrate actor, one actor per session
(A2A contextId), and expects four things of a BYO image (kagent
v1.0.0-alpha17, go/core/internal/substrate/actor_template.go):

  A2A v1 over gRPC on :80   kagent's A2A gateway dials the actor through
                            atenet-router with the gRPC binding
  GET /readyz on :8081      the wakeup probe; answered only once gRPC listens
  tasks in kagent's store   every task update is saved to the controller's
                            TaskStoreService -- the gateway returns the final
                            result from there, not from our stream
  config from kagent        KAGENT_CONFIG_JSON / KAGENT_AGENT_CARD_JSON,
                            materialized to /config

kagent-core, from the kagent repo at the cluster's tag, does the task store
and the request plumbing. This file is the executors and the two listeners.
The A2A 0.3 servers (agent/a2a_server.py, agent/crew.py) stay as they are for
the laptop.

  python -m agent.kagent_runtime director|scout|dp
  KAGENT_LOCAL=1 A2A_GRPC_PORT=50051 READYZ_PORT=8081 \\
      python -m agent.kagent_runtime scout      # no kagent: tasks in memory
"""

import asyncio
import json
import logging
import os
import re
import sys
from pathlib import Path

import grpc
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.request_handlers.grpc_handler import GrpcHandler
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import AgentCard, Part, TaskState, a2a_pb2, a2a_pb2_grpc
from google.protobuf.json_format import ParseDict
from grpc_health.v1 import health, health_pb2, health_pb2_grpc
from kagent.core import AsyncControllerClient, KAgentConfig
from kagent.core.a2a import (
    KAgentGrpcServerCallContextBuilder,
    KAgentRequestContextBuilder,
    TelemetryRequestHandler,
)
from kagent.core.a2a._task_store import KAgentRequestHandler, KAgentTaskStore

from agent import auth, crew, tools, tracing
from agent.director import Director

log = logging.getLogger("imagine.kagent")

LOCAL = os.environ.get("KAGENT_LOCAL") == "1"
CONFIG_DIR = Path(os.environ.get("KAGENT_CONFIG_DIR", "/config"))
# Substrate projects the actor's name (session-<uuid>) here; the task store
# derives the session from it.
IDENTITY = Path(os.environ.get("KAGENT_IDENTITY_FILE", "/run/kagent/identity/name"))
READYZ_PORT = int(os.environ.get("READYZ_PORT", "8081"))
# kagent mounts the actor's durable dir here. It is the only thing a snapshot
# keeps: a resumed actor is a fresh process.
STATE = Path(os.environ.get("DIRECTOR_STATE", "/data/director.json"))
_ANSI = re.compile(r"\x1b\[[0-9;]*m")


class _Executor(AgentExecutor):
    """One turn = one task: each progress line is a WORKING status update,
    then the reply as an artifact and a COMPLETED (or FAILED) status."""

    async def run(self, text: str, headers: dict, emit) -> str:
        raise NotImplementedError

    async def execute(self, context: RequestContext, event_queue) -> None:
        updater = TaskUpdater(event_queue, context.task_id, context.context_id)
        if LOCAL and context.current_task is None:
            # With kagent, KAgentRequestHandler submits the task (and waits for
            # it to be saved) before calling us. The plain SDK handler doesn't.
            await event_queue.enqueue_event(a2a_pb2.Task(
                id=context.task_id, context_id=context.context_id,
                status=a2a_pb2.TaskStatus(state=TaskState.TASK_STATE_SUBMITTED),
                history=[context.message]))

        # The Director emits from its own threads too (the progress ticker), so
        # lines cross into the loop through a queue.
        loop = asyncio.get_running_loop()
        lines: asyncio.Queue = asyncio.Queue()

        def emit(line: str) -> None:
            line = _ANSI.sub("", line or "").strip()
            if line:
                loop.call_soon_threadsafe(lines.put_nowait, line)

        async def pump() -> None:
            while (line := await lines.get()) is not None:
                await updater.update_status(
                    TaskState.TASK_STATE_WORKING, updater.new_agent_message([Part(text=line)]))

        pumper = asyncio.create_task(pump())
        headers = context.call_context.state.get("headers", {}) if context.call_context else {}
        ok = True
        try:
            reply = await self.run(context.get_user_input(), headers, emit)
        except asyncio.CancelledError:
            pumper.cancel()
            raise
        except Exception as exc:  # noqa: BLE001 -- the turn fails, the actor stays up
            log.exception("turn failed")
            reply, ok = f"! {exc}", False
        lines.put_nowait(None)
        await pumper

        await updater.add_artifact([Part(text=reply)], name="reply")
        message = updater.new_agent_message([Part(text=reply)])
        await (updater.complete(message) if ok else updater.failed(message))

    async def cancel(self, context: RequestContext, event_queue) -> None:
        # The SDK has already cancelled execute(); record the boundary.
        await TaskUpdater(event_queue, context.task_id, context.context_id).cancel()


class DirectorExecutor(_Executor):
    """One actor is one kagent session, so one Director per process. Its
    conversation, photo and last film are saved to /data after every turn and
    read back on a cold start -- what FULL memory snapshots did on raw Substrate."""

    def __init__(self) -> None:
        self.director: Director | None = None

    async def _get(self) -> Director:
        if self.director is None:
            d = Director(interactive=False)
            d.tools = await tools.list_tools()
            if STATE.exists():
                d.restore(json.loads(STATE.read_text()))
                log.info("restored the Director from %s", STATE)
            self.director = d
        return self.director

    def _save(self) -> None:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATE.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.director.state()))
        tmp.replace(STATE)            # atomic: a suspend mid-write keeps the old one

    async def run(self, text: str, headers: dict, emit) -> str:
        d = await self._get()
        # The signed-in person, if any: kagent passes the caller's
        # Authorization through. The gateway binds identity when the MCP
        # session connects, so a new identity means reconnecting.
        authz = headers.get("authorization", "")
        if auth.use_forwarded(authz[7:] if authz.lower().startswith("bearer ") else ""):
            await tools.reset()
        d.emit = emit
        trace = {k: v for k, v in headers.items() if k in ("traceparent", "tracestate")}
        try:
            with tracing.continue_from(trace):
                if re.search(r"\bpublish\b", text.lower()):
                    return await d.publish_again()
                return await d.turn(text)
        finally:
            await asyncio.to_thread(self._save)
            # Spans are batched; send this turn's now, before a suspend.
            await asyncio.to_thread(tracing.flush)


class CrewExecutor(_Executor):
    def __init__(self, role: str) -> None:
        self.role = role

    async def run(self, text: str, headers: dict, emit) -> str:
        trace = {k: v for k, v in headers.items() if k in ("traceparent", "tracestate", "baggage")}
        return await asyncio.to_thread(crew.answer, self.role, text, trace)


EXECUTORS = {"director": DirectorExecutor, "scout": lambda: CrewExecutor("scout"),
             "dp": lambda: CrewExecutor("dp")}


def _local_card(role: str, port: int) -> AgentCard:
    return ParseDict({
        "name": role, "description": f"imagine {role} (local)", "version": "v1",
        "supportedInterfaces": [{"url": f"127.0.0.1:{port}", "protocolBinding": "GRPC",
                                 "protocolVersion": "1.0"}],
        "capabilities": {"streaming": True},
        "defaultInputModes": ["text"], "defaultOutputModes": ["text"],
    }, AgentCard())


async def _readyz(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    request = await reader.readline()
    status = b"200 OK" if request.startswith(b"GET /readyz ") else b"404 Not Found"
    writer.write(b"HTTP/1.1 " + status + b"\r\nContent-Length: 2\r\nConnection: close\r\n\r\nOK")
    await writer.drain()
    writer.close()
    await writer.wait_closed()


async def serve(role: str) -> None:
    executor = EXECUTORS[role]()
    controller = None
    if LOCAL:
        port = int(os.environ.get("A2A_GRPC_PORT", "80"))
        card = _local_card(role, port)
        store = InMemoryTaskStore()
        handler = TelemetryRequestHandler(
            agent_executor=executor, task_store=store, agent_card=card,
            request_context_builder=KAgentRequestContextBuilder(task_store=store))
    else:
        config = KAgentConfig.from_file(CONFIG_DIR / "config.json")   # writes /config first
        port = config.runtime.port or 80
        card = ParseDict(json.loads((CONFIG_DIR / "agent-card.json").read_text()),
                         AgentCard(), ignore_unknown_fields=True)
        controller = AsyncControllerClient(config.api_url)
        store = KAgentTaskStore(controller, identity_path=IDENTITY)
        handler = KAgentRequestHandler(
            agent_executor=executor, task_store=store, agent_card=card,
            request_context_builder=KAgentRequestContextBuilder(task_store=store))

    server = grpc.aio.server()
    a2a_pb2_grpc.add_A2AServiceServicer_to_server(
        GrpcHandler(handler, context_builder=KAgentGrpcServerCallContextBuilder()), server)
    health_service = health.aio.HealthServicer()
    await health_service.set(a2a_pb2.DESCRIPTOR.services_by_name["A2AService"].full_name,
                             health_pb2.HealthCheckResponse.SERVING)
    health_pb2_grpc.add_HealthServicer_to_server(health_service, server)
    if server.add_insecure_port(f"[::]:{port}") == 0:
        raise RuntimeError(f"could not bind A2A gRPC on :{port}")
    await server.start()
    # host=None: every interface on every family. "::" alone is IPv6-only, and
    # Substrate probes the actor's IPv4 address.
    ready = await asyncio.start_server(_readyz, host=None, port=READYZ_PORT)
    log.info("%s: A2A gRPC on :%d, /readyz on :%d%s", role, port, READYZ_PORT,
             " (local, tasks in memory)" if LOCAL else "")
    try:
        await server.wait_for_termination()
    finally:
        ready.close()
        await health_service.enter_graceful_shutdown()
        await server.stop(grace=5)
        if controller:
            await controller.close()


if __name__ == "__main__":
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    which = sys.argv[1] if len(sys.argv) > 1 else "director"
    if which not in EXECUTORS:
        sys.exit(f"usage: python -m agent.kagent_runtime {'|'.join(EXECUTORS)}")
    asyncio.run(serve(which))
