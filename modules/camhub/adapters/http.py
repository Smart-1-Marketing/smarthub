"""One way to call a public data API, with the User-Agent every one of them
wants and a timeout every one of them needs.

Kept separate from `hub/outbound.py` on purpose: that helper exists to guard
fetches of URLs a *stranger typed* (SSRF), following redirects by hand and
capping bodies. These are provider endpoints we chose, on a fixed host list,
and the thing they need is the identifying header -- NWS throttles or
blocks a call without one -- read once from hub/config.py rather than
spelled in each adapter.

Tests patch `get_json` and `get_text` here, so no adapter opens a socket
under test.
"""
from __future__ import annotations

import json

import requests

TIMEOUT = 20


class SourceError(Exception):
    """A source could not be read. The message is for the health screen."""


def user_agent() -> str:
    try:
        from hub.config import settings
        return settings.camhub_user_agent
    except Exception:  # noqa: BLE001 -- config must not sink a fetch
        return "SmartHub-CamModule/1.0"


def _get(url: str, params=None, headers=None, timeout: int = TIMEOUT):
    merged = {"User-Agent": user_agent(), "Accept": "application/json, text/plain, */*"}
    merged.update(headers or {})
    try:
        return requests.get(url, params=params or None, headers=merged, timeout=timeout)
    except requests.RequestException as exc:
        raise SourceError(f"{type(exc).__name__}: {exc}") from exc


def get_json(url: str, params=None, headers=None, timeout: int = TIMEOUT):
    r = _get(url, params, headers, timeout)
    if r.status_code >= 400:
        raise SourceError(f"HTTP {r.status_code} from {url.split('?')[0]}")
    try:
        return r.json()
    except (ValueError, json.JSONDecodeError) as exc:
        raise SourceError(f"not JSON from {url.split('?')[0]}") from exc


def get_text(url: str, params=None, headers=None, timeout: int = TIMEOUT) -> str:
    r = _get(url, params, headers, timeout)
    if r.status_code >= 400:
        raise SourceError(f"HTTP {r.status_code} from {url.split('?')[0]}")
    return r.text
