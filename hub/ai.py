"""One OpenAI client for the whole Hub, with usage accounting.

Before v7 there were eight independent call sites — hub/seo.py, hub/faq.py,
modules/seo_images, image_creator, ads_builder, proposal_builder, sales_builder
and google_finder. Each read the key itself, each picked its own default model,
each wrote its own retry. The defaults had silently diverged: gpt-4o-mini in
five places, gpt-4o in the vision path, and gpt-5-mini in sales_builder. The
same prompt hit a different model depending on which screen you were on.

Nothing recorded what any of it cost.

This module fixes both. Every call goes through one function with one retry
policy and one timeout, and every call writes a usage row. That usage log is
not bookkeeping for its own sake — it is what makes "which tool is spending my
OpenAI budget" answerable, and it is the table the Ask assistant queries.
"""
from __future__ import annotations

import json
import time
from typing import Any

import requests

from hub import audit
from hub.config import settings

API = "https://api.openai.com/v1"

# Approximate USD per 1M tokens. Used for a running estimate on the spend
# dashboard — deliberately approximate, and clearly labelled as such wherever
# it is displayed. Update when pricing changes.
_PRICING = {
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-image-1": (5.00, 0.00),
}


class AIUnavailable(RuntimeError):
    """OpenAI is not configured, or failed after retries.

    Callers should catch this and fall back to their template path rather than
    surfacing it. Raw provider errors must never reach a customer-facing page —
    that leaked an API-key prefix onto a public lead form in the suite audit.
    """


def ready() -> bool:
    return settings.openai_ready


def estimate_cost(model: str, tokens_in: int, tokens_out: int) -> float:
    rate_in, rate_out = _PRICING.get(model, _PRICING["gpt-4o-mini"])
    return round(tokens_in / 1e6 * rate_in + tokens_out / 1e6 * rate_out, 6)


def _record(module: str, purpose: str, model: str, usage: dict,
            ms: int, ok: bool, error: str = "") -> None:
    if not settings.ai_usage_log:
        return
    tin = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
    tout = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
    # "tool", not "module": audit.log()'s first positional is module, so
    # module= in the extras raises TypeError. This would have fired on every
    # single AI call.
    audit.log("ai", "call", tool=module, purpose=purpose, model=model,
              tokens_in=tin, tokens_out=tout,
              est_cost=estimate_cost(model, tin, tout),
              ms=ms, ok=ok, error=(error or None))


def _post(path: str, payload: dict, timeout: int) -> dict:
    resp = requests.post(
        f"{API}{path}",
        headers={"Authorization": f"Bearer {settings.openai_key}",
                 "Content-Type": "application/json"},
        json=payload, timeout=timeout)
    if resp.status_code >= 400:
        # Deliberately does not include the response body: provider errors have
        # echoed back key prefixes before.
        raise AIUnavailable(f"OpenAI returned HTTP {resp.status_code}.")
    return resp.json()


def chat(messages: list[dict], *, module: str, purpose: str,
         model: str | None = None, json_mode: bool = False,
         max_tokens: int = 2000, temperature: float = 0.4,
         timeout: int | None = None) -> str:
    """Text completion. Raises AIUnavailable; never returns a partial string."""
    if not ready():
        raise AIUnavailable("OPENAI_API_KEY is not set.")
    model = model or settings.openai_model
    timeout = timeout or settings.openai_timeout
    payload: dict[str, Any] = {"model": model, "messages": messages,
                               "max_tokens": max_tokens, "temperature": temperature}
    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    last = ""
    for attempt in range(settings.openai_retries + 1):
        started = time.time()
        try:
            data = _post("/chat/completions", payload, timeout)
            ms = int((time.time() - started) * 1000)
            choice = (data.get("choices") or [{}])[0]
            # A truncated response is a failure, not a result. Parsing one as
            # JSON is what produced "Expecting value: line 1 column 1" on a
            # customer-facing page.
            if choice.get("finish_reason") == "length":
                _record(module, purpose, model, data.get("usage", {}), ms, False, "truncated")
                raise AIUnavailable("The model's answer was cut off before it finished.")
            _record(module, purpose, model, data.get("usage", {}), ms, True)
            return (choice.get("message") or {}).get("content", "") or ""
        except AIUnavailable:
            raise
        except Exception as exc:            # noqa: BLE001 — network/timeout
            last = type(exc).__name__
            _record(module, purpose, model, {}, int((time.time() - started) * 1000), False, last)
            if attempt < settings.openai_retries:
                time.sleep(1.5 * (attempt + 1))
    raise AIUnavailable(f"OpenAI did not respond after {settings.openai_retries + 1} attempts ({last}).")


def chat_json(messages: list[dict], *, module: str, purpose: str, **kw) -> dict:
    """chat() that guarantees a dict back, or raises."""
    raw = chat(messages, module=module, purpose=purpose, json_mode=True, **kw)
    try:
        return json.loads(raw)
    except ValueError as exc:
        raise AIUnavailable("The model returned something that wasn't valid JSON.") from exc


def vision(prompt: str, image_urls: list[str], *, module: str, purpose: str,
           model: str | None = None, **kw) -> str:
    """Image understanding — used by the SEO Image Pipeline for alt text."""
    content: list[dict] = [{"type": "text", "text": prompt}]
    for url in image_urls[:8]:
        content.append({"type": "image_url", "image_url": {"url": url}})
    return chat([{"role": "user", "content": content}],
                module=module, purpose=purpose,
                model=model or settings.openai_vision_model, **kw)


def image(prompt: str, *, module: str, purpose: str, size: str = "1024x1024",
          transparent: bool = False) -> bytes:
    """Generate an image, returned as raw bytes ready for hub.storage.put()."""
    if not ready():
        raise AIUnavailable("OPENAI_API_KEY is not set.")
    import base64
    model = settings.openai_image_model
    payload = {"model": model, "prompt": prompt, "size": size, "n": 1}
    if transparent:
        payload["background"] = "transparent"
    started = time.time()
    try:
        data = _post("/images/generations", payload, settings.openai_timeout * 2)
        _record(module, purpose, model, data.get("usage", {}),
                int((time.time() - started) * 1000), True)
        return base64.b64decode((data.get("data") or [{}])[0].get("b64_json", ""))
    except AIUnavailable:
        raise
    except Exception as exc:                # noqa: BLE001
        _record(module, purpose, model, {}, int((time.time() - started) * 1000), False, type(exc).__name__)
        raise AIUnavailable("Image generation failed.") from exc


def usage_summary(days: int = 30) -> dict:
    """Spend by module over a window — powers the provider-spend audit."""
    from datetime import datetime, timedelta, timezone
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    by_module: dict[str, dict] = {}
    for row in audit.read(limit=50000, module="ai"):
        try:
            when = datetime.fromisoformat(row.get("time", ""))
        except ValueError:
            continue
        if when < cutoff:
            continue
        key = row.get("tool") or "unknown"
        acc = by_module.setdefault(key, {"calls": 0, "tokens": 0, "cost": 0.0, "errors": 0})
        acc["calls"] += 1
        acc["tokens"] += int(row.get("tokens_in", 0)) + int(row.get("tokens_out", 0))
        acc["cost"] += float(row.get("est_cost", 0) or 0)
        if not row.get("ok"):
            acc["errors"] += 1
    for acc in by_module.values():
        acc["cost"] = round(acc["cost"], 4)
    return {"days": days, "by_module": by_module,
            "total_cost": round(sum(a["cost"] for a in by_module.values()), 4)}


def note_usage(module: str, response_json: dict, *, model: str = "",
               purpose: str = "", ok: bool = True, ms: int = 0) -> None:
    """Record spend for a call made outside this module's own client.

    Eight modules call api.openai.com directly. Rewriting all of them at once
    is a large, risky change; recording what they spend is one line each and
    carries no behavioural risk. Until they migrate onto chat()/vision(), this
    is what stops the cost estimate silently under-reporting.

    Safe to call with a partial or malformed response — a failure to record
    spend must never break the feature that spent it.
    """
    try:
        usage = (response_json or {}).get("usage") or {}
        mdl = model or (response_json or {}).get("model") or settings.openai_model
        _record(module, purpose or "external", str(mdl), usage, ms, ok)
    except Exception:                                   # noqa: BLE001
        pass


def note_sdk_usage(module: str, response, *, purpose: str = "",
                   ok: bool = True, ms: int = 0) -> None:
    """Same as note_usage(), for the official OpenAI SDK.

    The SDK returns a pydantic object rather than a dict, so `.usage` is an
    attribute chain instead of a key lookup. Two modules use the SDK and two
    use raw HTTP; supporting both is what closes the last of the untracked
    spend without rewriting either.
    """
    try:
        usage = getattr(response, "usage", None)
        payload = {
            "prompt_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
            "completion_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
        }
        model = str(getattr(response, "model", "") or settings.openai_model)
        _record(module, purpose or "external", model, payload, ms, ok)
    except Exception:                                   # noqa: BLE001
        pass
