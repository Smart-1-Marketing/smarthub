"""The client's own website login, sealed — hub/cms_credentials.py + hub/seo.py.

    python3 test_site_login.py

`setup.password` on `data/seo/<client>.json` is the client's real login to
their real website. It was written there as a plain string, and because that
store goes through `hub/jsonstore.py` it was mirrored verbatim into Postgres
and into every database backup taken since: the whole book of them, readable by
anybody holding one.

Same shape as `test_wordpress_publish.py`: no pytest, no new dependencies, a
throwaway data directory and SQLite database, so nothing here touches
/var/data or the real store.

## What is worth asserting

  * **The bytes on the disk.** "It is encrypted now" is a claim about a file,
    so the file is read back and searched for the password itself — in the SEO
    record, in the credential record, and in the JSON of every route answer.
    Asserting on the API alone would pass just as well if the plaintext were
    still sitting beside the sealed copy.

  * **It moves exactly once.** The migration shape `hub/ad_assets.py` records
    paying for is the one that rewrites its source on every read. So the
    second call must write nothing, and the assertion is on the file's mtime
    and contents rather than on the return value alone.

  * **It refuses to "migrate" where it cannot seal.** With no
    TOKEN_ENCRYPTION_KEY, `_seal()` stores the raw value by design so that a
    save can still say so — and moving a plaintext password from one
    jsonstore file to an identical one would spend the client's one copy on no
    improvement at all. The record has to stay exactly where it is.

  * **A rotated key reads as "cannot be read", never as "no login".** The
    third state is the one that cost Google Finder months, and reporting it as
    absent is what sends somebody to ask a client for their password again
    when the one on file was fine.

  * **A blank password keeps the stored one.** The setup form sends the field
    only when somebody typed in it, and eleven other answers on that form are
    saved by people who never touch it.

  * **Nothing returns it.** Not `site_login_state()`, not `client_detail()`,
    not the setup route's own answer. Asserted over every value each returns,
    rather than over the one key somebody remembered to strip.
"""
import json
import os
import shutil
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

_TMP = tempfile.mkdtemp(prefix="s1-sitelogin-")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(_TMP, "t.db")
os.environ.setdefault("SECRET_KEY", "site-login-test")
os.environ.setdefault("PANEL_PASSWORD", "test")
os.environ["HUB_DATA_DIR"] = _TMP
os.environ.pop("OPENAI_API_KEY", None)

from cryptography.fernet import Fernet                      # noqa: E402

KEY_A = Fernet.generate_key().decode("ascii")
KEY_B = Fernet.generate_key().decode("ascii")
os.environ["TOKEN_ENCRYPTION_KEY"] = KEY_A

PASS = FAIL = 0


def check(label, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print("  ok   " + label)
    else:
        FAIL += 1
        print("  FAIL " + label + (("  — " + str(detail)) if detail else ""))


from hub import cms_credentials, jsonstore, seo             # noqa: E402

SECRET = "Sup3r-Secret-Client-Pw!"
OTHER = "A-Completely-Different-One"


def seo_path(client):
    return os.path.join(jsonstore.data_dir("seo"), seo.slugify(client) + ".json")


def cred_path(client):
    return cms_credentials._path(client, cms_credentials.SITE_LOGIN)


def read_raw(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return ""


def plant_legacy(client, password=SECRET, login="admin@example.com"):
    """An SEO record exactly as it was written before this change."""
    seo.save_store(client, {
        "client": client,
        "setup": {"access_method": "WordPress admin",
                  "access_url": "https://example.com/wp-login.php",
                  "login": login, "password": password,
                  "blogs_enabled": "yes", "completed": True},
        "business_info": {}, "questions": [], "answers": {},
        "pages": {}, "sitemap": []})


def nowhere_in(blob, needle=SECRET):
    """The secret does not appear anywhere in a structure, at any depth.

    Built as a walk rather than a key check on purpose: a subset that happens
    not to name `password` today is one the next field added to it prints.
    """
    return needle not in json.dumps(blob, default=str)


# ------------------------------------------------- the migration, once only
print("moving a plaintext login out of the SEO record")
CLIENT = "Bayside Dental"
plant_legacy(CLIENT)
check("the fixture really is the old shape — the password is in the SEO file "
      "on the disk, in the clear",
      SECRET in read_raw(seo_path(CLIENT)))

store = seo.load_store(CLIENT)
moved = seo.seal_site_login(CLIENT, store)
check("the move reports that it wrote", moved is True)
check("the SEO file on the disk no longer contains the password",
      SECRET not in read_raw(seo_path(CLIENT)))
check("and the SEO record has no password key at all, not an emptied one",
      "password" not in (seo.load_store(CLIENT).get("setup") or {}))
check("every other setup answer survived the move",
      (seo.load_store(CLIENT)["setup"].get("access_method") == "WordPress admin"
       and seo.load_store(CLIENT)["setup"].get("login") == "admin@example.com"
       and seo.load_store(CLIENT)["setup"].get("completed") is True))

raw_cred = read_raw(cred_path(CLIENT))
check("a credential record was written for this client", bool(raw_cred))
check("and the password is NOT in it in the clear — the whole point",
      SECRET not in raw_cred)
check("what is in it is a Fernet token", '"enc": true' in raw_cred.lower())
check("the login name came across, so nobody has to retype it",
      json.loads(raw_cred).get("login") == "admin@example.com")
check("and the record says it was migrated rather than typed by a person",
      "migrated" in (json.loads(raw_cred).get("saved_by") or ""))

before = read_raw(cred_path(CLIENT))
mtime_before = os.path.getmtime(cred_path(CLIENT))
time.sleep(1.1)
again = seo.seal_site_login(CLIENT, seo.load_store(CLIENT))
check("a second pass writes nothing — the migration shape hub/ad_assets.py "
      "records paying for is the one that rewrites its source every read",
      again is False)
check("the credential file was not touched on the second pass",
      os.path.getmtime(cred_path(CLIENT)) == mtime_before
      and read_raw(cred_path(CLIENT)) == before)


# ------------------------------------------- what a screen is allowed to know
print("\nwhat a screen may know")
st = cms_credentials.site_login_state(CLIENT)
check("it says there is a login on file", st.get("has_password") is True)
check("it says the login is sealed", st.get("sealed") is True)
check("it says the login is readable", st.get("readable") is True)
check("and it carries no error", not st.get("error"))
check("the password is nowhere in the state, at any depth", nowhere_in(st))
check("the login NAME is offered, because that is not the secret",
      st.get("login") == "admin@example.com")

detail = seo.client_detail(CLIENT)
check("client_detail() reports the login state", bool(detail.get("site_login")))
check("client_detail() still answers setup_has_password, so the panel that "
      "reads it keeps working",
      detail.get("setup_has_password") is True)
check("and the password appears nowhere in the whole client detail",
      nowhere_in(detail))


# --------------------------------------------------- the three states apart
print("\nthe three states, kept apart")
os.environ["TOKEN_ENCRYPTION_KEY"] = KEY_B
rotated = cms_credentials.site_login_state(CLIENT)
check("with the key rotated it still says a login is on file — reporting it "
      "as absent is what sends somebody to ask a client for it again",
      rotated.get("has_password") is True)
check("it says the login cannot be read", rotated.get("readable") is False)
check("and names the rotated key as the reason",
      "rotated" in (rotated.get("error") or "").lower())
check("still nothing resembling a password in that answer", nowhere_in(rotated))

os.environ.pop("TOKEN_ENCRYPTION_KEY", None)
noKey = cms_credentials.site_login_state(CLIENT)
check("with no key at all it still says a login is on file",
      noKey.get("has_password") is True)
check("and names TOKEN_ENCRYPTION_KEY, which is the fix",
      "TOKEN_ENCRYPTION_KEY" in (noKey.get("error") or ""))
check("encryption_state() says this deployment cannot seal",
      cms_credentials.encryption_state().get("configured") is False)

# A deployment with no key must not "migrate" — moving plaintext from one
# jsonstore file to an identical one is not an improvement, and it spends the
# client's one copy.
UNSEALABLE = "Harbor Plumbing"
plant_legacy(UNSEALABLE, password=OTHER, login="owner@harbor.test")
did = seo.seal_site_login(UNSEALABLE, seo.load_store(UNSEALABLE))
check("with no key, the move refuses", did is False)
check("and the SEO record keeps its password rather than losing it to a "
      "second plaintext file",
      OTHER in read_raw(seo_path(UNSEALABLE)))
check("no credential file was created for it",
      not os.path.exists(cred_path(UNSEALABLE)))
check("client_detail() still keeps that plaintext off the wire",
      nowhere_in(seo.client_detail(UNSEALABLE), OTHER))
check("and still reports that a password is on file, so the panel can say "
      "where it is", seo.client_detail(UNSEALABLE).get("setup_has_password") is True)

os.environ["TOKEN_ENCRYPTION_KEY"] = KEY_A
check("once the key is back, the same record moves",
      seo.seal_site_login(UNSEALABLE, seo.load_store(UNSEALABLE)) is True)
check("and the SEO file is clean afterwards",
      OTHER not in read_raw(seo_path(UNSEALABLE)))


# ------------------------------------------------------------ saving anew
print("\nsaving a login the ordinary way")
NEW = "Cedar Grove Vet"
res = cms_credentials.save_site_login(NEW, login="front-desk",
                                      password=SECRET, actor="rep@smart1.test")
check("the save reports ok", res.get("ok") is True)
check("it answers with the state, and the state has no password",
      bool(res.get("state")) and nowhere_in(res))
check("the file on disk does not contain the password",
      SECRET not in read_raw(cred_path(NEW)))

blank = cms_credentials.save_site_login(NEW, login="front-desk-renamed",
                                        password="", actor="rep@smart1.test")
check("a blank password does NOT clear the stored one — the form sends the "
      "field only when somebody typed in it",
      blank.get("state", {}).get("has_password") is True)
check("and the other fields still update around it",
      cms_credentials.site_login_state(NEW).get("login") == "front-desk-renamed")
check("the stored secret is still the original one",
      cms_credentials._unseal(
          json.loads(read_raw(cred_path(NEW))).get("secret"))[0] == SECRET)

cms_credentials.save_site_login(NEW, login="front-desk-renamed",
                                password=OTHER, actor="rep@smart1.test")
check("a typed password DOES replace it",
      cms_credentials._unseal(
          json.loads(read_raw(cred_path(NEW))).get("secret"))[0] == OTHER)
check("and the old one is gone from the file",
      SECRET not in read_raw(cred_path(NEW)))

check("a site login is not reachable through get(), which is the WordPress "
      "application-password path and nothing else",
      "app_password" not in cms_credentials.state(NEW, cms_credentials.WORDPRESS)
      and cms_credentials.state(NEW, cms_credentials.WORDPRESS)["connected"] is False)


# ------------------------------------------------------------- the sweep
print("\nthe sweep, for the clients nobody opens")
for name in ("Sweep One", "Sweep Two"):
    plant_legacy(name, login=f"{seo.slugify(name)}@test")
# A store with no client name recorded: counted and skipped, never filed under
# a slug the name would not find again.
jsonstore.write_json(os.path.join(jsonstore.data_dir("seo"), "nameless.json"),
                     {"setup": {"password": SECRET}}, indent=1)

out = seo.seal_all_site_logins()
check("the sweep sealed both named records", out.get("sealed") == 2, out)
check("it counted the nameless one rather than guessing an owner",
      out.get("unnamed") == 1, out)
check("both SEO files are clean afterwards",
      SECRET not in read_raw(seo_path("Sweep One"))
      and SECRET not in read_raw(seo_path("Sweep Two")))
check("and each client got its own credential file",
      os.path.exists(cred_path("Sweep One"))
      and os.path.exists(cred_path("Sweep Two")))
check("the nameless record is left exactly as it was, not silently emptied",
      SECRET in read_raw(os.path.join(jsonstore.data_dir("seo"), "nameless.json")))

second = seo.seal_all_site_logins()
check("a second sweep finds nothing left to seal", second.get("sealed") == 0, second)
check("and it does not re-report the records it already moved",
      second.get("checked") == 1, second)


# ------------------------------------------------------------- the route
print("\nthe setup route")
os.environ["HUB_SKIP_SCHEDULER"] = "1"
import hub                                                   # noqa: E402
from hub import auth as _auth                                # noqa: E402

app = hub.create_hub_app()
app.config["TESTING"] = True
client_http = app.test_client()
# current_user() reads the signed cookie, not the Flask session — the
# middleware in wsgi.py has to answer with no application context.
client_http.set_cookie(_auth.COOKIE_NAME, _auth.issue_cookie_value("Tester"))

ROUTED = "Northside Law"
r = client_http.post("/api/seo/setup", json={
    "client": ROUTED, "access_method": "WordPress admin",
    "access_url": "https://northside.test/wp-login.php",
    "login": "northside-admin", "password": SECRET,
    "webmaster_status": "has", "blogs_enabled": "no", "completed": True})
check("the route accepts the form", r.status_code == 200, r.status_code)
body = r.get_json() or {}
check("it does not hand the password back", nowhere_in(body))
check("it reports no error storing the login", not body.get("site_login_error"), body)
check("the SEO record it wrote has no password in it",
      SECRET not in read_raw(seo_path(ROUTED)))
check("and no password key either",
      "password" not in (seo.load_store(ROUTED).get("setup") or {}))
check("the other answers did store",
      seo.load_store(ROUTED)["setup"].get("webmaster_status") == "has")
check("the password is sealed in the credential store",
      cms_credentials.site_login_state(ROUTED).get("has_password") is True
      and SECRET not in read_raw(cred_path(ROUTED)))

r2 = client_http.post("/api/seo/setup", json={
    "client": ROUTED, "login": "northside-admin", "notes": "call before 3pm"})
check("saving the form again without touching the password keeps it",
      cms_credentials.site_login_state(ROUTED).get("has_password") is True)
check("and the unrelated answer stored",
      seo.load_store(ROUTED)["setup"].get("notes") == "call before 3pm")

# A record still carrying plaintext is also cleaned by a save, not only by a
# page read.
LEGACY_ROUTE = "Pinecrest Roofing"
plant_legacy(LEGACY_ROUTE, login="pinecrest")
r3 = client_http.post("/api/seo/setup", json={
    "client": LEGACY_ROUTE, "notes": "seen"})
check("a save on a legacy record seals it too", r3.status_code == 200)
check("the plaintext is gone from that SEO record",
      SECRET not in read_raw(seo_path(LEGACY_ROUTE)))
check("and it is on file sealed",
      cms_credentials.site_login_state(LEGACY_ROUTE).get("has_password") is True)

# A credential store that cannot be written must not read as "Saved". The
# answers on that form are eleven fields somebody typed; the password is one of
# them, and "it saved" when it did not is how a rep learns a week later.
_real_save = cms_credentials.save_site_login


def _broken_save(*_a, **_kw):
    raise RuntimeError("disk is read-only")


cms_credentials.save_site_login = _broken_save
try:
    rbroken = client_http.post("/api/seo/setup", json={
        "client": "Elmwood Clinic", "access_method": "Wix",
        "login": "elmwood", "password": SECRET, "notes": "typed by a person"})
finally:
    cms_credentials.save_site_login = _real_save
check("a credential store that cannot be written still answers 200 — the ten "
      "other answers on that form did store",
      rbroken.status_code == 200, rbroken.status_code)
bbroken = rbroken.get_json() or {}
check("and it says the password did not store, rather than 'Saved'",
      bool(bbroken.get("site_login_error")), bbroken)
check("the failure names what failed without quoting the password",
      "site login" in (bbroken.get("site_login_error") or "").lower()
      and SECRET not in rbroken.get_data(as_text=True))
check("the setup answers really did land",
      seo.load_store("Elmwood Clinic")["setup"].get("notes") == "typed by a person")
check("and no plaintext password was left behind in the SEO record instead",
      SECRET not in read_raw(seo_path("Elmwood Clinic")))

# The route the page actually reads, over a client that has a login on file:
# a walk of the whole answer, not a check on the one key somebody stripped.
r4 = client_http.get("/api/seo/detail?name=" + ROUTED)
check("the detail route answers", r4.status_code == 200, r4.status_code)
d4 = r4.get_json() or {}
check("it reports the login state to the panel",
      (d4.get("site_login") or {}).get("has_password") is True, d4.get("site_login"))
check("and the password is nowhere in the bytes it sends",
      SECRET not in r4.get_data(as_text=True))

# The same route over the legacy record that could not be sealed: the plaintext
# is still on the disk, and the answer must still not carry it.
os.environ.pop("TOKEN_ENCRYPTION_KEY", None)
STILL_PLAIN = "Lakeside Movers"
plant_legacy(STILL_PLAIN, password=OTHER, login="lakeside")
r5 = client_http.get("/api/seo/detail?name=" + STILL_PLAIN)
check("a record that could not be sealed still answers", r5.status_code == 200,
      r5.status_code)
check("and its plaintext password is not in the bytes either",
      OTHER not in r5.get_data(as_text=True))
check("while the panel is still told a password exists",
      (r5.get_json() or {}).get("setup_has_password") is True)
os.environ["TOKEN_ENCRYPTION_KEY"] = KEY_A


# ------------------------------------------------------------- the page
print("\nthe page")
html = open(os.path.join(ROOT, "hub", "templates", "seo_client.html"),
            encoding="utf-8").read()
check("the setup panel has a row for what is on file",
      'id="su_passState"' in html)
check("it reads the sealed state rather than a bare boolean",
      "showPassState(" in html and "d.site_login" in html)
check("it reports the rotated-key error rather than silence",
      "st.error" in html)
check("it says out loud when a login is stored in the clear",
      "stored in the clear" in html)
check("and it promises the password is never shown",
      "never shown here" in html)
check("a failed credential save is reported instead of 'Saved'",
      "site_login_error" in html)
check("nothing on the page asks the server for the stored site password",
      "d.setup.password" not in html and "su_pass').value=" not in html)


# --------------------------------------------------------------- the job
print("\nthe scheduled sweep")
from hub import scheduler                                   # noqa: E402

check("the sweep is a registered job", "site_logins" in scheduler.JOBS)
check("it runs on the leader like every other job, not on a raw timer",
      scheduler.JOBS["site_logins"][1].__name__ == "job_seal_site_logins")
check("its description says what it is for",
      "plain text" in scheduler.JOBS["site_logins"][2])
plant_legacy("Job Fixture", login="job@test")
# Two records are outstanding here, not one: "Lakeside Movers" above was
# planted while the key was unset and deliberately left where it was.
result = scheduler.job_seal_site_logins(app)
check("running the job seals both records still outstanding",
      result.get("sealed") == 2, result)
check("including the one an earlier pass refused because there was no key",
      OTHER not in read_raw(seo_path("Lakeside Movers"))
      and cms_credentials.site_login_state("Lakeside Movers")
      .get("has_password") is True)
check("and the one nobody had opened a page for",
      SECRET not in read_raw(seo_path("Job Fixture"))
      and cms_credentials.site_login_state("Job Fixture")
      .get("has_password") is True)
check("and it reports its own outcome rather than returning nothing",
      isinstance(result, dict) and "checked" in result)

os.environ.pop("TOKEN_ENCRYPTION_KEY", None)
plant_legacy("No Key Fixture", password=OTHER, login="nokey@test")
nokey_run = scheduler.job_seal_site_logins(app)
check("with no key the job seals nothing and says so",
      nokey_run.get("sealed") == 0 and "TOKEN_ENCRYPTION_KEY" in (nokey_run.get("note") or ""),
      nokey_run)
os.environ["TOKEN_ENCRYPTION_KEY"] = KEY_A


print(f"\n{PASS} passed, {FAIL} failed")
shutil.rmtree(_TMP, ignore_errors=True)
sys.exit(1 if FAIL else 0)
