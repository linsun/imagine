# One image for every keynote process that runs in Kubernetes: the Director
# (A2A, deployed by kagent as a BYO agent), Scout, DP, the viewfinder (photo
# inbox + stage page) and the camera/stage/post/publish MCP servers. The
# Deployment picks which one with its command.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

# ffmpeg for the credits, espeak-ng stands in for macOS `say`, DejaVu for text.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg espeak-ng fonts-dejavu-core \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY agent/requirements.txt agent-requirements.txt
COPY servers/requirements.txt servers-requirements.txt
RUN pip install --no-cache-dir -r agent-requirements.txt -r servers-requirements.txt \
    pillow-heif==1.1.1

COPY agent ./agent
COPY servers ./servers
COPY assets ./assets
COPY music ./music
COPY cast ./cast
# Spoken lines recorded on the Mac with `say` (servers/post_mcp.py --render-voices).
COPY voices ./voices

# Handles (img_x, vid_x) and inbox photos live here.
ENV OUTPUT_DIR=/data/outputs INBOX_DIR=/data/inbox
RUN mkdir -p /data/outputs /data/inbox && chmod -R 777 /data

EXPOSE 8080
CMD ["python", "-m", "agent.a2a_server"]
