"""Does the key actually work? — one cheap authenticated call per provider.

`is_live()` on each service answers a different, weaker question: is there a
non-empty string in the environment. That is what the dashboard's chips have
always shown, and it is exactly the answer that is wrong in the two cases
that matter after somebody has just pasted keys into Render:

  * the key is set **under a name nothing reads** — the chip says "mock mode"
    beside a key plainly present, which reads as "the tool is broken";
  * the key is set and **the provider refuses it** — truncated on paste, from
    the wrong account, revoked, or out of credit. The chip says "live", every
    screen looks healthy, and the failure surfaces as an empty voice track or
    a render that never arrives.

So this module asks each provider. One request each, to the cheapest
authenticated endpoint it publishes — no generation, nothing billable beyond
an API call, and nothing written.

Four rules, each of which is a way to report a confident wrong answer:

  * **No key is "not measured", never a cross.** A provider nobody has
    configured has not failed; it has not been asked.
  * **Refused and unreachable are different answers.** A 401 means the key is
    wrong. A timeout means we could not tell, and saying "wrong key" then
    sends somebody to rotate a key that was fine.
  * **A 404 does not condemn a key.** These endpoints are the part most
    likely to move between API versions; if the host answers but the path is
    not there, that is this file being out of date, not the key being bad.
  * **The key never leaves this process.** Results carry a state and a
    sentence, never the value, because they are rendered into a page and
    pasted into chats.
"""

import concurrent.futures

import requests

from . import (cloudinary_service, creatomate_service, elevenlabs_service,
               heygen_service, openai_service, pexels_service, pixabay_service,
               runway_service)

TIMEOUT = 10

# Order is the order the dashboard shows them in — roughly the order a
# commercial passes through them, so a gap reads as the step it will stop at.
# The MEMBERSHIP is config.py's V1_PROVIDERS + V1_5_PROVIDERS, and
# test_commercial_providers.py asserts that, because a provider added there
# and forgotten here would simply never be checked and nothing would say so.
PROVIDERS = ["openai", "pexels", "pixabay", "heygen", "runway",
             "elevenlabs", "creatomate", "cloudinary"]

LABELS = {
    "openai": "OpenAI", "pexels": "Pexels", "pixabay": "Pixabay",
    "heygen": "HeyGen", "runway": "Runway", "elevenlabs": "ElevenLabs",
    "creatomate": "Creatomate", "cloudinary": "Cloudinary",
}

# What stops working when the key does. Shown beside a failure, because
# "Creatomate refused the key" and "no commercial can be rendered" are the
# same fact and only the second one says why it matters.
COSTS = {
    "openai": "Concepts, scripts and AI stills fall back to templates.",
    "pexels": "Free stock search returns placeholders.",
    "pixabay": "Free stock search returns placeholders.",
    "heygen": "Spokesperson scenes produce no clip.",
    "runway": "AI video scenes produce no clip.",
    "elevenlabs": "Commercials render with no voice track.",
    "creatomate": "Nothing can be rendered — this is the last step.",
    "cloudinary": "Assets and finished commercials are not stored, and "
                  "provider links expire.",
}

# The env names each key is accepted under, so a provider reporting "no key"
# can say what to call the variable rather than leaving somebody to guess.
ENV_NAMES = {
    "openai": "OPENAI_API_KEY",
    "pexels": "PEXELS_API / PEXELS_API_KEY",
    "pixabay": "PIXABAY_API / PIXABAY_API_KEY",
    "heygen": "HEYGEN_API / HEYGEN_API_KEY",
    "runway": "RUNWAY_API / RUNWAY_API_KEY",
    "elevenlabs": "ELEVENLABS_API / ELEVENLABS_API_KEY",
    "creatomate": "CREATOMATE_API / CREATOMATE_API_KEY",
    "cloudinary": "CLOUDINARY_URL",
}


def _result(state, detail):
    return {"state": state, "detail": detail}


def _probe(label, method, url, **kwargs):
    """One request, mapped onto the four honest outcomes."""
    kwargs.setdefault("timeout", TIMEOUT)
    try:
        r = requests.request(method, url, **kwargs)
    except requests.Timeout:
        return _result("unreachable",
                       f"{label} did not answer within {TIMEOUT}s — the key was "
                       f"not checked.")
    except Exception as exc:                            # noqa: BLE001
        return _result("unreachable",
                       f"Could not reach {label} ({type(exc).__name__}) — the "
                       f"key was not checked.")
    if r.status_code in (401, 403):
        return _result("rejected", f"{label} refused the key (HTTP {r.status_code}).")
    if r.status_code == 429:
        # The key was accepted well enough to be rate-limited against it.
        return _result("ok", f"{label} accepted the key (rate-limited right now).")
    if r.status_code in (404, 405):
        return _result("inconclusive",
                       f"{label} answered, but the endpoint this check uses is "
                       f"gone (HTTP {r.status_code}). The key was not judged.")
    if r.status_code >= 500:
        return _result("inconclusive",
                       f"{label} returned HTTP {r.status_code} — a fault on "
                       f"their side, so the key was not judged.")
    if r.status_code >= 400:
        return _result("rejected", f"{label} rejected the request (HTTP {r.status_code}).")
    return _result("ok", f"{label} accepted the key.")


def _check_openai():
    return _probe("OpenAI", "GET", "https://api.openai.com/v1/models",
                  headers={"Authorization": f"Bearer {openai_service._key()}"})


def _check_pexels():
    # The video search endpoint, not the image one: this checks the call the
    # module actually makes, so a key valid for one product and not the other
    # is caught here rather than at the first search.
    return _probe("Pexels", "GET", pexels_service.BASE_URL,
                  headers={"Authorization": pexels_service._key()},
                  params={"query": "office", "per_page": 1})


def _check_pixabay():
    # Pixabay authenticates in the query string and answers 400, not 401, to a
    # bad key — so the generic 4xx branch is what catches it here.
    return _probe("Pixabay", "GET", pixabay_service.BASE_URL,
                  params={"key": pixabay_service._key(), "q": "office", "per_page": 3})


def _check_heygen():
    return _probe("HeyGen", "GET", f"{heygen_service.BASE_URL}/v2/avatars",
                  headers=heygen_service._headers())


def _check_runway():
    return _probe("Runway", "GET", f"{runway_service.BASE_URL}/organization",
                  headers=runway_service._headers())


def _check_elevenlabs():
    return _probe("ElevenLabs", "GET",
                  f"{elevenlabs_service.BASE_URL}/user/subscription",
                  headers=elevenlabs_service._headers())


def _check_creatomate():
    # Listing one render is the cheapest authenticated read Creatomate
    # publishes. It creates nothing.
    return _probe("Creatomate", "GET", f"{creatomate_service.BASE_URL}/renders",
                  headers=creatomate_service._headers(), params={"limit": 1})


def _check_cloudinary():
    """Cloudinary is an SDK, not a URL, so it does not go through _probe.

    `api.ping()` is an Admin API call: rate-limited, but not one of the
    credit-billed operations (a credit is transformations, storage or
    delivery), so this check does not move the bill it is reporting on.
    """
    try:
        import cloudinary.api
    except ImportError:
        return _result("inconclusive",
                       "The Cloudinary SDK is not installed in this "
                       "environment, so the key was not checked.")
    try:
        cloudinary_service._ensure_configured()
        pong = cloudinary.api.ping()
    except Exception as exc:                            # noqa: BLE001
        name = type(exc).__name__
        if "Auth" in name or "Unauthorized" in name or "401" in str(exc):
            return _result("rejected", "Cloudinary refused the credentials.")
        return _result("unreachable",
                       f"Could not reach Cloudinary ({name}) — the credentials "
                       f"were not checked.")
    if (pong or {}).get("status") == "ok":
        return _result("ok", "Cloudinary accepted the credentials.")
    return _result("inconclusive",
                   "Cloudinary answered without an ok status, so the "
                   "credentials were not judged.")


_CHECKS = {
    "openai": _check_openai, "pexels": _check_pexels, "pixabay": _check_pixabay,
    "heygen": _check_heygen, "runway": _check_runway,
    "elevenlabs": _check_elevenlabs, "creatomate": _check_creatomate,
    "cloudinary": _check_cloudinary,
}

_LIVE = {
    "openai": openai_service, "pexels": pexels_service, "pixabay": pixabay_service,
    "heygen": heygen_service, "runway": runway_service,
    "elevenlabs": elevenlabs_service, "creatomate": creatomate_service,
    "cloudinary": cloudinary_service,
}


def key_present(name):
    """Whether a key is configured — the weak question, kept separate."""
    service = _LIVE.get(name)
    if service is None:
        return False
    try:
        return bool(service.is_live())
    except Exception:                                   # noqa: BLE001
        return False


def status():
    """Key present or not, for every provider. No network."""
    return {name: key_present(name) for name in PROVIDERS}


def check_one(name):
    row = {"provider": name, "label": LABELS.get(name, name.title()),
           "key_present": key_present(name), "cost": COSTS.get(name, ""),
           "env": ENV_NAMES.get(name, "")}
    if name not in _CHECKS:
        return dict(row, state="absent", detail="No check is defined for this provider.")
    if not row["key_present"]:
        return dict(row, state="absent",
                    detail=f"No key set ({ENV_NAMES.get(name, '')}) — running in "
                           f"mock mode, not measured.")
    try:
        outcome = _CHECKS[name]()
    except Exception as exc:                            # noqa: BLE001
        # A check that raises must not take the panel down with it, and must
        # not be reported as the provider refusing the key.
        outcome = _result("inconclusive",
                          f"The check itself failed ({type(exc).__name__}), so "
                          f"the key was not judged.")
    return dict(row, **outcome)


def check_all(names=None):
    """Every provider at once.

    In parallel because they are eight independent HTTP calls and in sequence
    a couple of slow ones would push the request past the point where somebody
    reloads the page and fires all eight again.
    """
    names = [n for n in (names or PROVIDERS) if n in PROVIDERS]
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(names) or 1) as pool:
        return list(pool.map(check_one, names))
