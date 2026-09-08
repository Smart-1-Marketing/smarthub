"""A client-facing link, masked under s1report.co, with every open counted.

    python3 test_short_links.py

No pytest, no new dependencies, a temporary data directory so this never
touches /var/data or the real one. Nothing here reaches Short.io:
`hub.short_links.requests` is stubbed with fake responses, so what is worth
asserting is what this module does around the call rather than the call
itself.
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1shortlinks_test_")
os.environ["HUB_DATA_DIR"] = os.path.join(TMP, "data")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "t.db")
os.environ["SECRET_KEY"] = "short-links-test-secret"
os.environ["SHORT_IO_API_KEY"] = "sk_test_key"
os.environ.pop("SHORT_IO_DOMAIN", None)

_passed, _failed = 0, 0


def check(label, got, want):
    global _passed, _failed
    if got == want:
        _passed += 1
        print(f"  ok    {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}\n          got  {got!r}\n          want {want!r}")


def section(title):
    print(f"\n{title}\n{'-' * len(title)}")


# hub.config.settings is a frozen dataclass built once at import, so the key
# above has to be set before hub.short_links (which reads it through
# hub.config.settings) is imported.
from hub import short_links as SL                             # noqa: E402
import requests as _requests                                  # noqa: E402


class _Resp:
    """A stand-in for requests.Response -- only what this module reads."""
    def __init__(self, status_code=200, data=None, raise_on_read=False):
        self.status_code = status_code
        self._data = data if data is not None else {}
        self._raise = raise_on_read

    def json(self):
        if self._raise:
            raise ValueError("not json")
        return self._data


class _Unreachable(_requests.exceptions.ConnectionError):
    """The real exception family requests.post/get actually raise -- a bare
    Exception subclass would not be caught by mask()'s and clicks()'s
    `except requests.RequestException`, which is correct: only a real
    transport failure should be swallowed into a not-measured/refused answer."""


def _fake_post_factory(status_code=200, data=None, raise_exc=None):
    calls = []

    def _post(url, json=None, headers=None, timeout=None):
        calls.append({"url": url, "json": json, "headers": headers})
        if raise_exc:
            raise raise_exc
        return _Resp(status_code, data)
    _post.calls = calls
    return _post


def _fake_get_factory(status_code=200, data=None, raise_exc=None):
    calls = []

    def _get(url, params=None, headers=None, timeout=None):
        calls.append({"url": url, "params": params, "headers": headers})
        if raise_exc:
            raise raise_exc
        return _Resp(status_code, data)
    _get.calls = calls
    return _get


def _link_response(path="acme-hvac/sep/2026/website-refresh",
                   domain="s1report.co", link_id="lnk_1"):
    return {
        "id": link_id,
        "idString": link_id,
        "path": path,
        "originalURL": "https://smart1.agency/sales/builder/p/tok1",
        "secureShortURL": f"https://{domain}/{path}",
        "shortURL": f"https://{domain}/{path}",
        "cloaking": True,
    }


def _reset_registry():
    """Empties the registry for the next section.

    Through jsonstore.delete_json(), never a bare file remove -- exactly the
    trap this codebase's own notes describe: removing only the disk file
    leaves the mirrored copy in the database, and the next read_json()
    restores it right back before returning, silently undoing the reset."""
    from hub import jsonstore
    jsonstore.delete_json(SL._registry_path())


# =====================================================================
section("Configuration -- no key is not_configured, never a silent fallback")
# =====================================================================
check("configured() reads the real key", SL.configured(), True)
check("why_not() is empty when configured", SL.why_not(), "")


class _FakeSettings:
    """A stand-in for hub.config.settings -- only the two fields this module
    reads. Swapped in and out rather than reloading hub.config, so the rest
    of this process's already-imported modules are never disturbed."""
    def __init__(self, key="sk_test_key", domain="s1report.co"):
        self.short_io_key = key
        self.short_io_domain = domain


_real_settings = SL.settings

SL.settings = _FakeSettings(key="")
check("configured() is False with no key", SL.configured(), False)
out = SL.mask("https://smart1.agency/p/x", client="Acme", project="Preview")
check("mask() refuses rather than sending the plain URL silently",
      out["ok"], False)
check("and says why", out["reason"], "not_configured")

SL.settings = _FakeSettings(key="sk_test_key")
check("configured() again once the key is back", SL.configured(), True)


# =====================================================================
section("The slug: client/month/year/project")
# =====================================================================
import datetime as _dt
when = _dt.datetime(2026, 9, 3, tzinfo=_dt.timezone.utc)
check("each segment is slugified on its own",
      SL.build_path("Riverside HVAC, LLC", "Website Refresh", when=when),
      "riverside-hvac/sep/2026/website-refresh")
check("the client segment agrees with client_key.name_slug elsewhere in the Hub",
      SL.build_path("Riverside HVAC, LLC", "x", when=when).split("/")[0],
      "riverside-hvac")
check("a project title is not run through the name-dropping slugifier",
      SL.build_path("Acme", "The Big Fall Campaign", when=when),
      "acme/sep/2026/the-big-fall-campaign")
check("an empty client falls back to a literal placeholder rather than a blank segment",
      SL.build_path("", "Preview", when=when).split("/")[0], "client")


# =====================================================================
section("mask() -- refusals before anything is sent")
# =====================================================================
_reset_registry()
out = SL.mask("not-a-url", client="Acme", project="Preview")
check("a URL with no scheme is refused", (out["ok"], out["reason"]), (False, "bad_url"))

out = SL.mask("https://smart1.agency/p/x", client="", project="Preview")
check("no client is refused", (out["ok"], out["reason"]), (False, "no_client"))

out = SL.mask("https://smart1.agency/p/x", client="Acme", project="")
check("no project is refused", (out["ok"], out["reason"]), (False, "no_project"))

out = SL.mask("https://smart1.agency/p/x", client="Acme", project="Preview",
             domain="not-ours.co")
check("a domain outside this account's four is refused",
      (out["ok"], out["reason"]), (False, "bad_domain"))
check("DOMAINS lists exactly this account's four",
      SL.DOMAINS, ("s1dev.co", "s1leads.co", "s1report.co", "s1snap.co"))
check("and the default is s1report.co",
      SL.settings.short_io_domain or SL.CLIENT_FACING_DOMAIN, "s1report.co")


# =====================================================================
section("mask() -- creating a link")
# =====================================================================
_reset_registry()
url1 = "https://smart1.agency/sales/builder/p/tok-abc"
SL.requests.post = _fake_post_factory(200, _link_response())
out = SL.mask(url1, client="Acme HVAC", project="Website Refresh", when=when,
             actor="todd")
check("a new link is created", out["ok"], True)
check("and marked as new, not reused", out["reused"], False)
check("cloaking is requested so the address bar stays on our domain",
      SL.requests.post.calls[-1]["json"]["cloaking"], True)
check("allowDuplicates is off, so Short.io tells us about a real collision",
      SL.requests.post.calls[-1]["json"]["allowDuplicates"], False)
check("the auth header carries the raw key, no Bearer prefix",
      SL.requests.post.calls[-1]["headers"]["Authorization"], "sk_test_key")
check("the built path was sent", SL.requests.post.calls[-1]["json"]["path"],
      "acme-hvac/sep/2026/website-refresh")
check("the record is persisted", SL.by_path("acme-hvac/sep/2026/website-refresh")
      is not None, True)
check("for_url() finds it by destination, with no network call",
      SL.for_url(url1)["short_url"], out["short_url"])

before = len(SL.requests.post.calls)
out2 = SL.mask(url1, client="Acme HVAC", project="Website Refresh", when=when)
check("masking the same destination again reuses the stored record",
      out2["reused"], True)
check("and never calls Short.io a second time",
      len(SL.requests.post.calls), before)


# =====================================================================
section("mask() -- a slug already pointing somewhere else is never overwritten")
# =====================================================================
_reset_registry()
SL.requests.post = _fake_post_factory(
    200, _link_response(path="acme-hvac/sep/2026/website-refresh"))
first = SL.mask(url1, client="Acme HVAC", project="Website Refresh", when=when)
check("the first link is created at the plain slug", first["path"],
      "acme-hvac/sep/2026/website-refresh")

url2 = "https://smart1.agency/sales/builder/p/tok-def"
SL.requests.post = _fake_post_factory(
    200, _link_response(path="acme-hvac/sep/2026/website-refresh-2"))
second = SL.mask(url2, client="Acme HVAC", project="Website Refresh", when=when)
check("a different destination for the same slug is never silently repointed",
      second["path"] != first["path"], True)
check("it is suffixed instead", second["path"],
      "acme-hvac/sep/2026/website-refresh-2")
check("and the request Short.io actually saw carries the suffixed path",
      SL.requests.post.calls[-1]["json"]["path"],
      "acme-hvac/sep/2026/website-refresh-2")

# Every numbered variant already taken by a different destination -> refused.
_reset_registry()
for n in range(0, SL._MAX_SUFFIX + 2):
    p = "acme-hvac/sep/2026/website-refresh" + (f"-{n+1}" if n else "")
    SL._save_record({"id": f"id{n}", "path": p, "domain": "s1report.co",
                     "short_url": f"https://s1report.co/{p}",
                     "original_url": f"https://smart1.agency/other-{n}",
                     "client": "Acme HVAC", "project": "Website Refresh",
                     "title": "", "created_at": "2026-01-01T00:00:00+00:00",
                     "created_by": "", "clicks_total": None, "clicks_human": None,
                     "clicks_checked_at": "", "clicks_measured": False,
                     "clicks_error": ""})
SL.requests.post = _fake_post_factory(200, _link_response())
out = SL.mask("https://smart1.agency/sales/builder/p/tok-new",
             client="Acme HVAC", project="Website Refresh", when=when)
check("exhausting every numbered variant is refused, not sent anyway",
      (out["ok"], out["reason"]), (False, "path_taken"))
check("and nothing was sent to Short.io for it",
      len(SL.requests.post.calls), 0)


# =====================================================================
section("mask() -- Short.io's own answers")
# =====================================================================
_reset_registry()
SL.requests.post = _fake_post_factory(409, {"error": "path taken"})
out = SL.mask(url1, client="Acme", project="Preview", when=when)
check("a 409 from Short.io itself is a conflict, not a crash",
      (out["ok"], out["reason"]), (False, "conflict"))

_reset_registry()
SL.requests.post = _fake_post_factory(401, {"error": "Invalid API key"})
out = SL.mask(url1, client="Acme", project="Preview", when=when)
check("a refused key is reported with Short.io's own reason",
      (out["ok"], out["reason"], out["error"]),
      (False, "refused", "Invalid API key"))

_reset_registry()
SL.requests.post = _fake_post_factory(raise_exc=_Unreachable("timed out"))
out = SL.mask(url1, client="Acme", project="Preview", when=when)
check("an unreachable Short.io never raises out of mask()",
      (out["ok"], out["reason"]), (False, "unreachable"))


# =====================================================================
section("clicks() -- tri-state, never a bare number")
# =====================================================================
SL.requests.get = _fake_get_factory(200, {"totalClicks": 7, "humanClicks": 5})
out = SL.clicks("lnk_1")
check("a real answer is measured", out, {"measured": True, "total": 7, "human": 5})

SL.requests.get = _fake_get_factory(200, {})
out = SL.clicks("lnk_1")
check("an answer with no click count is not_measured, never zero",
      out["measured"], False)

SL.requests.get = _fake_get_factory(401, {"error": "bad key"})
out = SL.clicks("lnk_1")
check("a refused key is not_measured with the reason", out["measured"], False)
check("no link id at all is not_measured", SL.clicks("")["measured"], False)

SL.requests.get = _fake_get_factory(raise_exc=_Unreachable("dns"))
out = SL.clicks("lnk_1")
check("a network failure never raises out of clicks()", out["measured"], False)


# =====================================================================
section("refresh_all() -- behind a button, one merge, not a call per link")
# =====================================================================
_reset_registry()
SL.requests.post = _fake_post_factory(
    200, _link_response(path="acme/sep/2026/preview", link_id="lnk_a"))
r1 = SL.mask("https://smart1.agency/p/a", client="Acme", project="Preview", when=when)
SL.requests.post = _fake_post_factory(
    200, _link_response(path="beta/sep/2026/preview", link_id="lnk_b"))
r2 = SL.mask("https://smart1.agency/p/b", client="Beta", project="Preview", when=when)

def _get_with_counts(url, params=None, headers=None, timeout=None):
    link_id = url.rsplit("/", 1)[-1]
    if link_id == "lnk_a":
        return _Resp(200, {"totalClicks": 3, "humanClicks": 2})
    return _Resp(401, {"error": "bad key"})
SL.requests.get = _get_with_counts

summary = SL.refresh_all(limit=10)
check("one measured, one not", summary, {"checked": 1, "failed": 1, "of": 2})
rows = {r["id"]: r for r in SL.all_links()}
check("the measured row is updated", rows["lnk_a"]["clicks_total"], 3)
check("the unmeasured row keeps its error and no invented zero",
      (rows["lnk_b"]["clicks_measured"], rows["lnk_b"]["clicks_total"]),
      (False, None))
check("all_links(client=) narrows to one client",
      [r["id"] for r in SL.all_links(client="Beta")], ["lnk_b"])


SL.settings = _real_settings

print(f"\n{_passed} passed, {_failed} failed")
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if _failed else 0)
