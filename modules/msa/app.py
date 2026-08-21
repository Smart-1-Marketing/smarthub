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

from flask import Flask, jsonify, render_template, request, send_file

app = Flask(__name__)

# This page is mounted PUBLIC -- a client signing an agreement has no staff
# login. So every write here is reachable by anyone who finds the URL, and
# signing costs real money: a PDF render, a Cloudinary upload and a lead
# delivered into Suite. Six an hour per address is far above what a genuine
# signer needs and far below what makes a script worth writing.
SIGN_LIMIT = int(os.environ.get("MSA_SIGN_LIMIT", "6"))


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


@app.get("/")
def index():
    return render_template("index.html",
                           sections=rendered_body({"company": "{company}",
                                                   "address": "{address}",
                                                   "date": "{date}"}),
                           launch_times=LAUNCH_TIMES,
                           rate_card_note=RATE_CARD_NOTE,
                           not_ready=bool(_placeholder_count()))


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
    base = Path("/var/data") if Path("/var/data").is_dir() else \
        Path(__file__).parent.parent.parent / "data"
    d = base / "msa"
    d.mkdir(parents=True, exist_ok=True)
    return d


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
