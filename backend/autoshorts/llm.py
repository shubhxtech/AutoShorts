"""On-device LLM. No API keys, nothing leaves the machine.

Backends:
  mlx     Apple Silicon (mlx-lm), loaded in-process
  server  any local OpenAI-compatible server: Ollama (default http://localhost:11434/v1), LM Studio,
          llama.cpp server, vLLM. This is how Windows and Linux run the LLM on NVIDIA, AMD or CPU.

One model is kept in memory at a time and dropped after a few idle minutes
(or explicitly, before Whisper or rendering needs the RAM). Every call asks for
JSON matching a Pydantic schema; malformed output is retried with the parse
error fed back to the model.
"""

from __future__ import annotations

import gc
import json
import re
import threading
import time
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from . import logs, settings

log = logs.get("llm")
T = TypeVar("T", bound=BaseModel)

_lock = threading.RLock()
_state: dict = {"name": None, "model": None, "tokenizer": None, "last_used": 0.0}
IDLE_UNLOAD_S = 300


class LLMUnavailable(Exception):
    pass


def backend() -> str:
    b = (settings.get("llm_backend") or "auto").lower()
    if b in ("mlx", "server"):
        return b
    try:
        import mlx_lm  # noqa: F401
        return "mlx"
    except ImportError:
        return "server"


def available() -> bool:
    if not settings.get("llm_enabled"):
        return False
    if backend() == "server":
        return _server_up()
    try:
        import mlx_lm  # noqa: F401
    except ImportError:
        return False
    return True


_server_check = {"at": 0.0, "ok": False}


def _server_up() -> bool:
    import httpx

    if time.time() - _server_check["at"] < 20:
        return _server_check["ok"]
    try:
        r = httpx.get(settings.get("llm_server_url").rstrip("/") + "/models", timeout=3)
        ok = r.status_code == 200
    except Exception:
        ok = False
    _server_check.update(at=time.time(), ok=ok)
    return ok


def _server_complete(system: str, user: str, max_tokens: int, temperature: float, json_mode: bool) -> str:
    import httpx

    body = {"model": settings.get("llm_server_model"), "temperature": temperature, "top_p": 0.9,
            "max_tokens": max_tokens, "stream": False,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    r = httpx.post(settings.get("llm_server_url").rstrip("/") + "/chat/completions", json=body, timeout=600)
    if r.status_code >= 400:
        raise LLMUnavailable(f"LLM server error {r.status_code}: {r.text[:300]}")
    return r.json()["choices"][0]["message"]["content"] or ""


def _load(name: str):
    with _lock:
        if _state["name"] == name and _state["model"] is not None:
            return _state["model"], _state["tokenizer"]
        unload()
        from mlx_lm import load

        log.info("loading local model %s (first run downloads it)", name)
        t0 = time.time()
        model, tokenizer = load(name)
        log.info("model ready in %.1fs", time.time() - t0)
        _state.update(name=name, model=model, tokenizer=tokenizer, last_used=time.time())
        _start_reaper()
        return model, tokenizer


def unload() -> None:
    if backend() == "server":
        _server_unload()
        return
    with _lock:
        if _state["model"] is None:
            return
        log.info("unloading %s", _state["name"])
        _state.update(name=None, model=None, tokenizer=None)
        gc.collect()
        try:
            import mlx.core as mx

            mx.clear_cache()
        except Exception:  # noqa: S110 - best-effort cleanup, nothing to recover
            pass


def _server_unload() -> None:
    """Free the GPU for Whisper and image models: Ollama drops a model on keep_alive=0."""
    import httpx

    url = settings.get("llm_server_url").rstrip("/")
    if ":11434" not in url:
        return
    try:
        httpx.post(url.rsplit("/v1", 1)[0] + "/api/generate",
                   json={"model": settings.get("llm_server_model"), "keep_alive": 0}, timeout=10)
    except Exception:
        log.debug("Ollama did not accept the unload request", exc_info=True)


_reaper_started = False


def _start_reaper() -> None:
    global _reaper_started
    if _reaper_started:
        return
    _reaper_started = True

    def loop():
        while True:
            time.sleep(30)
            if _state["model"] is not None and time.time() - _state["last_used"] > IDLE_UNLOAD_S:
                unload()

    threading.Thread(target=loop, name="llm-reaper", daemon=True).start()


def _prompt(tokenizer, system: str, user: str) -> str:
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    try:
        return tokenizer.apply_chat_template(messages, add_generation_prompt=True, tokenize=False,
                                             enable_thinking=False)
    except TypeError:
        return tokenizer.apply_chat_template(messages, add_generation_prompt=True, tokenize=False)


def complete(system: str, user: str, *, max_tokens: int = 1500, temperature: float = 0.4,
             model: str | None = None, json_mode: bool = False) -> str:
    if not available():
        raise LLMUnavailable("local LLM is disabled, or no backend is running (install Ollama, or mlx-lm on Apple Silicon)")
    if backend() == "server":
        with _lock:
            return _strip_thinking(_server_complete(system, user, max_tokens, temperature, json_mode))
    name = model or settings.get("llm_model")
    from mlx_lm import generate
    from mlx_lm.sample_utils import make_sampler

    with _lock:
        mdl, tok = _load(name)
        prompt = _prompt(tok, system, user)
        out = generate(mdl, tok, prompt=prompt, max_tokens=max_tokens,
                       sampler=make_sampler(temp=temperature, top_p=0.9), verbose=False)
        _state["last_used"] = time.time()
    return _strip_thinking(out)


def _strip_thinking(text: str) -> str:
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    return text.replace("<think>", "").strip()


def extract_json(text: str):
    """Pull the first complete JSON object or array out of model output."""
    text = _strip_thinking(text)
    fence = re.search(r"```(?:json)?\s*(.+?)```", text, re.S)
    if fence:
        text = fence.group(1)
    start = min([i for i in (text.find("{"), text.find("[")) if i >= 0], default=-1)
    if start < 0:
        raise ValueError("no JSON found in model output")
    opener = text[start]
    closer = "}" if opener == "{" else "]"
    depth, in_str, esc = 0, False, False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
            if depth == 0 and ch == closer:
                chunk = text[start:i + 1]
                chunk = re.sub(r",\s*([}\]])", r"\1", chunk)   # trailing commas
                return json.loads(chunk)
    raise ValueError("unterminated JSON in model output")


def structured(schema: type[T], system: str, user: str, *, max_tokens: int = 2000, temperature: float = 0.3,
               model: str | None = None, attempts: int = 2) -> T:
    """Ask for JSON matching `schema` and return the validated object."""
    shape = json.dumps(schema.model_json_schema(), ensure_ascii=False)
    sys_prompt = (f"{system}\n\nReply with a single JSON object that matches this JSON Schema and nothing else:\n"
                  f"{shape}")
    prompt = user
    last_err: Exception | None = None
    for attempt in range(attempts):
        raw = complete(sys_prompt, prompt, max_tokens=max_tokens, temperature=temperature if attempt == 0 else 0.1,
                       model=model, json_mode=True)
        try:
            return schema.model_validate(extract_json(raw))
        except (ValueError, ValidationError) as exc:
            last_err = exc
            log.warning("model returned invalid JSON (attempt %d): %s", attempt + 1, str(exc)[:300])
            prompt = (f"{user}\n\nYour previous reply could not be parsed: {str(exc)[:400]}\n"
                      "Reply again with only the corrected JSON object.")
    raise ValueError(f"model never produced valid JSON: {last_err}")
