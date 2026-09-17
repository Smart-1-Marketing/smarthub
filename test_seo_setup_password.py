"""The client's website password, and the backup it was being written into.

    python3 test_seo_setup_password.py

Same shape as the other test files here — no pytest, no new dependencies, a
temporary data directory and a throwaway SQLite database, so it never touches
/var/data or the real one.

## Why this file exists

`hub/cms_credentials.py` opens by explaining what it is for, and names this in
passing as work that had not been done:

    the SEO store's own `setup.password` is written to
    `data/seo/<client>.json` in plain text and, because that goes through
    `hub/jsonstore.py`, mirrored verbatim into Postgres and into every
    database backup.

That is a client's own CMS login — the credential that opens their live
website — and the exposure is not the disk. `hub/jsonstore.py` mirrors every
write into the database on purpose, so a plain string in this store is a plain
string in Postgres and in every backup taken since it was saved.

**Nothing reads it back.** Driven rather than assumed: a sweep of 261
responses across every SEO, client, backup and database route found the saved
password in none of them. `client_detail()` strips it and the record page only
ever learns whether one exists. That is what makes the fix sealing rather than
deletion — the field is clearly meant to be kept (the page says "saved — leave
blank to keep"), and that no route returns it reads as an omission rather than
a decision somebody made. Destroying a credential a rep deliberately saved is
not a conclusion this change is entitled to reach on its own.

The assertions below are the three states `hub/sealing.py` keeps apart, plus
the two that a check like this usually gets wrong: that a *rotated key* is an
error and never "there is no password", and that the sweep does not mark itself
done — because the plaintext is in the backups, and restoring one brings it
back.
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1seopw_")
DISK = os.path.join(TMP, "disk")
os.makedirs(DISK, exist_ok=True)
os.environ["HUB_DATA_DIR"] = DISK
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "t.db")
os.environ["SECRET_KEY"] = "seo-pw-test"

from cryptography.fernet import Fernet                      # noqa: E402

KEY_A = Fernet.generate_key().decode()
KEY_B = Fernet.generate_key().decode()
os.environ["TOKEN_ENCRYPTION_KEY"] = KEY_A

from hub import auth, create_hub_app, jsonstore, sealing, seo   # noqa: E402

_passed, _failed = 0, 0
SECRET = "ZZ-sentinel-password-9713-ZZ"


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


def store_path(client):
    return os.path.join(seo._store_base(), seo.slugify(client) + ".json")


def on_disk(client):
    try:
        return Path(store_path(client)).read_text(encoding="utf-8")
    except OSError:
        return ""


def in_mirror(client):
    """What the database actually holds for this store — the point of all this."""
    return jsonstore._fetch(jsonstore.key_for(store_path(client))) or ""


def save(client, **setup):
    st = seo.load_store(client)
    st.setdefault("setup", {}).update(setup)
    seo.save_store(client, st)


# ---------------------------------------------------------------------------
section("1. The secret does not reach the disk, and does not reach the backup")
# The disk is the visible half and the smaller one. Everything written through
# hub/jsonstore.py is mirrored into Postgres verbatim, so a plain password here
# is a plain password in every database backup taken since it was saved --
# which is the sentence hub/cms_credentials.py wrote down and left.
save("Acme Co", login="rep@example.com", password=SECRET)
check("the password is not in the file", SECRET in on_disk("Acme Co"), False)
check("and not in the database mirror", SECRET in in_mirror("Acme Co"), False)
check("the mirror is holding something, so that is not a vacuous pass",
      len(in_mirror("Acme Co")) > 20, True)
check("the fields beside it are untouched",
      seo.load_store("Acme Co")["setup"]["login"], "rep@example.com")
check("and it reads back", seo.setup_password("Acme Co"), (SECRET, ""))

section("2. Sealing survives the read-modify-write everything else does")
# Twenty-odd callers load this store, change one key and save the lot back.
# A seal that is not idempotent double-encrypts on the second one, and the
# value is then unreadable with the very key it was saved under.
st = seo.load_store("Acme Co")
st["questions"] = ["unrelated edit"]
seo.save_store("Acme Co", st)
check("an unrelated save does not seal it twice",
      seo.setup_password("Acme Co"), (SECRET, ""))
check("...and still nothing plain on disk", SECRET in on_disk("Acme Co"), False)
check("the unrelated edit landed",
      seo.load_store("Acme Co")["questions"], ["unrelated edit"])

section("3. A rotated key is an error, never 'there is no password'")
# connected_accounts_result() in Google Finder is the precedent: a rotated key
# reading as an empty book cost that module months of silent failure. Answering
# "no password" sends somebody to re-enter a credential that is already there,
# and hides the one fact that would have explained it.
os.environ["TOKEN_ENCRYPTION_KEY"] = KEY_B
value, err = seo.setup_password("Acme Co")
check("the value is not handed back", value, "")
check("an error is", bool(err), True)
check("...and it names the variable to fix", "TOKEN_ENCRYPTION_KEY" in err, True)
rec_setup = (seo.load_store("Acme Co").get("setup") or {})
check("and the record still says a password is on file",
      sealing.has_secret(rec_setup.get("password")), True)
os.environ["TOKEN_ENCRYPTION_KEY"] = KEY_A
check("the right key reads it again", seo.setup_password("Acme Co"), (SECRET, ""))

section("4. With no key it is stored in the clear, and said out loud")
os.environ.pop("TOKEN_ENCRYPTION_KEY", None)
state = sealing.encryption_state()
check("sealing reports itself unconfigured", state["configured"], False)
check("...in words that name the variable",
      "TOKEN_ENCRYPTION_KEY" in state["note"], True)
save("Nokey Ltd", password=SECRET)
check("the value really is in the clear", SECRET in on_disk("Nokey Ltd"), True)
check("and the shape says so, rather than passing as sealed",
      sealing.is_sealed((seo.load_store("Nokey Ltd")["setup"])["password"]), False)
check("a store with a plain password is reported by name",
      "Nokey Ltd" in seo.plaintext_password_clients(), True)

section("5. The sweep, which is what the already-saved ones depend on")
check("with no key it refuses and says why",
      seo.seal_existing_setup_passwords()["ran"], False)
check("...and names the variable",
      "TOKEN_ENCRYPTION_KEY" in seo.seal_existing_setup_passwords()["reason"], True)
os.environ["TOKEN_ENCRYPTION_KEY"] = KEY_A
result = seo.seal_existing_setup_passwords()
check("with a key it seals what was left in the clear", result["sealed"] >= 1, True)
check("...and the plaintext is gone from the file",
      SECRET in on_disk("Nokey Ltd"), False)
check("...and from the mirror", SECRET in in_mirror("Nokey Ltd"), False)
check("...and the value is still readable",
      seo.setup_password("Nokey Ltd"), (SECRET, ""))
check("nothing is reported in the clear any more",
      seo.plaintext_password_clients(), [])
again = seo.seal_existing_setup_passwords()
check("a second run seals nothing", again["sealed"], 0)
check("...and counts what is already done", again["already"] >= 1, True)

section("6. No marker, because restoring a backup brings the plaintext back")
# The opposite choice from modules/check_reconciliation's upload sweep, and for
# a reason worth asserting rather than describing: that one clears a directory
# local to an instance, so a shared "done" flag would leave a second instance's
# disk untouched for ever. This store is shared -- and a marker would STILL be
# wrong, because the plaintext is in the backups and a restore reinstates it.
jsonstore.write_json(store_path("Nokey Ltd"),
                     {"client": "Nokey Ltd", "setup": {"password": SECRET}},
                     indent=1)
check("a restored backup really is plain again",
      SECRET in on_disk("Nokey Ltd"), True)
healed = seo.seal_existing_setup_passwords()
check("the next boot seals it rather than skipping it", healed["sealed"] >= 1, True)
check("...and it is gone again", SECRET in on_disk("Nokey Ltd"), False)
check("no 'already swept' flag is stored anywhere in the store",
      "swept" in on_disk("Nokey Ltd").lower(), False)

section("7. Nothing serves it, which is why sealing is the fix and not deleting")
# Driven against the real routes rather than read off the source: prose naming
# a strip is not a strip. A response that carried it would mean sealing the
# store had moved the exposure rather than closed it.
app = create_hub_app()
client = app.test_client()
client.set_cookie(auth.COOKIE_NAME, auth.issue_cookie_value("Test Rep"))
seen, leaked = 0, []
for rule in app.url_map.iter_rules():
    path = str(rule)
    if "<" in path or "GET" not in (rule.methods or set()):
        continue
    if not (path.startswith("/api/seo") or path.startswith("/seo")
            or path.startswith("/api/client") or path.startswith("/api/backup")
            or path.startswith("/api/db")):
        continue
    for query in ("?name=Acme Co", "?client=Acme Co", ""):
        try:
            body = client.get(path + query).get_data().decode("utf-8", "replace")
        except Exception:                                   # noqa: BLE001
            continue
        seen += 1
        if SECRET in body:
            leaked.append(path + query)
check("a real sweep of the routes ran", seen > 50, True)
check("and no response carries the password", leaked, [])
# The one thing a screen IS told, so the page can say "leave blank to keep".
record = seo.client_detail("Acme Co")
check("the record says a password is on file",
      record.get("setup_has_password"), True)
check("...without carrying it",
      "password" in (record.get("setup") or {}), False)
check("...and says whether it is sealed",
      record.get("setup_password_sealed"), True)

section("8. An empty password is not a password")
# A sealed blob is a dict and every dict is truthy, so the `bool(...)` this
# replaced would have drawn "(saved — leave blank to keep)" over a client who
# has never had one saved.
save("Empty Inc", login="only-a-login")
rec = seo.client_detail("Empty Inc")
check("no password on file reads as none", rec.get("setup_has_password"), False)
check("and an empty sealed blob does too",
      sealing.has_secret({"enc": True, "data": ""}), False)
check("while a real one reads as one",
      sealing.has_secret({"enc": True, "data": "x"}), True)

section("9. The panel says it, because an unsealed value looks like nothing")
from hub import diagnostics                                 # noqa: E402
row = diagnostics.check_seo_setup_passwords()
check("all sealed reads ok", row.state, "ok")
check("...and still says the old backups hold them",
      "backup" in row.detail.lower(), True)
jsonstore.write_json(store_path("Plain Co"),
                     {"client": "Plain Co", "setup": {"password": SECRET}},
                     indent=1)
row = diagnostics.check_seo_setup_passwords()
check("a plain password on disk warns", row.state, "warn")
check("...and names the client", "Plain Co" in row.detail, True)
check("...and the fix names the variable",
      "TOKEN_ENCRYPTION_KEY" in (row.fix or ""), True)
check("the row is registered on the panel",
      diagnostics.check_seo_setup_passwords in diagnostics.CHECKS, True)

section("10. One copy of the sealing, not a fourth")
# hub/cms_credentials.py, hub/ghl_oauth.py and check_reconciliation had each
# written this. A fourth caller is where it moves rather than being copied --
# the reason hub/jsonstore.py, hub/storage.py and hub/images.py exist.
from hub import cms_credentials                             # noqa: E402
check("cms_credentials seals through the shared module",
      cms_credentials._seal is sealing.seal, True)
check("...and unseals through it", cms_credentials._unseal is sealing.unseal, True)
src = (ROOT / "hub" / "cms_credentials.py").read_text(encoding="utf-8")
check("...and keeps no Fernet of its own", "Fernet(" in src, False)

shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{'-' * 60}\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
