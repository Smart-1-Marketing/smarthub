"""
Where a client's own files can come from.

The upload panel used to offer three: their computer, their camera, and a web
address. That is the shortest possible list, and it is the wrong one — a client
asked for "your photos" reaches for the place the photos already are, which is
almost never a folder on a laptop. It is their phone's Google Photos, the
agency's Dropbox, the Drive folder the last designer left them, or their own
Instagram and Facebook pages. Offering only "choose a file" means somebody has
to download twelve photos out of Instagram and upload them again, and what
actually happens is that they do not.

Cloudinary's own widget already speaks all of those; nothing had switched them
on. `PICKER_UPLOAD_SOURCES` defaulted to `local,camera,url`, so the default was
the poorest thing on offer.

Three rules here, each a way to be confidently wrong:

* **A source is offered from this catalogue or not at all.** An unknown string
  passed through to the widget is a tab that either does not draw or draws and
  fails, and both look like our page being broken. A name we do not recognise
  is dropped and *named* on the admin page rather than forwarded — the same
  answer `hub/knack_websites.py` gives a value Knack would refuse.

* **A billed add-on is off until somebody turns it on.** Shutterstock, Getty,
  iStock and Unsplash are Cloudinary add-on subscriptions. Listed without the
  subscription behind them, the client gets a tab that consents and then fails
  for a reason that is nothing to do with them — which is exactly why Google
  Ads came off the Google Access list. They are named here with the variable
  that enables them, so a screen can say what is available instead of
  pretending the choice was never there.

* **A per-source key is an override, not a requirement.** Google Drive, Dropbox
  and Instagram work on Cloudinary's own registered apps, and **the client signs
  in to their own account either way** — that is the whole point, and the Hub
  never sees the password. Supplying our own client id only changes the name on
  the consent screen and lifts the referrer restrictions; it does not gate the
  source, so a missing key is never a reason to hide a tab and never something
  a staff screen has to report.
"""

from __future__ import annotations

import os

# key            what the widget calls it
# label          what the client reads on the tab
# what           one line for staff: where these files come from
# addon          True when it needs a paid Cloudinary add-on subscription
# env            optional per-source credential, or ""
# option         the widget option that credential fills in
CATALOGUE: list[dict] = [
    {"key": "local", "label": "My files", "always": True,
     "what": "Their computer, or their phone's photo roll.",
     "addon": False, "env": "", "option": ""},
    {"key": "camera", "label": "Camera", "always": True,
     "what": "Take the photo there and then — the one that gets used on site.",
     "addon": False, "env": "", "option": ""},
    {"key": "url", "label": "Web address", "always": True,
     "what": "Paste a link to a file that is already online.",
     "addon": False, "env": "", "option": ""},
    {"key": "image_search", "label": "Image search", "always": False,
     "what": "Search the web from inside the widget.",
     "addon": False, "env": "", "option": ""},
    {"key": "google_drive", "label": "Google Drive", "always": False,
     "what": "The Drive folder the last designer left them. They sign in and "
             "authorise it themselves.",
     "addon": False, "env": "PICKER_GOOGLE_DRIVE_CLIENT_ID",
     "option": "googleDriveClientId"},
    {"key": "google_photos", "label": "Google Photos", "always": False,
     "what": "Where a phone backs its camera roll up to.",
     "addon": False, "env": "", "option": ""},
    {"key": "dropbox", "label": "Dropbox", "always": False,
     "what": "Where most of the shared folders we are sent already live.",
     "addon": False, "env": "PICKER_DROPBOX_APP_KEY", "option": "dropboxAppKey"},
    {"key": "facebook", "label": "Facebook", "always": False,
     "what": "Their own page's photos, without downloading them first.",
     "addon": False, "env": "", "option": ""},
    {"key": "instagram", "label": "Instagram", "always": False,
     "what": "Their own feed — usually the best photography a local business "
             "owns.",
     "addon": False, "env": "PICKER_INSTAGRAM_CLIENT_ID",
     "option": "instagramClientId"},
    {"key": "shutterstock", "label": "Shutterstock", "always": False,
     "what": "Licensed stock. Needs the Cloudinary add-on.",
     "addon": True, "env": "", "option": ""},
    {"key": "getty", "label": "Getty Images", "always": False,
     "what": "Licensed stock. Needs the Cloudinary add-on.",
     "addon": True, "env": "", "option": ""},
    {"key": "istock", "label": "iStock", "always": False,
     "what": "Licensed stock. Needs the Cloudinary add-on.",
     "addon": True, "env": "", "option": ""},
    {"key": "unsplash", "label": "Unsplash", "always": False,
     "what": "Free stock. Needs the Cloudinary add-on — and the picker already "
             "searches Unsplash directly, so this is the redundant one.",
     "addon": True, "env": "", "option": ""},
]

BY_KEY = {s["key"]: s for s in CATALOGUE}

# Everything that runs on Cloudinary's own registered apps. The add-on
# libraries are deliberately absent: see the second rule at the top.
DEFAULT = [s["key"] for s in CATALOGUE if not s["addon"]]


def _list(name: str) -> list[str]:
    return [v.strip().lower() for v in (os.environ.get(name) or "").split(",") if v.strip()]


def _split(raw: list[str]) -> tuple[list[str], list[str]]:
    """Keep the ones this catalogue knows; hand back the rest so they can be named."""
    keep, unknown = [], []
    for key in raw:
        if key in BY_KEY:
            if key not in keep:
                keep.append(key)
        elif key not in unknown:
            unknown.append(key)
    return keep, unknown


def configured() -> tuple[list[str], list[str]]:
    """(sources to offer, names we did not recognise).

    `PICKER_UPLOAD_SOURCES`, when set, is the whole list and wins outright —
    that is the escape hatch for a deployment that wants something narrower.
    Otherwise every source that needs no subscription is offered, plus whichever
    add-on libraries `PICKER_STOCK_SOURCES` names.
    """
    explicit = _list("PICKER_UPLOAD_SOURCES")
    if explicit:
        keep, unknown = _split(explicit)
        return (keep or list(DEFAULT)), unknown

    stock, unknown = _split(_list("PICKER_STOCK_SOURCES"))
    return list(DEFAULT) + [k for k in stock if k not in DEFAULT], unknown


def enabled() -> list[str]:
    return configured()[0]


def known(key: str) -> bool:
    """Is this a source the widget could have reported?

    Asked when recording an upload rather than `key in enabled()`: a source
    switched off between the widget opening and the file landing must not turn
    a real Instagram upload into a row labelled "local". What matters is that
    the name is one of ours.
    """
    return str(key or "").strip().lower() in BY_KEY


def widget_options() -> dict:
    """The per-source credentials, for the widget's own option names.

    Only the ones actually set are returned. Sending an empty
    `dropboxAppKey` is worse than sending none: the widget takes it at its word
    and the tab fails against an app key of "".
    """
    out: dict[str, str] = {}
    on = set(enabled())
    for s in CATALOGUE:
        if not s["env"] or s["key"] not in on:
            continue
        value = (os.environ.get(s["env"]) or "").strip()
        if value:
            out[s["option"]] = value
    return out


def unknown() -> list[str]:
    """Source names in the environment that this catalogue does not know.

    The only thing about the source list worth putting on a staff screen. One
    that is working is not a finding — a page that reports a roster of green
    ticks anyway is read once and skipped for ever, and it pushes the client
    list below the fold. A name the widget does not recognise is different: it
    draws a broken tab or no tab, and somebody has to correct the variable.
    """
    return configured()[1]


def client_line() -> str:
    """The sentence the upload panel shows a client, built from what is on.

    Written from the live list rather than typed into the template, because a
    paragraph naming Dropbox on a deployment where Dropbox is off is a promise
    the panel cannot keep.
    """
    on = enabled()
    names = [BY_KEY[k]["label"] for k in on
             if k in BY_KEY and not BY_KEY[k]["always"]]
    lead = "Bring them from wherever they already are: your computer or phone"
    if not names:
        return lead + ", the camera, or a web address."
    if len(names) == 1:
        tail = names[0]
    else:
        tail = ", ".join(names[:-1]) + " or " + names[-1]
    return f"{lead}, the camera, a web address — or straight from {tail}."
