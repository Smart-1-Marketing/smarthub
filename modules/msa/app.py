"""Master Services Agreement — sign it here, get a PDF, land as a lead.

Replaces the Smart1 Solutions page. What it does:

  1. Client fills in company name and address.
  2. Those values drop into the agreement text live, so they're reading the
     document as it will be signed rather than a template with blanks.
  3. Two checkboxes — the Agreement including the Wholesale Rate Card
     terms, and the office closures.
  4. They type their name as a signature.
  5. A PDF is generated, stored, and the link is attached to the lead.
  6. They download it.

## Deliberate differences from the original

**The closures aren't linked out.** Section 6.1.b of the agreement refers to
the days support is unavailable. Agreeing to something that lives behind a
link you may not have opened is a weak agreement, so the table is on this
page, above the checkbox that refers to it.

**The rate card is not.** Section 6.1.a says rates change, on notice, at most
once a year — so a copy pasted into this file would be the version current
when the file was last edited, presented to a client as though it were
today's. The clause is quoted; the card itself stays the separate document
the clause says it is.

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
# This is the executed MSA text, transcribed as given. Structure:
#
#   ("Heading", ["paragraph", "paragraph", ...]),
#
# Placeholders substituted at render time, in the page and in the PDF:
#   {company}  {address}  {date}
#
# Nothing else is interpreted, so an ordinary contract can be pasted almost
# verbatim. Keep the placeholders spelled exactly as above.
#
# ## Two things about this text that are deliberate
#
# **The counterparty is the signer.** The source document named Smart 1
# Marketing itself as "Partner", because it was written when TS Newstart and
# Smart 1 Marketing were two entities and Smart 1 was the one buying
# trafficking services. Now that it is one entity trading as the other, that
# wording would have every client signing an agreement between Smart 1
# Marketing and Smart 1 Marketing, naming the client nowhere -- and the
# {company} and {address} fields the page collects would appear in the
# document not at all. So "Partner" is the signing company. Anything else
# makes this page a form that produces a contract about somebody else.
#
# **Cross-references are left as they were written.** Section 5.3 says "this
# Section 4" and section 10 cites "Section 4" for confidentiality and
# "Section 7" for the clause that is actually section 8. Those are errors in
# the source, but correcting a cross-reference in an executed agreement is a
# legal edit, not a typo fix, so they are transcribed unchanged and flagged
# instead. Section 7.3 also refers to an "exhibit A" that is not part of the
# text supplied.

MSA_BODY: list[tuple[str, list[str]]] = [
    ("Parties", [
        "This Master Services Agreement (\"Agreement\") is between {company}, "
        "located at {address}, and its Affiliates (collectively \"Partner\") "
        "and TS Newstart, LLC DBA Smart 1 Marketing (\"S1M\"). This Agreement "
        "is entered into this {date} (the \"Effective Date\").",
    ]),
    ("Recitals", [
        "A. S1M is in the business of providing digital trafficking services.",
        "B. Partner desires to engage the services of S1M in connection with "
        "its digital sales efforts on the terms and conditions provided herein.",
        "NOW, THEREFORE, the parties hereto, in consideration of the mutual "
        "covenants and agreements contained herein and other good and valuable "
        "consideration, the receipt and sufficiency of which are hereby "
        "acknowledged, agree as follows:",
    ]),
    ("1. Definitions", [
        "a. \"Services\" means the delivery of ad trafficking services and "
        "related technical support services as further described herein.",
        "b. \"Affiliate\" means, with respect to an entity, any entity, whether "
        "incorporated or not, that controls, is controlled by, or under common "
        "control with the first entity or its corporate parent, where "
        "\"control\" (or variants of it) shall mean the ability (whether "
        "directly or indirectly) to direct the affairs of another by means of "
        "ownership, contract or otherwise.",
        "c. \"Partner\" shall mean {company} and all Affiliates.",
    ]),
    ("2. Services", [
        "Subject to the terms and conditions of this Agreement, S1M shall "
        "provide Services to Partner and Partner hereby engages S1M to provide "
        "the Services. During the term of the Agreement, S1M agrees, as "
        "applicable and at its sole cost and expense, to the following:",
        "1. In rendering all Services, S1M shall act at all times in the best "
        "interest of Partner and shall not take or permit any action which "
        "would disparage or bring harm to the name, reputation and goodwill of "
        "Partner or its business.",
        "2. The Services: (I) Provide forty (40) hours per week of internet "
        "order trafficking and optimization for Partner\u2019s digital local, "
        "national and agency campaigns, which shall also include the "
        "scheduling of automated reports set up 5 business days after campaign "
        "launch, Monday through Friday, 8:30 a.m. until 5:30 p.m. EST. "
        "(II) Provide a defined digital workflow process between S1M and the "
        "Partner utilizing a S1M digital Insertion Order directed to their "
        "designated support portal (orders@smart1marketing.com). "
        "(III) Provide testing of the digital creative assets and attach those "
        "assets to the corresponding campaigns to complete campaign "
        "trafficking. In addition, S1M will provide feedback to Partner\u2019s "
        "Digital Group or the client/agency directly if assets either do not "
        "meet specifications or incur problems during testing. (IV) Provide "
        "weekly outstanding digital assets reports for Partner\u2019s digital "
        "campaigns. (V) Consulting and review of services for best practices "
        "and optimization of content and revenue.",
        "3. To the extent reasonably practicable, and when advised in writing "
        "by the Partner as to the specific policy, S1M shall render all "
        "services consistent with Partner policies.",
    ]),
    ("3. Payments", [
        "1. Payment Terms: On or about the first day of each month, S1M will "
        "invoice Partner for Total Cost owed to S1M for the previous calendar "
        "month. Any discounts or credits issued will be applied to the "
        "following month\u2019s invoice. Partner agrees to pay according to the "
        "term below:",
        "\u2003a. NET 30 Terms: Partner pays this invoice in full within thirty "
        "(30) days following the date of the invoice.",
        "2. Non-Payment: If Partner does not pay the invoice within thirty (30) "
        "days following the date of the invoice, interest of 3.5% per month "
        "will be charged on the overdue balance owed to S1M.",
        "3. Cancellation of Overdue Accounts: S1M reserves the right to suspend "
        "or terminate services if invoices are more than 90 days overdue, but "
        "will provide Partner within five (5) business days notice to cure such "
        "default.",
        "4. Refunds: Any request for a refund should be made to "
        "admin@smart1marketing.com. All approved refunds will be made by check "
        "and only to the address as provided by the Partner.",
        "5. Credit Card Payments: Payments via credit card will be assessed a "
        "3.5% convenience fee for each transaction.",
    ]),
    ("4. Term", [
        "This Agreement will become effective as of the date referenced above "
        "and shall continue for an initial period of one (1) year "
        "(\"term\"). This Agreement will auto-renew annually for subsequent "
        "one (1) year periods (the initial one-year period and each subsequent "
        "renewal period being a \"Term\"). Partner will provide a written "
        "sixty (60) day notice prior to auto-renew for non-renewal of services.",
    ]),
    ("5. Confidentiality", [
        "1. Partner obligations: Partner acknowledges that the terms and "
        "conditions of this Agreement, and any other information provided to "
        "Partner by S1M marked as \"Confidential\" and/or \"Proprietary\" "
        "incorporate confidential and proprietary information developed by, "
        "acquired by, or licensed to S1M. Partner and its Affiliates will take "
        "all reasonable precautions necessary to safeguard the confidentiality "
        "of the S1M Information. Neither Partner nor its Affiliates will "
        "disclose, in whole or in part, any part of the S1M Information to any "
        "individual or entity, except to those of Partner\u2019s employees or "
        "consultants who require access for Partner\u2019s authorized use of the "
        "S1M and agree to comply with the use and nondisclosure restrictions "
        "applicable to the S1M Confidential Information under this Agreement.",
        "2. S1M Obligations: S1M acknowledges that, during the Term it will "
        "have access to Partner\u2019s confidential clientele information, "
        "Partner and clientele trademarks and other information uploaded by "
        "Partner to S1M for the purpose of order and creative insertion and "
        "that such information is confidential and proprietary (\"Partner "
        "Confidential Information\"). S1M will take all reasonable precautions "
        "necessary to safeguard the confidentiality of the Partner and "
        "clientele Confidential Information and prevent the disclosure of the "
        "Partner and clientele Confidential Information to any individual or "
        "entity, except to those employees of S1M who require access in "
        "connection with the provision of the Services hereunder.",
        "3. Exceptions: the confidentiality obligations set forth in this "
        "Section 4 shall not apply, or shall cease to apply, to information "
        "which (I) was publicly available at the time of disclosure to the "
        "other party, or (II) becomes generally known to the public after "
        "disclosure to the other party, through no fault of the other party, or "
        "(III) is disclosed under force of law, governmental regulation or "
        "court order.",
    ]),
    ("6. Service Level Agreement", [
        "1. Partner acknowledges and understands that except as otherwise "
        "provided in this Agreement, S1M does not warrant that the services "
        "will be uninterrupted or error free and that S1M may occasionally "
        "experience an outage due to internet disruptions or commit an error "
        "upon order or creative entry. Subject to the foregoing, except for "
        "system upgrades, the Services provided shall be fully functional and "
        "operational not less than ninety-seven percent (97%) of the time, "
        "eight (8) hours per day, Monday through Friday.",
        "\u2003a. Service Pricing:",
        "\u2003\u20031. Partner is provided with a Wholesale Rate Card. Service "
        "Provider will invoice based on the wholesale pricing shown.",
        "\u2003\u20032. For items on the Rate Card that require a Custom Quote, "
        "Service Provider will return pricing as a Retail cost to Partner.",
        "\u2003\u20033. Hourly rates will be invoiced in increments no lower than "
        "15 minutes.",
        "\u2003\u20034. Rates are determined by industry demand. Rates are subject "
        "to change. Changes will be communicated 30 days prior to "
        "implementation.",
        "\u2003\u20035. There will be no more than 1 rate change per year. Any such "
        "rate increase shall not be effective as to existing contracts for a "
        "term of ninety (90) days following implementation of the new rate.",
        "\u2003b. Office Closures: there will be no support available on the days "
        "set out in the Office Closures table below.",
        "\u2003\u2003S1M also reserves the right for up to 3 additional office "
        "closures throughout the year. Partner will be sent written "
        "notification a minimum of 2 weeks in advance with 1 follow up the week "
        "prior and a reminder the day before.",
    ]),
    ("7. Partner Obligations", [
        "Partner agrees with the following:",
        "1. Separate written Nondisclosure agreement exists between S1M and "
        "Partner. That agreement will control and apply according to its terms "
        "and conditions to all confidential information.",
        "2. Partner is responsible for the accuracy, quality, and legality of "
        "the contracts sent to S1M and the means by which they were acquired.",
        "3. The Partner shall provide reasonable assistance, cooperation, "
        "timely decisions and support in connection with the provision of "
        "Services by S1M. Please see exhibit A.",
        "4. Creative Asset access to digital creative versus server based "
        "and/or log-ins to Partner creative producers.",
        "5. Partner shall pay each invoice no later than thirty (30) days after "
        "receipt.",
    ]),
    ("8. Force Majeure", [
        "To the extent S1M is prevented from performing any of its obligations "
        "hereunder due to circumstances reasonably beyond its control "
        "(including, but not limited to, the action or inaction of any "
        "governmental, civil, or military authority; a strike, lockout or other "
        "labor dispute; or a fire, flood, war, riot, theft, earthquake or other "
        "natural disaster, acts of terrorism or other civil disturbance) and "
        "not involving such party\u2019s negligence, such party shall not be "
        "liable to the other party for any losses or damages arising out of "
        "such non-performance. In the event a party hereto is prevented from "
        "meeting its obligations by such circumstances, and such party is "
        "unable to provide assurances that recovery will occur within five (5) "
        "days, or recovery fails to occur within five (5) days, the other party "
        "hereto shall have the right to terminate this Agreement, effective "
        "thirty (30) days upon delivery of written notice of the same to the "
        "other party, and no party shall be liable to any other arising out of "
        "such termination, except for obligations existing prior to such "
        "termination.",
    ]),
    ("9. Independent Contractor", [
        "Nothing in this Agreement shall create any joint venture or "
        "principal-agent relationship between Partner and S1M. No other person "
        "shall be deemed to be a third party beneficiary of this Agreement. S1M "
        "agrees to furnish the Services as provided herein as an independent "
        "contractor using its own means. S1M shall select and shall have full "
        "and complete control of and responsibility for all employees employed "
        "or used by S1M in the conduct of S1M independent business and none of "
        "said employees shall be, or be deemed to be, the employee of Partner "
        "for any purpose whatsoever, and Partner shall have no duty, liability "
        "or responsibility, of any kind, to or for the acts or omissions of "
        "such agents or employees of S1M. S1M further agrees to comply with all "
        "laws governing employees and agrees to accept exclusive liability for "
        "the payment of any payroll taxes or contributions for unemployment "
        "insurance or old age pensions or annuities or social security payments "
        "which are measured by the wages, salaries, or other remuneration paid "
        "to the employees of S1M. S1M agrees to comply with all valid "
        "administrative regulations respecting the assumption of liability for "
        "such taxes and contributions.",
    ]),
    ("10. Termination", [
        "After the initial Term, either party may terminate this Agreement "
        "prior to expiration of its Term via written notice in advance of at "
        "least ninety (90) days or immediately in the event of the other "
        "party\u2019s material breach of the confidentiality obligations set "
        "forth in Section 4 hereof. Further, either party may terminate this "
        "Agreement prior to expiration of its Term: (I) in the event of the "
        "other party\u2019s material breach of any of the provisions hereof and "
        "the failure of the breaching party to cure such breach to the "
        "reasonable satisfaction of the non-breaching party within fifteen (15) "
        "days after receipt of written notice informing it of such material "
        "breach, (II) in accordance with the provisions of Section 7 hereof, or "
        "(III) in the event a petition seeking composition of creditors, the "
        "protection afforded by the United States Bankruptcy Code or benefit of "
        "other laws affecting the rights of creditors generally is filed by or "
        "against the other party and such petition remains unstayed or "
        "undismissed for a period of thirty (30) days. Upon termination of this "
        "Agreement, all Services provided to the Partner hereunder will "
        "terminate.",
    ]),
    ("11. Intellectual Property", [
        "S1M shall retain all right, title, and interest, including copyright, "
        "computer programming code (including object code and source code), and "
        "creative materials provided under the Agreement. Partner retains all "
        "right, title and interest in the creative materials (the \"works\") "
        "subject to S1M\u2019s underlying ownership in the platform and the tools "
        "and any templates used in connection with the services provided under "
        "this Agreement. To the extent permissible by applicable law, all such "
        "works shall be deemed works made for hire under U.S. Copyright law. "
        "With respect to all other rights of intellectual property of any type "
        "related to the works, and to the extent any work is not a work made "
        "for hire, Partner hereby assigns to S1M all right, title, and interest "
        "in such works and all intellectual property related thereto. S1M may "
        "use and distribute works as part of its portfolio for promotional "
        "purposes.",
    ]),
    ("12. General", [
        "The parties agree to indemnify, defend and hold each other harmless "
        "for liabilities arising or allegedly arising out of the actions or "
        "omissions of their respective agents. The terms herein are severable "
        "allegedly arising out of the action or omissions of their respective "
        "agents. The terms herein are severable and independently enforceable, "
        "and are to be applied in accord with Ohio law (without regard to "
        "conflicts of laws principles), with venue and jurisdiction in any "
        "court of competent jurisdiction located in Franklin County, Ohio. This "
        "Agreement may be modified only in a signed, written document executed "
        "by representatives having actual authority to act on behalf of each of "
        "the parties hereto. This Agreement was subject to negotiation and "
        "mutual compromise jointly by the parties prior to signature and "
        "neither party is entitled to any favorable presumptions regarding how "
        "it is to be interpreted.",
    ]),
    ("13. Non-Solicitation", [
        "1. Employee Non-Solicit: During the term of engagement with S1M and "
        "for twenty-four (24) months following the termination of services "
        "contract for any reason Partner agrees not to directly or indirectly, "
        "hire, solicit, retain, or encourage to leave the employ of S1M (or "
        "assist any other person or entity in hiring, soliciting, retaining or "
        "encouraging) any person who is then or was within six (6) months of "
        "the date of contract end, an employee of S1M.",
        "2. Customer Non-Solicit: During the contract period and for "
        "twenty-four (24) months following the termination of services, Partner "
        "shall not, directly or indirectly, solicit or induce, or attempt to "
        "solicit or induce, any customer, supplier, licensee, licensor or other "
        "business relation of S1M to terminate its relationship or contract "
        "with the Partner, to cease doing business with the Company, or in any "
        "way interfere with the relationship between any such customer, "
        "supplier, licensee or business relation and S1M (including making any "
        "negative statements or communications concerning S1M or their "
        "employees).",
    ]),
]

# Section 6.1.b refers to this table rather than to a separate page. The
# agreement's own words, in the agreement, beats a link the signer may not
# have opened -- which is why the closures are set out here in full.
#
# This replaced a table of campaign launch times. Those were never in the
# MSA: they were written for a draft of this page, and the checkbox beside
# them asked the signer to agree to "the campaign launch times set out
# above". Once the real agreement went in, nothing was set out above, and a
# signed statement of agreement to a document that does not exist is worse
# than a missing feature.
OFFICE_CLOSURES: list[tuple[str, str]] = [
    ("New Year\u2019s Day", "Closed"),
    ("Memorial Day", "Closed"),
    ("Independence Day", "Closed"),
    ("Labor Day", "Closed"),
    ("Thanksgiving", "Closed"),
    ("Friday after Thanksgiving (Black Friday)", "Closed"),
    ("Christmas Eve", "Open 8:30 a.m. \u2013 12:00 p.m."),
    ("Christmas Day", "Closed"),
    ("New Year\u2019s Eve", "Open 8:30 a.m. \u2013 12:00 p.m."),
]

# Section 6.1.a, in the signer's own words rather than ours. The rate card is
# a separate document by design: it changes, on the notice terms the clause
# sets out, and a copy pasted in here would be the version that was current
# when this file was last edited.
RATE_CARD_NOTE = (
    "Partner is provided with a Wholesale Rate Card and is invoiced on the "
    "wholesale pricing shown. Items requiring a Custom Quote are returned as "
    "a Retail cost. Hourly rates are invoiced in increments no lower than 15 "
    "minutes. Rates are determined by industry demand and are subject to "
    "change on 30 days\u2019 notice, with no more than one rate change per year; "
    "a rate increase does not take effect on an existing contract for ninety "
    "(90) days after implementation. See section 6 above for the full terms.")


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
                           office_closures=OFFICE_CLOSURES,
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
    if not (body.get("agree_terms") and body.get("agree_closures")):
        # Both boxes, server-side. A checkbox enforced only in the browser is
        # not evidence that anyone agreed to anything.
        return jsonify({"error": "Both agreements must be accepted."}), 400

    signed_at = datetime.now(timezone.utc)
    ctx = {"company": company, "address": address,
           "date": signed_at.strftime("%B %-d, %Y") if os.name != "nt"
                   else signed_at.strftime("%B %d, %Y")}

    from .pdf import build_msa_pdf
    pdf_bytes = build_msa_pdf(
        sections=rendered_body(ctx), office_closures=OFFICE_CLOSURES,
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
                    "agreed_agreement": "yes", "agreed_office_closures": "yes",
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
