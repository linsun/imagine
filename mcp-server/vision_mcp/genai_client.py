"""Shared google-genai client factory used by the image and video tools."""

import os

from google import genai

_client: genai.Client | None = None


def get_client() -> genai.Client:
    global _client
    if _client is None:
        api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not api_key:
            raise RuntimeError(
                "GEMINI_API_KEY (or GOOGLE_API_KEY) is not set. "
                "Get a key at https://aistudio.google.com/apikey"
            )
        _client = genai.Client(api_key=api_key)
    return _client
