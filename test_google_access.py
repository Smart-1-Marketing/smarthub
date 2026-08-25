"""Google Access: the paused Ads flow, and who an invite is actually for.

    python3 test_google_access.py

Same shape as the other test files here: no pytest, no new dependencies, a
throwaway SQLite database and a temporary data directory, so it never touches
/var/data or the real one. Nothing reaches Google — the client registry is a
fixture and no invite is ever consented to.

## What is worth asserting

**Google Ads is paused, and paused means gone from the client's screen.** Ads
was never like the others: there is no "add this email" call, so we send a
manager-account link invitation from our own MCC, which needs an approved
developer token and our own refresh token. Neither is set. Left in the list it
failed in the worst available place — after the client had ticked it, read a
page promising it, and signed in. So the assertion is not that the code is
tidy but that no client-facing page still offers or explains it.

**A parked service is not a deleted one.** Requests created before this still
carry `"ads"` in their stored service list. Those rows must not KeyError the
page they appear on, must not silently vanish from the record they belong to,
and must still be closeable by a human — a row nobody can mark is a request
that reads "waiting" for ever.

**Existing and new are different questions, and neither may be guessed.** An
existing client is matched against the Hub registry exactly or not at all —
"Riverside HVAC" must never file against "Riverside HVAC Supply", which is the
substring guess `hub/client_key.py` exists to refuse. A new business has no
client record to join to, so it is written through `hub/leads.py` on the way
past; otherwise the only trace of a prospect we just asked for Google access
is a row in this module that nothing else reads.

**A new business that is already a client is refused, not deduplicated.**
Capturing a lead for a live client puts a duplicate contact into Smart 1
Suite, which is the one thing the Leads panel cannot undo for you.

**The Hub client ID field is gone.** It was optional, typed by hand, and blank
on nearly every row — which is why the Client 360 access card answered "no
access on file" for clients whose Analytics we had been granted months
earlier. The join is derived from the name and the website instead, so nothing
has to be filled in for it to work.
"""
import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

_TMP = tempfile.mkdtemp(prefix="s1-googleaccess-")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(_TMP, "t.db")
os.environ["HUB_DATA_DIR"] = _TMP
os.environ["HUB_LEADS_FILE"] = os.path.join(_TMP, "leads.jsonl")
os.environ.setdefault("SECRET_KEY", "google-access-test")
os.environ.setdefault("PANEL_PASSWORD", "test")
os.environ.setdefault("PUBLIC_BASE_URL", "https://hub.example")
os.environ.setdefault("GOOGLE_ACCESS_AGENCY_EMAIL", "access@smart1.example")
# Deliberately unset the dedicated pair, so the fallback below is what runs.
os.environ.pop("GOOGLE_ACCESS_CLIENT_ID", None)
os.environ.pop("GOOGLE_ACCESS_CLIENT_SECRET", None)
os.environ["GOOGLE_CLIENT_ID"] = "hub-shared.apps.googleusercontent.com"
os.environ["GOOGLE_CLIENT_SECRET"] = "hub-shared-secret"

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
    print("-" * 60)


from modules.google_access import config, grants  # noqa: E402
from modules.google_access import google_client as gc  # noqa: E402


# ---------------------------------------------------------------------------
section("Google Ads is paused, and nothing offers it")
# ---------------------------------------------------------------------------
check("it is not a service any more", "ads" not in config.SERVICES)
check("...nor in the order the pages walk", "ads" not in config.SERVICE_ORDER)
check("no scope asks for adwords",
      "https://www.googleapis.com/auth/adwords"
      not in config.scopes_for(list(config.SERVICES)))
check("config carries no Ads credentials to leave half-set",
      not [n for n in dir(config) if n.startswith("ADS_")],
      [n for n in dir(config) if n.startswith("ADS_")])
check("and no ads_configured() for a page to report on",
      not hasattr(config, "ads_configured"))
check("grants has no Ads branch to run", not hasattr(grants, "refresh_ads_status"))
check("google_client sends no invitations",
      not [n for n in dir(gc) if n.startswith("ads_")],
      [n for n in dir(gc) if n.startswith("ads_")])

MOD = os.path.join(ROOT, "modules", "google_access")
for name in ("connect.html", "connect_done.html", "connect_error.html",
             "_connect_base.html"):
    body = open(os.path.join(MOD, "templates", name), encoding="utf-8").read()
    check(f"the client never reads about Ads in {name}",
          "Google Ads" not in body and "ads.google.com" not in body)

# The note is the point of a pause: staff must be able to find out why.
check("config says why it is parked and what brings it back",
      "PARKED: Google Ads" in open(os.path.join(MOD, "config.py"),
                                   encoding="utf-8").read())
check("and the admin page says so out loud",
      "Google Ads is paused" in open(os.path.join(MOD, "templates",
                                                  "admin_index.html"),
                                     encoding="utf-8").read())


# ---------------------------------------------------------------------------
section("A parked service is not a deleted one")
# ---------------------------------------------------------------------------
check("a stored 'ads' key still has a name", config.label_for("ads") == "Google Ads (paused)")
check("...distinguishable from a live one", config.label_for("ga4") == "Google Analytics")
check("an unknown key falls back to itself rather than blank",
      config.label_for("whatever") == "whatever")
check("scopes_for ignores a key it no longer knows",
      config.scopes_for(["ads"]) == [])


# ---------------------------------------------------------------------------
section("The OAuth client is named, not merely present")
# ---------------------------------------------------------------------------
check("nothing is reported missing", config.configured() == [], config.configured())
check("because the Hub's shared client filled in",
      config.GOOGLE_CLIENT_ID == "hub-shared.apps.googleusercontent.com")
check("and the page can say which one that was",
      config.oauth_client_source() == "GOOGLE_CLIENT_ID",
      config.oauth_client_source())
check("the redirect URI it must carry is spelled out",
      config.redirect_uri() == "https://hub.example/connect/callback",
      config.redirect_uri())


# ---------------------------------------------------------------------------
section("The composed app, and the invite form")
# ---------------------------------------------------------------------------
from werkzeug.test import Client as WClient                     # noqa: E402

import wsgi                                                     # noqa: E402
from hub import clients_registry, leads                         # noqa: E402
from modules.google_access import app as ga_app                 # noqa: E402
from modules.google_access.models import AccessRequest, db      # noqa: E402

# The registry is a fixture: exact-match behaviour is what is being asserted,
# and a live Knack pull would make the answer depend on the day.
FIXTURE = {
    "riverside hvac": {"name": "Riverside HVAC", "url": "https://riversidehvac.example",
                       "domain": "riversidehvac.example"},
}
clients_registry.find_client = lambda name: FIXTURE.get(str(name or "").strip().lower())

composed = WClient(wsgi.application)
composed.post("/login", data={"password": os.environ["PANEL_PASSWORD"], "name": "T"})

page = composed.get("/tools/google-access/")
check("the admin page answers", page.status_code == 200, page.status_code)
html = page.get_data(as_text=True)
check("there is no Hub client ID field left to leave blank",
      "hub_client_id" not in html)
check("it asks existing or new", 'name="client_type"' in html
      and 'value="existing"' in html and 'value="new"' in html)
check("existing gets the Client 360 lookup", "/api/clients/search" in html)
check("Google Ads is not offered as a tickbox", 'value="ads"' not in html)


def create(**payload):
    return composed.post("/tools/google-access/api/requests", data=payload)


section("Existing or new is asked, and neither is guessed")

res = create(client_name="Riverside HVAC", service="ga4")
check("a request with no answer is refused", res.status_code == 400, res.status_code)
check("...and says what it wants", "existing client or a new one"
      in res.get_json()["error"], res.get_json())

res = create(client_type="existing", client_name="Riverside HVAC Supply", service="ga4")
check("an existing client that is not in the registry is refused",
      res.status_code == 400 and not res.get_json()["ok"])
check("...naming New as the way out", "New" in res.get_json()["error"],
      res.get_json())

res = create(client_type="existing", client_name="Riverside HVAC",
             service="ga4", service_extra="")
body = res.get_json()
check("an exact registry name is accepted", res.status_code == 200 and body["ok"], body)
check("the link is the public one, not a /tools path",
      body["link"].startswith("https://hub.example/connect/"), body.get("link"))
check("no lead is created for a client we already have", body.get("lead") is None)

res = create(client_type="new", client_name="Riverside HVAC", service="ga4")
check("a 'new' business that is already a client is refused",
      res.status_code == 400 and "already a client" in res.get_json()["error"],
      res.get_json())

res = create(client_type="new", client_name="Northgate Dental",
             client_email="owner@northgatedental.example", service="ga4")
body = res.get_json()
check("a genuinely new business is accepted", res.status_code == 200 and body["ok"], body)
check("...and comes back with a lead id", bool((body.get("lead") or {}).get("lead_id")),
      body.get("lead"))

res = create(client_type="new", client_name="Cranbrook Roofing", service="gtm")
check("a new business with no email still creates the invite",
      res.get_json()["ok"] is True)
check("...and still stores the lead",
      bool(res.get_json()["lead"]["lead_id"]), res.get_json()["lead"])
check("...saying why it did not reach the Suite",
      "email" in res.get_json()["lead"]["note"].lower(), res.get_json()["lead"])

res = create(client_type="existing", client_name="Riverside HVAC")
check("a request with no service is refused", res.status_code == 400)
res = create(client_type="existing", client_name="Riverside HVAC", service="ads")
check("...and a parked service does not count as one", res.status_code == 400,
      res.get_json())


section("What the lead store actually received")

rows = [r for r in leads.listing(days=2)["leads"] if r["source"] == "google_access"]
names = sorted(r["company"] for r in rows)
check("both new businesses are in the lead store",
      names == ["Cranbrook Roofing", "Northgate Dental"], names)
check("and neither existing client is", "Riverside HVAC" not in names, names)
check("each carries the request it came from",
      all(r["meta"].get("google_access_request_id") for r in rows),
      [r["meta"] for r in rows])


section("Nothing writes the Hub client ID any more")

with wsgi.hub_app.app_context():
    stored = AccessRequest.query.order_by(AccessRequest.id).all()
    check("every request created here has one",
          len(stored) == 3, len(stored))
    check("and not one of them carries a hand-typed Hub client ID",
          all(r.hub_client_id is None for r in stored),
          [r.hub_client_id for r in stored])
    check("the existing client kept the registry's URL",
          stored[0].website == "https://riversidehvac.example", stored[0].website)
    check("the join is derived instead, off the domain",
          stored[0].client_key().startswith("d:"), stored[0].client_key())
    check("a new business with no URL still derives a key off the name",
          stored[2].client_key().startswith("n:"), stored[2].client_key())

    # A request written before Ads was parked. This is the row every page in
    # the module has to survive.
    from modules.google_access.models import new_token
    from modules.google_access.grants import expiry_from_now
    legacy = AccessRequest(token=new_token(), client_name="Legacy Co",
                           created_by="test", expires_at=expiry_from_now(),
                           status="pending")
    legacy.services = ["ga4", "ads"]
    db.session.add(legacy)
    db.session.commit()
    legacy_id, legacy_token = legacy.id, legacy.token


section("A request created before the pause still works")

listing = composed.get("/tools/google-access/")
check("the requests table renders it", listing.status_code == 200, listing.status_code)
check("...naming Ads as paused rather than as a raw key",
      "Google Ads (paused)" in listing.get_data(as_text=True))

detail = composed.get(f"/tools/google-access/r/{legacy_id}")
check("its own record opens", detail.status_code == 200, detail.status_code)
detail_html = detail.get_data(as_text=True)
check("...and the Ads row is still on it rather than quietly dropped",
      "Google Ads (paused)" in detail_html)
check("...with no Ads status button that would call a route we deleted",
      "refreshBtn" not in detail_html)

res = composed.post(f"/tools/google-access/api/requests/{legacy_id}/mark",
                    data={"service": "ads", "status": "skipped"})
check("a human can still close the parked row off",
      res.status_code == 200 and res.get_json()["ok"], res.get_json())

client_page = composed.get(f"/connect/{legacy_token}")
check("the client's own page still loads", client_page.status_code == 200,
      client_page.status_code)
check("...and does not offer them Ads",
      "Google Ads" not in client_page.get_data(as_text=True))

started = composed.post(f"/connect/{legacy_token}/start", data={"service": "ads"})
check("ticking a parked service cannot 500 the consent step",
      started.status_code in (200, 302), started.status_code)


# ---------------------------------------------------------------------------
print("\n" + "-" * 60)
print(f"{PASS} passed, {FAIL} failed")
shutil.rmtree(_TMP, ignore_errors=True)
sys.exit(1 if FAIL else 0)
