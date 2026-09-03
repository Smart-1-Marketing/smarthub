"""One signed link per client account, and the four pages behind it.

A location manager, an owner and whoever answers the reviews all get the same
link. They pick their location on the form; everything lands in one shared
queue. One token per **client**, never one per location, because the whole
point is that the queue is shared — a link per location is the inbox-per-shop
arrangement this module exists to replace, reissued as URLs.

## The token is derived, not stored

`itsdangerous` over the Hub's own `SECRET_KEY`, carrying the client's name and
website. Nothing is written when a link is made, so a link cannot be created
twice, cannot go missing, and is the same string every time it is generated —
which matters, because it is pasted into an email once and lived with for a
year.

Two consequences, both deliberate:

* **The client's own name is inside their own link.** Anybody who base64-
  decodes it sees the business it belongs to. That is the business reading
  their own link, and it buys a token that never needs a lookup and cannot be
  enumerated into somebody else's.
* **Revocation needs somewhere to write.** So there is exactly one small
  file for it, keyed by normalised name and carrying the name — turning a
  link off is rare and deliberate, and a "revoked" list is a much smaller
  thing to keep than a token table.

## What a revoked link says

The same thing a link that never existed says. `hub/ads_builder`'s client
estimate settled this: a client-facing URL that distinguishes "expired" from
"never real" tells whoever is probing which tokens are worth trying.
"""
from __future__ import annotations

import os

from itsdangerous import URLSafeSerializer

from hub import jsonstore
from hub.client_key import normalise_name

REVOKED_FILE = "link_revocations.json"

# Client-facing pages, and what each is for. Read by the module (to route),
# by the client pages (to link between themselves) and by the staff screen
# that hands the links out, so a page added later cannot be one nobody can
# reach — the failure CLAUDE.md counts six of.
PAGES: dict[str, dict] = {
    "request": {"label": "Send us something to post",
                "help": "A photo, an offer, an event — with the date you need "
                        "it live."},
    "ideas": {"label": "Ideas to swipe through",
              "help": "One line each. Like or Pass; it steers what we suggest "
                      "next."},
    "approve": {"label": "Posts waiting for you",
                "help": "Approve, ask for a change, or skip."},
    "preferences": {"label": "What to write about",
                    "help": "Topics you want, topics to leave alone, and "
                            "anything standing."},
}


def _secret() -> str:
    try:
        from hub.config import settings
        secret = settings.secret_key
    except Exception:                                     # noqa: BLE001
        secret = ""
    # Every spelling in the group, not one: hub/config.py resolves all three
    # and a fallback that names a single name is how a deployment setting the
    # other one silently gets an ephemeral secret — which here would mean
    # every client's link changing on every restart, with nothing saying so.
    # Through hub/signing.py, which resolves every spelling and, with none
    # set, hands back an ephemeral secret rather than the literal that used to
    # sit here. A literal is the same string on every deployment, so a link to
    # any client's approvals page could be minted by anybody reading this file;
    # an ephemeral one means the links stop resolving after a restart, which
    # `signing.report()` says out loud on /status rather than leaving to be
    # discovered by a client whose page went quiet.
    from hub import signing as _signing
    if secret and not _signing.is_weak(secret):
        return secret
    return _signing.value()


def _serializer() -> URLSafeSerializer:
    # No expiry. A link on a client's intranet page has to keep working, and
    # a link that silently stops is one nobody reports — they just stop
    # sending photographs.
    return URLSafeSerializer(_secret(), salt="s1hub-social-client")


def token_for(client: str, url: str = "") -> str:
    client = str(client or "").strip()[:200]
    if not client:
        return ""
    return _serializer().dumps([client, str(url or "").strip()[:300]])


def client_for(token: str) -> tuple[str, str] | None:
    """(name, url) for a valid, unrevoked token; None for anything else."""
    try:
        payload = _serializer().loads(str(token or ""))
    except Exception:                                     # noqa: BLE001
        return None
    if not isinstance(payload, list) or not payload or not str(payload[0] or "").strip():
        return None
    name = str(payload[0])[:200]
    url = str(payload[1])[:300] if len(payload) > 1 else ""
    if is_revoked(name):
        return None
    return name, url


# ------------------------------------------------------------------ revoking
def _path() -> str:
    return os.path.join(jsonstore.data_dir("social"), REVOKED_FILE)


def _revocations() -> list[dict]:
    blob = jsonstore.read_json(_path(), default=None)
    if isinstance(blob, dict) and isinstance(blob.get("revoked"), list):
        return [r for r in blob["revoked"] if isinstance(r, dict)]
    return []


def is_revoked(client: str) -> bool:
    key = normalise_name(str(client or ""))
    if not key:
        return True
    return any(normalise_name(str(r.get("client") or "")) == key
               for r in _revocations())


def revoke(client: str, actor: str = "") -> bool:
    from datetime import datetime, timezone
    if is_revoked(client):
        return True
    rows = _revocations()
    rows.append({"client": str(client or "").strip()[:200],
                 "by": str(actor or "")[:120],
                 "at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
    return jsonstore.write_json(_path(), {"revoked": rows[:2000]}, indent=1)


def restore(client: str) -> bool:
    key = normalise_name(str(client or ""))
    rows = [r for r in _revocations()
            if normalise_name(str(r.get("client") or "")) != key]
    return jsonstore.write_json(_path(), {"revoked": rows}, indent=1)


# ------------------------------------------------------------------ addresses
def _origin(value: str) -> str:
    """scheme://host, with any path thrown away.

    A dispatcher-mounted module's `request.url_root` carries its own mount, so
    pasting this module's path onto it builds `/tools/social/tools/social/…` —
    a 404 the client meets and nobody else does. `PUBLIC_BASE_URL` is
    documented as an origin and has held a whole callback URL before now, so
    it is trimmed rather than trusted. `modules/image_picker/provisioning.py`
    says the same thing about the same trap.
    """
    raw = str(value or "").strip().rstrip("/")
    if not raw:
        return ""
    if "//" not in raw:
        return raw.split("/")[0]
    scheme, _, rest = raw.partition("//")
    return f"{scheme}//{rest.split('/')[0]}"


def link(client: str, url: str = "", page: str = "request",
         base: str = "", mount: str = "/tools/social") -> str:
    if page not in PAGES:
        page = "request"
    token = token_for(client, url)
    if not token:
        return ""
    root = _origin(base)
    if not root:
        try:
            from hub.config import settings
            root = _origin(settings.public_base_url or "")
        except Exception:                                 # noqa: BLE001
            root = ""
    return f"{root}{mount}/c/{token}/{page}"


def all_links(client: str, url: str = "", base: str = "") -> list[dict]:
    """Every client-facing page, with its address and what it is for.

    The set is drawn from `PAGES` rather than written out again on the staff
    screen: a page added here appears there without a template edit, which is
    how the Digital Dictionary sat served and unreachable for months while a
    dashboard row promised it.
    """
    return [{"page": key, "label": meta["label"], "help": meta["help"],
             "url": link(client, url, key, base)}
            for key, meta in PAGES.items()]
