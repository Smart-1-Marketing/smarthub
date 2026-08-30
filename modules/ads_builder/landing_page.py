"""Reading the landing page, rather than describing it.

The generator's "landing page analysis" was written by a model that had never
seen the page: it was handed a URL in a prompt and produced confident,
plausible recommendations about a hero and a call-to-action nobody had looked
at. That is the worst shape available here — the rep quotes it to the client,
and it is fiction.

So the page is **fetched** and its conversion points are **counted off the
markup** before anything is asked of a model. The observed half is fact; the
model is given the facts and asked only for judgment, and the two are kept
apart on screen so a rep can tell which is which.

Same discipline as ``hub/alt_text.py``: a check that could not run says **not
measured**, never a tick and never a zero. A page we could not fetch produces
"we could not reach it and here is the status", not a clean-looking report
saying it has no forms.
"""
from __future__ import annotations

import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import requests

TIMEOUT = 20
MAX_BYTES = 2_000_000
UA = "Mozilla/5.0 (compatible; Smart1Hub/1.0; +https://smart1-hub.onrender.com)"

# Chat widgets identify themselves in the markup they inject. Named rather
# than guessed at from the word "chat": a page with a "Chat with us" heading
# and no widget converts nobody, and reporting one is how a conversion point
# that does not exist ends up in a client document.
CHAT_SIGNATURES = (
    ("Intercom", r"intercom(?:cdn|\.io|settings)"),
    ("Drift", r"js\.driftt\.com|drift\.com/include"),
    ("Tawk.to", r"embed\.tawk\.to"),
    ("LiveChat", r"cdn\.livechatinc\.com"),
    ("Zendesk", r"static\.zdassets\.com|zopim"),
    ("HubSpot", r"js\.hs-scripts\.com|hs-banner"),
    ("Podium", r"connect\.podium\.com"),
    ("Facebook Messenger", r"connect\.facebook\.net/.*customerchat"),
    ("Tidio", r"code\.tidio\.co"),
    ("Crisp", r"client\.crisp\.chat"),
)

# Booking tools, same reasoning: "Book now" that scrolls to a form is a form.
BOOKING_SIGNATURES = (
    ("Calendly", r"calendly\.com"),
    ("Acuity", r"acuityscheduling\.com|squarespacescheduling\.com"),
    ("HubSpot Meetings", r"meetings\.hubspot\.com"),
    ("Housecall Pro", r"book\.housecallpro\.com"),
    ("ServiceTitan", r"book\.servicetitan\.com"),
    ("Square Appointments", r"squareup\.com/appointments"),
    ("Setmore", r"setmore\.com"),
    ("Google Reserve", r"reserve\.google\.com"),
)

CTA_WORDS = re.compile(
    r"\b(call|book|schedule|request|get (?:a )?(?:quote|estimate)|free (?:quote|estimate|consult\w*)"
    r"|contact|buy|shop|order|apply|sign up|start|claim|download|directions|"
    r"talk to|speak to|find out|learn more|see pricing|view pricing|get started)\b", re.I)

# Most calls to action on a real landing page are ANCHORS styled as buttons, not
# <button> elements — a page can be covered in "Get a free quote" and have no
# <button> on it anywhere. So a link counts as a CTA when it says something a
# CTA says, or when it carries a class a builder gives its buttons. Matched on
# the class token rather than a substring, or "subtle" matches "btn".
CTA_CLASS = re.compile(
    r"(?:^|[\s_-])(?:btn|button|cta|elementor-button|wp-block-button__link|"
    r"hs-button|w-button|sqs-block-button-element)(?:$|[\s_-])", re.I)


class _Page(HTMLParser):
    """Whatever the markup actually says. No judgment here at all."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.meta_description = ""
        self.has_viewport = False
        self.forms = []                 # one dict per <form>
        self.tel_links = []
        self.mailto_links = []
        self.map_links = []
        self.text_chunks = []
        self.headings = []
        self.buttons = []          # {text, kind: "button" | "link", href}
        self._in = ""
        self._form = None
        self._link = None          # the anchor whose text we are collecting

    # -- helpers
    @staticmethod
    def _attr(attrs, name):
        for k, v in attrs:
            if k.lower() == name:
                return v or ""
        return ""

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in ("title", "h1", "h2", "h3", "button", "script", "style"):
            self._in = tag

        if tag == "meta":
            name = self._attr(attrs, "name").lower()
            if name == "viewport":
                self.has_viewport = True
            elif name == "description":
                self.meta_description = self._attr(attrs, "content")[:400]

        elif tag == "form":
            self._form = {"action": self._attr(attrs, "action"),
                          "method": (self._attr(attrs, "method") or "get").lower(),
                          "fields": 0, "required": 0, "kinds": []}

        elif tag in ("input", "select", "textarea") and self._form is not None:
            kind = (self._attr(attrs, "type") or ("select" if tag == "select" else "text")).lower()
            if kind in ("hidden", "submit", "button", "image", "reset"):
                return
            self._form["fields"] += 1
            if any(k.lower() == "required" for k, _ in attrs):
                self._form["required"] += 1
            if kind not in self._form["kinds"]:
                self._form["kinds"].append(kind)

        elif tag == "a":
            href = self._attr(attrs, "href").strip()
            low = href.lower()
            if low.startswith("tel:"):
                number = href[4:].strip()
                if number and number not in self.tel_links:
                    self.tel_links.append(number)
            elif low.startswith("mailto:"):
                address = href[7:].split("?")[0].strip()
                if address and address not in self.mailto_links:
                    self.mailto_links.append(address)
            elif "google.com/maps" in low or "maps.app.goo.gl" in low or "goo.gl/maps" in low:
                if href not in self.map_links:
                    self.map_links.append(href)
            # Collect the link's own text; whether it is a CTA is decided when
            # the tag closes and we know what it said.
            self._link = {"href": href, "text": "",
                          "styled": bool(CTA_CLASS.search(self._attr(attrs, "class")))
                                    or self._attr(attrs, "role").lower() == "button"}

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag == "form" and self._form is not None:
            self.forms.append(self._form)
            self._form = None
        if tag == "a" and self._link is not None:
            link = self._link
            self._link = None
            text = " ".join(link["text"].split())[:120]
            # A styled button with no words is a chevron or an icon and tells a
            # reader nothing, so it is not reported as a conversion point.
            if text and (link["styled"] or CTA_WORDS.search(text)):
                self._add_button(text, "link", link["href"])
        if tag == self._in:
            self._in = ""

    def _add_button(self, text, kind, href=""):
        if any(b["text"].lower() == text.lower() for b in self.buttons):
            return
        self.buttons.append({"text": text, "kind": kind, "href": href})

    def handle_data(self, data):
        text = " ".join(data.split())
        if not text:
            return
        if self._in in ("script", "style"):
            return
        if self._in == "title" and not self.title:
            self.title = text[:200]
        elif self._in in ("h1", "h2", "h3"):
            self.headings.append({"level": self._in, "text": text[:200]})
        elif self._in == "button":
            self._add_button(text[:120], "button")
        if self._link is not None:
            self._link["text"] += " " + text
        if len(self.text_chunks) < 900:
            self.text_chunks.append(text)


def fetch(url: str) -> dict:
    """Request the page. A failure is reported, never rendered as an absence."""
    target = str(url or "").strip()
    if not target:
        return {"ok": False, "url": "", "status": None,
                "error": "No landing page URL on this campaign."}
    if not re.match(r"^https?://", target, re.I):
        target = "https://" + target
    if not urlparse(target).netloc:
        return {"ok": False, "url": target, "status": None,
                "error": "That does not look like a website address."}

    try:
        resp = requests.get(target, timeout=TIMEOUT, allow_redirects=True,
                            headers={"User-Agent": UA,
                                     "Accept": "text/html,application/xhtml+xml"})
    except requests.RequestException as exc:
        return {"ok": False, "url": target, "status": None,
                "error": f"Could not reach the page: {exc}"}

    body = resp.content[:MAX_BYTES]
    try:
        html = body.decode(resp.encoding or "utf-8", errors="replace")
    except (LookupError, TypeError):
        html = body.decode("utf-8", errors="replace")

    return {
        "ok": resp.ok,
        "url": resp.url,
        "requested_url": target,
        "status": resp.status_code,
        "redirected": resp.url.rstrip("/") != target.rstrip("/"),
        "html": html if resp.ok else "",
        "error": "" if resp.ok else f"The page answered HTTP {resp.status_code}.",
    }


def _signatures(html: str, table) -> list[str]:
    return [name for name, pattern in table if re.search(pattern, html, re.I)]


def observe(url: str, fetched: dict = None) -> dict:
    """Conversion points counted off the page, plus what could not be read.

    Every item carries ``evidence`` — the actual number, address or widget
    name found — because "this page has a phone number" and "this page has
    (317) 555-0142" are different claims, and only the second one can be
    checked by the person reading the report.
    """
    got = fetched if fetched is not None else fetch(url)
    base = {
        "url": got.get("url") or url,
        "measured": bool(got.get("ok")),
        "status": got.get("status"),
        "redirected": bool(got.get("redirected")),
        "error": got.get("error", ""),
        "conversion_points": [],
        "mobile_viewport": None,      # None = not measured, never False by default
        "title": "", "meta_description": "", "headings": [],
        "text": "",
    }
    if not got.get("ok"):
        base["note"] = ("The page could not be read, so its conversion points are "
                        "NOT MEASURED — not zero.")
        return base

    html = got.get("html") or ""
    page = _Page()
    try:
        page.feed(html)
    except Exception:  # noqa: BLE001 — malformed markup must not lose the fetch
        pass

    points = []

    for number in page.tel_links[:10]:
        points.append({"kind": "calls", "label": "Click-to-call link",
                       "evidence": number})
    for address in page.mailto_links[:10]:
        points.append({"kind": "email_leads", "label": "Email address link",
                       "evidence": address})
    for form in page.forms[:10]:
        points.append({
            "kind": "form_submissions",
            "label": f"Form with {form['fields']} field{'s' if form['fields'] != 1 else ''}",
            "evidence": (f"{form['method'].upper()} → {form['action'] or 'same page'}"
                         + (f", {form['required']} required" if form["required"] else "")),
        })
    for tool in _signatures(html, BOOKING_SIGNATURES):
        points.append({"kind": "appointment_bookings", "label": "Booking tool",
                       "evidence": tool})
    for widget in _signatures(html, CHAT_SIGNATURES):
        points.append({"kind": "chat_conversations", "label": "Chat widget",
                       "evidence": widget})
    for link in page.map_links[:5]:
        points.append({"kind": "directions", "label": "Directions link",
                       "evidence": link})

    # Every call to action found, button or styled link, each with the words it
    # actually says. A <button> counts as-is: it is a button, which is a call to
    # action whatever it is labelled.
    for cta in page.buttons[:15]:
        points.append({
            "kind": "cta",
            "label": "Call-to-action " + ("button" if cta["kind"] == "button" else "link"),
            "evidence": cta["text"] + (f"  →  {cta['href']}" if cta.get("href") else ""),
        })

    base.update({
        "conversion_points": points,
        "mobile_viewport": page.has_viewport,
        "title": page.title,
        "meta_description": page.meta_description,
        "headings": page.headings[:25],
        "form_count": len(page.forms),
        # Capped: this goes into a prompt, and a 200 KB page of boilerplate
        # crowds out the instructions that matter.
        "text": " ".join(page.text_chunks)[:6000],
        "note": "",
    })
    return base


def kinds_found(observed: dict) -> set:
    return {p["kind"] for p in (observed or {}).get("conversion_points") or []}


def missing_for(observed: dict, wanted_actions) -> list[dict]:
    """Conversion actions the client wants that the page does not support.

    This is the finding that changes a campaign: bidding for appointment
    bookings against a page with no booking tool spends the budget and books
    nobody. Reported only when the page was actually read — an unmeasured page
    cannot be said to be missing anything.
    """
    if not (observed or {}).get("measured"):
        return []
    from .spec import CONVERSION_LABELS
    found = kinds_found(observed)
    out = []
    for key in wanted_actions or []:
        if key in CONVERSION_LABELS and key not in found:
            out.append({"kind": key, "label": CONVERSION_LABELS[key]})
    return out


def headings_line(observed: dict) -> str:
    """The page's headings as one line, each with the level it was set at.

    ``observe`` returns ``{"level": "h1", "text": ...}`` rows, and the level is
    half the fact: one h1 and nine h2s is a different page from nine h1s, and
    "is the headline clear" cannot be judged without knowing which of them is
    the headline.

    It lives here rather than in the module that first needed it, because it
    describes *this* function's output: two readings of one shape drift the
    day either end of it changes.
    """
    out = []
    for h in (observed or {}).get("headings") or []:
        text = str(h.get("text") or "").strip() if isinstance(h, dict) else str(h).strip()
        if not text:
            continue
        level = str(h.get("level") or "").strip() if isinstance(h, dict) else ""
        out.append(f"{level}: {text}" if level else text)
    return " | ".join(out)


def summary_line(observed: dict) -> str:
    if not (observed or {}).get("measured"):
        return "Not measured — " + ((observed or {}).get("error") or "the page could not be read.")
    points = observed.get("conversion_points") or []
    if not points:
        return ("Read the page and found no conversion point on it — no form, no "
                "click-to-call, no booking tool, no chat.")
    counts = {}
    for p in points:
        counts[p["label"].split(" with ")[0]] = counts.get(p["label"].split(" with ")[0], 0) + 1
    return " · ".join(f"{n} × {label}" if n > 1 else label for label, n in counts.items())
