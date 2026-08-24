"""modules/msa — the signing page, and the embed that carries it.

    python3 test_msa_embed.py

Same shape as test_landing_maker.py: no pytest, no new dependencies, a
temporary data directory and a throwaway SQLite mirror, so it never touches
/var/data or the real database.

## Why this file exists

The MSA page is the second thing in the Hub read by someone who is not staff,
and it is the only one where that person signs a contract. That puts three
different kinds of failure on one page, each of which looks fine from inside
the Hub:

  1. **It must be reachable without a login.** A client signing an agreement
     has no Hub account. A login redirect here is a lost signature, and it is
     invisible to staff because staff are always signed in.

  2. **It must carry no staff chrome.** Same rule as a landing page, for the
     same reason — the sidebar and the feedback tab do not belong on
     smart1marketing.com. Checked on the response the browser actually
     receives, since HubBar and the hub's after_request both rewrite HTML
     they did not write.

  3. **It must be framable by us and nobody else.** The scans widget sends
     `frame-ancestors *` because it is pasted onto clients' own domains and
     cannot know them in advance. This page is framed only on ours, and it
     submits a legally binding signature — a page anyone can frame is a page
     anyone can lay a transparent button over. So the allowlist is asserted
     here rather than left to whoever next edits the header.

And two traps that are specific to this embed:

  4. **The API path is relative.** The page calls `fetch('api/sign')`, which
     resolves against the DIRECTORY of the current URL. From /msa/embed that
     is /msa/api/sign; from /msa/embed/ it would be /msa/embed/api/sign — a
     404 nobody meets until they have read the whole agreement and pressed
     sign. So /embed has no trailing slash, and /embed/ redirects to it.

  5. **PLACEHOLDER text cannot be signed.** While the clause text is still
     the template, someone signing would be agreeing to the word
     PLACEHOLDER. That is worse than the page being unavailable, because it
     looks like an agreement. The server refuses, and the page says so at the
     top rather than letting them discover it at the bottom.
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1msa_test_")
DISK = os.path.join(TMP, "disk")
os.makedirs(DISK, exist_ok=True)

os.environ["HUB_DATA_DIR"] = DISK
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "mirror.sqlite3")
os.environ["SECRET_KEY"] = "msa-test-secret"
os.environ["PANEL_PASSWORD"] = "msa-test-password"

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


# The composed app, not the module's own — mount shadowing and the chrome
# injection only exist once everything is stacked together, which is the
# whole reason CLAUDE.md says to boot through wsgi.application.
from wsgi import application                                  # noqa: E402
from werkzeug.test import Client                              # noqa: E402

client = Client(application)


section("A client with no Hub login can reach it")

for path in ("/msa/", "/msa/embed", "/msa/embed.js"):
    r = client.get(path)
    check(f"{path} is served, not redirected to sign in", r.status_code, 200)

check("the hosted page is HTML",
      client.get("/msa/").headers["Content-Type"].startswith("text/html"), True)
check("the loader is JavaScript",
      client.get("/msa/embed.js").headers["Content-Type"]
      .startswith("application/javascript"), True)


section("No staff chrome reaches the client")

for path in ("/msa/", "/msa/embed"):
    body = client.get(path).get_data(as_text=True)
    check(f"{path} has no sidebar", "s1hub-sb" in body or 'class="sidebar"' in body, False)
    check(f"{path} has no feedback tab", "hub-feedback" in body, False)

embed_body = client.get("/msa/embed").get_data(as_text=True)
plain_body = client.get("/msa/").get_data(as_text=True)
check("the embed drops our own header too", 'class="embedded"' in embed_body, True)
check("and the hosted page keeps it", 'class="embedded"' in plain_body, False)


section("Only our domains may frame it")

csp = client.get("/msa/embed").headers.get("Content-Security-Policy", "")
check("the embed sends frame-ancestors", csp.startswith("frame-ancestors"), True)
check("smart1marketing.com is allowed", "https://smart1marketing.com" in csp, True)
check("so are its subdomains", "https://*.smart1marketing.com" in csp, True)
# The one that matters. "*" here would be a clickjacking hole on a page that
# takes a signature, and it is a single character to introduce by accident.
check("it is an allowlist, not a wildcard", "frame-ancestors *" in csp, False)
check("X-Frame-Options does not survive to contradict it",
      client.get("/msa/embed").headers.get("X-Frame-Options"), None)


section("The relative API path resolves from the embed URL")

# fetch('api/sign') resolves against the directory of the current URL, so the
# absence of a trailing slash is load-bearing rather than cosmetic.
check("the page calls its API with a same-origin literal",
      "fetch('api/sign'" in embed_body, True)
check("/msa/embed has no trailing slash", client.get("/msa/embed").status_code, 200)
r = client.get("/msa/embed/")
check("/msa/embed/ redirects rather than 404ing", r.status_code in (301, 308), True)
check("and it redirects under the mount, not to the hub app",
      r.headers.get("Location", "").endswith("/msa/embed"), True)
check("following it lands on the embed",
      'class="embedded"' in client.get("/msa/embed/", follow_redirects=True)
      .get_data(as_text=True), True)
# What the API path resolves to from /msa/embed, spelled out.
check("/msa/api/sign is a real route",
      client.post("/msa/api/sign", json={}).status_code != 404, True)


section("Template text cannot be signed")

import modules.msa.app as msa                                 # noqa: E402

signable = dict(company="Acme Marine, LLC", address="120 Harbor Road, Columbus, OH",
                signer="Dana Reed", email="dana@acmemarine.com",
                agree_rates=True, agree_launch=True)

_real_body = msa.MSA_BODY
msa.MSA_BODY = [("2. Services", ["PLACEHOLDER — paste the Services section."])]
check("signing is refused while a clause is PLACEHOLDER",
      client.post("/msa/api/sign", json=signable).status_code, 503)
check("and the page says so before they fill anything in",
      "isn't ready to sign yet" in client.get("/msa/embed").get_data(as_text=True), True)

msa.MSA_BODY = [("1. Parties", ["Entered into on {date} between Smart 1 "
                                "Marketing and {company}, at {address}."])]
check("the warning goes away once the clauses are real",
      "isn't ready to sign yet" in client.get("/msa/embed").get_data(as_text=True), False)


section("A signature is recorded, attributable, and downloadable")

r = client.post("/msa/api/sign", json=signable)
check("signing succeeds", r.status_code, 200)
signed = r.get_json()

# Guessable tokens were handing over other companies' signed contracts —
# address, signer name and IP included — because the download route is public.
check("the token is not just the company and a timestamp",
      len(signed["token"]) > len("acme-marine-llc-20260824133409"), True)
check("the download path is relative to the mount, as the page builds it",
      signed["download"].startswith("pdf/"), True)

pdf = client.get("/msa/" + signed["download"])
check("the PDF comes back", pdf.status_code, 200)
check("as a PDF", pdf.headers["Content-Type"], "application/pdf")
check("with something in it", len(pdf.get_data()) > 1000, True)
check("a guessed token gets nothing",
      client.get("/msa/pdf/acme-marine-llc-20260824133409").status_code, 404)

from hub import audit                                         # noqa: E402
entries = audit.read(limit=20, module="msa")
check("the signature reached the activity log", len(entries) >= 1, True)
# client= is what puts it on that client's 360 record. Logged without it, the
# entry exists and is attached to nobody, which is the same as not logging.
check("and carries the client, so it lands on their 360 record",
      entries[0].get("client"), "Acme Marine, LLC")

msa.MSA_BODY = _real_body


section("Both boxes are checked on the server")

for missing in ("agree_rates", "agree_launch"):
    payload = dict(signable)
    payload[missing] = False
    check(f"{missing} unchecked is refused",
          client.post("/msa/api/sign", json=payload).status_code in (400, 503), True)


# ------------------------------------------------------------------- summary
shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{'-' * 60}\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
