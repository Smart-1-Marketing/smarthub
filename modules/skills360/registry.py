"""The skills a client can have switched on.

One table, read by the 360 Skills tool (to draw the setup form), by
``/api/c360`` (to say which are active) and by Client 360 (to know which
card each key gates). A new skill is a row here plus a verifier; the
rest -- activation, share links, the tool's UI, the Client 360 gate --
comes for free. That is what lets a later skill learn from this one:
the shape is fixed, only the source changes.

``fields`` is what the rep is asked for. A field marked ``secret`` is sealed
by the store and never read back to a page; blank on a later save keeps the
stored value. ``verify(client, fields)`` must prove the skill works before
``activate`` is allowed -- a skill that cannot be verified is not switched on.
"""
from __future__ import annotations

SKILLS: tuple[dict, ...] = (
    {
        "key": "ecwid",
        "label": "Ecwid Ecommerce",
        "card": "Ecommerce",
        "icon": "&#128722;",
        "blurb": "Orders, revenue, discounts, abandoned carts, top products and a twelve-month "
                 "trend from the client's Ecwid store -- the hotsheet, on Client 360 and on a "
                 "link the client can keep open.",
        "fields": (
            {"name": "store_id", "label": "Ecwid Store ID", "placeholder": "111281497",
             "help": "Ecwid admin › Settings › General. Digits only."},
            {"name": "token", "label": "Secret API token", "secret": True, "placeholder": "secret_…",
             "help": "Ecwid admin › Apps › My apps › the custom app › Secret token. Not the public "
                     "token, not the OAuth client secret."},
        ),
        "shareable": True,
        "share_label": "Client hotsheet link",
    },
    {
        "key": "email",
        "label": "Email Creator",
        "card": "Email Creator",
        "icon": "&#9993;",
        "blurb": "Compose an on-brand email on Client 360, save it into the client's Smart 1 Suite "
                 "email builder, send yourself a test, or send it to chosen Suite contacts -- from "
                 "the client's own sub-account, once it is confirmed able to send.",
        "fields": (
            {"name": "from_name", "label": "From name", "placeholder": "Buckeye Lake Winery",
             "help": "Blank uses the Suite account's business name."},
            {"name": "from_email", "label": "From address", "placeholder": "hello@client.com",
             "help": "Blank uses the Suite account's business email. Its domain is what the "
                     "sending-domain check reads."},
        ),
        "shareable": False,
    },
)

BY_KEY = {s["key"]: s for s in SKILLS}


def public() -> list[dict]:
    """The table for a page: no callables, fields flagged rather than valued."""
    return [{k: v for k, v in s.items()} for s in SKILLS]


def known(key: str) -> bool:
    return key in BY_KEY
