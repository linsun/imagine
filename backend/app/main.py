"""FastAPI backend for the Gemini Vision Transform app.

Responsibilities:
- Accept webcam captures / uploads from the browser.
- Run instruction-driven transforms via the vision MCP server.
- Host a small Gemini agent for free-form chat requests.
- Persist and serve resulting images.
"""

import base64
import binascii
import io
import os
import uuid

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image
from pydantic import BaseModel

from app import agent, config, mcp_client

os.makedirs(config.OUTPUT_DIR, exist_ok=True)

app = FastAPI(title="Kubernetes Community Day New York 2026")


def _save_image(data: bytes, mime: str = "image/png") -> dict:
    ext = "png"
    if "jpeg" in mime or "jpg" in mime:
        ext = "jpg"
    elif "webp" in mime:
        ext = "webp"
    image_id = uuid.uuid4().hex
    filename = f"{image_id}.{ext}"
    path = os.path.join(config.OUTPUT_DIR, filename)
    with open(path, "wb") as f:
        f.write(data)
    return {"image_id": filename, "url": f"/outputs/{filename}"}


def _save_video(data: bytes) -> dict:
    filename = f"{uuid.uuid4().hex}.mp4"
    path = os.path.join(config.OUTPUT_DIR, filename)
    with open(path, "wb") as f:
        f.write(data)
    return {"video_id": filename, "url": f"/outputs/{filename}"}


def _load_image_b64(image_id: str) -> str:
    safe = os.path.basename(image_id)
    path = os.path.join(config.OUTPUT_DIR, safe)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="image_id not found")
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("ascii")


def _decode_data_url(data: str) -> bytes:
    if "," in data and data.strip().startswith("data:"):
        data = data.split(",", 1)[1]
    try:
        return base64.b64decode(data)
    except (binascii.Error, ValueError):
        raise HTTPException(status_code=400, detail="Invalid base64 image data")


class CaptureBody(BaseModel):
    image_b64: str


class TransformBody(BaseModel):
    image_id: str
    instruction: str = ""
    style_preset: str = ""


class AnimateBody(BaseModel):
    image_id: str
    instruction: str = ""
    motion_preset: str = ""


class ChatBody(BaseModel):
    message: str
    image_id: str | None = None


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


@app.get("/api/styles")
async def styles():
    try:
        return {"styles": await mcp_client.list_styles()}
    except Exception as exc:  # noqa: BLE001 - surface MCP connectivity issues
        raise HTTPException(status_code=502, detail=f"MCP server unavailable: {exc}")


@app.get("/api/video-styles")
async def video_styles():
    try:
        return {"styles": await mcp_client.list_video_styles()}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"MCP server unavailable: {exc}")


@app.post("/api/upload")
async def upload(file: UploadFile = File(...)):
    raw = await file.read()
    try:
        img = Image.open(io.BytesIO(raw))
        img.verify()
    except Exception:
        raise HTTPException(status_code=400, detail="Uploaded file is not a valid image")
    return _save_image(raw, file.content_type or "image/png")


@app.post("/api/capture")
async def capture(body: CaptureBody):
    raw = _decode_data_url(body.image_b64)
    return _save_image(raw, "image/png")


@app.post("/api/transform")
async def transform(body: TransformBody):
    image_b64 = _load_image_b64(body.image_id)
    try:
        result = await mcp_client.transform_image(
            image_b64, instruction=body.instruction, style_preset=body.style_preset
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Transform failed: {exc}")
    saved = _save_image(base64.b64decode(result["image_b64"]), result.get("mime", "image/png"))
    saved["note"] = result.get("note", "")
    return saved


@app.post("/api/animate")
async def animate(body: AnimateBody):
    image_b64 = _load_image_b64(body.image_id)
    try:
        result = await mcp_client.generate_video(
            image_b64, instruction=body.instruction, motion_preset=body.motion_preset
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Video generation failed: {exc}")
    return _save_video(base64.b64decode(result["video_b64"]))


@app.post("/api/chat")
async def chat(body: ChatBody):
    image_b64 = _load_image_b64(body.image_id) if body.image_id else None
    try:
        result = await agent.run(body.message, image_b64)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Agent error: {exc}")

    response = {"text": result.get("text", "")}
    if result.get("image_b64"):
        saved = _save_image(
            base64.b64decode(result["image_b64"]), result.get("mime", "image/png")
        )
        response.update(saved)
    if result.get("video_b64"):
        saved_video = _save_video(base64.b64decode(result["video_b64"]))
        response["video_url"] = saved_video["url"]
    return JSONResponse(response)


# Serve generated images. Mounted last so /api routes take precedence.
app.mount("/outputs", StaticFiles(directory=config.OUTPUT_DIR), name="outputs")
