"""What a QR code points at, and whose account the scan belongs to.

A QR code on a Connected TV end card is the only response mechanism the spot
has -- there is nothing to click on a television -- so it is the one element
in a commercial whose failure is total and silent. It scans, or the whole
:30 bought nothing.

## HighLevel does not publish a QR API, and that is the answer

"Can we build the QR code from GHL?" is the question this module exists to
settle so nobody has to ask it again. HighLevel renders QR codes inside its
funnel and website builders as a page element; there is no endpoint in the
public v2 API that returns one, so there is nothing here to call. Asking for
one anyway would mean a code that exists only as long as somebody keeps a
funnel page published, on a spot that runs for a quarter.

So the **image** is rendered locally by the `qrcode` package -- no key, no
provider, no expiry, and `services/qrcode_service.py` already does it
correctly (pure black on white, because a brand-tinted code fails to scan
from a couch).

What HighLevel genuinely decides is the half that was missing: **whose scan
it is**. A client with a Suite sub-account has one; a business we are pitching
does not, and their scans belong in Smart 1 Marketing's own location, which is
where prospects live already (`hub/leads.py` writes them there). Getting that
wrong does not break the code -- it files a paying client's response under the
agency, or a prospect's under nobody, and the campaign report is quietly wrong
for the whole flight.

## Three rules

**A destination is never invented.** No `https://<clientname>.com`, no
guessed landing page. With nothing to point at, `destination()` returns
nothing and says which field would fix it -- the rule
`modules/ads_builder/logo.py` works to, for the same reason: a QR code that
opens the wrong company's website is worse than an end card with no code on
it, because nobody proof-reads the thing that scans.

**The tracking is on the URL, not in a shortener.** A shortener is a second
service that has to still be running in a year, and a redirect a client cannot
see. UTM parameters travel in the URL itself, land in the client's own
analytics as well as the Suite, and survive us. Existing parameters on the
destination are kept -- a landing page that already carries a campaign tag was
built that way on purpose.

**Which account answered is printed.** `attribution()` is tri-state, the way
`hub/google_finder`'s platform notes are: filed to the client's own
sub-account, filed to Smart 1 Marketing because there is no client record, or
*not measured* because the Suite is not configured on this deployment at all.
A screen that shows a green tick over the third case is telling somebody the
scans are being counted when nothing is counting them.
"""

from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

# The UTM values a scan comes back with. They are constants rather than a
# form field because the whole point is that every commercial this Hub
# produces reports the same way -- a rep typing "QR" on one spot and "qr-code"
# on the next makes two rows in a report that should have one.
UTM_MEDIUM = "qr"
UTM_SOURCE_BY_PLATFORM = {
    "ctv": "ctv",
    "youtube": "youtube",
    "social": "social",
    "both": "video",
}
DEFAULT_UTM_SOURCE = "video"


def _clean(value) -> str:
    return str(value or "").strip()


def with_scheme(url: str) -> str:
    url = _clean(url)
    if not url:
        return ""
    if url.startswith(("http://", "https://")):
        return url
    return "https://" + url.lstrip("/")


def destination(*, landing_page: str = "", client_website: str = "",
                cta_website: str = "") -> dict:
    """Where the code should send someone, and where that came from.

    Order is deliberate: the landing page built for this campaign beats the
    website typed on the end card, which beats the client's home page. Sending
    a CTV viewer to a home page when a campaign landing page exists throws
    away the offer they just watched.

    Returns {"url", "source", "missing"} -- `missing` names the field to fill
    in when there is nothing, so a screen can say what to do rather than
    reporting a blank.
    """
    for value, source in ((landing_page, "landing_page"),
                          (cta_website, "cta_website"),
                          (client_website, "client_website")):
        if _clean(value):
            return {"url": with_scheme(value), "source": source, "missing": ""}
    return {"url": "", "source": "",
            "missing": ("No landing page, CTA website or client website is set, so "
                        "there is nowhere for the code to send anyone. Add a landing "
                        "page on the brief.")}


def tracked_url(url: str, *, campaign: str = "", platform: str = "both",
                content: str = "") -> str:
    """The destination with the scan's own UTM parameters on it.

    Parameters already on the URL win. A landing page handed over as
    `client.com/ac?utm_campaign=summer` was tagged by whoever built it, and
    overwriting that tag re-attributes traffic they are already reporting on.
    """
    url = with_scheme(url)
    if not url:
        return ""
    parts = urlparse(url)
    existing = dict(parse_qsl(parts.query, keep_blank_values=True))

    wanted = {
        "utm_source": UTM_SOURCE_BY_PLATFORM.get(platform, DEFAULT_UTM_SOURCE),
        "utm_medium": UTM_MEDIUM,
    }
    if _clean(campaign):
        wanted["utm_campaign"] = _slug(campaign)
    if _clean(content):
        wanted["utm_content"] = _slug(content)

    for key, value in wanted.items():
        existing.setdefault(key, value)

    return urlunparse(parts._replace(query=urlencode(existing)))


def _slug(value: str) -> str:
    out = []
    for ch in _clean(value).lower():
        if ch.isalnum():
            out.append(ch)
        elif out and out[-1] != "-":
            out.append("-")
    return "".join(out).strip("-")[:60]


def attribution(*, client_location_id: str = "", client_name: str = "") -> dict:
    """Which Suite account a scan from this code is filed under.

    Three answers, and only one of them is "we could not look":

      own      the client has a Suite sub-account and it is on file
      agency   no sub-account, so this is filed to Smart 1 Marketing, which
               is where a prospect's contact already goes
      unknown  the Suite is not configured on this deployment, so nothing is
               counting scans anywhere and the screen must say so

    Never raises, and never reaches the network for it: this is called while
    saving a CTA, and a Suite that is slow must not hold up a save.
    """
    location_id = _clean(client_location_id)
    if location_id:
        return {"state": "own", "location_id": location_id,
                "account": client_name or "this client",
                "note": ("Scans are filed to this client's own Smart 1 Suite "
                         "sub-account.")}

    configured, agency_location, why = _agency_location()
    if not configured:
        return {"state": "unknown", "location_id": "", "account": "",
                "note": ("Not measured — Smart 1 Suite is not configured on this "
                         "deployment, so nothing is counting scans. " + why)}
    return {"state": "agency", "location_id": agency_location,
            "account": _agency_name(),
            "note": ("No Suite sub-account on file for this business, so scans are "
                     "filed to Smart 1 Marketing — the same place a new lead goes. "
                     "They join the client's own record the day one exists.")}


def _agency_location() -> tuple[bool, str, str]:
    """Smart 1 Marketing's own location, through the one module that owns it.

    hub/ghl_contacts.py already resolves this and already refuses a companyId
    used as a locationId -- the mistake that would file every scan against the
    agency rather than the account. Reading it here rather than re-deriving it
    means that refusal covers this too.
    """
    try:
        from hub import ghl_contacts
    except Exception:                                        # noqa: BLE001
        return False, "", "The Suite integration is unavailable in this context."
    try:
        if not ghl_contacts.configured():
            return False, "", ghl_contacts.why_not()
        return True, ghl_contacts.location_id(), ""
    except Exception as exc:                                 # noqa: BLE001
        return False, "", str(exc)


def _agency_name() -> str:
    try:
        from hub import ghl_contacts
        return ghl_contacts.LOCATION_NAME
    except Exception:                                        # noqa: BLE001
        return "Smart 1 Marketing"


def plan(*, landing_page: str = "", client_website: str = "", cta_website: str = "",
         campaign: str = "", platform: str = "both", content: str = "",
         client_location_id: str = "", client_name: str = "") -> dict:
    """Everything a CTA needs to decide about its QR code, in one answer.

    One call rather than three so the destination, the tracking and the
    account cannot disagree with each other on screen -- which is what
    happened on the Sites Admin domain cell, where one half of a pair was
    built and the other reported a problem it could not fix.
    """
    where = destination(landing_page=landing_page, client_website=client_website,
                        cta_website=cta_website)
    filed = attribution(client_location_id=client_location_id, client_name=client_name)
    target = tracked_url(where["url"], campaign=campaign, platform=platform,
                         content=content) if where["url"] else ""
    return {
        "target_url": target,
        "destination_url": where["url"],
        "destination_source": where["source"],
        "missing": where["missing"],
        "attribution": filed,
        "provider_note": ("The code is rendered here, not in Smart 1 Suite — "
                          "HighLevel publishes no QR endpoint, and a code that "
                          "lives inside a funnel page stops working the day that "
                          "page is unpublished."),
    }
