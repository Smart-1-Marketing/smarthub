"""Master Services Agreement — sign it here, get a PDF, land as a lead.

Replaces the Smart1 Solutions page. What it does:

  1. Client fills in company name and address.
  2. Those values drop into the agreement text live, so they're reading the
     document as it will be signed rather than a template with blanks.
  3. Two checkboxes — the rate card, and the launch times.
  4. They type their name as a signature.
  5. A PDF is generated, stored, and the link is attached to the lead.
  6. They download it.

## Deliberate differences from the original

**Launch times aren't linked out.** The original pointed at a separate
Campaign Launch Times page. Agreeing to something that lives behind a link
you may not have opened is a weak agreement — the times are on this page,
above the checkbox that refers to them.

**No email.** The PDF link is saved on the lead instead. Nobody has to trust
that a message arrived, and the copy is findable months later against the
client rather than in someone's sent items.

**The agreement text lives in one place** — `MSA_BODY` below. It is the
contract; it should not be scattered through markup or duplicated between the
page and the PDF. Both render from this.
"""
from __future__ import annotations

import os
import re
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path

from flask import (Flask, Response, jsonify, make_response, redirect,
                   render_template, request, send_file, url_for)

from hub import audit

app = Flask(__name__)

# A signed agreement is client work, and until now none of it reached the
# activity log -- /api/integrity has been reporting this module as
# unattributable since it shipped. Logged with client=, so a signature shows
# up on that client's 360 record next to everything else we have done for
# them, which is where anyone would look for it.
log = audit.for_module("msa")

# This page is mounted PUBLIC -- a client signing an agreement has no staff
# login. So every write here is reachable by anyone who finds the URL, and
# signing costs real money: a PDF render, a Cloudinary upload and a lead
# delivered into Suite. Six an hour per address is far above what a genuine
# signer needs and far below what makes a script worth writing.
SIGN_LIMIT = int(os.environ.get("MSA_SIGN_LIMIT", "6"))

# Who may put this page in an iframe. An ALLOWLIST, not the "*" the scans
# widget uses -- that one is framed on clients' own domains and has to
# accept any of them, whereas this is only ever framed on ours and it
# submits a legally binding signature. A page anyone can frame is a page
# anyone can lay a transparent button over. Space-separated, CSP syntax.
FRAME_ANCESTORS = os.environ.get(
    "MSA_FRAME_ANCESTORS",
    "'self' https://smart1marketing.com https://*.smart1marketing.com"
).strip()


def _rate_limited(bucket: str, limit: int) -> bool:
    """Shared limiter, the same one the landing pages use. Never raises: a
    guard that 500s the page it protects has failed twice."""
    try:
        from hub import leads as _hub_leads
        return _hub_leads.rate_limited(bucket, request, limit)
    except Exception:                                   # noqa: BLE001
        return False

# ---------------------------------------------------------------------------
# The agreement
# ---------------------------------------------------------------------------
#
# PASTE THE REAL MSA TEXT HERE. Structure:
#
#   ("Heading", ["paragraph", "paragraph", ...]),
#
# Placeholders substituted at render time, in the page and in the PDF:
#   {company}  {address}  {date}
#
# Nothing else is interpreted, so an ordinary contract can be pasted almost
# verbatim. Keep the placeholders spelled exactly as above.

MSA_BODY: list[tuple[str, list[str]]] = [
    ("1. Parties", [
        "This Master Services Agreement (\"Agreement\") is entered into on "
        "{date} between Smart 1 Marketing (\"Smart 1\") and {company}, "
        "located at {address} (\"Client\").",
    ]),
    ("2. Services", [
        "PLACEHOLDER — paste the Services section from the current MSA here.",
    ]),
    ("3. Fees and Rate Card", [
        "PLACEHOLDER — paste the Fees section here.",
    ]),
    ("4. Term and Termination", [
        "PLACEHOLDER — paste the Term section here.",
    ]),
]

# Shown above the checkbox that refers to them, rather than behind a link.
LAUNCH_TIMES: list[tuple[str, str]] = [
    ("Connected TV / Streaming Audio", "5–7 business days from receipt of "
                                       "approved creative and tracking access."),
    ("Paid Search / Paid Social", "3–5 business days from account access."),
    ("Data-Targeted Display", "5–7 business days from approved creative."),
    ("Website SEO", "Work begins within 5 business days; results are "
                    "cumulative over the term."),
    ("Creative production", "Add 5–10 business days when Smart 1 is producing "
                            "the creative."),
]

RATE_CARD_NOTE = ("Pricing follows the Smart 1 rate card in force on the date "
                  "of signature. Rates are confirmed on each Insertion Order "
                  "before any campaign begins.")


def _sub(text: str, ctx: dict) -> str:
    for key, val in ctx.items():
        text = text.replace("{" + key + "}", str(val or ""))
    return text


def rendered_body(ctx: dict) -> list[dict]:
    return [{"heading": h, "paragraphs": [_sub(p, ctx) for p in ps]}
            for h, ps in MSA_BODY]


def _page(embedded: bool = False) -> str:
    """The signing page. One render, two framings.

    The embedded framing drops the Smart 1 header bar and the footer -- the
    host page already carries both, and a second logo halfway down someone
    else's page reads as a broken paste rather than an embed.
    """
    return render_template("index.html",
                           sections=rendered_body({"company": "{company}",
                                                   "address": "{address}",
                                                   "date": "{date}"}),
                           launch_times=LAUNCH_TIMES,
                           rate_card_note=RATE_CARD_NOTE,
                           not_ready=bool(_placeholder_count()),
                           embedded=embedded)


@app.get("/")
def index():
    return _page()


# ---------------------------------------------------------------------------
# Embedding on smart1marketing.com
# ---------------------------------------------------------------------------

def _framable(resp):
    """Let the allowlisted hosts frame this response, and nobody else."""
    resp.headers["Content-Security-Policy"] = (
        "frame-ancestors " + FRAME_ANCESTORS)
    # X-Frame-Options has no allowlist form: any value it could carry would
    # either forbid the embed outright or be honoured inconsistently, and
    # some browsers let it override CSP. Dropping it leaves one rule in
    # charge of the answer rather than two that can disagree.
    resp.headers.pop("X-Frame-Options", None)
    return resp


@app.get("/embed")
def embed():
    """The signing page for an iframe on the marketing site.

    No trailing slash, deliberately. The page's own call is written
    ``fetch('api/sign')`` -- a same-origin literal, which is the form
    tools/linkcheck.py can actually verify -- and a relative path resolves
    against the *directory* of the current URL. From ``/msa/embed`` that is
    ``/msa/api/sign``, which is right; from ``/msa/embed/`` it would be
    ``/msa/embed/api/sign``, which is a 404 nobody meets until they have
    filled in the whole agreement and pressed sign.
    """
    return _framable(make_response(_page(embedded=True)))


@app.get("/embed/")
def embed_slash():
    """A pasted trailing slash redirects rather than 404s.

    The distinction above is real but invisible to whoever is pasting the
    snippet into a page builder, and getting it wrong would show a client an
    empty frame on the marketing site. Redirecting costs one hop and means
    both spellings land on the URL whose relative API path resolves.

    url_for rather than a literal: this app is mounted under /msa by the
    dispatcher, so a hand-written "/embed" would point at the hub app and a
    relative "../embed" is resolved by the client. url_for asks the request
    for its own script root and gets /msa/embed under the mount and /embed
    standalone, which is right in both.
    """
    return redirect(url_for("embed"), code=308)


@app.get("/embed.js")
def embed_js():
    """One-line drop-in: writes the iframe and keeps it the right height.

    The Hub's URL appears once, in the script's own ``src``, and everything
    else is derived from it -- so moving the Hub to another host is a
    one-word edit on the marketing site rather than a hunt through a block
    of pasted markup.

    A contract is tall, and a tall document inside a fixed iframe means a
    scrollbar inside a scrollbar -- the single most reliable way to make a
    client abandon a signing page. So the frame reports its own height.
    """
    # Raw: the two regexes below carry \/ and \. and \?, which Python does
    # not recognise as escapes. It keeps them today and warns, and a future
    # release makes that an error -- at which point the loader silently stops
    # matching and every embed on the marketing site shows a frozen frame.
    js = r"""(function(){
  var s = document.currentScript;
  if (!s || !s.src) return;
  var base = s.src.replace(/\/embed\.js(\?.*)?$/, '');
  var origin = base.replace(/^(https?:\/\/[^\/]+).*$/, '$1');

  var frame = document.createElement('iframe');
  frame.src = base + '/embed';
  frame.title = 'Smart 1 Marketing — Master Services Agreement';
  frame.loading = 'lazy';
  frame.setAttribute('scrolling', 'no');
  frame.style.cssText = 'display:block;width:100%;border:0;' +
                        'height:' + (s.getAttribute('data-height') || '1200') + 'px;';
  s.parentNode.insertBefore(frame, s);

  window.addEventListener('message', function(e){
    /* Both checks, not either. The origin says the message came from the
       Hub; the source says it came from THIS frame rather than another
       Hub embed further down the same page. */
    if (e.origin !== origin) return;
    if (e.source !== frame.contentWindow) return;
    var d = e.data || {};
    if (d.type === 's1msa:height' && d.height) {
      frame.style.height = d.height + 'px';
    } else if (d.type === 's1msa:signed') {
      /* The confirmation renders at the top of the frame, which may be
         above the fold on the host page -- so a client who has just signed
         would be looking at whitespace. */
      try { frame.scrollIntoView({behavior: 'smooth', block: 'start'}); }
      catch (err) { frame.scrollIntoView(); }
    }
  });
})();"""
    return _framable(Response(
        js, mimetype="application/javascript",
        headers={"Cache-Control": "public, max-age=3600"}))


def _placeholder_count() -> int:
    """How many clauses are still template text.

    Asked by the health check and by the signature route, so it lives in one
    place. While this is non-zero the agreement cannot be signed.
    """
    return sum(1 for _, ps in MSA_BODY for p in ps if "PLACEHOLDER" in p)


@app.get("/health")
def health():
    placeholders = _placeholder_count()
    return jsonify({
        "status": "ok", "service": "msa",
        "sections": len(MSA_BODY),
        "placeholder_sections": placeholders,
        "ready": placeholders == 0,
        "note": ("The real agreement text hasn't been pasted into MSA_BODY yet "
                 "— the page will show PLACEHOLDER where the clauses go."
                 if placeholders else "Agreement text is in place."),
    })


@app.post("/api/sign")
def sign():
    """Record the signature, build the PDF, save it against the lead."""
    # The agreement text is still the template. Someone signing this would be
    # agreeing to the word PLACEHOLDER, which is not an agreement -- and is
    # worse than the page being unavailable, because it looks like one. So it
    # refuses, and says why.
    if _placeholder_count():
        return jsonify({"error": "This agreement isn't ready to sign yet. The "
                                 "clause text hasn't been finalised. Please "
                                 "contact your Smart 1 representative."}), 503

    if _rate_limited("msa-sign", SIGN_LIMIT):
        return jsonify({"error": "Too many attempts from this connection. "
                                 "Wait a few minutes and try again."}), 429

    body = request.get_json(silent=True) or {}
    company = str(body.get("company") or "").strip()
    address = str(body.get("address") or "").strip()
    signer = str(body.get("signer") or "").strip()
    email = str(body.get("email") or "").strip()

    missing = [label for label, val in
               (("company name", company), ("address", address),
                ("your name", signer), ("email", email)) if not val]
    if missing:
        return jsonify({"error": "Still needed: " + ", ".join(missing)}), 400
    if not (body.get("agree_rates") and body.get("agree_launch")):
        # Both boxes, server-side. A checkbox enforced only in the browser is
        # not evidence that anyone agreed to anything.
        return jsonify({"error": "Both agreements must be accepted."}), 400

    signed_at = datetime.now(timezone.utc)
    ctx = {"company": company, "address": address,
           "date": signed_at.strftime("%B %-d, %Y") if os.name != "nt"
                   else signed_at.strftime("%B %d, %Y")}

    from .pdf import build_msa_pdf
    pdf_bytes = build_msa_pdf(
        sections=rendered_body(ctx), launch_times=LAUNCH_TIMES,
        rate_card_note=RATE_CARD_NOTE,
        company=company, address=address, signer=signer,
        signed_at=signed_at, ip=_client_ip())

    # Company slug plus timestamp reads well in a filename, but it is also
    # guessable -- and the download route is public, so a guessed token
    # hands over another company's signed contract, with its address,
    # signer name and IP on it. The random tail makes the URL a
    # capability rather than a name.
    token = (re.sub(r"[^a-z0-9]+", "-", company.lower()).strip("-")[:50]
             + "-" + signed_at.strftime("%Y%m%d%H%M%S")
             + "-" + secrets.token_hex(8))
    _store_pdf(token, pdf_bytes)
    pdf_url = _upload(pdf_bytes, token, company)

    try:
        from hub import leads as hub_leads
        hub_leads.capture_and_deliver(
            source="msa", page="Master Services Agreement",
            fields={"name": signer, "email": email,
                    "phone": str(body.get("phone") or ""),
                    "company": company, "address": address,
                    "signed_at": signed_at.isoformat(timespec="seconds"),
                    "agreed_rate_card": "yes", "agreed_launch_times": "yes",
                    "signed_ip": _client_ip()},
            pdf_url=pdf_url, client=company,
            meta={"agreement": "MSA", "token": token})
    except Exception:                                   # noqa: BLE001
        app.logger.exception("MSA lead capture failed")

    # After the lead write, not before: this records what actually happened,
    # and whether the durable copy exists is part of that. A signature filed
    # with pdf_url empty is a real state -- Cloudinary unconfigured -- and
    # the log should say so rather than imply a stored contract.
    log("signed", client=company, signer=signer, token=token,
        stored=bool(pdf_url))

    return jsonify({
        "ok": True, "token": token, "pdf_url": pdf_url,
        "download": f"pdf/{token}",
        "note": f"Signed by {signer} for {company}.",
    })


@app.get("/pdf/<token>")
def download(token: str):
    """Serve the signed PDF back for download."""
    safe = re.sub(r"[^a-z0-9\-]", "", token.lower())[:80]
    path = _pdf_path(safe)
    if not safe or not path.exists():
        return jsonify({"error": "That agreement isn't available. It may have "
                                 "been signed on another deploy — the copy on "
                                 "the lead record is the durable one."}), 404
    return send_file(path, mimetype="application/pdf", as_attachment=True,
                     download_name=f"{safe}-msa.pdf")


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

def _pdf_dir() -> Path:
    """Where the convenience copy of a signed PDF sits.

    jsonstore.data_dir() rather than this module's own /var/data probe, per
    CLAUDE.md: the answer to "where is the data directory" belongs in one
    place, and this module having its own copy is how the two spellings drift
    apart. The PDFs themselves are deliberately NOT mirrored into the
    database -- they are binaries, not JSON, and Cloudinary already holds the
    durable copy, which is what /pdf/<token> says when a file is missing.
    """
    from hub import jsonstore
    return Path(jsonstore.data_dir("msa"))


def _pdf_path(token: str) -> Path:
    return _pdf_dir() / f"{token}.pdf"


def _store_pdf(token: str, data: bytes) -> None:
    try:
        _pdf_path(token).write_bytes(data)
    except OSError:
        pass          # Cloudinary is the durable copy; local is convenience


def _upload(pdf_bytes: bytes, token: str, company: str = "") -> str:
    """Store the signed agreement through the shared uploader.

    hub.storage already knows a PDF has to be stored raw -- an image-type PDF
    only delivers when the Cloudinary account has PDF delivery switched on,
    which it is not, so the client's link 403s and the tool looks broken.
    Calling the shared uploader keeps that in one place, per CLAUDE.md, and
    means this module is not the sixteenth to configure Cloudinary itself.
    """
    try:
        from hub import storage
        asset = storage.put("proposals", f"{token}.pdf", pdf_bytes,
                            subpath="msa", client=company,
                            tags=["msa", "signed-agreement"],
                            context={"agreement": "MSA", "company": company})
        return asset.url or ""
    except Exception as exc:                            # noqa: BLE001
        app.logger.warning("MSA upload failed: %s", exc)
        return ""


def _client_ip() -> str:
    fwd = request.headers.get("X-Forwarded-For", "")
    return (fwd.split(",")[-1].strip() if fwd else request.remote_addr) or ""
