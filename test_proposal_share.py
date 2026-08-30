"""The client's copy of a proposal — the link, the count, the acceptance.

    python3 test_proposal_share.py

Same shape as the others: no pytest, no new dependencies, a throwaway SQLite
database and a temporary data directory, nothing reaching a third party.

## Why this file exists

A read receipt is a number a rep acts on — they call, they follow up, they
stop chasing — so the ways it can quietly mean something else are the whole
feature:

  * **A mail security gateway opens every link in the message.** Counted, that
    reports every proposal as read the moment it is sent: a confident wrong
    answer that stops somebody chasing a client who has never seen it.
  * **The rep opens it to check the link works.** The feature was asked for
    with this rule attached — staff must be able to read the document without
    marking it read — and it is the one that would be broken silently, because
    the count would simply be one too high and nobody could tell which one.
  * **A reload is not a second read.**
  * **An acceptance belongs to one revision.** A quote edited after a client
    said yes must not carry that yes forward onto a document nobody agreed to.

And the two ordinary ways a client-facing page goes wrong: it must be
reachable without a Hub login, and a revoked link must answer exactly what a
link that never existed answers.
"""
import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

_TMP = tempfile.mkdtemp(prefix="s1-share-")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(_TMP, "t.db")
os.environ.setdefault("SECRET_KEY", "share-test")
os.environ.setdefault("PANEL_PASSWORD", "test")
os.environ.setdefault("HUB_DATA_DIR", _TMP)
os.environ.setdefault("PUBLIC_BASE_URL", "https://smart1.agency")

PASS = FAIL = 0


def check(label, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print("  ok   " + label)
    else:
        FAIL += 1
        print("  FAIL " + label + (("  — " + str(detail)) if detail else ""))


def section(title):
    print("\n" + title)
    print("-" * 62)


# ---------------------------------------------------------------------------
section("what counts as somebody having read it")
# ---------------------------------------------------------------------------
from hub import view_tracking as vt                                # noqa: E402

check("a real browser counts",
      vt.looks_automated("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                         "AppleWebKit/537.36 Chrome/120 Safari/537.36") == (False, ""))
for agent in ("Slackbot-LinkExpanding 1.0", "Mimecast-Link-Scanner/2",
              "python-requests/2.32", "facebookexternalhit/1.1",
              "Mozilla/5.0 (compatible; Googlebot/2.1)"):
    verdict, why = vt.looks_automated(agent)
    check(f"a machine does not: {agent.split('/')[0][:28]}", verdict and why, why)
check("and a request with no User-Agent at all is a script, not a reader",
      vt.looks_automated("")[0])
check("a prefetch is not a read",
      vt.looks_automated("Mozilla/5.0 Chrome/120",
                         {"sec-purpose": "prefetch"})[0])
check("the reason is carried, because 'we did not count that one' needs explaining",
      bool(vt.looks_automated("Slackbot")[1]))

check("a reload inside the window is the same read",
      vt.counts_as_new_view(1000.0, 1000.0 + 60) is False)
check("and opening it again the next day is a new one",
      vt.counts_as_new_view(1000.0, 1000.0 + 86400) is True)
check("a first visit always counts", vt.counts_as_new_view(None, 1000.0) is True)

check("a visitor is a keyed digest, never an address",
      "10.0.0.1" not in vt.visitor_hash("10.0.0.1", "k")
      and len(vt.visitor_hash("10.0.0.1", "k")) == 32)
check("and the key changes it, so digests are useless off this deployment",
      vt.visitor_hash("10.0.0.1", "a") != vt.visitor_hash("10.0.0.1", "b"))
check("a phone is told from a computer, and nothing narrower is kept",
      vt.device_kind("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)") == "phone"
      and vt.device_kind("Mozilla/5.0 (Macintosh)") == "computer")

# ---------------------------------------------------------------------------
section("through the running app")
# ---------------------------------------------------------------------------
from werkzeug.test import Client                                   # noqa: E402
import wsgi                                                        # noqa: E402
from hub import auth                                               # noqa: E402

builder = sys.modules.get("salesb_app")
if builder is None:                             # pragma: no cover - mount failed
    from modules.sales_builder import app as builder

BROWSER = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
           "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

staff = Client(wsgi.application)
staff.set_cookie(auth.COOKIE_NAME, auth.issue_cookie_value("Harness"),
                 domain="localhost")
# A client has no Hub session at all. Separate client object, deliberately:
# sharing one and deleting the cookie is how a test passes because the cookie
# was still there.
visitor = Client(wsgi.application)


def api(client, method, path, **kw):
    return getattr(client, method)(path, **kw).get_json()


state = {"client": "Riverstone Dental", "months": 6, "budget": 8000,
         "kpis": ["Cost per lead"],
         "objectives": ["Lead Generation"],
         "items": [{"category": "DISPLAY", "product": "Category", "rate": "CPM",
                    "rateValue": 4.25, "dollars": 8000}]}
quote = api(staff, "post", "/sales/builder/api/quotes",
            json={"data": state})["quote"]
qid = quote["id"]

check("a quote starts with no client link",
      api(staff, "get", f"/sales/builder/api/quotes/{qid}/share")["share"]["shared"]
      is False)

made = api(staff, "post", f"/sales/builder/api/quotes/{qid}/share")["share"]
token = made["token"]
check("creating one hands back a link", made["shared"] and made["url"], made)
check("built on PUBLIC_BASE_URL, not on the module's own mount",
      made["url"].startswith("https://smart1.agency/sales/builder/p/")
      and "/sales/builder/sales/builder/" not in made["url"], made["url"])
check("and sending it moves the quote off Draft",
      api(staff, "get", f"/sales/builder/api/quotes/{qid}")["quote"]["status"] == "Sent")
check("pressing it again keeps the same link, because one is already in an inbox",
      api(staff, "post", f"/sales/builder/api/quotes/{qid}/share")["share"]["token"]
      == token)

page = visitor.get(f"/sales/builder/p/{token}", headers={"User-Agent": BROWSER})
check("a client with no Hub login can open it", page.status_code == 200,
      page.status_code)
body = page.data.decode()
check("and it is the proposal, not a login form",
      "Riverstone Dental" in body and "Accept this proposal" in body)
check("the Hub's own chrome is not injected into a document a client reads",
      "hub-sidebar" not in body and "s1hub" not in body,
      [ln for ln in body.splitlines() if "s1hub" in ln][:2])
check("and it asks search engines not to index it", "noindex" in body)
# Being chrome-free also left hub-thinking.js off the page, so Accept grayed
# out and said one word with no mark beside it. hub/thinking.py inlines the
# mark; test_thinking.py holds it in step with the Hub's own.
check("the mark that says something is running is inlined instead",
      ".s1w-mark{" in body and "window.S1Wait = {" in body)

pdf = visitor.get(f"/sales/builder/p/{token}.pdf")
check("the document itself is served to the client",
      pdf.status_code == 200 and pdf.data[:4] == b"%PDF", pdf.status_code)

# --- the count -------------------------------------------------------------
def opens(client=staff):
    return api(client, "get", f"/sales/builder/api/quotes/{qid}/share")["share"]


check("fetching the page has not counted anything on its own",
      opens()["views"] == 0, opens()["views"])
check("nor has the client downloading the PDF",
      opens()["views"] == 0)

beacon = visitor.post(f"/sales/builder/api/p/{token}/opened",
                      headers={"User-Agent": BROWSER}).get_json()
check("the page reporting itself is what counts a view", beacon["counted"] is True)
check("and the panel says one open", opens()["views"] == 1, opens()["views"])

again = visitor.post(f"/sales/builder/api/p/{token}/opened",
                     headers={"User-Agent": BROWSER}).get_json()
check("a reload inside the window is the same read, not a second one",
      again["counted"] is False and opens()["views"] == 1, opens())

scanner = Client(wsgi.application).post(
    f"/sales/builder/api/p/{token}/opened",
    headers={"User-Agent": "Mimecast-Link-Scanner/2.0"}).get_json()
check("a mail scanner opening every link in the message counts nothing",
      scanner["counted"] is False and opens()["views"] == 1, scanner)
check("and it answers 200 rather than erroring on a client's page",
      scanner["ok"] is True)

staff_open = staff.post(f"/sales/builder/api/p/{token}/opened",
                        headers={"User-Agent": BROWSER}).get_json()
check("THE RULE: a signed-in rep reading the client's copy is not counted",
      staff_open["counted"] is False and "staff" in staff_open["reason"],
      staff_open)
check("and the count is still one", opens()["views"] == 1, opens()["views"])
staff_page = staff.get(f"/sales/builder/p/{token}", headers={"User-Agent": BROWSER})
check("the rep's copy says so on the page, rather than leaving it to be trusted",
      "not counted" in staff_page.data.decode())
check("a rep can still read the PDF without marking it read",
      staff.get(f"/sales/builder/p/{token}.pdf").status_code == 200
      and opens()["views"] == 1)

check("what was counted is kept per revision, so 'have they seen THIS one' "
      "has an answer",
      opens()["views_this_revision"] == 1 and opens()["revision"] == 1, opens())
check("and each open records the device, which is all that is kept of a reader",
      opens()["opens"][0]["device"] in ("phone", "computer"), opens()["opens"][:1])

# --- acceptance ------------------------------------------------------------
bad = visitor.post(f"/sales/builder/api/p/{token}/accept",
                   json={"name": "", "email": ""})
check("an acceptance nobody can attribute is refused", bad.status_code == 400)

staff_accept = staff.post(f"/sales/builder/api/p/{token}/accept",
                          json={"name": "A Rep", "email": "rep@smart1marketing.com"})
check("and a rep cannot accept on the client's behalf",
      staff_accept.status_code == 403, staff_accept.status_code)

ok = visitor.post(f"/sales/builder/api/p/{token}/accept",
                  json={"name": "Jane Whitfield", "email": "jane@riverstone.com"},
                  headers={"User-Agent": BROWSER}).get_json()
check("the client accepting is recorded", ok["ok"] and ok["accepted"]["name"]
      == "Jane Whitfield", ok)
check("the quote is Approved without anybody clicking a pill",
      api(staff, "get", f"/sales/builder/api/quotes/{qid}")["quote"]["status"]
      == "Approved")
state_now = opens()
check("and the panel names who accepted, and which revision",
      state_now["accepted"]["name"] == "Jane Whitfield"
      and state_now["accepted"]["revision"] == 1, state_now["accepted"])
check("the page then shows it as accepted rather than asking again",
      "Accepted" in visitor.get(f"/sales/builder/p/{token}").data.decode())
twice = visitor.post(f"/sales/builder/api/p/{token}/accept",
                     json={"name": "Jane Again", "email": "jane@riverstone.com"}).get_json()
check("accepting twice does not file a second acceptance",
      twice.get("already") is True, twice)

# --- an updated quote ------------------------------------------------------
section("and again, when the quote is updated")
state["budget"] = 12000
api(staff, "put", f"/sales/builder/api/quotes/{qid}",
    json={"data": state, "bump_revision": True})
after = opens()
check("the same link still works — it is already in the client's inbox",
      visitor.get(f"/sales/builder/p/{token}").status_code == 200)
check("the revision moved", after["revision"] == 2, after["revision"])
check("the earlier acceptance is superseded, not carried onto a document "
      "nobody agreed to",
      after["accepted"] is None and after["superseded"]["revision"] == 1, after)
check("and the panel says the rep has edited it since sending",
      after["edited_since_sent"] is True, after)

re_opened = visitor.post(f"/sales/builder/api/p/{token}/opened",
                         headers={"User-Agent": BROWSER}).get_json()
check("a client opening the new version counts again, window or not",
      re_opened["counted"] is True, re_opened)
after2 = opens()
check("the total counts every open", after2["views"] == 2, after2["views"])
check("and the panel can still say how many were of THIS revision",
      after2["views_this_revision"] == 1, after2)

accepted_2 = visitor.post(f"/sales/builder/api/p/{token}/accept",
                          json={"name": "Jane Whitfield",
                                "email": "jane@riverstone.com"}).get_json()
check("the client can accept the new revision too", accepted_2["ok"])
check("and it is recorded against revision 2",
      opens()["accepted"]["revision"] == 2, opens()["accepted"])
check("re-sending clears the edited-since-sent warning",
      api(staff, "post", f"/sales/builder/api/quotes/{qid}/share")
      ["share"]["edited_since_sent"] is False)

# --- switching it off ------------------------------------------------------
api(staff, "post", f"/sales/builder/api/quotes/{qid}/share/revoke")
gone = visitor.get(f"/sales/builder/p/{token}")
check("a revoked link stops working", gone.status_code == 404)
never = visitor.get("/sales/builder/p/not-a-real-token-at-all")
check("and answers exactly what a link that never existed answers — a page "
      "that says which tokens are real is a page worth probing",
      never.status_code == gone.status_code
      and never.data == gone.data, (never.status_code, gone.status_code))
check("a revoked link cannot be accepted either",
      visitor.post(f"/sales/builder/api/p/{token}/accept",
                   json={"name": "X", "email": "x@y.com"}).status_code == 404)

# ---------------------------------------------------------------------------
print("\n" + "-" * 62)
print(f"{PASS} passed, {FAIL} failed")
shutil.rmtree(_TMP, ignore_errors=True)
sys.exit(1 if FAIL else 0)
