"""What this Hub signs with, and what happens when nothing is set.

Two defects, both live, both reproduced here before they were fixed.

**A literal fallback is a forgeable token.** `hub/users_routes.py` signs the
per-account session cookie -- user id, role, must-change-password -- read by
the middleware in `wsgi.py` in front of every mounted module, and with no
`SECRET_KEY` it fell back to the literal `"dev-only"`. A cookie signed with
that string claiming `{"r": "admin", "c": false}` was accepted as an Admin
session belonging to no account. Three more call sites had the same shape.

**And the safe fallback was not shared.** `hub/auth.py` and `hub/identity.py`
sign the *same salt* and each generated its own `secrets.token_hex(32)`, so
with nothing set they disagreed inside one process and each refused the
other's cookie -- while identity.py's docstring claimed they could not
disagree.

The sweep is the part that matters. A test naming the four call sites we fixed
proves nothing about the ninth, so `no_literal_fallbacks()` reads the **AST**
of every file that signs and requires the secret to come from
`hub/signing.py`. Prose is not a call site: `hub/signing.py` itself quotes all
four literals to explain them, so the check reads calls rather than text.

And *"every file that signs"* is not *"every file that imports a
serializer"* -- selecting on the import is how this sweep quietly stops
sweeping, because migrating a call site is exactly what removes it. Trimming
the imports the rewire left unused dropped its scope from eight files to
three, silently, and the count was the only thing that noticed. It selects on
the serializer name **or** a reach into `hub/signing.py`, and the eight sites
are named as well as counted.
"""
from __future__ import annotations

import ast
import os
import pathlib
import sys

for _k in ("SECRET_KEY", "FLASK_SECRET_KEY", "SESSION_SECRET"):
    os.environ.pop(_k, None)
os.environ.setdefault("CLIENTS_DATA_DIR", "tests/fixtures/clients")

ROOT = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

_passed = 0
_failed = 0


def check(label, cond, detail=""):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  ok    {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}" + (f"  — {detail}" if detail else ""))


def section(title):
    print(f"\n{title}")


# --------------------------------------------------------------------------
# The sweep: nothing signs with a string out of its own source
# --------------------------------------------------------------------------
# The file that defines the rule, and the test that asserts it, both name the
# literals in order to explain them. Neither is a call site.
_SWEEP_EXEMPT = {"hub/signing.py", "test_signing.py"}

_SERIALIZERS = {"URLSafeSerializer", "URLSafeTimedSerializer"}


# Selecting on the serializer name alone is how this sweep quietly stops
# sweeping: migrating a call site to hub/signing.py removes its
# `URLSafeTimedSerializer` import, so the file it was written to audit drops
# out of its own scope. Found by trimming those imports and watching the count
# fall from eight to three. A file that signs is one that constructs a
# serializer OR reaches this module -- either way it is in scope, and only the
# first kind can carry a literal.
_REACHES_SIGNING = ("from hub import signing", "hub.signing", "from . import signing")


def _signing_files():
    """Every file that signs, however it gets its secret."""
    out = []
    for path in sorted(list(ROOT.glob("hub/**/*.py")) + list(ROOT.glob("modules/**/*.py"))):
        rel = path.relative_to(ROOT).as_posix()
        if "_attic" in rel or rel in _SWEEP_EXEMPT:
            continue
        try:
            src = path.read_text(errors="ignore")
        except Exception:                                   # noqa: BLE001
            continue
        if not any(n in src for n in _SERIALIZERS) \
                and not any(n in src for n in _REACHES_SIGNING):
            continue
        out.append((rel, src))
    return out


# Named, because a set of the right size and the wrong contents is the same
# failure one step on. These are the eight signing sites the module was
# written for; the sweep has to still be looking at each of them.
_KNOWN_SIGNING_SITES = (
    "hub/auth.py",
    "hub/client_portal.py",
    "hub/identity.py",
    "hub/quickbooks.py",
    "hub/users_routes.py",
    "modules/social_planner/links.py",
    "modules/suite_panel/app.py",
)


def no_literal_fallbacks():
    """Serializer constructions whose secret is a string literal in the file.

    The AST rather than the text, for the reason `hub/config.py`'s drift check
    gives: this repo explains its own traps by quoting them, and a text match
    reports the explanation as the defect.
    """
    findings = []
    for rel, src in _signing_files():
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = getattr(fn, "id", None) or getattr(fn, "attr", None)
            if name not in _SERIALIZERS or not node.args:
                continue
            findings += [f"{rel}:{node.lineno}"
                         for lit in _literal_secrets(node.args[0]) if lit]
    return findings


def _literal_secrets(arg):
    """Every string constant reachable as the secret of this construction.

    `a or b or "lit"` is a BoolOp, which is exactly the shape all four of the
    old fallbacks had; a bare `"lit"` is a Constant. Anything else -- a name, a
    call -- is resolved elsewhere and is not this check's business.
    """
    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
        return [arg.value]
    if isinstance(arg, ast.BoolOp):
        out = []
        for v in arg.values:
            out += _literal_secrets(v)
        return out
    return []


section("Nothing signs with a literal out of its own source")
_files = _signing_files()
_seen = {rel for rel, _ in _files}
check("the sweep found the files that sign", len(_files) >= len(_KNOWN_SIGNING_SITES),
      f"only {len(_files)}")
_missing = [f for f in _KNOWN_SIGNING_SITES if f not in _seen]
check("and it still covers every site this module was written for",
      not _missing, "dropped out of scope: " + ", ".join(_missing))
_lits = no_literal_fallbacks()
check("no serializer is constructed with a string literal secret", not _lits,
      "; ".join(_lits))

# And the sweep bites: hand it the shape that was live.
_BAD = '''
from itsdangerous import URLSafeTimedSerializer
def _s():
    return URLSafeTimedSerializer(secret or "dev-only", salt="x")
'''
_tree = ast.parse(_BAD)
_found = []
for _n in ast.walk(_tree):
    if isinstance(_n, ast.Call) and getattr(_n.func, "id", "") in _SERIALIZERS and _n.args:
        _found += _literal_secrets(_n.args[0])
check("and it names the shape that was live", "dev-only" in _found,
      f"got {_found}")


# --------------------------------------------------------------------------
# The secret itself
# --------------------------------------------------------------------------
from hub import signing                                     # noqa: E402

section("A secret, an ephemeral one, and never a literal")
_val, _src = signing.secret()
check("with nothing set the source is 'absent'", _src == signing.ABSENT, _src)
check("and the value is still long enough to sign with", len(_val) >= 32)
check("and it is none of the literals it replaced",
      _val not in signing._KNOWN_WEAK)
check("stable() is False, so a caller can say a link will not survive a restart",
      signing.stable() is False)
check("report() calls that an error rather than a warning",
      signing.report()["state"] == "error")
check("and the report names what it costs a client's share link",
      "share link" in signing.report()["detail"])

section("The same ephemeral secret for every caller in a process")
check("two reads agree", signing.value() == signing.value())
check("and serializers over one salt round-trip",
      signing.timed_serializer("t").loads(
          signing.timed_serializer("t").dumps({"a": 1}), max_age=99) == {"a": 1})

section("A placeholder is set and is still not a secret")
check("the env.example value is weak", signing.is_weak("change-me-to-something-strong"))
check("so are the four literals this replaced",
      all(signing.is_weak(x) for x in ("dev-only", "s1hub", "s1hub-social-dev",
                                       "smart1-client-links-development")))
check("a real secret is not", not signing.is_weak("a" * 40))
check("and neither is an empty one, which is absent rather than weak",
      not signing.is_weak(""))


# --------------------------------------------------------------------------
# The two defects, driven rather than described
# --------------------------------------------------------------------------
from itsdangerous import URLSafeTimedSerializer                 # noqa: E402
import hub.auth as _auth                                        # noqa: E402
import hub.identity as _identity                                # noqa: E402
import hub.users_routes as _users                               # noqa: E402

section("A forged session cookie is refused")
_forged = URLSafeTimedSerializer("dev-only", salt="s1hub-user").dumps(
    {"u": 1, "e": "nobody@example.test", "r": "admin", "s": 1, "c": False})
_got = _users.session_from_environ({"HTTP_COOKIE": f"{_users.COOKIE_NAME}={_forged}"})
check("the middleware reader refuses a cookie signed with the old literal",
      _got == {}, f"accepted {_got}")

section("Two readers of one salt agree")
# Asserted through the shared reading and through the serializers, never
# through a module global kept alive for the test: hub/auth.py had a `_SECRET`
# whose only reader was this line, so the assertion was propping itself up.
# A review bot named it as dead and was right about more than it could see.
check("identity resolves the shared secret rather than one of its own",
      _identity._secret() == signing.value())
_tok = _auth._serializer.dumps({"hello": 1})
try:
    _ok = _identity._serializer.loads(_tok, max_age=99999) == {"hello": 1}
except Exception:                                               # noqa: BLE001
    _ok = False
check("so a cookie issued by one verifies in the other", _ok)


# --------------------------------------------------------------------------
# What the status page says
# --------------------------------------------------------------------------
section("The status row says the true thing in all three states")


def _secret_row(env_value):
    """settings.status()'s Secret key row under a given environment."""
    import subprocess
    code = ("from hub.config import settings;"
            "r=[x for x in settings.status() if x['name']=='Secret key'][0];"
            "print(r['state']);print(r['note'])")
    env = dict(os.environ)
    for k in ("SECRET_KEY", "FLASK_SECRET_KEY", "SESSION_SECRET"):
        env.pop(k, None)
    if env_value is not None:
        env["SECRET_KEY"] = env_value
    out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                         text=True, env=env, cwd=str(ROOT))
    lines = [ln for ln in out.stdout.splitlines() if ln.strip()]
    return (lines[0], " ".join(lines[1:])) if lines else ("", "")


_state, _note = _secret_row(None)
check("unset is an error", _state == "error", f"{_state}: {_note[:80]}")
_state, _note = _secret_row("change-me-to-something-strong")
check("a placeholder is an error too, where it used to read ok",
      _state == "error", f"{_state}: {_note[:80]}")
check("and it says which of the two it is",
      "example or development value" in _note, _note[:120])
_state, _note = _secret_row("k" * 40)
check("a real secret is ok", _state == "ok", f"{_state}: {_note[:80]}")
check("and the row no longer claims everyone is logged out by every restart",
      "logged out by every restart" not in _note, _note[:120])


print("\n" + "-" * 60)
print(f"{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
