"""Text refinement using Ollama (Gemma) to clean up dictated text."""

import json
import urllib.request
import urllib.error
from config import OLLAMA_URL, OLLAMA_MODEL, REFINER_PROMPT


def refine(raw_text: str) -> str:
    """Send raw transcription to Ollama/Gemma for cleanup. Falls back to raw text on error."""
    if not raw_text:
        return raw_text

    prompt = REFINER_PROMPT.format(text=raw_text)
    payload = json.dumps({
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": 0.3,
            "num_predict": 1024,
        },
    }).encode("utf-8")

    req = urllib.request.Request(
        f"{OLLAMA_URL}/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            refined = body.get("response", "").strip()
            return refined if refined else raw_text
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
        print(f"Ollama unavailable ({e}), returning raw transcript.")
        return raw_text
