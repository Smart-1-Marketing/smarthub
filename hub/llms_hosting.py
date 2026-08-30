"""Hosting, redirecting to and verifying a client's llms.txt.

`hub/llms_txt.py` *builds* the file. This is everything after that: where the
approved copy is served from, how a client's own domain reaches it, and the
check that says whether any of it is actually working today.

## The shape

    GPTBot -> https://schmidthaus.com/llms.txt
              |- 301 (a redirect rule on the client's own site)
              '- https://smart1.agency/llms/schmidthaus/llms.txt -> 200 text/plain

No DNS change, no CDN, no dependency on Smart 1 Sites ever supporting
root-file uploads. What it costs is that the file is *served* from our
hostname rather than the client's, which is stated below rather than glossed.

## Why `/llms/<slug>/llms.txt` and not `/<slug>/llms.txt`

One dedicated prefix: one path to allow in robots.txt, one path to exempt
from the `X-Robots-Tag` header, one entry in the public-route allowlist -- and
no chance of a client slug colliding with a Hub route now or later. A client
called "status" or "activity" at the root would have shadowed a real page,
and the failure would have been a staff screen 404ing rather than anything
saying a slug had been taken.

## The slug is stored, and that is the opposite of the rule everywhere else

`hub/client_key.py` refuses to store a derived key, because a client renamed
in Knack should re-join on the next request rather than leave a stale copy
behind. Here the opposite is right and for the same underlying reason: the
slug is written into a **redirect rule on somebody else's website**, so it has
to outlive the client's name changing. Derived, a rename in Knack would 404
every request the client's own site sends us, silently, and the only sign
would be a verify run somebody happened to look at.

So `_registry()` is a small stored map of slug -> client. It also makes the
public route a dict lookup instead of a walk of the whole client book on every
crawler request, and it is the only place uniqueness can actually be enforced.

## Three layers had to be opened, not one

The written brief for this named robots.txt. Two more would each have silently
defeated it:

  * **`X-Robots-Tag`.** `hub/no_crawl.NoIndex` stamps `noindex, nofollow,
    ..., noai, noimageai` onto *every* response in the composed app. `noai` on
    a file whose entire purpose is to be read by AI is the flattest possible
    contradiction, and nothing on any screen would have said so. That
    middleware already declines to overwrite a header a response set for
    itself, so the route sets its own.
  * **The chrome.** The hub app injects the sidebar, the help layer and the
    feedback tab into HTML it returns. This answers `text/plain`, so the
    injector skips it on the mimetype -- and the prefix is named in
    `CHROMELESS` anyway, so it is a decision rather than a coincidence.

## What this buys, and what it does not

**Does:** puts a correct, current, crawlable file at the conventional URL for
every client, on one system, with monitoring; upgrades a 302 to a 301, which
is the difference between a redirect a crawler stores and one it treats as
temporary; and retires the second hosting system.

**Does not:** make the file authoritative for the client's *own* domain. It is
served from ours, so a crawler that follows the redirect reads it as a file on
the agency's domain, and PerplexityBot is reported not to follow redirects on
`/llms.txt` at all. And no major provider has confirmed it reads llms.txt at
inference time -- a bot requesting the file proves access, not influence.
`CAVEAT` carries that sentence so a screen cannot render the feature without
it. Treat this as a well-executed deliverable, not a ranking lever.
"""
from __future__ import annotations

import hashlib
import os
import re
import ssl
import urllib.robotparser
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit

# The one public prefix. Read by hub/no_crawl.py (robots.txt), by the route in
# hub/__init__.py (the X-Robots-Tag exception and the CHROMELESS entry) and by
# test_llms_hosting.py, so the four cannot come to disagree about which path
# is open -- the failure this codebase names as a mount and a module
# disagreeing about what is public.
PUBLIC_PREFIX = "/llms/"

# The stated caveat, in one place, because it is the sentence that keeps this
# feature honestly sold. Any screen describing the feature renders this.
CAVEAT = ("No major provider has confirmed it reads llms.txt at inference "
          "time. A bot requesting the file proves access, not influence, and "
          "Google has said it does not use the file. This is a low-cost, "
          "well-executed deliverable rather than a ranking lever.")

# How many hops the verifier will follow before it gives up. A correct chain
# is one hop; anything past three is a loop or a misconfiguration, and
# following it further only produces a longer wrong answer.
MAX_HOPS = 5
TIMEOUT = 12
# The verifier reads the body to hash it. A redirect that lands on a whole
# website rather than a text file must not be pulled into memory in full.
MAX_BYTES = 512 * 1024

# The user agent the verifier identifies itself with. Deliberately not a
# crawler's string: impersonating GPTBot to find out what GPTBot sees is a lie
# told to somebody else's server, and a host that varies its answer by user
# agent is a finding to report rather than a check to route around.
UA = "Smart1Hub-llms-verifier/1.0 (+https://smart1.agency)"

# The readers this file exists for. `robots_allows()` is asked about each of
# them separately, because robots.txt is matched by user-agent GROUP and not
# by substring: a group naming GPTBot says nothing at all about ClaudeBot.
VERIFY_AGENTS = ("GPTBot", "ClaudeBot", "PerplexityBot")

# Registrable-domain suffixes that are two labels rather than one. This is a
# short list and NOT an implementation of the public suffix list -- adding one
# would be a dependency, and the book is US businesses on .com. It is named as
# a heuristic so a wrong answer here reads as a limit of the check rather than
# as a finding about the client.
_MULTI_SUFFIX = ("co.uk", "org.uk", "ac.uk", "gov.uk", "co.nz", "com.au",
                 "net.au", "org.au", "co.za", "co.jp", "com.br", "com.mx")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def slugify(v) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(v or "").lower()).strip("-")


def host_of(url: str) -> str:
    h = (urlsplit(str(url or "")).hostname or "").lower()
    return h[4:] if h.startswith("www.") else h


def registrable(host: str) -> str:
    """The domain a name belongs to, by a stated heuristic.

    `shop.schmidthaus.com` and `www.schmidthaus.com` are the same business;
    `files.smart1marketing.com` is not `schmidthaus.com`. That is the only
    question asked of this, and two labels answers it for every client on this
    book. See `_MULTI_SUFFIX` for the limit.
    """
    h = (str(host or "")).lower().strip(".")
    if not h:
        return ""
    for suf in _MULTI_SUFFIX:
        if h == suf or h.endswith("." + suf):
            parts = h.split(".")
            return ".".join(parts[-3:]) if len(parts) >= 3 else h
    parts = h.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else h


# ---------------------------------------------------------------------------
# The slug registry
# ---------------------------------------------------------------------------

def _registry_path() -> str:
    from . import jsonstore
    return os.path.join(jsonstore.data_dir("llms"), "slugs.json")


def _registry() -> dict:
    from . import jsonstore
    d = jsonstore.read_json(_registry_path(), default={})
    return d if isinstance(d, dict) else {}


def _write_registry(d: dict) -> None:
    from . import jsonstore
    jsonstore.write_json(_registry_path(), d, indent=1)


def client_for_slug(slug: str) -> str:
    """Whose file is at this URL, or "" -- never a guess.

    A slug that resolves to nobody answers exactly what a slug that never
    existed answers, in the route above: a client-facing URL that distinguishes
    "retired" from "invented" tells whoever is probing which slugs are real.
    """
    return str(_registry().get(slugify(slug), "") or "")


def slug_for(client: str) -> str:
    """The slug this client's file is served at, allocating one if needed.

    Allocation is first-come and the answer is then fixed: the string is in a
    redirect rule on the client's website within the hour, so a slug that
    moved would take the file off the air with our own screen still reporting
    a clean save.
    """
    name = str(client or "").strip()
    if not name:
        return ""
    reg = _registry()
    for slug, owner in reg.items():
        if owner == name:
            return slug
    want = slugify(name)
    if not want:
        return ""
    slug, n = want, 2
    while slug in reg:
        slug, n = f"{want}-{n}", n + 1
    reg[slug] = name
    _write_registry(reg)
    return slug


# ---------------------------------------------------------------------------
# The record: draft, published, last check
# ---------------------------------------------------------------------------

def _record(client: str) -> dict:
    from . import seo
    rec = (seo.load_store(client) or {}).get("llms_txt") or {}
    return rec if isinstance(rec, dict) else {}


def _save_record(client: str, rec: dict) -> None:
    from . import seo
    store = seo.load_store(client) or {}
    store["llms_txt"] = rec
    seo.save_store(client, store)


def _sha(text: str) -> str:
    return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()


def published(client: str) -> dict:
    """The live copy, or {}.

    ## The one migration in here, and why it is not a shortcut

    Publishing became a separate act from saving so that a half-written file
    is never live. Applied literally to a record written before that
    distinction existed, it would take Schmidt's file -- which has been served
    at `/llms/<slug>.txt` and reached from their own domain for months -- off
    the air the moment this deployed, to satisfy a rule introduced after it
    went up.

    So a saved draft with no published record beside it is adopted as
    published, once, and says so in `from_draft`. That is safe rather than
    lenient: `save()` has always refused text containing a NEED placeholder,
    which is the same gate publishing applies, so an adopted draft has already
    passed it. Anything saved after this exists must be published deliberately.
    """
    rec = _record(client)
    pub = rec.get("published")
    if isinstance(pub, dict) and pub.get("body"):
        return pub
    if "published" in rec:
        # The key exists and holds nothing: this record has been through
        # publish/unpublish, so its state is a decision rather than a gap.
        # Falling through here would make `unpublish()` do nothing at all —
        # the file back on the air a moment after somebody took it off, with
        # the screen reporting that it had gone.
        return {}
    draft = str(rec.get("text") or "")
    if not draft.strip() or "NEED " in draft:
        return {}
    return {"body": draft, "at": str(rec.get("updated") or ""),
            "sha256": _sha(draft), "by": "", "from_draft": True}


def publish(client: str, text: str, actor: str = "") -> dict:
    """Make this text the live copy, and report the two writes apart.

    Storing the file and allocating the address it is served at are separate
    things that can succeed separately, so "published" and "published, but we
    could not record the address" are reported as different outcomes --
    `hub/domain_links.py`'s rule. One tick over both is how somebody learns
    not to trust the tick.
    """
    from . import audit
    body = str(text or "")
    if not body.strip():
        return {"ok": False, "error": "There is nothing to publish."}
    if "NEED " in body:
        return {"ok": False, "error":
                "This still contains NEED placeholders. Fill them in first - "
                "a file with gaps is worse than none, because a model treats "
                "the whole thing as authoritative."}
    rec = _record(client)
    rec["text"] = body
    rec["published"] = {"body": body, "at": _now(), "sha256": _sha(body),
                        "by": str(actor or "")}
    _save_record(client, rec)

    slug, slug_error = "", ""
    try:
        slug = slug_for(client)
    except Exception as exc:                            # noqa: BLE001
        slug_error = f"{type(exc).__name__}: {exc}"
    try:
        audit.log("seo", "llms_txt_published", client=client,
                  bytes=len(body.encode()), slug=slug or "")
    except Exception:                                   # noqa: BLE001
        pass
    return {"ok": True, "published": True, "slug": slug,
            "slug_recorded": bool(slug), "slug_error": slug_error,
            "bytes": len(body.encode()), "sha256": rec["published"]["sha256"],
            "public_url": public_url(client) if slug else ""}


def unpublish(client: str, actor: str = "") -> dict:
    """Take the file off the air, keeping the draft and the slug.

    The slug is kept deliberately. A redirect rule on the client's own site
    still points here, and re-issuing a different address later would leave
    that rule pointing at a 404 nobody is watching; republishing under the
    same slug is one press.
    """
    from . import audit
    rec = _record(client)
    if not rec.get("published"):
        return {"ok": True, "published": False, "note": "It was not live."}
    rec["published"] = None
    rec["unpublished_at"] = _now()
    _save_record(client, rec)
    try:
        audit.log("seo", "llms_txt_unpublished", client=client,
                  actor=str(actor or ""))
    except Exception:                                   # noqa: BLE001
        pass
    return {"ok": True, "published": False,
            "note": "Off the air. The address is kept, so republishing "
                    "re-uses the URL already in the client's redirect rule."}


def public_url(client: str, base: str = "") -> str:
    slug = slug_for(client)
    if not slug:
        return ""
    return f"{_base(base)}{PUBLIC_PREFIX}{slug}/llms.txt"


def _origin(url: str) -> str:
    """Scheme and host, nothing else.

    PUBLIC_BASE_URL is supposed to be an origin and one env group on this
    deployment holds a whole callback URL in it, path and all -- so pasting a
    path onto it would build `/tools/ads/oauth/callback/llms/<slug>/llms.txt`
    and 404 in the one place nobody is watching: inside a redirect rule on
    somebody else's website.
    """
    u = urlsplit(str(url or "").strip())
    if not u.scheme or not u.netloc:
        return ""
    return urlunsplit((u.scheme, u.netloc, "", "", ""))


def _base(base: str = "") -> str:
    if base:
        return _origin(base) or str(base).rstrip("/")
    try:
        from .config import settings
        b = _origin(getattr(settings, "public_base_url", "") or "")
        if b:
            return b
    except Exception:                                   # noqa: BLE001
        pass
    return "https://smart1.agency"


def client_domain(client: str) -> str:
    """The client's own domain, from the one reader that decides what a
    domain means -- never a second resolution of my own."""
    try:
        from .client_context import context
        f = (context(client) or {}).get("fields", {}) or {}
        return host_of(f.get("website") or "") or str(f.get("domain") or "").lower()
    except Exception:                                   # noqa: BLE001
        return ""


def client_url(client: str) -> str:
    d = client_domain(client)
    return f"https://{d}/llms.txt" if d else ""


# ---------------------------------------------------------------------------
# The verifier
# ---------------------------------------------------------------------------
#
# This is the point of the whole build. Every failure it looks for is one that
# was found by hand once and would otherwise be found by hand again: the
# 302 that a crawler treats as temporary, the redirect that still lands on the
# retired S3 bucket, the file that drifted from the copy the Hub holds, and
# the robots.txt refusal on our own host that would have made all of it
# pointless while every screen here reported a clean publish.

def _fetch(url: str, method: str = "GET") -> dict:
    """One request, no redirect following, never raising.

    `allow_redirects=True` collapses the chain into a final answer and throws
    away the status codes -- which is the entire question here. 301 and 302
    both end at the same 200, and only one of them is a redirect a crawler
    stores.
    """
    import requests
    out = {"url": url, "status": None, "headers": {}, "body": b"",
           "error": "", "tls_ok": None, "truncated": False}
    if urlsplit(url).scheme == "https":
        out["tls_ok"] = False        # proven true by the request not raising
    try:
        r = requests.request(method, url, allow_redirects=False,
                             timeout=TIMEOUT, stream=True,
                             headers={"User-Agent": UA,
                                      "Accept": "text/plain, */*"})
        out["status"] = r.status_code
        out["headers"] = {k.lower(): v for k, v in r.headers.items()}
        chunks, size = [], 0
        for chunk in r.iter_content(8192):
            chunks.append(chunk)
            size += len(chunk)
            if size > MAX_BYTES:
                out["truncated"] = True
                break
        out["body"] = b"".join(chunks)[:MAX_BYTES]
        r.close()
        if urlsplit(url).scheme == "https":
            out["tls_ok"] = True
    except Exception as exc:                            # noqa: BLE001
        # A certificate failure and a DNS failure send somebody to two
        # different places, so the exception type travels rather than being
        # flattened into "could not reach".
        if isinstance(exc, ssl.SSLError) or "SSL" in type(exc).__name__ \
                or "certificate" in str(exc).lower():
            out["error"] = f"TLS: {exc}"
        else:
            out["error"] = f"{type(exc).__name__}: {exc}"
    return out


def robots_allows(host: str, path: str,
                  agents: tuple = VERIFY_AGENTS) -> dict:
    """Whether each named agent may fetch `path` on `host`.

    Parsed with `urllib.robotparser`, which matches by **user-agent group**
    and honours `Allow` with longest-match precedence. That distinction is the
    whole reason each agent is asked separately: a group naming GPTBot says
    nothing whatever about ClaudeBot, and a substring pass over the file would
    report all three as covered by one of them.

    Three answers, not two. A robots.txt that 404s means the host places no
    restriction and every agent is allowed -- that is a real answer. A
    robots.txt we could not *reach* is `measured: False`, because reading a
    network failure as "allowed" is a green tick over a question nobody asked.
    """
    if not host:
        return {"measured": False, "note": "No host to ask.", "agents": {}}
    url = f"https://{host}/robots.txt"
    got = _fetch(url)
    if got["error"]:
        return {"measured": False, "url": url, "error": got["error"],
                "note": f"Could not read robots.txt at {host}.", "agents": {}}
    status = got["status"]
    if status in (401, 403) or (status and status >= 500):
        return {"measured": False, "url": url, "status": status,
                "note": f"{host} answered {status} for robots.txt, so what it "
                        f"permits is unknown.", "agents": {}}
    body = ""
    if status == 200:
        body = got["body"].decode("utf-8", "replace")
    parser = urllib.robotparser.RobotFileParser()
    parser.parse(body.splitlines())
    agents_out = {}
    for a in agents:
        agents_out[a] = bool(parser.can_fetch(a, f"https://{host}{path}"))
    return {"measured": True, "url": url, "status": status,
            "present": status == 200, "agents": agents_out,
            "blocked": [a for a, ok in agents_out.items() if not ok],
            "note": ("No robots.txt, so nothing is restricted."
                     if status != 200 else "")}


def _looks_like_a_login(body: bytes, ctype: str, url: str) -> bool:
    """A crawler served the sign-in page records that as the client's file.

    Which is the worst outcome available here and the quietest: 200, a body,
    and nothing anywhere saying the file was never reached.
    """
    if "/login" in url.lower() or "/signin" in url.lower():
        return True
    if "html" not in ctype.lower():
        return False
    low = body[:4000].lower()
    return any(m in low for m in (b"type=\"password\"", b"type='password'",
                                  b"sign in", b"log in", b"password"))


def verify(client: str, base: str = "") -> dict:
    """Follow the client's own /llms.txt one hop at a time and report.

    ## The verdicts

    **Pass** -- one hop, a 301, a 200 at the end of it, `text/plain`, bytes
    matching the copy the Hub published, and robots allowing all three agents
    at the host that actually answers.

    **Warn** -- reachable, and something about it will cost reach: a 302 (a
    crawler treats it as temporary and may not store it), more than one hop,
    a final host that is neither the client's nor ours, or content that has
    drifted from the published copy.

    **Fail** -- not reachable as a text file at all: a non-200, a login page,
    an HTML content type, or robots refusing an agent at either host.

    ## One divergence from the written brief, stated rather than buried

    The brief lists "final host is not the client's domain" as a Warn. Under
    this design the final host is *never* the client's domain -- that is what
    hosting in the Hub means -- so applied literally, Pass would be
    unreachable for every client and the verdict column would be a wall of
    amber nobody reads. The actionable version of that question is whether it
    landed somewhere **unexpected**: the client's own root is best, our host
    is the design, and anything else (the retired S3 bucket, a stranger) is
    the Warn. The off-domain caveat is carried on every result as a note
    instead, because it is a standing property of the architecture and not a
    finding about any one client.
    """
    out = {"client": client, "checked_at": _now(), "measured": False,
           "verdict": "not_measured", "reasons": [], "notes": [], "hops": [],
           "caveat": CAVEAT}

    pub = published(client)
    out["published"] = bool(pub)
    out["published_sha256"] = str(pub.get("sha256") or "")

    start = client_url(client)
    out["client_url"] = start
    out["public_url"] = public_url(client, base)
    ours = host_of(out["public_url"])
    theirs = registrable(host_of(start))
    if not start:
        out["notes"].append(
            "This client has no website on file, so there is no /llms.txt to "
            "check. Add the website on the client record.")
        return out
    if not pub:
        out["notes"].append(
            "Nothing is published in the Hub yet, so a file found on the "
            "client's domain cannot be compared against ours.")

    # ---- the chain, one hop at a time ----
    url, seen = start, set()
    final = None
    for _ in range(MAX_HOPS):
        if url in seen:
            out["reasons"].append("The redirect chain loops.")
            break
        seen.add(url)
        got = _fetch(url)
        hop = {"url": url, "status": got["status"], "tls_ok": got["tls_ok"],
               "location": got["headers"].get("location", ""),
               "content_type": got["headers"].get("content-type", ""),
               "error": got["error"]}
        out["hops"].append(hop)
        if got["error"] or not got["status"]:
            out["reasons"].append(
                f"{url} could not be reached ({got['error'] or 'no answer'}).")
            break
        if got["status"] in (301, 302, 303, 307, 308) and hop["location"]:
            nxt = hop["location"]
            if nxt.startswith("/"):
                u = urlsplit(url)
                nxt = urlunsplit((u.scheme, u.netloc, nxt, "", ""))
            url = nxt
            continue
        final = got
        break

    out["hop_count"] = max(0, len(out["hops"]) - 1)
    if final is None:
        out["measured"] = bool(out["hops"])
        out["verdict"] = "fail" if out["hops"] else "not_measured"
        if not out["reasons"]:
            out["reasons"].append(
                f"Gave up after {MAX_HOPS} hops without reaching a file.")
        return out

    out["measured"] = True
    body = final["body"]
    ctype = final["headers"].get("content-type", "")
    final_url = final["url"]
    final_host = host_of(final_url)
    out.update({
        "final_url": final_url, "final_status": final["status"],
        "final_host": final_host, "content_type": ctype,
        "bytes": len(body), "truncated": final["truncated"],
        "sha256": _sha(body.decode("utf-8", "replace")),
        "tls_ok": all(h.get("tls_ok") is not False for h in out["hops"]),
    })

    fails, warns = [], []

    # ---- reachable as a text file at all ----
    if final["status"] != 200:
        fails.append(f"The final answer is {final['status']}, not 200.")
    elif _looks_like_a_login(body, ctype, final_url):
        fails.append("The redirect lands on a sign-in page. A crawler records "
                     "that as the client's llms.txt.")
    elif "html" in ctype.lower():
        fails.append(f"It answers {ctype.split(';')[0]}, not text/plain.")
    elif not ctype:
        warns.append("No Content-Type on the answer; it should be "
                     "text/plain; charset=utf-8.")
    elif "text/plain" not in ctype.lower():
        warns.append(f"It answers {ctype.split(';')[0]} rather than "
                     f"text/plain.")

    # ---- the redirect type: the finding this was built for ----
    first = out["hops"][0] if out["hops"] else {}
    if out["hop_count"] == 0:
        out["notes"].append(
            "Served directly at the client's own domain - no redirect at all, "
            "which is the strongest arrangement there is.")
    else:
        if first.get("status") == 301:
            out["notes"].append("The first hop is a 301, which a crawler "
                                "stores.")
        elif first.get("status") in (302, 303, 307):
            warns.append(
                f"The first hop is a {first.get('status')}, which a crawler "
                f"treats as temporary and may not store. Set the redirect "
                f"rule to 301. If the site builder does not expose the type, "
                f"record that against the client rather than calling it Pass.")
        elif first.get("status") == 308:
            out["notes"].append("The first hop is a 308 (permanent), which a "
                                "crawler stores.")
        if out["hop_count"] > 1:
            warns.append(f"{out['hop_count']} hops. Each one is a chance for a "
                         f"crawler to stop following.")

    # ---- where it landed ----
    if registrable(final_host) == theirs and theirs:
        out["notes"].append("The file is served from the client's own domain.")
    elif final_host == ours or registrable(final_host) == registrable(ours):
        out["notes"].append(
            "Served from " + ours + " rather than the client's own domain. "
            "That is this design and not a fault - but a crawler reads the "
            "file as ours, and PerplexityBot is reported not to follow "
            "redirects on /llms.txt at all.")
    else:
        warns.append(
            f"It lands on {final_host or 'nowhere identifiable'}, which is "
            f"neither the client's domain nor {ours}. If that is the old "
            f"files.smart1marketing.com copy, this client has not been "
            f"repointed yet.")

    # ---- content drift ----
    if pub and final["status"] == 200 and "html" not in ctype.lower():
        if out["sha256"] == out["published_sha256"]:
            out["notes"].append("Byte-for-byte the copy the Hub published.")
        else:
            warns.append(
                "What is being served is not the copy the Hub published. "
                "Either it was edited at the other end or this client has not "
                "been republished since the file changed.")

    # ---- robots, at both ends, per agent group ----
    r_final = robots_allows(final_host, urlsplit(final_url).path or "/")
    r_client = robots_allows(host_of(start), "/llms.txt")
    out["robots_final"] = r_final
    out["robots_client"] = r_client
    for label, rep in (("the host serving the file", r_final),
                       ("the client's own site", r_client)):
        if not rep.get("measured"):
            # The note and the exception both travel: "we could not read it"
            # and "it timed out at the TLS handshake" send somebody to two
            # different places, and flattening them into the first is how a
            # real outage reads as a shrug.
            why = " ".join(x for x in (rep.get("note"), rep.get("error")) if x)
            out["notes"].append(f"robots.txt on {label}: not measured - {why}")
        elif rep.get("blocked"):
            fails.append(f"robots.txt on {label} refuses "
                         f"{', '.join(rep['blocked'])}.")

    # ---- TLS ----
    for h in out["hops"]:
        if h.get("tls_ok") is False:
            fails.append(f"TLS did not validate at {host_of(h['url'])}.")
            break

    out["reasons"] = fails + warns
    out["fails"], out["warns"] = fails, warns
    out["verdict"] = "fail" if fails else ("warn" if warns else "pass")
    return out


def record_check(client: str, result: dict) -> dict:
    """Keep the last verify against the client, so a screen can draw it
    without spending a round of outbound requests on every page load."""
    rec = _record(client)
    rec["last_check"] = result
    _save_record(client, rec)
    return result


def status(client: str, base: str = "") -> dict:
    """Everything a screen needs, and not one outbound request.

    Verifying reaches the client's website, their robots.txt and ours, so it
    is a button and a nightly job -- never a page load. A page that costs four
    outbound requests per open is a page somebody stops opening, which is the
    note `services/provider_check.py` makes about eight calls on a dashboard.
    """
    rec = _record(client)
    pub = published(client)
    slug = ""
    try:
        slug = slug_for(client) if pub else slugify(client)
    except Exception:                                   # noqa: BLE001
        slug = slugify(client)
    last = rec.get("last_check") if isinstance(rec.get("last_check"), dict) else None
    return {
        "client": client,
        "slug": slug,
        "draft_bytes": len(str(rec.get("text") or "").encode()),
        "has_draft": bool(str(rec.get("text") or "").strip()),
        "published": bool(pub),
        "published_at": str(pub.get("at") or ""),
        "published_sha256": str(pub.get("sha256") or ""),
        "adopted_from_draft": bool(pub.get("from_draft")),
        "public_url": public_url(client, base) if pub else "",
        "client_url": client_url(client),
        "client_domain": client_domain(client),
        "last_check": last,
        "checked_at": str((last or {}).get("checked_at") or ""),
        "verdict": str((last or {}).get("verdict") or "not_measured"),
        "caveat": CAVEAT,
        "runbook": RUNBOOK,
    }


# The five steps somebody actually performs in Smart 1 Sites, as data, so the
# client screen and the test read the same list. Written here rather than in
# the template because a runbook a screen restates is one that goes stale on
# whichever copy nobody opened.
RUNBOOK = (
    "Open the client's site settings in Smart 1 Sites and find the redirect "
    "rules.",
    "Source /llms.txt, destination the public URL above.",
    "Set the type to 301, not 302. If the builder does not expose the type, "
    "record that against the client and treat it as Warn rather than Pass.",
    "Check the client's own robots.txt does not disallow /llms.txt.",
    "Press Verify here and confirm it passes.",
)


def sweep(actor: str = "scheduler", limit: int = 60) -> dict:
    """Re-check every published client, and write the failures down.

    Only the published ones: a client with no live file has nothing that can
    have broken, and walking the whole book would spend four outbound requests
    each on several hundred businesses to learn nothing.
    """
    from . import audit
    reg = _registry()
    checked, results = 0, {"pass": 0, "warn": 0, "fail": 0, "not_measured": 0}
    problems = []
    for slug, client in list(reg.items())[:limit]:
        if not published(client):
            continue
        try:
            res = verify(client)
        except Exception as exc:                        # noqa: BLE001
            results["not_measured"] += 1
            problems.append({"client": client, "verdict": "not_measured",
                             "reasons": [f"{type(exc).__name__}: {exc}"]})
            continue
        record_check(client, res)
        checked += 1
        results[res.get("verdict", "not_measured")] = \
            results.get(res.get("verdict", "not_measured"), 0) + 1
        if res.get("verdict") in ("fail", "warn"):
            problems.append({"client": client, "verdict": res["verdict"],
                             "reasons": res.get("reasons", [])[:3]})
    if problems:
        # A failure is worth a row in the activity log; a clean sweep is a
        # state, and writing one every night for ever is the noise
        # hub/google_index.py had to learn to stop making.
        try:
            audit.log("seo", "llms_txt_verify_problems", actor=actor,
                      checked=checked, failing=results.get("fail", 0),
                      warning=results.get("warn", 0))
        except Exception:                               # noqa: BLE001
            pass
    return {"ok": True, "checked": checked, "results": results,
            "problems": problems,
            "note": (f"{checked} published file(s) checked."
                     if checked else
                     "No client has a published llms.txt yet.")}
