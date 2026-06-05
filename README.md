# Imagine

Capture or upload a photo in your browser, then ask an AI agent to reimagine it
&mdash; "add a NYC vibe and connect the people with bridges", "give it Japanese
culture", and more. You can also **animate the result into a short video clip**.
Image transforms are powered by Google Gemini's Nano Banana image model; video is
powered by Veo.

Inspired by [kagent_vision](https://github.com/automateyournetwork/kagent_vision),
re-architected to run cleanly in Kubernetes.

## Architecture

Three small services:

| Component | What it does | Tech |
| --- | --- | --- |
| `frontend/` | Single-page UI: webcam capture, upload, presets, chat, before/after | nginx + vanilla JS + Tailwind |
| `backend/` | API + lightweight Gemini agent; owns image storage/serving | FastAPI |
| `mcp-server/` | Reusable MCP tools that do the actual image transforms | FastMCP + google-genai |

```
Browser  -->  Backend (agent)  -->  MCP server  -->  Gemini (Nano Banana)
                   ^                      |
                   +-- base64 image bytes-+   (no shared volume needed)
```

The MCP server returns transformed images as base64, so the backend owns
storage and serving and no ReadWriteMany volume is required across pods.

## Prerequisites

- Docker
- A Google AI (Gemini) API key: https://aistudio.google.com/apikey
- For Kubernetes: `kubectl` and [`kind`](https://kind.sigs.k8s.io/) (or Docker Desktop's k8s)

Set your key:

```bash
cp .env.example .env
# edit .env and set GEMINI_API_KEY=...
```

## Option A: Run locally with Docker Compose

```bash
docker compose up --build
```

Open http://localhost:8088

- Frontend: http://localhost:8088
- Backend API: http://localhost:8080 (e.g. `GET /healthz`, `GET /api/styles`)
- MCP server: http://localhost:8000/mcp

## Option B: Run on Kubernetes (kind)

```bash
./scripts/kind-up.sh         # create local cluster
./scripts/build-and-load.sh  # build images + load into kind
./scripts/deploy.sh          # create secret from .env, apply manifests, wait
```

Then access the app (using localhost so the browser webcam works without TLS):

```bash
kubectl port-forward -n default svc/frontend 8088:80
```

Open http://localhost:8088

Useful checks:

```bash
kubectl get pods -n default
kubectl logs -n default deploy/backend
kubectl logs -n default deploy/vision-mcp
```

## Using the app

1. Click **Start camera** then **Take photo**, or **Upload** an image.
2. Pick a **preset** (NYC Vibe + Bridges, Japanese Culture, ...) and click
   **Transform image** &mdash; or type a custom instruction.
3. Pick a **motion style** and click **Animate image -> video** to turn the
   current image into a short Veo clip (~1-2 min).
4. Click **Ask agent** to chat naturally; the agent decides which tool to call
   (transform, generate, or animate).
5. See the before/after image and the generated video.

## MCP tools

| Tool | Description |
| --- | --- |
| `list_styles` | List built-in image style presets |
| `list_video_styles` | List built-in video motion presets |
| `transform_image(image_b64, instruction, style_preset)` | Edit an image via instruction/preset |
| `generate_image(prompt)` | Generate a brand-new image from text |
| `generate_video(image_b64, instruction, motion_preset)` | Animate an image into a short video (Veo, long-running) |

## Adding a style preset

Edit [`mcp-server/vision_mcp/presets.py`](mcp-server/vision_mcp/presets.py) and
add an entry to `PRESETS` with a `label` and a descriptive `prompt`. It will
automatically show up in the frontend preset bar.

## Configuration

| Env var | Default | Where |
| --- | --- | --- |
| `GEMINI_API_KEY` | (required) | backend + mcp-server |
| `IMAGE_MODEL` | `gemini-2.5-flash-image` | mcp-server (try `gemini-3.1-flash-image`) |
| `VIDEO_MODEL` | `veo-3.1-lite-generate-preview` | mcp-server (try `veo-3.1-fast-generate-preview`) |
| `VIDEO_DURATION` | `6` | mcp-server (clip length in seconds; shorter = faster) |
| `TEXT_MODEL` | `gemini-2.5-flash` | backend (agent reasoning) |
| `MCP_URL` | `http://vision-mcp:8000/mcp` | backend |
| `OUTPUT_DIR` | `/data/outputs` | backend |

## Troubleshooting

- **No styles load / "MCP server unavailable"**: the backend can't reach the MCP
  server. Check `kubectl logs -n default deploy/vision-mcp` and that `MCP_URL` is correct.
- **Camera blocked**: browsers only allow `getUserMedia` over `https://` or
  `http://localhost`. Use the `port-forward` URL (localhost), not a pod IP.
- **"No image returned from model"**: your key may not have access to the image
  model, or the prompt was refused. Try a different `IMAGE_MODEL` or instruction.
- **Image generation is slow**: transforms typically take ~10-20s; the frontend
  shows a status while it works.
- **Video generation fails / times out**: Veo is a paid preview and latency
  varies. The default `veo-3.1-lite-generate-preview` is the lowest-latency
  option; you can also try `veo-3.1-fast-generate-preview`. Ensure your key has
  Veo access. The pipeline waits up to ~15 minutes before giving up.

## License

Apache 2.0
