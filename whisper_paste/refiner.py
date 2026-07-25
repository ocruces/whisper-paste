"""Text refinement using Ollama (Gemma) to clean up dictated text."""

import json
import logging
import re
import urllib.request
import urllib.error
from whisper_paste.config import OLLAMA_URL, OLLAMA_MODEL, REFINER_PROMPT

logger = logging.getLogger(__name__)

# Whatever answers on OLLAMA_URL gets its reply pasted at the user's cursor (or
# typed as keystrokes), and Ollama's API is unauthenticated — so the response is
# validated before it is trusted. \t and \n are deliberately allowed: multi-line
# formatting is what the refiner is for (see config.REFINER_PROMPT). What is
# rejected is the part with no legitimate role in dictation output: ESC and the
# other C0/C1 controls, which yield ANSI escape sequences that can rewrite a
# terminal's display or overwrite text with \r/backspace, and the invisible
# bidi/zero-width characters of the Trojan Source class.
_FORBIDDEN_CONTROL = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
# Zero-width chars, LRM/RLM, bidi embeddings/overrides, bidi isolates, BOM.
_FORBIDDEN_INVISIBLE = re.compile(
    "[\u200b-\u200f\u202a-\u202e\u2066-\u2069\ufeff]"
)

# A cleanup pass tightens text; it does not balloon it. Anything past this is a
# malfunctioning model or something that is not a model at all.
MAX_EXPANSION_FACTOR = 3
MAX_EXPANSION_CHARS = 100


def _normalise(text: str) -> str:
    """Fold line-ending and separator variants onto \\n, then trim."""
    for sep in ("\r\n", "\r", "\u2028", "\u2029"):
        text = text.replace(sep, "\n")
    return text.strip()


def _is_acceptable(refined: str, raw_text: str) -> bool:
    """True if `refined` is plausible cleanup output that is safe to paste."""
    if len(refined) > MAX_EXPANSION_FACTOR * len(raw_text) + MAX_EXPANSION_CHARS:
        return False
    return not (_FORBIDDEN_CONTROL.search(refined) or _FORBIDDEN_INVISIBLE.search(refined))


PROBE_TIMEOUT = 5  # seconds; the probe runs at startup and must not stall it


def _get_json(path: str):
    """GET `path` from the configured Ollama server and return the decoded body."""
    req = urllib.request.Request(f"{OLLAMA_URL}{path}")
    with urllib.request.urlopen(req, timeout=PROBE_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _model_available(wanted: str, names) -> bool:
    """True if `wanted` is among `names`, allowing an unqualified name to match a tag.

    'gemma3' matches a reported 'gemma3:latest', but 'gemma3:4b' does not match
    'gemma3:27b' — a different size is a different model, not a match.
    """
    if wanted in names:
        return True
    if ":" not in wanted:
        return any(name.split(":")[0] == wanted for name in names)
    return False


def probe():
    """Check the configured Ollama endpoint. Returns (ok, message); never raises.

    This is a misconfiguration detector, not an authentication check — anything
    listening on the port can return a version string and claim to be Ollama.
    The response validation in ``refine`` is what actually protects the text
    that gets pasted, and it holds regardless of who answers.
    """
    try:
        version = _get_json("/api/version").get("version", "unknown")
        tags = _get_json("/api/tags")
    except Exception as e:
        return False, (
            f"Ollama not reachable at {OLLAMA_URL} ({e}) — dictation will paste "
            f"the raw transcript. Start it with: ollama serve"
        )

    models = tags.get("models", []) if isinstance(tags, dict) else []
    names = [m.get("name", "") for m in models if isinstance(m, dict)]

    if not _model_available(OLLAMA_MODEL, names):
        return False, (
            f"Ollama {version} answered at {OLLAMA_URL} but model "
            f"'{OLLAMA_MODEL}' is not available — run: ollama pull {OLLAMA_MODEL}"
        )

    return True, f"Ollama {version} at {OLLAMA_URL}, model '{OLLAMA_MODEL}' ready."


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
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
        logger.warning("Ollama unavailable (%s), returning raw transcript.", e)
        return raw_text

    refined = body.get("response") if isinstance(body, dict) else None
    if not isinstance(refined, str):
        logger.warning("Refiner returned no usable text, using raw transcript.")
        return raw_text

    refined = _normalise(refined)
    if not refined:
        return raw_text

    if not _is_acceptable(refined, raw_text):
        logger.warning(
            "Refiner output rejected (%d chars for a %d-char transcript, or "
            "disallowed characters) — using raw transcript.",
            len(refined), len(raw_text),
        )
        return raw_text

    return refined
