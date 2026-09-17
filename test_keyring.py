"""hub/keyring.py — the keys that open a sealed value, and rotation.

    python3 test_keyring.py

Same shape as test_site_login.py: no pytest, no new dependencies, a throwaway
data directory, nothing touching /var/data.

## Why this file exists

Seven modules each built their own `Fernet(key)`, six over the same
`TOKEN_ENCRYPTION_KEY`. Each takes exactly one key, and that is the defect:
**it made that variable unrotatable.** Changing it — after a leak or a staff
departure, which is precisely when it must change — locked out every client's
WordPress application password, every client's own website login, the
GoHighLevel tokens and the Google tokens in the same moment, with no way back
but re-consenting and re-entering each one by hand.

## What is worth asserting

  * **A value sealed under the OLD key still opens once the new key leads.**
    That single property is the whole point; if it fails, a rotation is still
    an outage and this module bought nothing.

  * **A new seal always uses the NEWEST key.** Otherwise the old key can never
    be dropped and the rotation never finishes — it just becomes two keys
    forever.

  * **A blob no key can open is an ERROR, never an empty value.** The Google
    Finder lesson: "no credential" sends somebody to reconnect something that
    is connected.

  * **No key material in any answer.** `state()` and every error message are
    read for the key itself, not for the one field somebody remembered to
    strip.

  * **The stored shape did not change**, so records written before this module
    are still readable by it — asserted against bytes built the old way.
"""
import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

_TMP = tempfile.mkdtemp(prefix="s1-keyring-")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(_TMP, "t.db")
os.environ.setdefault("SECRET_KEY", "keyring-test")
os.environ.setdefault("PANEL_PASSWORD", "test")
os.environ["HUB_DATA_DIR"] = _TMP

from cryptography.fernet import Fernet                      # noqa: E402

OLD = Fernet.generate_key().decode("ascii")
NEW = Fernet.generate_key().decode("ascii")
THIRD = Fernet.generate_key().decode("ascii")

PASS = FAIL = 0


def check(label, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print("  ok   " + label)
    else:
        FAIL += 1
        print("  FAIL " + label + (("  — " + str(detail)) if detail else ""))


from hub import keyring                                     # noqa: E402

SECRET = "a-client-website-password"


def read_raw(path):
    """The bytes on disk. Asserting on the stored blob rather than on what an
    accessor returns is the difference between "it re-sealed" and "it still
    reads" -- the second is true either way."""
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return ""


def only(**env):
    """Set exactly this key configuration and nothing left over."""
    for name in (keyring.SINGULAR, keyring.PLURAL):
        os.environ.pop(name, None)
    for name, value in env.items():
        os.environ[name] = value


# ------------------------------------------------- the rotation itself
print("a key rotation is not an outage")
only(TOKEN_ENCRYPTION_KEY=OLD)
sealed_old = keyring.seal(SECRET)
check("a value seals under the key of the day", sealed_old.get("enc") is True)
check("and the ciphertext is not the value", SECRET not in json.dumps(sealed_old))

# The rotation: new key in front, old key kept behind it.
only(TOKEN_ENCRYPTION_KEYS=f"{NEW},{OLD}")
value, err = keyring.unseal(sealed_old)
check("a value sealed under the OLD key still opens once the new one leads — "
      "the property the whole module exists for",
      value == SECRET and not err, err or value)

sealed_new = keyring.seal(SECRET)
check("and a NEW seal uses the newest key, so the old one can be dropped",
      Fernet(NEW.encode()).decrypt(sealed_new["data"].encode()).decode() == SECRET)
bad = False
try:
    Fernet(OLD.encode()).decrypt(sealed_new["data"].encode())
except Exception:                                           # noqa: BLE001
    bad = True
check("the old key cannot open what the new key sealed", bad)

# Finishing the rotation: drop the old key. What was re-sealed still opens,
# what was never re-sealed is now honestly unreadable rather than silently gone.
only(TOKEN_ENCRYPTION_KEY=NEW)
value, err = keyring.unseal(sealed_new)
check("after dropping the old key, re-sealed values still open",
      value == SECRET and not err, err)
value, err = keyring.unseal(sealed_old)
check("and one never re-sealed reads as an ERROR, not as empty",
      value == "" and bool(err), (value, err))
check("naming the rotation as the reason", "rotated" in err.lower(), err)
check("and naming the variable that would recover it",
      keyring.PLURAL in err, err)


# ------------------------------------------------------- what it reports
print("\nwhat a panel may know")
only(TOKEN_ENCRYPTION_KEYS=f"{NEW},{OLD}")
st = keyring.state()
check("it says sealing works", st["configured"] is True)
check("it counts the keys", st["keys"] == 2, st)
check("and says a rotation is in progress", st["rotating"] is True)
check("no key material is in the state, at any depth",
      NEW not in json.dumps(st) and OLD not in json.dumps(st))

only(TOKEN_ENCRYPTION_KEY=NEW)
st = keyring.state()
check("one key is not a rotation", st["rotating"] is False and st["keys"] == 1)

only()
st = keyring.state()
check("with no key it says so", st["configured"] is False)
check("and names the variable to set", keyring.SINGULAR in st["note"])
plain = keyring.seal(SECRET)
check("a value saved then is stored in the clear", plain.get("enc") is False)
check("and says so rather than looking encrypted", plain["data"] == SECRET)

only(TOKEN_ENCRYPTION_KEY="not-a-valid-fernet-key")
st = keyring.state()
check("a key that is not a Fernet key is refused, not half-used",
      st["configured"] is False, st)
check("and the note says a credential saved now would be in the clear",
      "clear" in st["note"], st["note"])
check("no key material in that note either",
      "not-a-valid-fernet-key" not in json.dumps(st))

# One good key beside one bad one must not disable sealing: the deployment
# still has a working key, and refusing to seal would put credentials in the
# clear over a typo in a variable nobody is using yet.
only(TOKEN_ENCRYPTION_KEYS=f"{NEW},not-a-key")
st = keyring.state()
check("one usable key beside an unusable one still seals",
      st["configured"] is True and st["keys"] == 1, st)
check("and the unusable one is counted and named as ignored",
      st["unusable"] == 1 and "ignored" in st["note"], st)


# ----------------------------------------------- the spellings, together
print("\nboth spellings, and the one that must not be lost")
only(TOKEN_ENCRYPTION_KEYS=NEW, TOKEN_ENCRYPTION_KEY=OLD)
check("the plural leads, so new writes use it",
      keyring.key_values()[0] == NEW, keyring.key_values()[:1])
check("but the singular is KEPT, not replaced — everything already on the "
      "disk is sealed under it",
      OLD in keyring.key_values())
value, err = keyring.unseal(sealed_old)
check("so a record sealed under the singular still opens mid-rotation",
      value == SECRET and not err, err)

only(TOKEN_ENCRYPTION_KEYS=f"{NEW},{NEW},{OLD}")
check("a key repeated in the list is counted once",
      keyring.key_values() == [NEW, OLD], keyring.key_values())


# ------------------------------------------------------ re-seal, and shape
print("\nfinishing a rotation, and the shape on disk")
only(TOKEN_ENCRYPTION_KEYS=f"{NEW},{OLD}")
check("a blob under the old key is flagged for re-sealing",
      keyring.needs_reseal(sealed_old) is True)
check("one already under the newest key is not",
      keyring.needs_reseal(keyring.seal(SECRET)) is False)
only(TOKEN_ENCRYPTION_KEY=NEW)
check("with one key there is nothing to re-seal",
      keyring.needs_reseal(sealed_old) is False)
check("and an unreadable blob is not 'fixed' by re-sealing it",
      keyring.needs_reseal({"enc": True, "data": "nonsense"}) is False)
check("an unsealed blob needs nothing",
      keyring.needs_reseal({"enc": False, "data": "x"}) is False)

# A record written by the OLD code, byte for byte, must still open. If the
# shape had changed this module would have needed a migration and did not.
legacy = {"enc": True,
          "data": Fernet(NEW.encode()).encrypt(SECRET.encode()).decode("ascii")}
value, err = keyring.unseal(legacy)
check("a record written before this module still opens", value == SECRET, err)
check("a non-dict is refused rather than raising",
      keyring.unseal("nope") == ("", "No credential is stored."))


# ------------------------------------------- the stores that read through it
print("\nthe stores on the ring")
from hub import cms_credentials                             # noqa: E402

only(TOKEN_ENCRYPTION_KEY=OLD)
cms_credentials.save_site_login("Rotation Co", login="rep",
                                password=SECRET, actor="test")
only(TOKEN_ENCRYPTION_KEYS=f"{NEW},{OLD}")
state = cms_credentials.site_login_state("Rotation Co")
check("a client login sealed before a rotation is still readable after it",
      state["has_password"] is True and state["readable"] is True, state)
check("and the panel does not report it as an error", not state["error"], state)
check("the password is still nowhere in what the panel sees",
      SECRET not in json.dumps(state, default=str))

only(TOKEN_ENCRYPTION_KEY=THIRD)
state = cms_credentials.site_login_state("Rotation Co")
check("a key rotated away entirely reads as unreadable, never as 'no login'",
      state["has_password"] is True and state["readable"] is False, state)

only(TOKEN_ENCRYPTION_KEY=OLD)
check("cms_credentials reads its encryption state from the ring, so the panel "
      "and /status cannot drift into two answers",
      cms_credentials.encryption_state() == keyring.state())


# --------------------------------------------- a rotation that can FINISH
print("\na rotation that can be completed, not just survived")
# needs_reseal() had no caller when it shipped. Without one, the old key can
# never be dropped: everything stays readable only because the ring still
# carries it, which is a rotation that never ends. This is the assertion that
# says the third step of it happens.
only(TOKEN_ENCRYPTION_KEY=OLD)
cms_credentials.save_site_login("Rotate Co", login="rep", password=SECRET,
                                actor="test")
_path = cms_credentials._path("Rotate Co", cms_credentials.SITE_LOGIN)
_before = json.loads(read_raw(_path))["secret"]["data"]

only(TOKEN_ENCRYPTION_KEYS=f"{NEW},{OLD}")
_st = cms_credentials.site_login_state("Rotate Co")
check("the record is readable during the rotation", _st["readable"] is True)
_after = json.loads(read_raw(_path))["secret"]["data"]
check("and reading it RE-SEALS it under the newest key", _before != _after)

only(TOKEN_ENCRYPTION_KEY=NEW)
check("so with the old key dropped entirely it still opens — the rotation "
      "can be finished rather than carried forever",
      cms_credentials.site_login_state("Rotate Co")["readable"] is True)

# A read that writes on every pass is the hub/ad_assets.py defect this repo
# records paying for.
_settled = read_raw(_path)
cms_credentials.site_login_state("Rotate Co")
check("a later read rewrites nothing", read_raw(_path) == _settled)

# The WordPress kind goes through get(), a different call site.
only(TOKEN_ENCRYPTION_KEY=OLD)
cms_credentials.save("Rotate Co", cms_credentials.WORDPRESS,
                     rest_root="https://x.test/wp-json/", username="u",
                     app_password="abcd EFGH ijkl MNOP", actor="test")
_wp = cms_credentials._path("Rotate Co", cms_credentials.WORDPRESS)
_wp_before = json.loads(read_raw(_wp))["secret"]["data"]
only(TOKEN_ENCRYPTION_KEYS=f"{NEW},{OLD}")
check("get() returns the application password during a rotation",
      cms_credentials.get("Rotate Co")["app_password"] == "abcdEFGHijklMNOP")
check("and re-seals that record too", json.loads(read_raw(_wp))["secret"]["data"] != _wp_before)
only(TOKEN_ENCRYPTION_KEY=NEW)
check("which survives the old key being dropped",
      cms_credentials.get("Rotate Co").get("app_password") == "abcdEFGHijklMNOP")

# An unreadable record must not be "repaired" by re-sealing it, and must not
# lose what is stored.
only(TOKEN_ENCRYPTION_KEY=THIRD)
_sealed_away = read_raw(_path)
cms_credentials.site_login_state("Rotate Co")
check("a record no key can open is left exactly as it is, not rewritten",
      read_raw(_path) == _sealed_away)


# ------------------------------------------- every store, through a rotation
print("\nevery store survives a rotation")
from modules.skills360 import store as _sk                 # noqa: E402
from modules.youtube_studio import store as _yt            # noqa: E402

only(TOKEN_ENCRYPTION_KEY=OLD)
_sk_blob = _sk.seal(SECRET)
_yt_blob = _yt.encrypt(SECRET)

only(TOKEN_ENCRYPTION_KEYS=f"{NEW},{OLD}")
check("skills360 reads a secret sealed before the rotation",
      _sk.unseal(_sk_blob) == SECRET)
check("youtube_studio reads a token sealed before the rotation",
      _yt.decrypt(_yt_blob) == SECRET)

# Re-sealed during the rotation, then the old key is dropped. If a new write
# had used the OLD key the rotation could never finish, and this is what says
# so rather than the prose above.
_sk_new, _yt_new = _sk.seal(SECRET), _yt.encrypt(SECRET)
only(TOKEN_ENCRYPTION_KEY=NEW)
check("a skills360 secret re-sealed mid-rotation opens under the new key alone",
      _sk.unseal(_sk_new) == SECRET)
check("and a youtube_studio token does too", _yt.decrypt(_yt_new) == SECRET)
check("while the un-re-sealed one is now unreadable rather than silently empty-"
      "looking to the panel — skills360 reports token_readable separately",
      _sk.unseal(_sk_blob) == "")

# The contracts each module chose are NOT flattened by moving to the ring.
# youtube_studio and google_finder refuse rather than write an OAuth token in
# the clear; that refusal is the thing a shared helper could most easily have
# smoothed away.
only()
refused = ""
try:
    _yt.encrypt(SECRET)
except ValueError as exc:
    refused = str(exc)
check("with no key at all youtube_studio REFUSES rather than storing in the "
      "clear — the contract the shared helper must not soften",
      "not configured" in refused, refused or "it encrypted!")

from modules.google_finder import app as _gf               # noqa: E402

refused = ""
try:
    _gf._fernet()
except RuntimeError as exc:
    refused = str(exc)
check("and google_finder refuses too, for its OAuth refresh tokens",
      "not configured" in refused, refused or "it returned a cipher!")

only(TOKEN_ENCRYPTION_KEYS=f"{NEW},{OLD}")
check("google_finder's health check reads the RING, not one spelling — it "
      "answered False mid-rotation before this",
      _gf._keyring_configured() is True)


# ------------------------------------------------------------- the audit
print("\nwhat has not moved across yet")
from hub import integrity                                   # noqa: E402

files = {f["file"] for f in integrity.check_own_fernet()}
check("hub/keyring.py is not reported as drift from itself",
      "hub/keyring.py" not in files, files)
check("nor is cms_credentials, which now reads the ring",
      "hub/cms_credentials.py" not in files, files)
check("nor ghl_oauth", "hub/ghl_oauth.py" not in files, files)
check("a test building a key for a fixture is not drift",
      not any(os.path.basename(f).startswith("test_") for f in files), files)
check("nor google_finder, skills360 or youtube_studio, which moved in this "
      "change — the list is empty now, and an empty audit is only worth "
      "anything because the rotation itself is asserted above",
      files == set(), files)
# An empty audit is worth nothing unless it can be shown to detect something.
# "Every module has moved across" and "the scan silently stopped scanning"
# render identically, and this repo has paid for that difference before. So a
# file with the defect in it is planted, found, and removed.
_planted = os.path.join(ROOT, "modules", "_keyring_drift_probe.py")
try:
    with open(_planted, "w", encoding="utf-8") as _fh:
        _fh.write("from cryptography.fernet import Fernet\n\n\n"
                  "def cipher():\n    return Fernet(b'x')\n")
    _probe = {f["file"] for f in integrity.check_own_fernet()}
    check("a module building its own Fernet IS found — the empty list above is "
          "a real answer and not a scan that has stopped running",
          "modules/_keyring_drift_probe.py" in _probe, _probe)
finally:
    if os.path.exists(_planted):
        os.remove(_planted)
check("and the probe is gone again, so the repo is left as it was",
      not os.path.exists(_planted))
check("with it gone the audit is empty once more",
      integrity.check_own_fernet() == [])
check("it is registered on /api/integrity", any(
    g["key"] == "own_fernet" for g in integrity.run()["groups"]))


print(f"\n{PASS} passed, {FAIL} failed")
shutil.rmtree(_TMP, ignore_errors=True)
sys.exit(1 if FAIL else 0)
