from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .settings import Settings


def _json_request(
    url: str,
    method: str = "GET",
    body: dict | None = None,
    timeout: float = 8.0,
) -> dict:
    request = Request(
        url,
        data=json.dumps(body).encode("utf-8") if body is not None else None,
        headers={"Content-Type": "application/json"} if body is not None else {},
        method=method,
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        try:
            payload = json.loads(exc.read().decode("utf-8", errors="replace"))
        except Exception:
            payload = {}
        detail = payload.get("message") or payload.get("error")
        if isinstance(detail, dict):
            detail = detail.get("message")
        raise RuntimeError(str(detail or f"LM Studio returned {exc.code}")) from exc
    except (URLError, TimeoutError) as exc:
        raise RuntimeError("LM Studio is unavailable. Press Load AI in Clip Maker first.") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("LM Studio returned an invalid response")
    return payload


def _controller_request(
    settings: Settings,
    path: str,
    method: str = "GET",
    timeout: float = 8.0,
) -> dict:
    try:
        return _json_request(
            settings.lmstudio_control_url.rstrip("/") + path,
            method=method,
            timeout=timeout,
        )
    except RuntimeError as exc:
        raise RuntimeError(
            "AI Council's LM Studio controller is unavailable. Start its host controller first."
        ) from exc


def lmstudio_state(settings: Settings) -> dict:
    if settings.llm_active_profile:
        from .llm import selected
        profile=selected(settings)
        return {"ok":True,"mode":"profile","profile":{k:profile[k] for k in ["name","location","kind","model"]}}
    return _controller_request(settings, "/state")


def set_lmstudio_mode(settings: Settings, mode: str) -> dict:
    if settings.llm_active_profile:
        raise ValueError("Manage this connection in Settings > AI tools. Load local models in their provider app.")
    if mode not in {"ai", "gaming"}:
        raise ValueError("Mode must be ai or gaming")
    timeout = 230.0 if mode == "ai" else 90.0
    result = _controller_request(
        settings,
        "/start" if mode == "ai" else "/stop",
        method="POST",
        timeout=timeout,
    )
    state = lmstudio_state(settings)
    return {**result, "state": state}


def lmstudio_chat(
    settings: Settings,
    prompt: str,
    *,
    temperature: float = 0.8,
    max_tokens: int = 180,
    timeout: float = 300.0,
) -> str:
    if settings.llm_active_profile:
        from .llm import chat
        return chat(settings,prompt,temperature=temperature,max_tokens=max_tokens,timeout=timeout)
    state = lmstudio_state(settings)
    if not state.get("server_up") or not state.get("model_loaded"):
        raise RuntimeError(
            "LM Studio is in Gaming Mode. Press Load AI in Clip Maker, then try again."
        )
    payload = _json_request(
        settings.lmstudio_api_url.rstrip("/") + "/chat/completions",
        method="POST",
        body={
            "model": settings.lmstudio_model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
            "reasoning_effort": "none",
            "max_tokens": max_tokens,
            "stream": False,
        },
        timeout=timeout,
    )
    try:
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError("LM Studio returned no usable message") from exc
    result = str(content or "").strip().strip('"')
    if not result:
        raise RuntimeError("LM Studio returned an empty message")
    return result
