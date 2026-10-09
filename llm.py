"""LLM providers: local Ollama (default) and Anthropic Claude API. Stdlib only."""
import json
import re
import threading
import urllib.error
import urllib.request

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"


class LLMError(Exception):
    pass


_NO_TEMPERATURE = set()  # Claude models that refuse a temperature setting
_GPU = threading.Lock()  # one local-model call at a time (drafts and fit rating share the GPU)


def _post(url, payload, headers=None, timeout=900):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:500]
        raise LLMError(f"HTTP {e.code} from {url}: {body}") from None
    except urllib.error.URLError as e:
        raise LLMError(f"Cannot reach {url}: {e.reason}") from None


def _get(url, timeout=15):
    req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise LLMError(f"HTTP {e.code} from {url}") from None
    except urllib.error.URLError as e:
        raise LLMError(f"Cannot reach {url}: {e.reason}") from None


def model_label(settings, provider=None):
    provider = provider or settings.get("provider", "anthropic")
    if provider == "anthropic":
        return f"Claude API · {settings.get('anthropic_model')}"
    return f"Local Ollama · {settings.get('ollama_model')}"


def complete(settings, system, user, provider=None, json_mode=True, max_tokens=8000, model=None):
    """Return (text, provider, model). `model` overrides the Claude model (fit rating uses a cheaper one)."""
    provider = provider or settings.get("provider", "anthropic")
    temp = float(settings.get("temperature", 0.3))
    if provider == "anthropic":
        key = settings.get("anthropic_api_key") or ""
        if not key:
            raise LLMError("No Claude API key set. Add one in Settings, or switch to the local model.")
        model = model or settings.get("anthropic_model") or "claude-sonnet-5-5"
        # cache the (long, repeated) system prompt: your profile + inventory. Cached reads cost ~10%.
        body = {"model": model, "max_tokens": max_tokens,
                "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                "messages": [{"role": "user", "content": user}]}
        if model not in _NO_TEMPERATURE:
            body["temperature"] = temp
        hdr = {"x-api-key": key, "anthropic-version": ANTHROPIC_VERSION}
        try:
            resp = _post(ANTHROPIC_URL, body, headers=hdr)
        except LLMError as e:
            # newer models reject sampling settings; drop them and remember for this model
            if "temperature" in str(e) and "temperature" in body:
                _NO_TEMPERATURE.add(model)
                body.pop("temperature")
                resp = _post(ANTHROPIC_URL, body, headers=hdr)
            else:
                raise
        text = "".join(b.get("text", "") for b in resp.get("content", []) if b.get("type") == "text")
        if not text:
            raise LLMError(f"Empty response from Claude (stop_reason={resp.get('stop_reason')})")
        return text, "anthropic", model
    base = (settings.get("ollama_url") or "http://localhost:11434").rstrip("/")
    model = settings.get("ollama_model") or "qwen3:14b"
    payload = {
        "model": model,
        "stream": False,
        "think": False,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "options": {"num_ctx": int(settings.get("ollama_num_ctx", 24576)), "temperature": temp,
                    "num_predict": max_tokens},
    }
    if json_mode:
        payload["format"] = "json"
    with _GPU:
        resp = _post(base + "/api/chat", payload)
    text = (resp.get("message") or {}).get("content", "")
    if not text:
        raise LLMError("Empty response from Ollama")
    return text, "ollama", model


def test_connection(settings, provider=None):
    provider = provider or settings.get("provider", "anthropic")
    if provider == "anthropic":
        text, _, model = complete(settings, "Reply with the single word OK.", "ping",
                                  provider="anthropic", json_mode=False, max_tokens=5)
        return {"ok": True, "detail": f"Claude API reachable ({model}): {text.strip()[:20]}"}
    base = (settings.get("ollama_url") or "").rstrip("/")
    tags = _get(base + "/api/tags")
    names = [m.get("name") for m in tags.get("models", [])]
    want = settings.get("ollama_model")
    if want not in names:
        return {"ok": False, "detail": f"Ollama reachable, but model '{want}' not found. Available: {', '.join(names[:15])}",
                "models": names}
    return {"ok": True, "detail": f"Ollama reachable; model '{want}' available.", "models": names}


def parse_json(text):
    """Extract the first JSON object from model output (tolerates fences / think tags)."""
    t = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t.strip())
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    start = t.find("{")
    end = t.rfind("}")
    if start >= 0 and end > start:
        body = t[start:end + 1]
        try:
            return json.loads(body)
        except json.JSONDecodeError as e:
            fixed = repair_json(body)
            if fixed is not None:
                return fixed
            raise LLMError(f"Model returned invalid JSON: {e}") from None
    raise LLMError("Model did not return JSON")


def repair_json(s, max_fixes=25):
    """Fix the slips models make in long JSON — a missing comma between items, or a trailing comma —
    using the parser's own error position. Returns the parsed object, or None if it can't be fixed."""
    for _ in range(max_fixes):
        try:
            return json.loads(s)
        except json.JSONDecodeError as e:
            p = e.pos
            if e.msg.startswith("Expecting ',' delimiter"):
                s = s[:p] + "," + s[p:]                       # "a": 1 "b": 2  ->  "a": 1, "b": 2
            elif e.msg.startswith("Illegal trailing comma") or (
                    e.msg.startswith(("Expecting property name", "Expecting value")) and p < len(s) and s[p] in "]}"):
                # Python 3.13+ says "Illegal trailing comma" and points at the comma; older versions point after it
                q = p if p < len(s) and s[p] == "," else p - 1
                while q >= 0 and s[q] in " \t\r\n":
                    q -= 1
                if q < 0 or s[q] != ",":
                    return None
                s = s[:q] + s[q + 1:]                        # [1, 2, ]  ->  [1, 2]
            else:
                return None
    return None
