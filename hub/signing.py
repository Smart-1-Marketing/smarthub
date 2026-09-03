"""What this Hub signs with, decided once.

Eight things here are signed with `itsdangerous` — the shared-password
session, the per-account session cookie, the QuickBooks OAuth state, the Suite
install state, a client's social links, a client's approvals page — and each
resolved the secret its own way. Two of the six resolutions were right and
four were not, in the same direction, and the difference is what happens when
no secret is set.

**`hub/auth.py` and `hub/identity.py` fall back to a random ephemeral
secret**, and auth.py's comment says exactly why: everybody re-logs-in after a
restart, which is noticed and is not forgeable. It fails closed.

**The other four fall back to a literal in the source** — `"dev-only"`,
`"smart1-client-links-development"`, `"s1hub"`, `"s1hub-social-dev"` — and a
literal fails *open*: it is the same string on every deployment, so anybody
who can read the source can mint a token. The worst of the four is
`hub/users_routes.py`, which signs the **per-account session cookie** carrying
the user id, the role and the must-change-password flag, read by the
middleware in `wsgi.py` in front of every module. With `SECRET_KEY` unset, a
cookie signed `"dev-only"` claiming `{"r": "admin", "c": false}` was accepted
as an Admin session belonging to no account. That is not hypothetical: it was
minted and accepted before this module was written.

**And the safe half was not safe either.** `auth.py` and `identity.py` sign
the *same salt* (`s1hub-session`) and each generated its **own**
`secrets.token_hex(32)` at import — so with no secret set they disagree inside
a single process, and a cookie issued by one is refused by the other, silently.
identity.py's own docstring claims the opposite ("both read hub.config so
neither can know a spelling the other does not"), which is true of the
spellings and was false of the fallback. The ephemeral secret is resolved
**once per process** here, so two readers of one salt agree by construction.

Three rules, and each is a way the old shape went wrong.

**Never a literal.** `secret()` answers with a real secret or with an
ephemeral one, and there is no third branch. A token that cannot be verified
after a restart is a re-login or a share link that stops resolving — both
noticed, neither forgeable. A token anybody can forge is noticed by nobody.

**A placeholder is not a secret.** `hub/config.py` has detected the
env.example values all along and no signing site asked it: a variable left at
its example value is a value everybody has, so it is read here as absent
rather than used. The four literals this module replaced are on that list too
— they were real fallbacks in real code, so from today they are real known
secrets — and nothing speculative is added beside them, the `ALIASES` rule.

**Say what it costs, because it is not the same cost for everybody.** An
ephemeral secret means a re-login for a session cookie and a **dead link on
somebody else's website** for a client's social or approvals page. The second
is the one that must not be silent, so `report()` names which state we are in
and what it costs, and `/status` prints it. `hub/config.py`'s own status row
has said *"sessions are not signed without it, so everyone is logged out by
every restart"* the whole time — a true description of two of the eight and a
wrong one about the four that were forgeable instead.

Nothing in here may raise. A signing helper that breaks the page it protects
is worse than the drift it replaced, so every entry point answers.
"""
from __future__ import annotations

import os
import secrets

from itsdangerous import URLSafeSerializer, URLSafeTimedSerializer

# The spellings hub/config.py resolves, restated only for the fallback path
# where config itself could not be imported. Naming one of the three is how a
# deployment setting another silently drops to an ephemeral secret.
_ENV_NAMES = ("SECRET_KEY", "FLASK_SECRET_KEY", "SESSION_SECRET")

# Values that are set and still are not secrets. The first group is what
# hub/config.py already calls a placeholder; the rest are the literals this
# module replaced, which are in the source and therefore known to anybody who
# can read it. Deliberately not speculative -- a guessed entry turns a working
# deployment into a finding, which is how a check gets switched off.
_KNOWN_WEAK = (
    "change-me-to-something-strong",
    "dev-only-change-me",
    "dev-only",
    "s1hub",
    "s1hub-social-dev",
    "s1hub-suite-oauth",
    "smart1-client-links-development",
)

CONFIGURED = "configured"
PLACEHOLDER = "placeholder"
ABSENT = "absent"

# Resolved once, so every caller in this process shares it. Two readers of one
# salt must agree, and they did not when each generated its own.
_EPHEMERAL = secrets.token_hex(32)


def _raw() -> str:
    """Whatever is set, through hub.config so all three spellings resolve."""
    try:
        from hub.config import settings as _cfg
        value = _cfg.secret_key or ""
    except Exception:                                       # noqa: BLE001
        value = ""
    if not value:
        for name in _ENV_NAMES:
            value = os.environ.get(name) or ""
            if value:
                break
    # Render stores quotes literally, which hub/config.py already warns about.
    return (value or "").strip().strip('"').strip("'")


def is_weak(value: str) -> bool:
    """A value that is set and is still not a secret."""
    v = (value or "").strip().strip('"').strip("'")
    if not v:
        return False
    if v in _KNOWN_WEAK:
        return True
    try:
        from hub.config import settings as _cfg
        return bool(_cfg.is_placeholder(v))
    except Exception:                                       # noqa: BLE001
        return False


def secret() -> tuple[str, str]:
    """The signing secret and where it came from.

    `(value, CONFIGURED)` for a real one; `(ephemeral, PLACEHOLDER)` when a
    variable is set to a value everybody has; `(ephemeral, ABSENT)` when none
    is set. The two are kept apart because they send somebody to different
    places -- change the variable, or set it.
    """
    try:
        raw = _raw()
    except Exception:                                       # noqa: BLE001
        return _EPHEMERAL, ABSENT
    if not raw:
        return _EPHEMERAL, ABSENT
    if is_weak(raw):
        return _EPHEMERAL, PLACEHOLDER
    return raw, CONFIGURED


def value() -> str:
    """The secret alone, for a caller that has no use for the provenance."""
    return secret()[0]


def stable() -> bool:
    """Is a token minted now still valid after a restart?

    False on an ephemeral secret. The callers that hand a token to somebody
    else -- a client's social page, the approvals index -- want this before
    they promise a link keeps working.
    """
    return secret()[1] == CONFIGURED


def serializer(salt: str) -> URLSafeSerializer:
    return URLSafeSerializer(value(), salt=salt)


def timed_serializer(salt: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(value(), salt=salt)


def report() -> dict:
    """What state signing is in, and what that costs, for a status screen."""
    _, source = secret()
    if source == CONFIGURED:
        return {"state": "ok", "source": source,
                "detail": "Signed sessions and client share links survive a restart."}
    fix = ("SECRET_KEY is set to an example or development value, which is a "
           "value everybody has" if source == PLACEHOLDER else
           "No SECRET_KEY, FLASK_SECRET_KEY or SESSION_SECRET is set")
    return {
        "state": "error", "source": source,
        "detail": (f"{fix}, so this Hub is signing with a secret generated at "
                   "start-up. Nothing can be forged, and nothing survives a "
                   "restart: everyone is signed out, and every client share "
                   "link -- social approvals, the client links page -- stops "
                   "resolving until it is re-sent."),
    }
