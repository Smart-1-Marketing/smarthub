"""hub/outbound.py — one reading of "may this Hub fetch that URL".

    python3 test_outbound.py

No pytest and no new dependencies. Nothing here reaches the network: DNS and
`requests.get` are both stubbed, because what is worth asserting is what this
does with each answer rather than what the internet happens to be doing.

## What is worth asserting

  * **The check is on the resolved addresses, not the hostname.** A perfectly
    public name pointing at a private address is the whole trick, and a
    hostname denylist catches none of it. This is the one assertion that makes
    the rest worth having.

  * **Every redirect hop is re-checked.** A guard that validates the first URL
    and lets `requests` follow a 302 to `http://169.254.169.254/` has checked
    nothing — so redirects are followed by hand and the hop is where the
    refusal has to land.

  * **Unresolvable is refused, not allowed.** "We could not look" has never
    been permission anywhere else in this Hub.

  * **The body is capped on the bytes actually read.** Not on a Content-Length
    a server is free to understate, and the caller is told it was cut short so
    it can say it only saw part of the page rather than concluding something is
    absent from it.
"""
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

PASS = FAIL = 0


def check(label, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print("  ok   " + label)
    else:
        FAIL += 1
        print("  FAIL " + label + (("  — " + str(detail)) if detail else ""))


from hub import outbound                                      # noqa: E402

# --------------------------------------------------------------- the check
print("\nwhat may be fetched")

_real_addr = outbound.socket.getaddrinfo
DNS = {}


def fake_getaddrinfo(host, port, *a, **kw):
    if host not in DNS:
        raise OSError("no such host")
    return [(2, 1, 6, "", (DNS[host], port))]


outbound.socket.getaddrinfo = fake_getaddrinfo

DNS.update({"good.example": "93.184.216.34",
            # The whole trick: a public name that resolves inside.
            "sneaky.example": "169.254.169.254",
            "db.example": "127.0.0.1",
            "vpc.example": "10.1.2.3"})

ok, why = outbound.safe_url("https://good.example/page/")
check("a public host resolves and is allowed", ok is True, why)

ok, why = outbound.safe_url("https://sneaky.example/")
check("a PUBLIC NAME resolving to a private address is refused", ok is False)
check("and the refusal names the address rather than the name alone",
      "169.254.169.254" in why, why)
ok, _ = outbound.safe_url("https://db.example/")
check("loopback is refused however it is spelled", ok is False)
ok, _ = outbound.safe_url("https://vpc.example/")
check("a private range is refused", ok is False)

ok, why = outbound.safe_url("https://nowhere.example/")
check("a name that does not resolve is refused, not allowed", ok is False)
check("and says so rather than blaming the address", "does not resolve" in why, why)

for bad, label in [("file:///etc/passwd", "file://"),
                   ("ftp://good.example/x", "ftp://"),
                   ("", "an empty string"),
                   ("https://", "a URL with no host")]:
    ok, _ = outbound.safe_url(bad)
    check(f"{label} is refused", ok is False)

ok, why = outbound.safe_url("https://user:pw@good.example/")
check("credentials in the URL are refused", ok is False)
check("and it says they are never sent", "never sends" in why, why)

ok, _ = outbound.safe_url("http://good.example/")
check("plain http to a public host is allowed — WordPress sites redirect, and "
      "refusing the scheme here would refuse the redirect that fixes it",
      ok is True)


# --------------------------------------------------------------- fetching
print("\nfetching one")


class Resp:
    def __init__(self, status=200, headers=None, body=b"", redirect_to=""):
        self.status_code = status
        self.headers = dict(headers or {})
        if redirect_to:
            self.status_code = 302
            self.headers["Location"] = redirect_to
        self._body = body
        self.encoding = "utf-8"
        self.apparent_encoding = "utf-8"
        self.url = ""

    @property
    def is_redirect(self):
        return self.status_code in (301, 302, 303, 307, 308) \
            and bool(self.headers.get("Location"))

    def iter_content(self, n):
        for i in range(0, len(self._body), n):
            yield self._body[i:i + n]

    def close(self):
        pass


PLAN = {}
ASKED = []


def fake_get(url, headers=None, timeout=None, allow_redirects=None, stream=None):
    ASKED.append(url)
    if url not in PLAN:
        raise outbound.requests.RequestException("nothing planned for " + url)
    return PLAN[url]


outbound.requests.get = fake_get

PLAN.clear(); ASKED.clear()
PLAN["https://good.example/a"] = Resp(body=b"<html>hello</html>")
r, why = outbound.fetch("https://good.example/a")
check("an ordinary page comes back", r is not None and r.text == "<html>hello</html>", why)
check("and is not marked truncated", r.truncated is False)

# The hop is where a guard that checked only the first URL has already lost.
PLAN.clear(); ASKED.clear()
PLAN["https://good.example/a"] = Resp(redirect_to="http://sneaky.example/meta")
r, why = outbound.fetch("https://good.example/a")
check("a redirect INTO our own network is refused at the hop", r is None)
check("and the refusal says it was the redirect",
      "redirected somewhere we do not fetch" in why, why)
check("and the second address was never requested",
      ASKED == ["https://good.example/a"], ASKED)

PLAN.clear(); ASKED.clear()
PLAN["https://good.example/a"] = Resp(redirect_to="https://good.example/b")
PLAN["https://good.example/b"] = Resp(body=b"landed")
r, why = outbound.fetch("https://good.example/a")
check("a redirect to a public address is followed", r is not None and r.text == "landed", why)

PLAN.clear(); ASKED.clear()
for i in range(9):
    PLAN[f"https://good.example/{i}"] = Resp(redirect_to=f"https://good.example/{i+1}")
r, why = outbound.fetch("https://good.example/0")
check("a redirect chain longer than the cap stops rather than following",
      r is None and "redirects more times" in why, why)

PLAN.clear(); ASKED.clear()
_empty = Resp(body=b"")
_empty.status_code = 302          # a 302 carrying no Location at all
r, why = outbound.fetch("https://good.example/a")
PLAN["https://good.example/a"] = _empty
r, why = outbound.fetch("https://good.example/a")
check("a 302 with no Location is read as a page rather than followed into an "
      "empty string", r is not None and r.status_code == 302, (r, why))

PLAN.clear(); ASKED.clear()
PLAN["https://good.example/big"] = Resp(body=b"x" * (3 * 1024 * 1024))
r, why = outbound.fetch("https://good.example/big", max_bytes=1024)
check("a body over the cap is cut off at the cap",
      r is not None and len(r.content) == 1024, r and len(r.content))
check("and the caller is told it was cut short, so it cannot conclude "
      "something is absent from a page it only half read",
      r.truncated is True)

PLAN.clear(); ASKED.clear()
r, why = outbound.fetch("https://good.example/gone")
check("a host that refuses the connection is a reason, not an exception",
      r is None and "Could not reach" in why, why)

PLAN.clear(); ASKED.clear()
r, why = outbound.fetch("http://sneaky.example/latest/meta-data/")
check("the metadata endpoint is refused before any request is made",
      r is None and ASKED == [], (why, ASKED))

PLAN.clear(); ASKED.clear()
SENT = {}


def recording_get(url, headers=None, **kw):
    SENT.update(headers or {})
    return Resp(body=b"hi")


outbound.requests.get = recording_get
outbound.fetch("https://good.example/a", headers={"X-Test": "1"})
check("a caller's headers are sent", SENT.get("X-Test") == "1", SENT)
check("...alongside the user agent rather than replacing it",
      "Smart1Hub" in (SENT.get("User-Agent") or ""), SENT)
outbound.requests.get = fake_get

outbound.socket.getaddrinfo = _real_addr

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
