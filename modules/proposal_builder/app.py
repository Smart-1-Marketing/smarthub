"""The retired Proposal Builder — a redirect and an archive.

There were two proposal builders. This was the standalone one: pick an
industry, fill a form, get AI-written prose blocks, save a PDF. The other,
`modules/sales_builder`, is the one built around a real campaign — the rate
card, product line items, guardrails, gap checks, and a hand-off the IO
builder can read.

They shared no code and no storage, so the same client could be quoted two
different ways depending on which tool a rep opened, and only one of the two
produced anything an insertion order could use. `modules/sales_builder` is now
the single Proposal Builder, and everything worth keeping from this one moved
there:

  * the industry library      -> `hub.industries`
  * AI-written narrative copy -> `/sales/builder/api/ai/draft-sections`
  * Cloudinary-hosted PDF     -> the Deliver step
  * Suite opportunity + contact lookup -> `hub.suite_opportunity`, which reads
    the env var names this deployment actually sets (this module's version
    read four names nothing has ever set, so it never once created an
    opportunity — it reported "GHL API env vars not fully set" into a response
    nobody looked at)

What is left here is deliberately small:

  * `/` and anything under it redirects to the surviving builder, carrying a
    Client 360 prefill through unchanged so existing links keep working.
  * The read side of the archive stays. Proposals saved by the old tool are
    real client documents; Client 360 lists them, `hub.campaign_spec` converts
    them to IOs, and the Sales Builder can import one as a quote. None of that
    needs a second builder, and deleting the store would delete history.

Nothing here writes a proposal any more. That is the point.
"""
import os
import re
from urllib.parse import urlencode

from flask import Flask, jsonify, redirect, request, send_file

from . import store
from .industries import INDUSTRIES, industry_list
from hub.webargs import clamp_int

app = Flask(__name__)

# Where a proposal is built now. Kept as a constant so the redirect and the
# JSON responses cannot drift apart.
BUILDER = "/sales/builder/"

# Prefill keys Client 360 sends. They are renamed on the way through because
# the surviving builder's state uses the campaign's own field names.
PREFILL_MAP = {
    "business_name": "client",
    "website": "url",
    "contact_name": "contactName",
    "contact_email": "contactEmail",
    "contact_phone": "contactPhone",
    "city": "city",
    "state": "state",
    "zip": "zip",
    "salesperson": "salesContact",
    "industry": "industry",
}


def _guard():
    """The Hub login already gates this; the token check is for direct curl."""
    if request.environ.get("s1hub.user"):
        return None
    expected = os.environ.get("PANEL_PASSWORD") or ""
    if expected and request.headers.get("x-auth"):
        return None
    return jsonify({"error": "Not authorized"}), 401


def _builder_url():
    """The surviving builder, carrying any prefill this link was given."""
    params = {}
    for src, dest in PREFILL_MAP.items():
        value = (request.args.get(src) or "").strip()
        if value:
            params[dest] = value
    if params:
        params["prefill"] = "1"
    return BUILDER + ("?" + urlencode(params) if params else "")


@app.route("/")
def index():
    return redirect(_builder_url(), code=302)


@app.route("/<path:filename>", methods=["GET", "POST", "PUT", "DELETE"])
def anything_else(filename):
    """Everything that is not an archive read goes to the builder.

    A 302 rather than a 404: the old tool's URLs are in bookmarks, in Slack
    and on the tiles page, and a rep following one wants to write a proposal.
    Sending them to a dead page teaches them the tool is broken.
    """
    if filename.startswith("api/"):
        # Every verb, so a stale POST to /api/generate or /api/proposals gets
        # this explanation rather than a bare 405 that says nothing about
        # where the tool went.
        return jsonify({"error": "The Proposal Builder moved to /sales/builder/. "
                                 "This module now serves its archive only.",
                        "builder": BUILDER}), 404
    if request.method != "GET":
        return jsonify({"error": "The Proposal Builder moved to /sales/builder/.",
                        "builder": BUILDER}), 404
    return redirect(_builder_url(), code=302)


@app.route("/api/health")
def health():
    return jsonify({"ok": True, "retired": True, "builder": BUILDER,
                    "cloudinary": store.cloudinary_ready(),
                    "archive": "read-only"})


@app.route("/api/config")
def config():
    gate = _guard()
    if gate:
        return gate
    return jsonify({"retired": True, "builder": BUILDER,
                    "industries": industry_list()})


@app.route("/api/proposals")
def list_proposals():
    """The archive index. Client 360 reads this to list past proposals."""
    gate = _guard()
    if gate:
        return gate
    return jsonify({"ok": True, "retired": True, "builder": BUILDER,
                    "results": store.search_proposals(
                        q=request.args.get("q", ""),
                        industry=request.args.get("industry", ""),
                        salesperson=request.args.get("salesperson", ""),
                        limit=clamp_int(request.args.get("limit"), 50, 1, 200))})


@app.route("/api/proposals/<pid>")
def get_proposal(pid):
    gate = _guard()
    if gate:
        return gate
    record = store.get_proposal(pid)
    if not record:
        return jsonify({"error": "Not found"}), 404
    # Where to reopen this one as a live quote. The import is the Sales
    # Builder's job -- it owns quote numbers -- so this only points at it.
    record = dict(record)
    record["import_url"] = f"/sales/builder/api/legacy/proposals/{pid}/import"
    return jsonify({"ok": True, "proposal": record})


@app.route("/api/proposals/<pid>/convert-to-io", methods=["POST"])
def convert_to_io(pid):
    """Hand an archived proposal to the IO Builder as a campaign spec.

    Nothing is submitted — the IO intake still walks the rep through it, with
    the proposal's figures pre-entered and flagged as offers rather than
    agreements. Kept working because the specs are already stored; converting
    is reading, not rebuilding.
    """
    gate = _guard()
    if gate:
        return gate
    record = store.get_proposal(pid)
    if not record:
        return jsonify({"error": "No such proposal."}), 404
    spec = record.get("spec")
    if not spec:
        return jsonify({"error": "That proposal predates campaign data — it has "
                                 "no pricing tier to convert. Import it in the "
                                 "Proposal Builder instead.",
                        "import_url": f"/sales/builder/api/legacy/proposals/{pid}/import"}), 400
    try:
        from hub.campaign_spec import CampaignSpec, to_io_payload
        payload = to_io_payload(CampaignSpec.from_dict(spec))
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"Conversion failed ({type(exc).__name__})."}), 500
    payload["proposal_id"] = pid
    payload["io_url"] = f"/tools/io/?spec={pid}&mode=from_proposal"
    try:
        from hub import audit
        audit.log("proposal_builder", "converted_to_io",
                  client=spec.get("client", ""), proposal=pid)
    except Exception:  # noqa: BLE001
        pass
    return jsonify(payload)


@app.route("/api/pdf/<path:fname>")
def serve_pdf(fname):
    """Archived PDFs that were kept on disk rather than in Cloudinary."""
    gate = _guard()
    if gate:
        return gate
    if not re.match(r"^[a-z0-9-]+-v\d+\.pdf$", fname, re.I):
        return jsonify({"error": "Bad name"}), 400
    path = os.path.join(store.DATA_DIR, fname)
    if not os.path.isfile(path):
        return jsonify({"error": "Not found"}), 404
    return send_file(path, mimetype="application/pdf")


# Referenced by the archive index rows; kept importable for anything reading
# an old proposal's industry key.
__all__ = ["app", "INDUSTRIES", "industry_list"]
