"""One Pickaxe client for the whole Hub, with usage accounting.

Mirrors hub/ai.py deliberately. A Pickaxe call is an outbound spend on a
metered provider, exactly like an OpenAI call, and the reason ai.py exists —
"which tool is spending my budget" has to stay answerable — applies here
identically. Pickaxe bills per use rather than per token, so each call writes
one quotas row (`quotas.record("pickaxe", ...)`) and the usage page counts
calls; no ceiling is invented, per the rule the HeyGen row already follows.

Every Pickaxe in the workspace already carries its own model, prompt frame,
knowledge base and RAG budget. The Hub does not restate any of that: it names
a Pickaxe and passes input. That is the whole point of calling Pickaxe rather
than rebuilding it here — see hub/pickaxe_registry.py for which Pickaxes the
Hub calls and why. Everything absorbable was absorbed instead
(hub/prompts_harvested.py); only the two with knowledge bases the Hub cannot
hold earn a live call.

The settings live in hub/config.py (PICKAXE_API_KEY, PICKAXE_BASE,
PICKAXE_WORKSPACE_ID, PICKAXE_TIMEOUT, PICKAXE_RETRIES), and a missing key is
a warn row on /health rather than a silent degrade.

VERIFY ON FIRST LIVE CALL: what Pickaxe's own published pages confirm is the
base host (api.pickaxe.co), Bearer auth, one endpoint per agent, server-side
conversation ids and usage in every response. The exact path spelling
(``/agents/<id>``) and the response field names are transcribed from their
examples rather than exercised — their docs site is unreachable from this
sandbox, and Pickaxe has changed paths between API versions before. That is
why every caller catches PickaxeUnavailable and falls back to the Hub's own
AI: a wrong path here costs the Pickaxe answer, never the feature, and the
recorded ``http_404`` rows are what would say so.
"""
from __future__ import annotations

import time
from typing import Any

import requests

from hub import quotas
from hub.config import settings


class PickaxeUnavailable(RuntimeError):
    """Pickaxe is not configured, or failed after retries.

    Callers catch this and fall back, the same contract as ai.AIUnavailable.
    Raw provider errors must never reach a page — provider errors have echoed
    back key prefixes before, which is why no response body is ever carried.
    """


def ready() -> bool:
    return settings.pickaxe_ready


def _record(module: str, purpose: str, pickaxe_id: str,
            ok: bool, error: str = "") -> None:
    # Cannot raise: a failure to record spend must never break the feature
    # that spent it — hub/quotas.py's own rule for every record helper.
    try:
        quotas.record("pickaxe", module=module, units=1, model=pickaxe_id,
                      detail=(f"{purpose} — {error}" if error else purpose)[:120],
                      ok=ok)
    except Exception:                                   # noqa: BLE001
        pass


def ask(pickaxe_id: str, *, module: str, purpose: str,
        message: str | None = None, inputs: dict | None = None,
        conversation_id: str | None = None, user_id: str | None = None,
        workspace_id: str | None = None,
        timeout: int | None = None) -> str:
    """Run one Pickaxe and return its reply. Raises PickaxeUnavailable.

    message        for chat Pickaxes (chatflag=true)
    inputs         for form Pickaxes — keyed by the "userinput:<uuid>" field
                   ids in that Pickaxe's promptframe; hub/pickaxe_registry.py
                   holds the maps so call sites never carry raw UUIDs
    user_id        the Hub user, so Pickaxe can keep per-user memory
    workspace_id   required with a Personal API Key; a workspace-scoped key
                   already knows its workspace and ignores it
    """
    if not ready():
        raise PickaxeUnavailable("PICKAXE_API_KEY is not set.")
    timeout = timeout or settings.pickaxe_timeout

    payload: dict[str, Any] = {}
    if message is not None:
        payload["message"] = message
    if inputs:
        payload["inputs"] = inputs
    if conversation_id:
        payload["conversationId"] = conversation_id
    if user_id:
        payload["userId"] = user_id
    ws = workspace_id or settings.pickaxe_workspace_id
    if ws:
        payload["workspaceId"] = ws

    last = ""
    for attempt in range(settings.pickaxe_retries + 1):
        try:
            resp = requests.post(
                # One endpoint per agent — see the VERIFY note in the module
                # docstring before trusting this path further than a fallback.
                f"{settings.pickaxe_base}/agents/{pickaxe_id}",
                headers={"Authorization": f"Bearer {settings.pickaxe_key}",
                         "Content-Type": "application/json"},
                json=payload, timeout=timeout)
            if resp.status_code >= 400:
                # Body deliberately omitted: provider errors have echoed back
                # key prefixes before.
                _record(module, purpose, pickaxe_id, False, f"http_{resp.status_code}")
                raise PickaxeUnavailable(f"Pickaxe returned HTTP {resp.status_code}.")
            text = _reply_text(resp.json())
            if not text:
                _record(module, purpose, pickaxe_id, False, "empty")
                raise PickaxeUnavailable("Pickaxe returned an empty answer.")
            _record(module, purpose, pickaxe_id, True)
            return strip_outro(text)
        except PickaxeUnavailable:
            raise
        except Exception as exc:            # noqa: BLE001 — network/timeout
            last = type(exc).__name__
            _record(module, purpose, pickaxe_id, False, last)
            if attempt < settings.pickaxe_retries:
                time.sleep(1.5 * (attempt + 1))
    raise PickaxeUnavailable(
        f"Pickaxe did not respond after {settings.pickaxe_retries + 1} attempts ({last}).")


def _reply_text(data) -> str:
    """The reply out of whichever field the API put it in.

    Tolerant on purpose, for the reason the VERIFY note gives: the response
    shape is transcribed from examples, not exercised. A shape none of these
    match reads as an empty answer, which the caller's fallback covers.
    """
    if not isinstance(data, dict):
        return ""
    for key in ("response", "message", "output", "text", "answer", "reply"):
        val = data.get(key)
        if isinstance(val, str) and val.strip():
            return val
    inner = data.get("data")
    if isinstance(inner, dict):
        return _reply_text(inner)
    choices = data.get("choices")
    if isinstance(choices, list) and choices:
        msg = (choices[0] or {}).get("message") or {}
        val = msg.get("content")
        if isinstance(val, str) and val.strip():
            return val
    return ""


# Every Smart 1 Test promptframe ends by offering to email the answer and
# generate a PDF. Those offers are for Pickaxe's own chat UI; arriving inside
# a Hub tool they read as the bot asking the rep questions nobody can answer.
# Strip trailing lines that are only that outro.
_OUTRO_MARKERS = (
    "would you like this to be sent",
    "would you prefer to download this as a pdf",
    "kindly provide the email address",
    "generate a downloadable pdf",
    "do not ask any further questions",
)


def strip_outro(text: str) -> str:
    lines = str(text or "").rstrip().split("\n")
    while lines:
        tail = lines[-1].strip().lower()
        if not tail or any(m in tail for m in _OUTRO_MARKERS):
            lines.pop()
            continue
        break
    return "\n".join(lines).rstrip()
