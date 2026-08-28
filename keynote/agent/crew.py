"""Scout and DP: two A2A agents behind agentgateway.

Minimal A2A -- an agent card plus JSON-RPC message/send. Enough for
agentgateway to classify the traffic, rewrite the card's url so the Director
cannot bypass the gateway on later turns, and log a2a.method.

  python -m agent.crew scout   # :9101
  python -m agent.crew dp      # :9102
"""

import os
import sys

import uvicorn
from fastapi import FastAPI, Request
from openai import OpenAI

from agent import tracing

LLM = os.environ.get("AGW_LLM", "http://localhost:3000")

SCOUT = """You enrich the user's idea into vivid visual wording. Nothing else.

Hard limit: 20 words. Return only the phrase, no markdown, no preamble.

You are editing a real photograph of a real audience in a real room. The people
and the room STAY EXACTLY AS THEY ARE. You are adding atmosphere on top.

NEVER invent a location, building, era or event the user did not mention.
NEVER describe the people, their clothing, their faces or the venue.
If the user names a theme, enrich THAT theme and nothing else.

In:  "make them dance, japanese vibe"
Out: "dancing joyfully, paper lanterns and drifting sakura petals, warm festival
      light, ukiyo-e colour"

In:  "make them dance"
Out: "dancing joyfully, warm celebratory light, a sense of movement and energy"
"""

DP = """Give ONE short line of motion and music direction. Nothing else.

Hard limit: 25 words. Return only the line, no markdown, no preamble.

The people and the room are fixed -- you are choosing how they MOVE and what
the audience HEARS. Never relocate them, never add a setting.
The video model makes its own audio, so ALWAYS name the music.

In:  "dancing joyfully, paper lanterns, warm festival light"
Out: "People dancing in place, gentle handheld sway, slow push in. Upbeat taiko
      and shamisen, crowd laughing and clapping."
"""

ROLES = {
    "scout": (SCOUT, int(os.environ.get("SCOUT_PORT", "9101")), "Location Scout"),
    "dp": (DP, int(os.environ.get("DP_PORT", "9102")), "Director of Photography"),
}


def build(role: str) -> FastAPI:
    prompt, port, title = ROLES[role]
    app = FastAPI()
    # The gateway holds the provider credential. The SDK insists on a non-empty
    # api_key, so send a placeholder -- never a real provider key.
    client = OpenAI(base_url=f"{LLM}/v1", api_key="agentgateway", timeout=120)

    card = {
        "name": title,
        "description": f"{title} for the keynote film crew.",
        "url": f"http://localhost:{port}/",
        "version": "1.0.0",
        "protocolVersion": "0.3.0",
        "capabilities": {"streaming": False},
        "defaultInputModes": ["text/plain"],
        "defaultOutputModes": ["text/plain"],
        "skills": [{
            "id": role,
            "name": title,
            "description": prompt.split("\n")[0],
            "tags": ["film", "prompt-craft"],
        }],
    }

    @app.get("/.well-known/agent.json")
    @app.get("/.well-known/agent-card.json")
    async def agent_card():
        # agentgateway rewrites `url` to point at itself. That rewrite is the
        # demo beat: the agent cannot advertise a way around the gateway.
        return card

    @app.post("/")
    async def rpc(request: Request):
        body = await request.json()
        parts = (body.get("params", {}).get("message", {}) or {}).get("parts", []) or []
        text = "\n".join(p.get("text", "") for p in parts if p.get("kind", "text") == "text")
        # Carry the caller's trace forward so the Scout/DP LLM call hangs off
        # the same trace as the turn that asked for it, instead of starting a
        # brand new one.
        incoming = {k: v for k, v in request.headers.items()
                    if k.lower() in ("traceparent", "tracestate", "baggage")}
        try:
            reply = client.chat.completions.create(
                extra_headers=incoming,
                model="director",
                messages=[{"role": "system", "content": prompt},
                          {"role": "user", "content": text}],
                temperature=0.9,
            ).choices[0].message.content
        except Exception as exc:  # surface it as JSON-RPC, not an opaque 500
            return {
                "jsonrpc": "2.0", "id": body.get("id"),
                "error": {"code": -32000, "message": f"{role}: {exc}"},
            }
        return {
            "jsonrpc": "2.0",
            "id": body.get("id"),
            "result": {
                "kind": "message",
                "role": "agent",
                "messageId": f"{role}-reply",
                "parts": [{"kind": "text", "text": reply}],
            },
        }

    return app


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "scout"
    uvicorn.run(build(which), host="0.0.0.0", port=ROLES[which][1], log_level="warning")
