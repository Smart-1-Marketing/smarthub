"""360 Skills: the store, the Ecwid hotsheet maths, the Suite email path,
the three blueprints and the Client 360 gate.

    python3 test_skills360.py

Same shape as the other test files here -- no pytest, no new dependencies,
a temporary data directory and a throwaway SQLite database, so it never
touches /var/data or the real one. Every network call is monkeypatched at
the module attribute and restored in a finally; nothing here reaches Ecwid
or HighLevel.

What it holds, and from which direction each fails:

  1. **A skill is on only after it verified.** activate() with credentials
     Ecwid refuses must leave the skill off -- the card on Client 360 is a
     promise the tool must not make.
  2. **Secrets never reach a page.** get() masks the token; the raw value
     is reachable only through secret(), and the Fernet path round-trips.
  3. **The hotsheet maths** on a fixed set of orders: this week, last month,
     the twelve-month series, discounts, top products, abandoned carts.
  4. **The gate.** /api/c360 decorates each group with `skills`; render()
     draws the Ecommerce and Email Creator cards only inside that gate; the
     lifted renderEcwid() keeps its three empties apart in node.
  5. **The public link** answers with no login, dies with the skill, and
     404s (not a login form) for a token nobody made.
  6. **Every route answers JSON**, including on a store that will not write.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="skills360_test_")
os.environ["HUB_DATA_DIR"] = os.path.join(TMP, "data")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "t.db")
os.environ["SECRET_KEY"] = "skills360-test-secret"
os.environ["PANEL_PASSWORD"] = "skills360-test-password"
os.environ.pop("TOKEN_ENCRYPTION_KEY", None)
os.environ.pop("GHL_PRIVATE_TOKEN", None)

_passed, _failed = 0, 0


def check(label, got, want=True):
    global _passed, _failed
    if got == want:
        _passed += 1
        print(f"  ok    {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}\n          got  {got!r}\n          want {want!r}")


def section(title):
    print(f"\n{title}\n{'-' * len(title)}")


from modules.skills360 import app as sk_app            # noqa: E402
from modules.skills360 import ecwid, registry, store, suite_email  # noqa: E402

CLIENT = "Buckeye Lake Winery"

# ------------------------------------------------------------------------
section("1. The store: configure, verify-then-activate, mask, share")

rec = store.set_skill(CLIENT, "ecwid", {"store_id": "111281497", "token": "secret_abcdefghijklmnop"},
                      by="Todd", secret_fields=("token",))
check("saving credentials does not switch the skill on", rec.get("active"), False)
check("the token is masked on the way out", rec.get("token_masked"), "secr…mnop")
check("and never present in the clear", "secret_abcdefghijklmnop" in json.dumps(rec), False)
check("secret() is the only way to the raw value", store.secret(CLIENT, "ecwid", "token"), "secret_abcdefghijklmnop")
check("a blank secret on a later save keeps the stored one",
      (store.set_skill(CLIENT, "ecwid", {"store_id": "111281497", "token": ""}, secret_fields=("token",)),
       store.secret(CLIENT, "ecwid", "token"))[1], "secret_abcdefghijklmnop")
check("active_keys() is empty before activation", store.active_keys(CLIENT), [])
check("a share link cannot be made for an inactive skill (route refuses)",
      registry.BY_KEY["ecwid"]["shareable"], True)

store.activate(CLIENT, "ecwid", by="Todd", verified={"store_name": "BLW Store", "order_count": 3})
check("activate() switches it on", store.active_keys(CLIENT), ["ecwid"])
check("and records what verification found", store.skill(CLIENT, "ecwid").get("store_name"), "BLW Store")
sh = store.add_share(CLIENT, "ecwid", by="Todd", label="Owner")
check("a share is an unguessable token", bool(sh.get("ok")) and len(sh["token"]) >= 30, True)
found = store.resolve_share(sh["token"])
check("resolve_share() finds it by exact token", (found or {}).get("client"), CLIENT)
check("a prefix of the token resolves nothing", store.resolve_share(sh["token"][:20]), None)
store.deactivate(CLIENT, "ecwid", by="Todd")
check("switching off retires the link", store.resolve_share(sh["token"]), None)
check("but keeps the credentials for next time", store.secret(CLIENT, "ecwid", "token"), "secret_abcdefghijklmnop")
store.activate(CLIENT, "ecwid", by="Todd")
store.deactivate(CLIENT, "ecwid", by="Todd", forget=True)
check("switch off & forget deletes them", store.secret(CLIENT, "ecwid", "token"), "")
check("and the shares", store.get(CLIENT)["shares"], [])

# Fernet round trip when a key is present
try:
    from cryptography.fernet import Fernet
    os.environ["TOKEN_ENCRYPTION_KEY"] = Fernet.generate_key().decode()
    sealed = store.seal("secret_zzz")
    check("with TOKEN_ENCRYPTION_KEY the secret is sealed", sealed.get("enc"), True)
    check("and unseals to the same value", store.unseal(sealed), "secret_zzz")
    os.environ.pop("TOKEN_ENCRYPTION_KEY", None)
    check("a rotated-away key reads as empty, never as garbage", store.unseal(sealed), "")
except ImportError:
    print("  skip  cryptography not installed")

# ------------------------------------------------------------------------
section("2. Ecwid: verify() gates on the right token shape, dashboard() maths")

check("a public token is refused before any network", ecwid.verify("1", "public_abc").get("ok"), False)
check("a non-numeric store id is refused", ecwid.verify("abc", "secret_abc").get("ok"), False)

NOW = datetime(2026, 9, 12, 15, 0, tzinfo=timezone.utc)      # a Saturday; week starts Sun Sep 6


def o(day, total, items=(), disc=0.0, status="SHIPPED"):
    return {"orderNumber": 100 + int(day[-2:]), "createDate": f"2026-{day} 10:00:00 +0000", "total": total,
            "couponDiscount": disc, "fulfillmentStatus": status,
            "items": [{"productId": pid, "name": nm, "price": pr, "quantity": q} for pid, nm, pr, q in items]}


ORDERS = [
    o("09-10", 120.0, [(1, "Cab", 30.0, 4)]),                          # this week
    o("09-07", 80.0, [(2, "Riesling", 20.0, 4)], disc=8.0),             # this week, discounted
    o("09-02", 50.0, [(1, "Cab", 25.0, 2)]),                            # this month, last week
    o("08-20", 300.0, [(3, "Case", 300.0, 1)], status="AWAITING_PROCESSING"),  # last month
    o("01-15", 45.0, [(2, "Riesling", 45.0, 1)]),                       # this year
]
PRODUCTS = [{"id": 1, "name": "Cabernet"}, {"id": 2, "name": "Riesling"}, {"id": 3, "name": "Case of 12"}]
CARTS = [{"createDate": "2026-09-11 09:00:00 +0000", "cart": {"total": 60.0}},
         {"createDate": "2026-07-01 09:00:00 +0000", "cart": {"total": 10.0}}]

d = ecwid.dashboard("999", "secret_x", now=NOW, orders=ORDERS, products=PRODUCTS, carts=CARTS)
check("dashboard() answers ok on fixtures", d.get("ok"), True)
P = d["periods"]
check("this week counts Sunday onward", P["week"]["orders"], 2)
check("this week revenue", P["week"]["revenue"], 200.0)
check("last week is the seven days before", P["last_week"]["orders"], 1)
check("this month", P["month"]["orders"], 3)
check("last month", P["last_month"]["revenue"], 300.0)
check("this year", P["year"]["orders"], 5)
check("discount rate this week", P["week"]["discounts"], {"orders_with": 1, "amount": 8.0, "rate": 50.0})
check("order status buckets", P["last_month"]["status"]["pending"], 1)
check("product names come from the catalog, not the line item", d["top_products"]["week"][0]["name"], "Cabernet")
check("top products are by revenue", [r["name"] for r in d["top_products"]["year"]],
      ["Case of 12", "Cabernet", "Riesling"])
check("twelve months, oldest first", len(d["monthly"]) == 12 and d["monthly"][-1]["month"] == "Sep 26", True)
check("this month's bar", d["monthly"][-1]["revenue"], 250.0)
check("abandoned carts this week", d["abandoned"]["week"], {"carts": 1, "value": 60.0})
check("latest orders lead with the newest", d["recent"][0]["number"], 110)
W = d["windows"]
check("three rolling windows are computed at once", sorted(W.keys(), key=int), ["30", "90", "365"])
check("last 30 days counts the four September orders", (W["30"]["orders"], W["30"]["revenue"]), (4, 550.0))
check("against the 30 days before them", W["30"]["prev_orders"], 0)
check("a window with no prior period says so rather than dividing by zero", W["30"]["change_pct"], None)
check("last 365 days is everything", W["365"]["orders"], 5)
check("window top products are named from the catalog", W["30"]["top_products"][0]["name"], "Case of 12")
check("the fixtures are not mutated with a private key", "_dt" in ORDERS[0], False)

calls = []


def _fake_get(store_id, token, path, params=None):
    calls.append(path)
    if path == "/profile":
        return {"generalInfo": {"storeUrl": "https://blw.example"}, "settings": {"storeName": "BLW"}}
    if path == "/orders":
        return {"total": 42, "items": []}
    raise ecwid.EcwidError("unexpected")


_orig_get = ecwid._get
ecwid._get = _fake_get
try:
    v = ecwid.verify("111281497", "secret_good")
    check("verify() reads the profile and the orders scope", (v.get("ok"), v.get("store_name"), v.get("order_count")),
          (True, "BLW", 42))
    check("both endpoints were asked", calls, ["/profile", "/orders"])
finally:
    ecwid._get = _orig_get


def _refuse(store_id, token, path, params=None):
    raise ecwid.EcwidError("Ecwid refused the token.")


ecwid._get = _refuse
try:
    check("a refused token verifies false with a readable reason",
          ecwid.verify("111281497", "secret_bad"), {"ok": False, "error": "Ecwid refused the token."})
finally:
    ecwid._get = _orig_get

# ------------------------------------------------------------------------
section("3. Suite email: domain check states, readiness tri-state, render")

_orig_txt = suite_email._txt
suite_email._txt = lambda name: None
try:
    check("DNS that will not answer is not measured, not missing",
          suite_email.domain_check("hi@blw.com")["state"], "not_measured")
finally:
    suite_email._txt = _orig_txt
suite_email._txt = lambda name: ["v=spf1 include:mailgun.org ~all"] if name == "blw.com" else []
try:
    r = suite_email.domain_check("hi@blw.com")
    check("SPF naming mailgun reads as verified", (r["state"], r["spf"]), ("verified", True))
finally:
    suite_email._txt = _orig_txt
suite_email._txt = lambda name: ["v=spf1 include:_spf.google.com ~all"] if name == "blw.com" else []
try:
    r = suite_email.domain_check("hi@blw.com")
    check("SPF without the carrier and no DKIM is missing", r["state"], "missing")
    check("and says which records to add", [x["type"] for x in r["records_needed"]], ["TXT", "TXT", "CNAME"])
finally:
    suite_email._txt = _orig_txt
suite_email._txt = lambda name: ["v=DKIM1; k=rsa; p=MIIB"] if name == "krs._domainkey.blw.com" else []
try:
    check("a DKIM key at an LC selector is enough", suite_email.domain_check("hi@blw.com")["dkim"], "krs")
finally:
    suite_email._txt = _orig_txt
check("no address is its own state", suite_email.domain_check("")["state"], "no_address")

_orig_account = suite_email.account
suite_email.account = lambda client, url="": {"state": "not_connected", "detail": "No Suite link.", "location_id": "", "token": None}
try:
    r = suite_email.readiness(CLIENT)
    check("no sub-account: not ready, and the reason is the account's", (r["ready"], r["detail"]), (False, "No Suite link."))
finally:
    suite_email.account = _orig_account

posted = []


def _fake_call(token, method, path, *, scope_hint="", **kw):
    posted.append((method, path, kw.get("json") or kw.get("params")))
    if path.startswith("/locations/"):
        return {"location": {"id": "LOC1", "name": "BLW", "email": "hello@blw.com", "website": "https://blw.com"}}
    if path == "/emails/builder" and method == "GET":
        return {"total": 2, "data": [{}, {}]}
    if path == "/emails/builder" and method == "POST":
        return {"redirect": "TPL9"}
    if path == "/emails/builder/data":
        return {"ok": "true", "previewUrl": "https://preview"}
    if path == "/contacts/upsert":
        return {"contact": {"id": "C1"}}
    if path == "/conversations/messages":
        if kw["json"]["contactId"] == "BAD":
            raise suite_email.SuiteEmailError("Smart 1 Suite could not accept that (no email).")
        return {"messageId": "M1", "emailMessageId": "E1", "conversationId": "CV1"}
    if path == "/contacts/":
        return {"contacts": [{"id": "C2", "email": "a@b.com", "firstName": "Ann", "tags": ["vip"]},
                             {"id": "C3", "firstName": "No Email"}], "total": 2}
    return {}


_orig_call = suite_email._call
suite_email.account = lambda client, url="": {"state": "connected", "detail": "", "location_id": "LOC1", "token": "tok"}
suite_email._call = _fake_call
suite_email._txt = lambda name: ["v=spf1 include:mailgun.org ~all"] if name == "blw.com" else []
try:
    r = suite_email.readiness(CLIENT)
    check("with an account, a from-address and a builder that answers: ready", r["ready"], True)
    check("the from-address came from the Suite account", (r["from"]["email"], r["from"]["source"]), ("hello@blw.com", "suite"))
    check("the domain was measured on that address", r["domain"]["state"], "verified")
    check("no token value is anywhere in the answer", "tok" in json.dumps(r).replace("token", ""), False)
    r2 = suite_email.readiness(CLIENT, from_email="news@blw.com")
    check("the skill's own from-address wins", r2["from"]["source"], "skill")

    t = suite_email.push_template(CLIENT, title="Fall release", html="<p>hi</p>")
    check("push_template() creates then fills", (t["ok"], t["template_id"]), (True, "TPL9"))
    check("the fill carries the html as an html editor template",
          posted[-1][2]["editorType"] == "html" and posted[-1][2]["templateId"] == "TPL9", True)

    check("a test to a non-address is refused", suite_email.send_test(CLIENT, to="nope", subject="s", html="h", from_email="hello@blw.com")["ok"], False)
    t = suite_email.send_test(CLIENT, to="todd@smart1marketing.com", subject="Hello", html="<p>x</p>",
                              from_email="hello@blw.com", from_name="BLW")
    check("a test upserts a tagged contact and sends to it", (t["ok"], t["message_id"]), (True, "M1"))
    check("the test is marked as one in the subject", posted[-1][2]["subject"], "[TEST] Hello")
    check("and the from carries the name", posted[-1][2]["emailFrom"], "BLW <hello@blw.com>")
    check("the contact carried the hub-test tag", posted[-2][2].get("tags"), ["hub-test"])

    c = suite_email.search_contacts(CLIENT, q="a")
    check("contact search drops rows with no email", [x["id"] for x in c["contacts"]], ["C2"])

    b = suite_email.send_batch(CLIENT, contact_ids=["C2", "BAD", "C4"], subject="S", html="<p>", from_email="hello@blw.com")
    check("a batch sends one at a time and reports each", (b["sent"], b["failed"]), (2, 1))
    check("a bad recipient does not stop the rest", [x["ok"] for x in b["results"]], [True, False, True])
finally:
    suite_email.account = _orig_account
    suite_email._call = _orig_call
    suite_email._txt = _orig_txt

html = suite_email.render_html(subject="Fall", headline="New wines", body="Para one\n\nPara two", cta_text="Shop",
                               cta_url="https://blw.com/shop", brand={"colors": {"primary": "#7a1f3d"}, "logo_url": "https://l/logo.png"},
                               business="BLW", preview="Preview me")
check("render_html() is a single table email", html.count("<table") == 2 and "</html>" in html, True)
check("the brand color and logo are in", "#7a1f3d" in html and "https://l/logo.png" in html, True)
check("paragraphs are split on blank lines", html.count("<p "), 2)
check("the CTA is a bulletproof link", 'href="https://blw.com/shop"' in html, True)
check("body text is escaped", "<script" not in suite_email.render_html(subject="x", body="<script>alert(1)</script>"), True)
check("the unsubscribe merge field survives", "{{unsubscribe_link}}" in html, True)

# ------------------------------------------------------------------------
section("4. The blueprints: guard, JSON on every path, activation gate")

from flask import Flask  # noqa: E402
import hub.auth as _auth  # noqa: E402

check("the staff blueprint is guarded", len(sk_app.bp.before_request_funcs.get(None, [])) >= 1, True)
check("the Client 360 routes are guarded", len(sk_app.bp_client.before_request_funcs.get(None, [])) >= 1, True)
check("the public link is not", len(sk_app.bp_hot.before_request_funcs.get(None, [])), 0)

app = Flask(__name__, template_folder=str(ROOT / "hub" / "templates"))
app.register_blueprint(sk_app.bp, url_prefix=sk_app.MOUNT)
app.register_blueprint(sk_app.bp_client)
app.register_blueprint(sk_app.bp_hot, url_prefix=sk_app.HOT_MOUNT)
anon = app.test_client()
resp = anon.post(sk_app.MOUNT + "/api/ecwid/activate", json={"client": CLIENT})
check("anonymous activation is refused with JSON", (resp.status_code, resp.is_json), (401, True))
resp = anon.get("/api/client/skills/ecwid/dashboard?name=" + CLIENT)
check("anonymous Client 360 read is refused", resp.status_code, 401)

staff = app.test_client()
staff.set_cookie(_auth.COOKIE_NAME, _auth.issue_cookie_value("Todd"), domain="localhost")

resp = staff.get(sk_app.MOUNT + "/")
check("the tool page renders", resp.status_code, 200)
check("and lists every skill in the registry",
      all(s["label"].encode() in resp.data for s in registry.SKILLS), True)
resp = staff.get(sk_app.MOUNT + "/api/status?client=" + CLIENT)
check("status answers", resp.get_json().get("ok"), True)
resp = staff.post(sk_app.MOUNT + "/api/nope/save", json={"client": CLIENT})
check("an unknown skill is a JSON 404", (resp.status_code, resp.is_json), (404, True))

resp = staff.post(sk_app.MOUNT + "/api/ecwid/save", json={"client": CLIENT, "store_id": "111281497", "token": "secret_live"})
check("save stores without activating", (resp.get_json()["ok"], resp.get_json()["skill"]["active"]), (True, False))
check("the response carries the mask, not the token", "secret_live" in resp.get_data(as_text=True), False)

ecwid._get = _refuse
try:
    resp = staff.post(sk_app.MOUNT + "/api/ecwid/activate", json={"client": CLIENT})
    body = resp.get_json()
    check("activation with refused credentials answers ok:false", body.get("ok"), False)
    check("and leaves the skill OFF", store.active_keys(CLIENT), [])
finally:
    ecwid._get = _orig_get
ecwid._get = _fake_get
try:
    resp = staff.post(sk_app.MOUNT + "/api/ecwid/activate", json={"client": CLIENT})
    body = resp.get_json()
    check("activation with working credentials switches it on", (body.get("ok"), store.active_keys(CLIENT)), (True, ["ecwid"]))
    check("what verification found is on the skill", body["skill"].get("store_name"), "BLW")
finally:
    ecwid._get = _orig_get

check("skills_for() is what /api/c360 reads", sk_app.skills_for(CLIENT), ["ecwid"])
check("and never raises for a client nobody set up", sk_app.skills_for("Nobody Inc"), [])

resp = staff.post(sk_app.MOUNT + "/api/share", json={"client": CLIENT, "skill": "email"})
check("a link for an inactive skill is refused", resp.get_json().get("ok"), False)
resp = staff.post(sk_app.MOUNT + "/api/share", json={"client": CLIENT, "skill": "ecwid", "label": "Owner"})
link = resp.get_json()
check("a link for the active skill is made", link.get("ok") and link["url"].endswith("/hot/" + link["token"]), True)

_orig_dash = ecwid.dashboard
ecwid.dashboard = lambda sid, tok, **kw: {"ok": True, "periods": {}, "monthly": [], "order_count": 1}
try:
    resp = anon.get("/hot/" + link["token"])
    check("the client's page opens with no login", resp.status_code, 200)
    check("it is noindex", b"noindex" in resp.data, True)
    check("and carries the client's name, not the Hub nav", (CLIENT.encode() in resp.data, b"hub_sidebar" in resp.data), (True, False))
    resp = anon.get("/hot/" + link["token"] + "/data")
    body = resp.get_json()
    check("its data route answers", body.get("ok"), True)
    check("without the store id", "store" in body, False)
    resp = anon.get("/hot/not-a-token")
    check("a token nobody made is a 404, not a login form", (resp.status_code, b"no longer active" in resp.data), (404, True))
    resp = staff.get("/api/client/skills/ecwid/dashboard?name=" + CLIENT)
    check("Client 360's read answers with the store on it", resp.get_json().get("store", {}).get("store_name"), "BLW")
    resp = staff.get("/api/client/skills/ecwid/dashboard?name=Nobody Inc")
    check("and says 'off' for a client with the skill off", resp.get_json().get("state"), "off")
finally:
    ecwid.dashboard = _orig_dash

# the Ecwid webhook: minted at activation, forgets the cache, trusts nothing
st = staff.get(sk_app.MOUNT + "/api/status?client=" + CLIENT).get_json()
hook = st["record"]["skills"]["ecwid"].get("hook_token")
check("activation minted a webhook token", bool(hook) and len(hook) >= 30, True)
check("and the tool shows its address", st["record"]["skills"]["ecwid"].get("hook_url", "").endswith("/hot/ecwid-hook/" + hook), True)
forgotten = []
_orig_forget = ecwid.forget
ecwid.forget = lambda sid: forgotten.append(sid)
try:
    resp = anon.post("/hot/ecwid-hook/" + hook, json={"eventType": "order.created", "storeId": "111281497"})
    check("Ecwid can call it with no login", (resp.status_code, resp.get_json().get("ok")), (200, True))
    check("and the store's cache is forgotten", forgotten, ["111281497"])
    resp = anon.post("/hot/ecwid-hook/" + hook, json={"eventType": "order.created", "storeId": "999"})
    check("a body naming another store is ignored, quietly", resp.get_json().get("ignored"), True)
    check("...and forgets nothing", forgotten, ["111281497"])
    check("a token nobody minted is a 404", anon.post("/hot/ecwid-hook/nope", json={}).status_code, 404)
    check("the same token is kept across re-activation",
          (store.deactivate(CLIENT, "ecwid"), store.activate(CLIENT, "ecwid"), store.skill(CLIENT, "ecwid").get("hook_token"))[2], hook)
finally:
    ecwid.forget = _orig_forget

# the send log
store.log_send(CLIENT, "test", by="Todd", subject="Hello", count=1, to="todd@smart1marketing.com")
store.log_send(CLIENT, "batch", by="Todd", subject="Fall release", count=12, detail="1 failed")
sends = store.skill(CLIENT, "email").get("sends") or []
check("sends are logged newest first", [x["kind"] for x in sends], ["batch", "test"])
check("with the count and subject a rep needs", (sends[0]["count"], sends[0]["subject"]), (12, "Fall release"))

resp = staff.post(sk_app.MOUNT + "/api/share/revoke", json={"client": CLIENT, "token": link["token"]})
check("revoke works", resp.get_json().get("ok"), True)
check("and the link is dead", anon.get("/hot/" + link["token"]).status_code, 404)

resp = staff.get("/api/client/skills?name=" + CLIENT)
check("/api/client/skills lists active keys and the catalog",
      (resp.get_json()["active"], [c["key"] for c in resp.get_json()["catalog"]]), (["ecwid"], ["ecwid", "email"]))
resp = staff.post("/api/client/skills/email/test", json={"client": CLIENT, "to": "x@y.com"})
check("an email send with the skill off is refused, as JSON", (resp.is_json, resp.get_json().get("ok")), (True, False))
resp = staff.post("/api/client/skills/email/preview", json={"client": CLIENT, "subject": "S", "body": "b"})
check("preview needs no skill and returns html", "<html" in resp.get_json().get("html", ""), True)

# a store that will not write answers JSON, not a crash
_orig_update = store.jsonstore.update_json


def _boom(*a, **k):
    raise OSError(28, "No space left on device")


store.jsonstore.update_json = _boom
try:
    resp = staff.post(sk_app.MOUNT + "/api/ecwid/save", json={"client": CLIENT, "store_id": "1"})
    check("a disk that will not write answers JSON 500", (resp.status_code, resp.is_json), (500, True))
finally:
    store.jsonstore.update_json = _orig_update

# ------------------------------------------------------------------------
section("5. Client 360: the gate in render(), the section, the lifted renderer")

def _c360_source():
    """The Client 360 record as one text: the template plus its script modules
    (hub/client360_assets.MODULES), because the record's JavaScript lives in
    files now and a check that asks what the record does reads all of it."""
    import importlib, os as _os, sys as _sys
    _root = _os.path.dirname(_os.path.abspath(__file__))
    if _root not in _sys.path:
        _sys.path.insert(0, _root)
    return importlib.import_module("hub.client360_assets").source_text()

REC = _c360_source()
HUB = (ROOT / "hub" / "__init__.py").read_text(encoding="utf-8")
check("/api/c360 decorates each group with its skills", 'g["skills"] = skills_for(' in HUB, True)
check("the module is registered on the hub app", "register_skills360(app)" in HUB, True)
check("/hot/ is chromeless", '"/hot/",' in HUB, True)
check("the Ecommerce card is drawn inside the gate",
      REC.find("skillsOn.indexOf('ecwid')>-1") < REC.find('id="c-ecwid"') and "skillsOn.indexOf('ecwid')>-1" in REC, True)
check("the Email Creator card is drawn inside the gate",
      REC.find("skillsOn.indexOf('email')>-1") < REC.find('id="c-email"'), True)
check("the Skills section exists", "{key:'skills'" in REC, True)
check("both loaders honor the generation guard",
      REC.count("if(gen!==c360Generation) return;") >= 4, True)
check("the composer confirms before a batch send", "confirmed:true" in REC and "em-confirm" in REC, True)
check("the hero image can come from the client's own images", "em-pick" in REC and "/tools/seo-images/api/gallery?company=" in REC, True)
check("the Email Creator card shows recent sends", "Recent sends" in REC, True)
check("the Ecommerce card offers the rolling windows", "data-ec-days" in REC, True)
check("no card writes to the clipboard behind copyToClipboard's back",
      len(re.findall(r"navigator\.clipboard\.writeText\(", REC)), 1)

a = REC.find("/* ---- ecommerce (lifted")
b = REC.find("/* ---- end ecommerce ----")
SRC = REC[a:b] if 0 < a < b else ""
check("the ecommerce renderer is marked for lifting", bool(SRC))
if shutil.which("node") and SRC:
    driver = ("const esc=s=>String(s??'');window={CURRENT_CLIENT:'X'};"
              "const skMoney=n=>'$'+Number(n||0);const skMoney2=skMoney;const skDelta=()=>'';\n" + SRC
              + "\nconst out={off:renderEcwid({ok:false,state:'off'}),bad:renderEcwid({ok:false,state:'unreadable',error:'HTTP 500'}),"
                "ok:renderEcwid({ok:true,periods:{week:{revenue:5,orders:1},month:{},last_week:{},last_month:{},year:{}},monthly:[{month:'Sep 26',revenue:5,orders:1}],top_products:{},store:{store_name:'BLW'},order_count:1})};"
                "console.log(JSON.stringify(out));\n")
    r = subprocess.run(["node", "-"], input=driver, capture_output=True, text=True)
    check("the lifted renderer runs on its own", r.returncode, 0)
    out = json.loads(r.stdout or "{}") if r.returncode == 0 else {}
    check("skill off says so", "not switched on" in out.get("off", ""), True)
    check("unreadable says so, with the reason", "could not be read (HTTP 500)" in out.get("bad", ""), True)
    check("a good answer draws tiles and the store name", "This week" in out.get("ok", "") and "BLW" in out.get("ok", ""), True)
else:
    print("  skip  node not available")

# ------------------------------------------------------------------------
section("6. Registries the module must be in")

TOOLS = (ROOT / "hub" / "templates" / "tools.html").read_text(encoding="utf-8")
check("the tool is tiled exactly once", TOOLS.count('href="/tools/360-skills/"'), 1)
SCOPES = (ROOT / "hub" / "ghl_scopes.py").read_text(encoding="utf-8")
for s in ("emails/builder.readonly", "emails/builder.write", "conversations/message.write"):
    check(f"scope {s} is requested and names the email module",
          SCOPES.find(f'Scope("{s}"') > 0 and "modules/skills360/suite_email.py" in SCOPES[SCOPES.find(f'Scope("{s}"'):SCOPES.find(f'Scope("{s}"') + 400], True)
CI = (ROOT / ".github" / "workflows" / "checks.yml").read_text(encoding="utf-8")
check("this file runs in CI", "python3 test_skills360.py" in CI, True)

shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
