"""Reading a Google Drive link the agency already has, well enough to copy it.

Creative for a campaign lives in Drive. Knack's product record carries the
link -- up to four of them, `hub/knack_products.F_CREATIVE_URLS` -- and every
screen in the Hub that shows creative is showing somebody a Drive URL and
hoping they can open it. Which is the whole problem: a Drive link is an
address in somebody else's filing cabinet. It moves, it gets un-shared, the
person who owned the folder leaves, and the row on Client 360 goes on looking
exactly as healthy as it did the day it worked.

This module is the read half of moving that creative into the client library.
It does one job -- turn a Drive URL into the bytes behind it -- and it is
written to fail in a way somebody can act on.

## The scope, and why an empty answer is not an answer

`modules/google_finder` already holds connected Google accounts and their
refresh tokens. It did not ask for Drive. Google does not widen a refresh
token that has already been granted, so an account connected before
`drive.readonly` joined `SCOPES` keeps the narrower grant and every Drive call
it makes 403s **for ever** -- the exact failure google_finder documents at
length for Tag Manager, where a scope added later read on every screen as "this
login has no containers".

So `access()` answers with a *reason*, and the four are different situations:

    ok        we have a token that carries the Drive scope
    refused   the account is connected but was consented without Drive, and
              the fix is a reconnect -- which the connect URL forces consent
              on, so it is one click and not a support ticket
    reauth    the refresh token is dead
    none      no Google account is connected to this Hub at all

Nothing in this module ever reports "no files" for any of the last three.

## What it will not do

It does not write to Drive, trash, rename or move anything: the migration Todd
asked for copies, and the original folder stays exactly as it is. It exports
Google-native documents (Docs, Sheets, Slides) to PDF rather than pretending
they are files, and it skips shortcuts rather than following them into another
account's tree. Folders are walked to a bounded depth, because a creative
folder that contains someone's entire Drive is a mistake to notice rather than
a job to run.
"""
from __future__ import annotations

import logging
import re

import requests

logger = logging.getLogger(__name__)

DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.readonly"
API = "https://www.googleapis.com/drive/v3"

# A folder deeper than this is being walked by accident. Creative for one
# product is a folder, sometimes a folder of folders; six levels is somebody's
# whole Drive and the run should say so rather than spend an hour proving it.
MAX_DEPTH = 4
MAX_FILES = 400

# Google-native types have no bytes to download. Each exports to something a
# person can open; a Doc becomes the PDF anybody would have printed anyway.
_EXPORT = {
    "application/vnd.google-apps.document": ("application/pdf", ".pdf"),
    "application/vnd.google-apps.presentation": ("application/pdf", ".pdf"),
    "application/vnd.google-apps.spreadsheet": ("application/pdf", ".pdf"),
    "application/vnd.google-apps.drawing": ("image/png", ".png"),
}
FOLDER_MIME = "application/vnd.google-apps.folder"

_FIELDS = "id,name,mimeType,size,modifiedTime,webViewLink,md5Checksum,trashed"


# ---------------------------------------------------------------------------
# Which link is this
# ---------------------------------------------------------------------------

_PATTERNS = (
    ("folder", re.compile(r"drive\.google\.com/drive/(?:u/\d+/)?folders/([\w-]{10,})")),
    ("file", re.compile(r"drive\.google\.com/file/d/([\w-]{10,})")),
    ("file", re.compile(r"docs\.google\.com/\w+/d/([\w-]{10,})")),
    ("file", re.compile(r"drive\.google\.com/open\?id=([\w-]{10,})")),
    ("file", re.compile(r"[?&]id=([\w-]{10,})")),
)


def parse_link(url: str) -> tuple[str, str]:
    """("folder"|"file", id) for a Drive address, or ("", "") for anything else.

    The shape in the URL is a hint, not the answer: a `/file/d/` address can
    point at a folder somebody shared oddly, and `?id=` says nothing at all.
    `describe()` asks Drive what it actually is before anything is walked.
    """
    u = str(url or "").strip()
    if "google.com" not in u:
        return "", ""
    for kind, pattern in _PATTERNS:
        m = pattern.search(u)
        if m:
            return kind, m.group(1)
    return "", ""


def is_drive(url: str) -> bool:
    return bool(parse_link(url)[1])


# ---------------------------------------------------------------------------
# Whether we can read Drive at all, and why not
# ---------------------------------------------------------------------------

class DriveRefused(RuntimeError):
    """Google said no. Carries the reason, so a route can offer the fix."""

    def __init__(self, reason: str, detail: str = ""):
        self.reason = reason
        self.detail = detail
        super().__init__(detail or reason)


def access(email: str = "") -> dict:
    """A Drive access token, or the reason there isn't one.

    Returns {"ok", "reason", "email", "token", "detail"}. `reason` is one of
    ok / refused / reauth / none / unavailable, and never "empty".
    """
    try:
        from modules.google_finder import app as gf
    except Exception as exc:                            # noqa: BLE001
        return {"ok": False, "reason": "unavailable", "email": "", "token": "",
                "detail": f"Google Finder is not importable ({type(exc).__name__})."}

    try:
        accounts, err = gf.connected_accounts_result()
    except Exception as exc:                            # noqa: BLE001
        return {"ok": False, "reason": "unavailable", "email": "", "token": "",
                "detail": f"{type(exc).__name__}: {exc}"[:200]}
    if not accounts:
        return {"ok": False, "reason": "none", "email": "", "token": "",
                "detail": err or "No Google account is connected to the Hub."}

    wanted = str(email or "").strip().lower()
    ordered = ([a for a in accounts if a["email"].lower() == wanted] if wanted
               else list(accounts))
    if wanted and not ordered:
        return {"ok": False, "reason": "none", "email": wanted, "token": "",
                "detail": f"{wanted} is not connected to the Hub."}

    last = {"ok": False, "reason": "refused", "email": "", "token": "",
            "detail": "No connected Google account has been given Drive access."}
    for acc in ordered:
        try:
            token = gf.refresh_access_token(acc["email"], acc["refresh_token"])
        except gf.ReauthRequired:
            last = {"ok": False, "reason": "reauth", "email": acc["email"],
                    "token": "", "detail":
                    f"{acc['email']} needs to sign in to Google again."}
            continue
        except Exception as exc:                        # noqa: BLE001
            last = {"ok": False, "reason": "unavailable", "email": acc["email"],
                    "token": "", "detail": f"{type(exc).__name__}: {exc}"[:200]}
            continue
        if _has_drive(token):
            return {"ok": True, "reason": "ok", "email": acc["email"],
                    "token": token, "detail": ""}
        last = {"ok": False, "reason": "refused", "email": acc["email"],
                "token": "", "detail":
                f"{acc['email']} was connected before Drive access was asked "
                f"for. Reconnect that login on Google Access and it will be "
                f"granted -- Google never widens a token that already exists."}
    return last


def _has_drive(token: str) -> bool:
    """Does this access token actually carry the Drive scope?

    Asked of Google rather than assumed from SCOPES, because the token in hand
    was minted from a *grant*, and the grant is whatever the person consented
    to on the day they connected.
    """
    try:
        r = requests.get("https://www.googleapis.com/oauth2/v3/tokeninfo",
                         params={"access_token": token}, timeout=10)
        if not r.ok:
            return False
        return DRIVE_SCOPE in str((r.json() or {}).get("scope") or "")
    except Exception:                                   # noqa: BLE001
        return False


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

def _get(token: str, path: str, **params):
    r = requests.get(f"{API}{path}", params=params,
                     headers={"Authorization": f"Bearer {token}"}, timeout=45)
    if r.status_code in (401, 403):
        raise DriveRefused("refused", f"Drive refused this file (HTTP "
                                      f"{r.status_code}). It may not be shared "
                                      f"with the connected login.")
    if r.status_code == 404:
        raise DriveRefused("missing", "That Drive item no longer exists, or is "
                                      "not shared with the connected login.")
    r.raise_for_status()
    return r


def describe(token: str, file_id: str) -> dict:
    """One item's metadata. Raises DriveRefused rather than returning {}."""
    r = _get(token, f"/files/{file_id}", fields=_FIELDS,
             supportsAllDrives="true")
    return r.json() or {}


def list_folder(token: str, folder_id: str, *, depth: int = 0,
                prefix: str = "") -> list[dict]:
    """Every file under a folder, each carrying the path it was found at.

    Subfolders are walked, because creative folders are routinely one level of
    "Final" and "Revised" deep and flattening them loses which is which. The
    path is kept on the row so the copy can preserve it.
    """
    if depth >= MAX_DEPTH:
        return []
    out, page = [], None
    while True:
        r = _get(token, "/files",
                 q=f"'{folder_id}' in parents and trashed = false",
                 fields=f"nextPageToken,files({_FIELDS})",
                 pageSize=200, pageToken=page or "",
                 supportsAllDrives="true", includeItemsFromAllDrives="true")
        data = r.json() or {}
        for item in data.get("files") or []:
            if item.get("mimeType") == FOLDER_MIME:
                out.extend(list_folder(
                    token, item["id"], depth=depth + 1,
                    prefix=f"{prefix}{item.get('name', '')}/"))
            else:
                item["path"] = prefix
                out.append(item)
            if len(out) >= MAX_FILES:
                return out[:MAX_FILES]
        page = data.get("nextPageToken")
        if not page:
            return out


def files_for(token: str, url: str) -> list[dict]:
    """Every downloadable file behind one creative link.

    A link to a file is that file. A link to a folder is everything in it.
    Which of the two it is comes from Drive, not from the URL's shape.
    """
    _, ident = parse_link(url)
    if not ident:
        return []
    meta = describe(token, ident)
    if meta.get("trashed"):
        raise DriveRefused("missing", "That Drive item is in the trash.")
    if meta.get("mimeType") == FOLDER_MIME:
        return list_folder(token, ident)
    meta["path"] = ""
    return [meta]


def download(token: str, item: dict, *, max_bytes: int = 200 * 1024 * 1024) -> tuple[bytes, str]:
    """The bytes of one Drive item, and the filename to store them under.

    A Google-native document is exported rather than downloaded -- there are no
    bytes behind a Doc -- and a shortcut is refused rather than followed, since
    following one is how a copy job walks out of the folder it was given into
    somebody else's Drive.
    """
    mime = str(item.get("mimeType") or "")
    name = str(item.get("name") or item.get("id") or "file")
    if mime == "application/vnd.google-apps.shortcut":
        raise DriveRefused("shortcut", f"{name} is a shortcut to a file "
                                       f"somewhere else, not a file.")
    if mime.startswith("application/vnd.google-apps."):
        export = _EXPORT.get(mime)
        if not export:
            raise DriveRefused("unsupported",
                               f"{name} is a Google {mime.rsplit('.', 1)[-1]} "
                               f"and has no file to copy.")
        target, ext = export
        r = _get(token, f"/files/{item['id']}/export", mimeType=target,
                 supportsAllDrives="true")
        data = r.content
        if not name.lower().endswith(ext):
            name = f"{name}{ext}"
    else:
        size = int(item.get("size") or 0)
        if size and size > max_bytes:
            raise DriveRefused("too_big",
                               f"{name} is {size // 1048576} MB, over the "
                               f"{max_bytes // 1048576} MB copy limit.")
        r = _get(token, f"/files/{item['id']}", alt="media",
                 supportsAllDrives="true")
        data = r.content
    if not data:
        raise DriveRefused("empty", f"{name} came back empty.")
    if len(data) > max_bytes:
        raise DriveRefused("too_big", f"{name} is over the copy limit.")
    return data, name
