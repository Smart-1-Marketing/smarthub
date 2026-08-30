"""One reader of the OpenAI Responses API, for the two builders that use it.

The Proposal Builder and the IO Builder each carried their own copy of this
call, and the copies were the bug. The Proposal Builder's was found and fixed
-- the hosted web-search tool rode on **every** call, whether or not the
question had anything to look up, and a model that refuses the tool refuses
the whole request, so the search that was meant to help the ZIP lookup was
what stopped it and stopped seven other buttons with it. The IO Builder's copy
was never touched, so the identical failure was still live one module over:
four buttons -- the ZIP-radius lookup, the business description, the
landing-page review and the media-mix recommendation -- each returning a
different invented diagnosis of one shared cause.

That is the drift ``hub/storage.py`` and ``hub/images.py`` exist to stop,
wearing a model call: the next fix to it should land once.

Three ways of failing are named here rather than at each call site.

**The hosted tool is opt-in, and its refusal is not the end of the answer.**
Whether a hosted tool is available depends on the model, which is
``OPENAI_MODEL`` and is a 4o-class model on this deployment rather than the
``gpt-5-mini`` default written here. Where a caller genuinely wants live
search we ask for it and **fall back without it**: a list assembled with no
live lookup is worth having and is labelled; no list at all is a button that
does nothing.

**A refusal carries the API's own sentence.** ``raise_for_status()`` raises
"400 Client Error: Bad Request for url: …", which names neither the model nor
the thing it refused, so each caller reported a diagnosis it had invented and
none of them was checkable.

**An answer cut short is said to be that.** Reasoning and tool tokens count
against ``max_output_tokens``, so a truncated answer arrives with an empty
text body -- which the callers read as their own kinds of nothing ("OpenAI
returned no description", "No ZIP Codes were returned"), and two of them in
the IO Builder read as **success**: an empty landing-page review printed onto
the internal trafficking document, and a media-mix recommendation with every
field blank under a warning blaming the model for answering in prose.
"""
import os

import requests

ENDPOINT = "https://api.openai.com/v1/responses"


def post(payload, api_key):
    """The transport, on its own so a test can stand in front of it."""
    return requests.post(ENDPOINT,
                         headers={"Authorization": f"Bearer {api_key}",
                                  "Content-Type": "application/json"},
                         json=payload, timeout=120)


def error_line(resp):
    """What the API actually said, rather than the status line."""
    try:
        detail = (resp.json().get("error") or {}).get("message") or ""
    except (ValueError, AttributeError):
        detail = ""
    if not detail:
        detail = (resp.text or "")[:400].strip()
    return f"OpenAI returned HTTP {resp.status_code}" + (f": {detail}" if detail else ".")


def text_of(data):
    parts = []
    for item in data.get("output") or []:
        for content in item.get("content") or []:
            if content.get("type") in ("output_text", "text") and content.get("text"):
                parts.append(content["text"])
    return "\n".join(parts).strip()


def ask(prompt, *, module, purpose="", max_output_tokens=6000, search=False,
        call=None):
    """One call. Returns the text, or raises with the reason named.

    ``module`` and ``purpose`` are what the spend is recorded under, so the
    usage page can tell a ZIP lookup from a landing-page review rather than
    filing every call in a module under whichever purpose was written first.

    ``call`` is the transport, defaulting to ``post``. A caller passes its own
    only so a test already standing in front of that module's name goes on
    biting: the seam is the same one either way.
    """
    send = call or post
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OpenAI is not configured. Add OPENAI_API_KEY.")
    payload = {"model": os.getenv("OPENAI_MODEL", "gpt-5-mini"), "input": prompt,
               "max_output_tokens": max_output_tokens}
    if search:
        payload["tools"] = [{"type": "web_search"}]

    r = send(payload, api_key)
    if r.status_code == 400 and search:
        # The model would not take the tool. The question is still answerable.
        # Only on a 400: that is "this request is not something I can accept",
        # which is the tool refusal. A 401 is a key, a 429 is a rate limit and
        # a 5xx is theirs -- asking the identical question again costs a second
        # call and cannot change any of those answers.
        payload.pop("tools", None)
        r = send(payload, api_key)
    if r.status_code >= 400:
        raise RuntimeError(error_line(r))

    try:
        data = r.json()
    except ValueError:
        raise RuntimeError(
            "OpenAI returned a response that could not be read as JSON.") from None

    try:  # record spend so /diagnostics doesn't under-report
        from hub import ai as _hub_ai
        _hub_ai.note_usage(module, data, purpose=purpose)
    except Exception:  # noqa: BLE001
        pass

    text = text_of(data)
    if not text and data.get("status") == "incomplete":
        why = ((data.get("incomplete_details") or {}).get("reason") or "").replace("_", " ")
        raise RuntimeError("The model stopped before it answered"
                           + (f" ({why})" if why else "")
                           + ". Nothing was returned to show.")
    return text


__all__ = ["ask", "post", "error_line", "text_of", "ENDPOINT"]
