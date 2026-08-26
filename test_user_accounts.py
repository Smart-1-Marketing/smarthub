"""User accounts: the roster, the two levels, the starting password, crawlers.

    python3 test_user_accounts.py

Same shape as the other test files: no pytest, no new dependencies, and it runs
against a temporary data directory and a throwaway SQLite database, so it never
touches /var/data or the real one.

## Why this file exists

Every failure this covers is one that leaves every screen looking healthy:

  1.  a roster re-run resets
      passwords                   — a deploy silently handing fourteen accounts
                                    back to a password written down in this
                                    repository, with nothing on any page
                                    saying so
  2.  must_change_password is
      only a label                — the flag exists today and stops nothing;
                                    if the gate is ever removed, the roster
                                    keeps its starting password for ever and
                                    the Users panel still shows the pill
  3.  the Utilities gate is only
      in the nav                  — hiding a link is not a guard, and a
                                    General user typing /diagnostics is the
                                    whole test
  4.  a new Utilities route is
      not in the list             — the one failure mode of a central gate,
                                    so the sidebar's admin-only entries and
                                    access.UTILITY_PREFIXES are asserted
                                    against each other
  5.  a password or a hash
      reaches a page              — /api/users renders into a screen people
                                    screenshot
  6.  the throttle counts
      attempts but not addresses  — one guess against each of fourteen staff
                                    addresses never reaches six on any of them
  7.  robots.txt exists and the
      header does not             — robots.txt is a request; the header is the
                                    part that removes a page from an index,
                                    and only one of the two covers the mounted
                                    modules and the PDFs
  8.  forgot-password still
      offers a form               — a form that flags an admin on a queue
                                    nobody watches looks like it did something
"""
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1users_test_")
os.environ["HUB_DATA_DIR"] = os.path.join(TMP, "data")
os.environ["AUDIT_LOG_PATH"] = os.path.join(TMP, "audit.jsonl")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "db.sqlite3")
os.environ["SECRET_KEY"] = "users-test-secret"
os.environ["PANEL_PASSWORD"] = "users-test-shared"

from werkzeug.test import Client                                   # noqa: E402

from hub import access, auth, no_crawl, sidebar, user_directory     # noqa: E402
from wsgi import application                                        # noqa: E402

_passed = _failed = 0


def check(label, got, want):
    global _passed, _failed
    if got == want:
        _passed += 1
        print(f"  ok    {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}\n          got:  {got!r}\n          want: {want!r}")


def section(title):
    print(f"\n{title}\n{'-' * len(title)}")


DEFAULT = user_directory.DEFAULT_PASSWORD


def signed_in(email, password=DEFAULT):
    c = Client(application)
    c.post("/login", data={"email": email, "password": password, "next": "/"})
    return c


def settled(email, new_password):
    """Signed in AND past the forced password change."""
    c = signed_in(email)
    c.post("/account", data={"current": DEFAULT, "password": new_password})
    return c


# --------------------------------------------------- the roster as uploaded
section("Every person on the census has an account")

check("fourteen people on the roster", len(user_directory.ROSTER), 14)

_rows = {r["email"]: r for r in user_directory.roster_rows()}
check("fourteen distinct addresses", len(_rows), 14)

_admin_client = settled("john@smart1marketing.com", "a-long-enough-phrase")
_listing = _admin_client.get("/api/users").get_json()
_by_email = {u["email"]: u for u in _listing["users"]}

check("every roster address has an account",
      sorted(e for e in _rows if e in _by_email), sorted(_rows))
check("and every one of them can sign in — none left pending",
      sorted({u["status"] for e, u in _by_email.items() if e in _rows}), ["active"])

check("the five Admin rows are the five admin accounts",
      sorted(e for e, u in _by_email.items() if u["level"] == "Admin"),
      sorted(["george@smart1marketing.com", "john@smart1marketing.com",
              "kaden@smart1marketing.com", "louann@smart1marketing.com",
              "todd@smart1marketing.com"]))
check("the founding three keep super_admin, which is wider than Admin",
      sorted(e for e, u in _by_email.items() if u["role"] == "super_admin"),
      sorted(["george@smart1marketing.com", "kaden@smart1marketing.com",
              "todd@smart1marketing.com"]))
check("everybody else is General",
      all(_by_email[e]["level"] == "General"
          for e, r in _rows.items() if r["level"] == "General"), True)

section("Every census field is carried, and none of them is invented")

_aimee = _by_email["aimee@smart1marketing.com"]["profile"]
check("title", _aimee["title"], "Senior Campaign Strategist")
check("phone, normalised from the export's 309/631-2397",
      _aimee["phone"], "(309) 631-2397")
check("birthday, as ISO", _aimee["birthday"], "1971-04-17")
check("and shown unambiguously", _aimee["birthday_pretty"], "17 Apr 1971")
check("date of hire", _aimee["hired_at"], "2021-06-01")
check("first and last name kept apart",
      (_aimee["first_name"], _aimee["last_name"]), ("Aimee", "Tacey"))

# The one row the export wrote with a space instead of a slash. A formatter
# that only handled the common separator would leave this one number looking
# like a typo on the panel.
check("the odd separator in 614 4968470 formats like the rest",
      _by_email["louann@smart1marketing.com"]["profile"]["phone"],
      "(614) 496-8470")

# A number that is not ten digits comes back as it was given, not padded to
# look right. A phone number the Hub completed is one nobody can dial.
check("a short number is returned untouched rather than padded",
      user_directory.format_phone("614-555"), "614-555")
check("a date it cannot parse is empty, never today",
      user_directory.iso_date("sometime in 2019"), "")
check("no hire date means no length of service, rather than zero years",
      user_directory.years_of_service(""), None)


# ------------------------------------------------ the starting password
section("The starting password is valid for exactly one sign-in")

_first = signed_in("traci@smart1marketing.com")
_land = _first.get("/", follow_redirects=False)
check("a page redirects to the change form", _land.status_code, 302)
check("and names it", _land.headers.get("Location"), "/account?first=1")

_api = _first.get("/api/summary")
check("an API refuses rather than redirecting into an HTML login page",
      _api.status_code, 403)
check("and says where to go, so a fetch can report it",
      (_api.get_json() or {}).get("redirect"), "/account")

check("the form explains why they are on it",
      "Choose your own password" in _first.get("/account").get_data(as_text=True),
      True)

# The hub app's own before_request covers hub routes and nothing else, so a
# mounted module was a way straight past the whole gate: twenty tools, all
# reachable, with the "must change password" pill still showing on the panel.
_mod = _first.get("/tools/social/", follow_redirects=False)
check("a mounted module is not a way round the change", _mod.status_code, 302)
check("and sends them to the same form", _mod.headers.get("Location"),
      "/account?first=1")
check("a module API refuses rather than redirecting a fetch into HTML",
      _first.get("/tools/gpt-ads/api/ads").status_code, 403)

# The gate sits behind AuthGuard's public-prefix check, so nothing a client or
# a prospect opens can be caught by it.
_public = Client(application)
check("a public landing page is untouched by it",
      _public.get("/land/boat/").status_code, 200)
check("so is the MSA signing page", _public.get("/msa/").status_code, 200)

# The policy is what they replace it with, not what they were given.
_bad = _first.post("/account", data={"current": DEFAULT, "password": "Smart12026!"})
check("the starting password cannot be chosen as the new one", _bad.status_code, 400)
_short = _first.post("/account", data={"current": DEFAULT, "password": "short"})
check("nor can a short one", _short.status_code, 400)

_ok = _first.post("/account", data={"current": DEFAULT,
                                    "password": "a-phrase-i-will-remember"})
check("a real one is accepted", _ok.status_code, 302)
check("and the Hub opens up", _first.get("/").status_code, 200)
check("mounted modules included", _first.get("/tools/social/").status_code, 200)

# The reason this is one function and not two calls: a starting password that
# could be set without the flag is a permanent password nobody notices. Asserted
# on behaviour rather than on the source, because the source says both things
# in its docstring and would pass a text search either way round.
from hub import users as _users                                     # noqa: E402


def _rejected_by_policy(value):
    try:
        _users.check_password(value)
        return False
    except _users.UserError:
        return True


check("the policy really does reject the starting password, so the exemption "
      "that issues it is doing something",
      _rejected_by_policy(DEFAULT), True)

section("A roster re-run creates, and changes nothing else")

def _hub_app():
    """The hub Flask app, out from under the middleware stack."""
    app = application
    seen = 0
    while not hasattr(app, "mounts") and seen < 10:
        app = getattr(app, "app", None) or getattr(app, "wsgi_app", None)
        seen += 1
    return app.app


_flask = _hub_app()
with _flask.app_context():
    _before = _users.by_email("traci@smart1marketing.com").password_hash
    _users.set_role(_users.by_email("todd@smart1marketing.com"),
                    _users.by_email("traci@smart1marketing.com").id, "admin")
    _again = user_directory.sync_roster()
    _after = _users.by_email("traci@smart1marketing.com")

check("a second run creates nobody", _again["created"], [])
check("it does not reset the password somebody chose",
      _after.password_hash, _before)
check("and it does not take back a promotion made in the panel",
      _after.role, "admin")

with _flask.app_context():
    # Put it back so the level assertions below still describe the roster.
    _users.set_role(_users.by_email("todd@smart1marketing.com"),
                    _after.id, "member")


# -------------------------------------------------- General versus Admin
section("General Access reaches everything except Utilities")

_general = settled("aimee@smart1marketing.com", "another-good-passphrase")

for _path in ("/diagnostics", "/diagnostics/users", "/status", "/activity"):
    check(f"General is refused {_path}", _general.get(_path).status_code, 403)
for _path in ("/api/integrity", "/api/diagnostics", "/api/backup", "/api/quotas",
              "/api/users"):
    check(f"General is refused {_path}", _general.get(_path).status_code, 403)

check("the refusal names the section rather than showing a bare 403",
      "General Access" in _general.get("/diagnostics").get_data(as_text=True), True)
check("and an API refusal is JSON a panel can read",
      (_general.get("/api/integrity").get_json() or {}).get("level"), "General")

for _path in ("/", "/client360", "/tools", "/creative", "/qa", "/seo",
              "/sales/leads"):
    check(f"General still reaches {_path}",
          _general.get(_path).status_code, 200)

check("General reaches the mounted tools too",
      _general.get("/tools/social/").status_code, 200)

for _path in ("/diagnostics", "/status", "/activity", "/api/integrity",
              "/api/users"):
    check(f"Admin reaches {_path}", _admin_client.get(_path).status_code, 200)

check("the nav hides Utilities from General",
      "Diagnostics" in _general.get("/").get_data(as_text=True), False)
check("and shows it to Admin",
      "Diagnostics" in _admin_client.get("/").get_data(as_text=True), True)

# Hiding a link is not a guard. If this ever passes only because the link is
# gone, the four checks above are what would still catch it.
check("a hidden section is still refused when typed in by hand",
      _general.get("/activity").status_code, 403)

section("The gate list and the nav cannot drift apart")

_nav_admin_paths = {href for key, href, _i, _l, level in sidebar._ITEMS
                    if level == sidebar.ADMIN_ONLY and href}
_ungated = sorted(p for p in _nav_admin_paths if not access.is_utility(p))
check("every admin-only nav entry is a path the gate actually refuses",
      _ungated, [])

check("/status is gated but /statuses would not be — prefixes are segment-aware",
      (access.is_utility("/status"), access.is_utility("/statuses")), (True, False))
check("sign-in diagnostics stay open, or nobody locked out can report it",
      access.is_utility("/login/health"), False)
check("so does the version endpoint the sign-in page shows",
      access.is_utility("/api/version"), False)

section("The shared password is the emergency door, and it is Admin")

_shared = Client(application)
_shared.post("/login", data={"password": "users-test-shared", "name": "CI"})
check("it reaches Diagnostics", _shared.get("/diagnostics").status_code, 200)
check("and the integrity check the CI gate runs",
      _shared.get("/api/integrity").status_code, 200)
check("but it still cannot use the Users panel, which needs an account",
      _shared.get("/diagnostics/users").status_code, 403)


# ------------------------------------------------------ the Users panel
section("Nothing on the Users panel is a credential")

_raw = json.dumps(_admin_client.get("/api/users").get_json())
for _leak in ("password_hash", "reset_token_hash", "scrypt:", DEFAULT):
    check(f"the listing carries no {_leak}", _leak in _raw, False)

_uid = _by_email["brandon@smart1marketing.com"]["id"]
_set = _admin_client.post(f"/api/users/{_uid}/set-password", json={}).get_json()
check("the key icon generates a password when none is typed",
      _set.get("generated"), True)
check("long enough to satisfy the policy they replace it with",
      len(_set.get("password", "")) >= 12, True)
check("and the person it belongs to must replace it",
      _set["user"]["must_change_password"], True)

_typed = _admin_client.post(f"/api/users/{_uid}/set-password",
                            json={"password": "a-typed-passphrase"}).get_json()
check("a typed password is used as given", _typed.get("generated"), False)
_check_in = signed_in("brandon@smart1marketing.com", "a-typed-passphrase")
check("it signs them in", _check_in.get("/", follow_redirects=False).status_code, 302)
check("straight to the change form, because two people now know it",
      _check_in.get("/", follow_redirects=False).headers.get("Location"),
      "/account?first=1")

check("a General account cannot set anyone's password",
      _general.post(f"/api/users/{_uid}/set-password", json={}).status_code, 403)

# Whitespace-collapsed: the template wraps these sentences, so a literal
# search would fail on a line break rather than on a missing statement.
_panel = " ".join(
    _admin_client.get("/diagnostics/users").get_data(as_text=True).split())
check("the panel says what the two levels mean",
      "except Utilities" in _panel, True)
check("and that neither password route is emailed",
      "this Hub has no sender" in _panel, True)
check("and points at the same page a locked-out person would find",
      'href="/forgot"' in _panel, True)


# ------------------------------------------------------ forgot password
section("Forgotten passwords name a person, not a form")

_forgot = Client(application).get("/forgot")
_text = _forgot.get_data(as_text=True)
check("the page loads without a session", _forgot.status_code, 200)
check("it says what the user asked it to say",
      "Well, that" in _text and "not good" in _text, True)
check("and names John", "chat or email John" in _text, True)
check("with an address to use", user_directory.SUPPORT_EMAIL in _text, True)
check("the sign-in page links to it",
      'href="/forgot"' in Client(application).get("/login").get_data(as_text=True),
      True)
check("/reset with no token goes there rather than offering a form",
      Client(application).get("/reset").headers.get("Location"), "/forgot")


# ----------------------------------------------------------- brute force
# ------------------------------------------------------ who is signed in now
section("Signed in now — a headcount, not a claim about who is at their desk")

from hub import presence                                            # noqa: E402
from wsgi import hub_app                                            # noqa: E402

# The Hub keeps no session table, so this can only ever be "seen lately".
# Everything below is about that being said out loud rather than a number
# people would read as "at their desk".

with hub_app.app_context():
    presence.Presence.query.delete()
    presence.db.session.commit()
presence._LAST_WRITE.clear()

# Placed before the brute-force section below on purpose: hammering the login
# there locks this test client's own address, and nobody can be counted after
# that because nobody can sign in.
_mike = settled("mhawkins@smart1marketing.com", "another-long-phrase")

# A page in a MOUNTED MODULE, and nothing else. AuthGuard is WSGI middleware
# with no application context, so this is where a swallowed "Working outside
# of application context" would make the whole count read zero for ever.
_mike.get("/tools/social/")
with hub_app.app_context():
    _seen = presence.active()
check("a module page counts — AuthGuard has no app context and must push one",
      [p["name"] for p in _seen["people"]], ["Michael Hawkins"])
check("resolved to the account, not left as a display name",
      _seen["people"][0]["email"], "mhawkins@smart1marketing.com")
check("and not mistaken for the shared password",
      _seen["people"][0]["shared"], False)

# The throttle: a second request inside the minute must not rewrite the row.
with hub_app.app_context():
    _before = presence.Presence.query.filter_by(
        key="mhawkins@smart1marketing.com").first().last_seen
_mike.get("/")
_mike.get("/tools/social/")
with hub_app.app_context():
    _after = presence.Presence.query.filter_by(
        key="mhawkins@smart1marketing.com").first().last_seen
check("three more requests, still one write — a headcount is not worth a "
      "database write per request", _after, _before)

# The shared password is a session with nobody behind it.
_shared_now = Client(application)
_shared_now.post("/login", data={"password": "users-test-shared", "name": "CI"})
_shared_now.get("/")
with hub_app.app_context():
    _seen = presence.active()
check("it is counted", _seen["count"], 2)
check("but named as what it is", _seen["shared_sessions"], 1)
check("and the one line every screen prints says so",
      presence.summary_line(_seen),
      "2 people active in the last 15 minutes · 1 of them a shared-password "
      "session")

# The window. A row outside it is not "somebody who left" — it is simply not
# in the answer, and the answer always says how wide it is.
with hub_app.app_context():
    _row = presence.Presence.query.filter_by(key="mhawkins@smart1marketing.com").first()
    _row.last_seen = presence._now() - __import__("datetime").timedelta(minutes=31)
    presence.db.session.commit()
    _seen = presence.active()
check("half an hour later they are out of the window",
      [p["name"] for p in _seen["people"]], ["Shared login"])
check("and the number is never printed without the window it was measured over",
      str(presence.WINDOW_MINUTES) in presence.summary_line(_seen), True)

# Identity: exactly one account or none, never a guess. The module cookie
# carries a display name and nothing else, so this is the only way back to a
# person — and two people on this roster are called Todd.
with hub_app.app_context():
    check("a display name resolves to its one account",
          presence.identify("Michael Hawkins"),
          ("mhawkins@smart1marketing.com", False))
    check("a name no account carries is the shared password",
          presence.identify("Shared login"), ("", True))

    # Two accounts, one name. Guessing between them attributes one person's
    # presence to another; calling it a shared-password session is just as
    # wrong, because somebody real is signed in. It is neither.
    for _e in ("pat.twin1@smart1marketing.com", "pat.twin2@smart1marketing.com"):
        _users.create_account(email=_e, name="Pat Twin", role="member",
                              password="a-long-enough-phrase", status="active",
                              approved_by="test")
    check("two accounts sharing a name resolve to neither",
          presence.identify("Pat Twin"), ("", False))
    check("and are emphatically not counted as the shared password",
          presence.identify("Pat Twin")[1], False)
    _actor = _users.by_email("todd@smart1marketing.com")
    for _e in ("pat.twin1@smart1marketing.com", "pat.twin2@smart1marketing.com"):
        _users.delete(_actor, _users.by_email(_e).id)

# The privacy rule, asserted structurally: this table records that somebody
# was seen, never what they were looking at. A path column is a minute-by-
# minute log of what each member of staff was doing.
check("no page, path or URL is recorded",
      sorted(c.name for c in presence.Presence.__table__.columns),
      ["email", "id", "key", "last_seen", "name", "shared"])

# The headcount is on everybody's dashboard, so it cannot live on an
# admin-only path. /api/status does, which is why it is not a key on that.
check("/api/presence is not a Utilities path", access.is_utility("/api/presence"), False)
check("while /api/status still is", access.is_utility("/api/status"), True)
check("an anonymous request is refused",
      Client(application).get("/api/presence").status_code, 401)
_general_now = settled("lauren@smart1marketing.com", "general-long-phrase")
check("a General account can read the headcount",
      _general_now.get("/api/presence").status_code, 200)
check("and is still refused the checks beside it on the same card",
      _general_now.get("/api/status").status_code, 403)

_dash = _general_now.get("/").get_data(as_text=True)
check("the card is headed System status", "<h3>System status" in _dash, True)
check("and holds the headcount", 'id="presence"' in _dash, True)
check("a General account is told why the checks are missing rather than "
      "being shown a green zero",
      "The Hub&rsquo;s own checks run either way" in _dash, True)


section("Brute force: attempts, addresses and escalation")

auth.throttle_reset("10.0.0.1")
for _ in range(auth.LOGIN_MAX_ATTEMPTS):
    auth.throttle_fail("10.0.0.1", "aimee@smart1marketing.com")
_first_wait = auth.throttle_check("10.0.0.1")
check("six wrong guesses lock the address out", _first_wait > 0, True)

auth._attempts["10.0.0.1"]["locked_until"] = 0
for _ in range(auth.LOGIN_MAX_ATTEMPTS):
    auth.throttle_fail("10.0.0.1", "aimee@smart1marketing.com")
check("and the second lockout is longer than the first — a fixed window is a "
      "rate limit, not a deterrent",
      auth.throttle_check("10.0.0.1") > _first_wait, True)

auth.throttle_reset("10.0.0.2")
for _i in range(auth.STUFFING_MAX_EMAILS + 1):
    auth.throttle_fail("10.0.0.2", f"person{_i}@smart1marketing.com")
check("one guess each against five addresses is caught, which counting "
      "attempts alone cannot see",
      auth.throttle_check("10.0.0.2") > 0, True)
check("and the addresses guessed are hashed, not kept",
      any("@" in e for e in auth._attempts["10.0.0.2"]["emails"]), False)

check("a success clears the ladder, so three bad mornings do not compound",
      (auth.throttle_reset("10.0.0.1"), auth.throttle_check("10.0.0.1"))[1], 0)


class _Headers:
    def get(self, key, default=""):
        return "1.1.1.1, 2.2.2.2, 8.8.8.8" if key == "X-Forwarded-For" else default


check("the throttle keys on the last hop — the first is client-supplied",
      auth.client_ip(_Headers(), "5.5.5.5"), "8.8.8.8")
check("the status report names no address and no IP",
      sorted(auth.throttle_status()), sorted(
          ["tracked_ips", "locked_out", "stuffing_suspects", "window_seconds",
           "max_attempts", "ladder_seconds", "shared_across_workers"]))

_hammer = Client(application)
for _ in range(auth.LOGIN_MAX_ATTEMPTS + 2):
    _last = _hammer.post("/login", data={"email": "erik@smart1marketing.com",
                                         "password": "wrong"})
check("the sign-in page itself locks out, not just the helper",
      _last.status_code, 429)


# --------------------------------------------------------- crawlers
section("No search engine and no AI crawler reads this Hub")

_anon = Client(application)
_robots = _anon.get("/robots.txt")
_body = _robots.get_data(as_text=True)
check("robots.txt is served without a login — one behind a login is not read",
      _robots.status_code, 200)
check("it disallows everything", "User-agent: *\nDisallow: /" in _body, True)

# Named individually because several of these honour only their own token:
# Google-Extended and Applebot-Extended exist precisely so a site can refuse
# AI training while staying in the search index, and a wildcard does not
# always register with them.
for _bot in ("GPTBot", "ClaudeBot", "Google-Extended", "Applebot-Extended",
             "PerplexityBot", "CCBot", "Bytespider", "meta-externalagent"):
    check(f"{_bot} is named, not left to the wildcard",
          f"User-agent: {_bot}\nDisallow: /" in _body, True)
for _bot in ("Googlebot", "Bingbot", "DuckDuckBot", "YandexBot"):
    check(f"{_bot} is named", f"User-agent: {_bot}\nDisallow: /" in _body, True)

check("no Sitemap line — pointing a crawler at one while asking it not to "
      "crawl is a mixed message", "Sitemap" in _body, False)

# The header is the layer that actually removes a page from an index, and
# unlike a meta tag it reaches the PDFs, the CSVs and the JSON.
for _path in ("/robots.txt", "/login", "/forgot", "/api/version"):
    check(f"X-Robots-Tag on {_path}",
          _anon.get(_path).headers.get("X-Robots-Tag"), no_crawl.ROBOTS_TAG)
check("and on a mounted module, which a Flask after_request would have missed",
      _anon.get("/land/boat/").headers.get("X-Robots-Tag"), no_crawl.ROBOTS_TAG)
check("noindex is in it", "noindex" in no_crawl.ROBOTS_TAG, True)
check("so is the AI opt-out", "noai" in no_crawl.ROBOTS_TAG, True)

check("the sign-in page carries the meta tag as well, for a crawler that "
      "keeps the body and drops the headers",
      'name="robots"' in _anon.get("/login").get_data(as_text=True), True)
check("/llms.txt says the opposite of a client's llms.txt",
      "Do not index" in _anon.get("/llms.txt").get_data(as_text=True), True)


# ------------------------------------------------------------------- summary
shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{'-' * 60}\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
