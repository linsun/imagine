FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY vision_mcp ./vision_mcp

EXPOSE 8000

CMD ["python", "-m", "vision_mcp.server"]
