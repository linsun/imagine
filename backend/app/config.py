import os

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY", "")
MCP_URL = os.environ.get("MCP_URL", "http://localhost:8000/mcp")
TEXT_MODEL = os.environ.get("TEXT_MODEL", "gemini-2.5-flash")
OUTPUT_DIR = os.environ.get("OUTPUT_DIR", "/data/outputs")
