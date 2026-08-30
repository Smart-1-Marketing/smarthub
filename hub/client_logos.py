"""A logo we found is a logo the client's gallery should hold.

## Why this exists

Two things in this Hub find a client's logo, and neither of them kept it
anywhere a person could use it.

`hub/brand_lookup.py` calls Brandfetch, which is billed against a plan of a
hundred calls a month, and stores the JSON. `hub/scan_facts.brand_observed()`
reads `logo.logo_url` off the last Insites audit, which is where the logo
comes from for the majority of local businesses -- they publish no brand
record anywhere, and the last scan photographed their home page. So the Hub
knew the logo and the client's own gallery -- the one place a rep opens to put
a mark into an ad, a commercial, a landing page or a proposal -- did not have
it. Every one of those tools then asked somebody to go and find a logo that
had been on file since the scan ran. Nothing errored at any point.

`file_logos()` closes that: it takes what the two sources hold, stores the
bytes in Cloudinary and records them in the client's gallery under a **Logo**
collection, which is what `KIND_LABELS` in `modules/image_picker/filing.py`
calls the folder a gallery groups by.

## The rules, each of which is a way to be confidently wrong

**Nothing here ever calls a provider.** Brandfetch is billed per call and is
behind a button for that reason; a page load that filed logos would spend the
plan. This reads the answer that has *already* been paid for and the scan that
has already run. It is called *after* a lookup succeeds, and from a sweep a
person asks for -- never speculatively.

**Two sources that agree are one logo.** Compared on the **bytes**, not on the
URL: Brandfetch and Insites hand back the same mark under two different URLs
far more often than not, and deduping on the URL would file the same image
twice on every client. When they genuinely differ, both are filed -- a
horizontal lockup and a stacked mark are both useful, and choosing between
them is the rep's job rather than ours.

**A logo lifted off a page is a candidate, not an approved asset.** That is
`hub/scan_facts.py`'s rule and it survives the trip: each filed logo carries,
in its own alt text and label, whether it came from the client's brand record
or was seen on their website. It is filed into the gallery and it is still not
merged into `logos` -- `brand_guide_payload()`, `io_prefill` and
`landing_maker` read that, and a wrong logo on a client-facing document is
worse than none because nobody proof-reads the thing they recognize.

**Nothing is invented.** No `https://<clientname>.com/logo.png`, no favicon
scraped off a landing page -- the rule `modules/ads_builder/logo.py` works to.
A client with no logo anywhere gets nothing filed and is told which source had
nothing, because "we have no logo for them" and "we could not look" send
somebody to two different places.

**A URL that will not fetch is named, not skipped.** A dead link filed into a
gallery is worse than an empty one: it draws a broken tile that a rep reports
as the gallery being broken. The bytes are fetched here rather than handed to
Cloudinary to fetch precisely so that a 404 is an answer this function can
give.

**Running it twice files nothing twice.** The Cloudinary public_id is derived
from the content hash, and `filing.file_asset` already refuses a second row
for one `(provider, provider_image_id)` -- so a sweep that runs every night
costs one HEAD-shaped fetch per source and no uploads.

**And it never raises.** Every caller is finishing something that already
succeeded. Losing a brand lookup somebody just paid for because the gallery
write failed is a worse outcome than the logo not appearing in the gallery --
the rule `filing.file_asset` states in its own docstring.
"""
from __future__ import annotations

import hashlib
import os
import re

# The gallery collection these land in. `modules/image_picker/filing.py` maps
# a kind to the heading a gallery groups under; this one is declared there too
# so the folder has a name rather than showing as a bare key.
KIND = "logo"
LABEL = "Logo"

# What a source is called on screen. Where the logo came from is the fact a rep
# can act on; which of our tools did the reading is not -- the note
# `modules/ads_builder/logo.py` makes about naming Brandfetch to somebody who
# cannot rotate its key.
SOURCE_LABELS = {
    "brand": "their brand record",
    "scan": "seen on their website",
}

# A logo is small. Anything larger is not one, and downloading it to find that
# out is how one bad URL holds up a sweep.
MAX_BYTES = 5 * 1024 * 1024
TIMEOUT = 15


def _clean(v) -> str:
    return re.sub(r"\s+", " ", str(v or "")).strip()


def _ext(url: str, content_type: str = "") -> str:
    """The file extension to store under, from the URL then the content type.

    SVG matters here: a vector mark scales to a billboard and a 64px PNG does
    not, and Cloudinary keeps raw and image resources in separate namespaces,
    so getting this wrong files a logo somewhere the gallery cannot show it.
    """
    guess = os.path.splitext((url or "").split("?")[0])[1].lower()
    if guess in (".svg", ".png", ".jpg", ".jpeg", ".webp", ".gif"):
        return guess
    by_type = {"image/svg+xml": ".svg", "image/png": ".png",
               "image/jpeg": ".jpg", "image/webp": ".webp",
               "image/gif": ".gif"}
    return by_type.get((content_type or "").split(";")[0].strip().lower(), ".png")


def _fetch(url: str) -> tuple[bytes, str, str]:
    """``(bytes, extension, error)``. Never raises."""
    if not str(url or "").startswith(("http://", "https://")):
        return b"", "", "that is not a URL we can fetch"
    try:
        import requests
        r = requests.get(url, timeout=TIMEOUT, stream=True)
    except Exception as exc:                              # noqa: BLE001
        return b"", "", f"could not be reached ({type(exc).__name__})"
    if r.status_code == 404:
        return b"", "", "the link is dead (404)"
    if not r.ok:
        return b"", "", f"answered HTTP {r.status_code}"
    data = r.content or b""
    if not data:
        return b"", "", "the link returned nothing"
    if len(data) > MAX_BYTES:
        return b"", "", f"is {len(data) // 1048576} MB, which is not a logo"
    return data, _ext(url, r.headers.get("Content-Type", "")), ""


def candidates(client: str, domain: str = "") -> tuple[list[dict], list[str]]:
    """``(logos we hold a URL for, sources that had nothing or could not be read)``.

    Reads only. Brandfetch is not called -- see the module docstring -- so this
    is the stored brand record and the last completed site scan, in that order:
    a mark the client gave us beats one lifted off their home page.
    """
    found: list[dict] = []
    notes: list[str] = []

    try:
        from hub import client_brand
        kit = client_brand.brand_kit(client, domain)
        if kit.get("found"):
            for logo in (kit.get("logos") or []):
                url = _clean(logo.get("url"))
                if url:
                    found.append({"url": url, "source": "brand",
                                  "kind": _clean(logo.get("type")) or "logo"})
        else:
            notes.append("Brand record: " + (kit.get("note") or "nothing on file."))
    except Exception as exc:                              # noqa: BLE001
        notes.append(f"Brand record: could not be read ({type(exc).__name__}).")

    try:
        from hub import scan_facts
        dom = domain or ""
        if not dom:
            from hub.client_context import canonical_domain
            dom = canonical_domain(client) or ""
        if dom:
            seen = scan_facts.brand_observed(dom)
            if seen.get("error"):
                notes.append("Their last site scan could not be read, so "
                             "nothing was taken from it.")
            elif _clean(seen.get("logo_url")):
                found.append({"url": _clean(seen["logo_url"]), "source": "scan",
                              "kind": "logo",
                              "seen_at": seen.get("scanned_at") or ""})
            else:
                notes.append("Their website: " + (seen.get("note") or
                                                  "no logo read from it."))
        else:
            notes.append("No website on this client's record, so their site "
                         "could not be read for a logo.")
    except Exception as exc:                              # noqa: BLE001
        notes.append(f"Their website: could not be read ({type(exc).__name__}).")

    return found, notes


def file_logos(client: str, domain: str = "", *, actor: str = "system") -> dict:
    """File every logo we hold for this client into their gallery, once each.

    Returns what happened, in the shape the panels in this Hub already read:
    `filed` is what went in, `duplicate` is what was already there, `failed`
    names a URL that would not fetch and why, and `notes` carries a source
    that had nothing or could not be read. Nothing here is a bare count --
    "this client has no logo" and "we could not look for one" are different
    answers and only the first means go and ask them for one.

    Never raises.
    """
    client = _clean(client)
    out = {"client": client, "filed": [], "duplicate": [], "failed": [],
           "notes": [], "sources_agreed": False}
    if not client:
        out["notes"].append("No client named, so nothing was filed.")
        return out

    try:
        found, notes = candidates(client, domain)
    except Exception as exc:                              # noqa: BLE001
        out["notes"].append(f"Nothing could be read ({type(exc).__name__}).")
        return out
    out["notes"] = notes

    try:
        from hub import storage
        from modules.image_picker import filing
    except Exception as exc:                              # noqa: BLE001
        out["notes"].append("The client gallery is unavailable in this "
                            f"environment ({type(exc).__name__}), so nothing "
                            "was filed.")
        return out

    seen_hashes: dict[str, str] = {}          # sha256 -> the source that won it
    for item in found:
        url, source = item["url"], item["source"]
        data, ext, err = _fetch(url)
        if err:
            out["failed"].append({"url": url, "source": source,
                                  "from": SOURCE_LABELS.get(source, source),
                                  "error": err})
            continue

        digest = hashlib.sha256(data).hexdigest()
        if digest in seen_hashes:
            # The same mark under two URLs, which is the ordinary case. Said
            # out loud rather than silently dropped: two sources agreeing on a
            # logo is worth more confidence than either of them alone.
            out["sources_agreed"] = True
            continue
        seen_hashes[digest] = source

        short = digest[:16]
        try:
            stored = storage.put(
                "client_logos", f"logo-{short}{ext}", data,
                client=client, subpath=KIND,
                # Derived from the content, so a second run overwrites nothing
                # and creates nothing -- and two clients who share a logo still
                # get one row each, because the client is in the folder.
                public_id=f"{storage.settings.folder('client_logos')}/"
                          f"{storage.slug(client, 'client')}/{KIND}/{short}",
                overwrite=True,
                context={"client": client, "source": source},
                tags=["s1-logo", f"s1-logo-{source}"])
        except Exception as exc:                          # noqa: BLE001
            out["failed"].append({"url": url, "source": source,
                                  "from": SOURCE_LABELS.get(source, source),
                                  "error": f"could not be stored ({exc})"})
            continue

        where = SOURCE_LABELS.get(source, source)
        res = filing.file_asset(
            client_name=client,
            public_id=stored.public_id,
            url=stored.url,
            kind=KIND,
            label=LABEL,
            key=source,
            filename=f"logo-{short}{ext}",
            # The claim travels with the file. A mark seen on a home page is a
            # candidate, and the tile has to say so or somebody puts it on a
            # document a client reads.
            alt=f"{client} logo — {where}"
                + (f", {item['seen_at']}" if item.get("seen_at") else ""),
            resource_type="raw" if ext == ".svg" else "image",
            size_bytes=len(data),
            provider=f"logo_{source}",
            saved_by=actor or "system",
            create_client=True,
            # A logo is not campaign creative and must not be pushed into the
            # client's Suite media library by being filed here: that library is
            # what a funnel draws from, and putting an unapproved candidate in
            # it is how one appears on a live page.
            push_to_suite=False)
        row = {"url": stored.url, "source": source, "from": where,
               "public_id": stored.public_id}
        if not res.get("ok"):
            out["failed"].append({**row, "error": res.get("error") or
                                  "the gallery would not record it"})
        elif res.get("duplicate"):
            out["duplicate"].append(row)
        else:
            out["filed"].append(row)

    if out["filed"]:
        try:
            from hub import audit
            audit.log("brand", "logo_filed", actor=actor or "system",
                      client=client, count=len(out["filed"]),
                      sources=sorted({r["source"] for r in out["filed"]}))
        except Exception:                                 # noqa: BLE001
            pass
    return out


def summary(result: dict) -> str:
    """One line for a panel, saying which of the four things happened.

    A count on its own cannot tell "nothing to file" from "we could not look",
    and those send somebody to two different places.
    """
    filed, dup = len(result.get("filed") or []), len(result.get("duplicate") or [])
    failed = result.get("failed") or []
    bits = []
    if filed:
        froms = sorted({r["from"] for r in result["filed"]})
        bits.append(f"{filed} logo{'s' if filed != 1 else ''} filed to the "
                    f"gallery ({', '.join(froms)})")
    if dup:
        bits.append(f"{dup} already there")
    if result.get("sources_agreed"):
        bits.append("their brand record and their website carry the same mark")
    for f in failed:
        bits.append(f"one from {f['from']} {f['error']}")
    if not bits:
        return ("No logo was found to file. "
                + " ".join(result.get("notes") or [])).strip()
    return "; ".join(bits) + "."
