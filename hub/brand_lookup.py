"""One live brand lookup for the whole Hub, and it keeps what it paid for.

Three modules asked Brandfetch for a client's logo and colours — Image
Creator, Smart 1 Ads and the Suite Panel — and only one of them ever wrote
the answer down. So the Client 360 brand card, which reads *stored* brand
data and never fetches, was empty for almost every client while somebody had
been looking that same client up in Image Creator all month. Nothing errored
at either end: the lookup worked, the card said "No brand data on file yet",
and both were telling the truth about different things.

The rules here, each of which is a way that went wrong:

* **A lookup that succeeded is saved.** The plan allows a hundred calls a
  month; spending one and discarding the result means spending it again.
  ``hub/seo.save_brandfetch`` mirrors it into the database backup, keyed by
  domain *and* against the client when one is known — both, because the two
  readers key on different things (``hub/client_brand.py`` reads the client
  store, ``hub/seo.brand_for`` falls back to the domain cache) and a payload
  filed under only one of them is invisible to the other.

* **It is a call somebody asked for, never a page load.** It is billed. The
  Client 360 card offers a button; ``resolve()`` in Smart 1 Ads makes the
  same distinction for the same reason.

* **A refusal, an unreachable service and a domain nobody has heard of are
  three different answers.** Reporting all of them as "no logo" sends
  somebody to rotate a key that was fine, or to upload a logo for a client
  whose lookup simply timed out.

* **No key value ever leaves this module.** The result is rendered into a
  page and pasted into chats — the rule ``services/provider_check.py``
  already works to.
"""
from __future__ import annotations

import re

import requests

BRAND_URL = "https://api.brandfetch.io/v2/brands/{domain}"
SEARCH_URL = "https://api.brandfetch.io/v2/search/{name}"
TIMEOUT = 12


def domain_of(value: str) -> str:
    """The bare registrable host in whatever shape the caller had it."""
    v = str(value or "").strip().lower()
    v = re.sub(r"^[a-z]+://", "", v)
    v = v.split("/")[0].split("?")[0].removeprefix("www.")
    return v if "." in v else ""


def _key() -> str:
    # Through hub.config and nowhere else. Config accepts every spelling this
    # setting answers to (BRANDFETCH_API here, BRANDFETCH_API_KEY elsewhere),
    # and a second reader of os.environ is exactly the drift /api/integrity
    # exists to refuse.
    from hub.config import settings
    return (settings.brandfetch_key or "").strip()


def configured() -> bool:
    return bool(_key())


def _record(module: str, domain: str, **extra) -> None:
    try:
        from hub import quotas
        quotas.record("brandfetch", module=module, detail=domain, **extra)
    except Exception:                                   # noqa: BLE001
        pass


def _save(domain: str, payload: dict, client: str) -> None:
    try:
        from hub import seo
        seo.save_brandfetch(domain, payload, client=client or "")
    except Exception:                                   # noqa: BLE001
        pass          # the answer is still right for this request


def domain_for_name(name: str, module: str = "hub") -> str:
    """Brandfetch's own search, so "Icon Solar" works like "iconsolar.com".

    Best-effort by design: '' on anything that isn't a confident single
    answer, so the caller reports "we couldn't find that company" rather than
    looking up whoever came first.
    """
    key = _key()
    name = str(name or "").strip()
    if not key or not name:
        return ""
    try:
        r = requests.get(SEARCH_URL.format(name=name), params={"c": key},
                         timeout=TIMEOUT)
    except requests.RequestException:
        return ""
    _record(module, name, api="search")
    if not r.ok:
        return ""
    try:
        hits = r.json()
    except ValueError:
        return ""
    if isinstance(hits, list) and hits and isinstance(hits[0], dict):
        return domain_of(hits[0].get("domain") or "")
    return ""


def lookup(domain: str, client: str = "", module: str = "hub",
           *, use_cache: bool = True) -> dict:
    """``{"found": bool, "payload": dict, "note": str, "source": str}``.

    ``source`` is one of ``cache`` (already stored, nothing billed),
    ``lookup`` (a live call), or ``""`` when nothing answered.
    """
    domain = domain_of(domain)
    if not domain:
        return {"found": False, "payload": {}, "source": "",
                "note": "No website to look a brand up by."}

    if use_cache:
        try:
            from hub import seo
            cached = seo.brand_for(client or "", domain)
        except Exception:                               # noqa: BLE001
            cached = None
        if cached:
            # Filed against the client too, so the card that reads the client
            # store finds it next time without another domain round-trip.
            if client:
                _save(domain, cached, client)
            _record(module, domain, cached=True)
            return {"found": True, "payload": cached, "source": "cache",
                    "note": f"Already on file for {domain}."}

    key = _key()
    if not key:
        # The variable name belongs on Settings, where somebody can act on it.
        return {"found": False, "payload": {}, "source": "", "unconfigured": True,
                "note": "Brand lookup is not switched on for this deployment — "
                        "see the environment reference on Settings."}

    try:
        r = requests.get(BRAND_URL.format(domain=domain),
                         headers={"Authorization": f"Bearer {key}"},
                         timeout=TIMEOUT)
    except requests.RequestException as exc:
        return {"found": False, "payload": {}, "source": "",
                "note": f"The brand lookup could not be reached: {exc}"}

    _record(module, domain)                 # a refusal is a call, and is billed

    if r.status_code == 404:
        return {"found": False, "payload": {}, "source": "",
                "note": f"Nothing is published for {domain}."}
    if r.status_code in (401, 403):
        return {"found": False, "payload": {}, "source": "", "refused": True,
                "note": "The brand lookup refused our key — it may have been "
                        "rotated or run out of allowance."}
    if not r.ok:
        return {"found": False, "payload": {}, "source": "",
                "note": f"The brand lookup answered HTTP {r.status_code}."}
    try:
        payload = r.json()
    except ValueError:
        return {"found": False, "payload": {}, "source": "",
                "note": "The brand lookup returned something unreadable."}
    if not isinstance(payload, dict):
        return {"found": False, "payload": {}, "source": "",
                "note": "The brand lookup returned something unreadable."}

    payload.setdefault("domain", domain)
    _save(domain, payload, client)
    return {"found": True, "payload": payload, "source": "lookup",
            "note": f"Looked up from {domain} and saved against this client."}
