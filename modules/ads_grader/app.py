"""Smart 1 Ads Grader — a stranger's Google Ads account, scored, at /tools/ads-grader.

A prospect types their details, connects their own Google Ads account
read-only, and gets a scored report. Every one of them becomes a lead in the
Hub's own store, which is the point of the tool.

The rules that matter, in the order they bite.

**The lead is captured BEFORE OAuth starts.** A prospect who filled in the
form and then abandoned Google's consent screen is still a prospect who told
us who they are, and losing them to a step that has not happened yet is the
failure `modules/scans`' own capture path is built to avoid.

**No credential is ever stored.** The access token is a local variable in the
callback: it is used, it goes out of scope with the request, and there is no
table, setting or session that could hold it. `access_type=online`, so Google
never issues a refresh token to lose in the first place.

**Every page here is served to somebody with no Hub account.**
`PUBLIC_PREFIXES` is read by `wsgi.py` for both halves of the mount -- the
AuthGuard, so a stranger can open it, and HubBar, so the staff sidebar, help
layer and feedback tab are not injected into a page a prospect reads.

**Revoked, deleted and never-existed answer the same 404.** A client-facing
URL that says "that one expired" tells somebody probing which tokens are real.
"""
from __future__ import annotations

import logging
from pathlib import Path

from flask import Flask, jsonify, redirect, render_template, request

from . import VERSION, VERSION_DATE, grading, store

log = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent
app = Flask(__name__, template_folder=str(BASE_DIR / "templates"))
app.config.update(JSON_SORT_KEYS=False)

MOUNT = "/tools/ads-grader"

# Everything in this module is public: it is a lead magnet, and there is no
# staff screen here at all. One list, so the mount and the module can never
# disagree about what a stranger may reach -- the arrangement modules/scans and
# modules/ads_builder already use.
PUBLIC_PREFIXES = ("/",)

try:
    from hub import leads as hub_leads
except Exception:                                        # noqa: BLE001
    hub_leads = None


def _client_ip() -> str:
    """The address the rate limiter keys on, through the Hub's one reading.

    `client_ip(headers, remote_addr)` takes both -- called with neither it
    raised TypeError straight into the guard below, so the shared LAST-HOP
    rule never ran and this fell back to a fourth longhand copy of it.
    hub/auth.py's own docstring is about exactly that: the first entry in
    X-Forwarded-For is supplied by the client and trivially spoofed, it was
    written out longhand at four call sites, and one of them had it backwards.
    The fallback stays for a module running outside the Hub, and never raises.
    """
    try:
        from hub.auth import client_ip
        return client_ip(request.headers, request.remote_addr or "")
    except Exception:                                    # noqa: BLE001
        return (request.headers.get("X-Forwarded-For", "").split(",")[-1].strip()
                or request.remote_addr or "")


def _report_url(token: str) -> str:
    try:
        from hub.config import public_base_origin
        base = (public_base_origin() or "").rstrip("/")
    except Exception:                                    # noqa: BLE001
        base = ""
    return f"{base}{MOUNT}/r/{token}" if base else f"{MOUNT}/r/{token}"


@app.get("/")
def page_start():
    ok, missing = grading.configured()
    return render_template("grader_start.html", mount=MOUNT, configured=ok,
                           missing=missing, version=VERSION)


@app.post("/api/start")
def api_start():
    """Capture the lead, then hand back the Google sign-in URL.

    In that order, and it is the whole design: a prospect who abandons the
    consent screen has still told us who they are, and a capture that ran
    afterwards would lose every one of them.
    """
    body = request.get_json(silent=True) or {}
    email = str(body.get("email") or "").strip()
    phone = str(body.get("phone") or "").strip()
    company = str(body.get("company") or "").strip()
    if not company:
        return jsonify({"error": "Tell us the business name."}), 400
    if not email and not phone:
        # A lead with neither an email nor a phone number reads as a live
        # prospect on every count that follows and can be contacted by nobody
        # -- the refusal modules/ads_builder arrived at independently.
        return jsonify({"error": "We need an email address or a phone number "
                                 "so we can send your report."}), 400

    if hub_leads is not None:
        allowed, wait = hub_leads.rate_check(_client_ip())
        if not allowed:
            return jsonify({"error": "That is a lot of requests from one place. "
                                     f"Try again in about {wait} seconds."}), 429

    lead_id = ""
    if hub_leads is not None:
        try:
            answer = hub_leads.capture_and_deliver(
                source="ads_grader", page="ads-grader",
                fields={"name": str(body.get("name") or "").strip(),
                        "email": email, "phone": phone, "company": company,
                        "website": str(body.get("website") or "").strip()},
                client="", meta={"tool": "ads_grader"})
            lead_id = str(answer.get("lead_id") or "")
        except Exception:                                # noqa: BLE001
            # hub.leads never raises by design. If it somehow does, the
            # handshake still starts: losing the grade as well as the lead
            # would be the worse of the two failures.
            log.exception("ads_grader: lead capture failed")

    state = store.start_session(lead_id=lead_id, name=body.get("name") or "",
                                email=email, phone=phone, company=company,
                                website=body.get("website") or "")
    # Asked as a question rather than by building a URL and discarding it:
    # what this route needs to know is whether the tool can run at all, and
    # /connect builds the real address when the visitor gets there.
    ready, missing = grading.configured()
    if not ready:
        # The lead is already filed, which is why this is a 503 with the lead
        # kept rather than a refusal that loses both.
        return jsonify({"error": "This tool is not configured yet: "
                                 + ", ".join(missing),
                        "lead_saved": bool(lead_id)}), 503
    return jsonify({"ok": True, "lead_saved": bool(lead_id),
                    "connect_url": f"{MOUNT}/connect?state={state}"})


@app.get("/connect")
def oauth_connect():
    """Send the visitor to Google. Nothing is read or written on the way."""
    state = request.args.get("state", "")
    if not state:
        return redirect(MOUNT + "/")
    try:
        return redirect(grading.auth_url(state))
    except grading.GraderError as exc:
        return render_template("grader_error.html", mount=MOUNT,
                               message=str(exc)), 503


@app.get("/oauth/callback")
def oauth_callback():
    """Exchange, read, score, and let the token go.

    Everything Google gives us lives in this function's own scope. There is no
    branch out of it that writes a token, and no table with a column for one.
    """
    if request.args.get("error"):
        return render_template(
            "grader_error.html", mount=MOUNT,
            message="You did not grant access, so there was nothing to read. "
                    "Your details are with us and we can look another way."), 200
    session = store.take_session(request.args.get("state", ""))
    if not session:
        return render_template(
            "grader_error.html", mount=MOUNT,
            message="That sign-in link has already been used or has expired. "
                    "Start again and it will take a minute."), 400
    code = request.args.get("code", "")
    if not code:
        return render_template("grader_error.html", mount=MOUNT,
                               message="Google sent us back without an "
                                       "authorization code."), 400
    try:
        token = grading.exchange_code(code)
        customers = grading.accessible_customers(token)
    except grading.GraderError as exc:
        return render_template("grader_error.html", mount=MOUNT,
                               message=str(exc)), 502
    if not customers:
        return render_template(
            "grader_error.html", mount=MOUNT,
            message="That Google login does not have access to a Google Ads "
                    "account. Sign in with the login that manages your ads."), 200

    graded, empty, failed = [], [], []
    for customer_id in customers[:grading.MAX_ACCOUNTS_GRADED]:
        try:
            result = grading.grade_account(token, customer_id)
        except grading.GraderError as exc:
            failed.append({"customer_id": customer_id, "error": str(exc)})
            continue
        if not (result.get("totals") or {}).get("campaigns"):
            # Named, not dropped: "you have no active campaigns" is the single
            # most useful thing this tool can tell some of the people running
            # it, and a manager account looks exactly like this.
            empty.append({"customer_id": customer_id,
                          "account_name": result.get("account_name") or ""})
            continue
        result["skipped_accounts"] = max(
            0, len(customers) - grading.MAX_ACCOUNTS_GRADED)
        graded.append((customer_id, result))
    # The token is now finished with. Nothing above wrote it and nothing
    # below can reach it.
    del token

    if not graded:
        return render_template(
            "grader_error.html", mount=MOUNT,
            message=("We reached your Google Ads but found no campaign with "
                     "activity in the last 30 days. That is worth a "
                     "conversation on its own — we have your details."
                     if empty else
                     "We could not read your Google Ads account: "
                     + (failed[0]["error"] if failed else "no reason given.")),
            empty=empty, failed=failed), 200

    # The account with the most spend leads: it is the one the conversation is
    # about, and picking it after the read costs nothing where an account
    # picker before it would have meant holding on to the token.
    graded.sort(key=lambda pair: (pair[1].get("totals") or {}).get("cost") or 0,
                reverse=True)
    tokens = []
    for customer_id, result in graded:
        result["empty_accounts"] = empty
        row = store.save_result(
            lead_id=session["lead_id"], company=session["company"],
            website=session["website"], customer_id=customer_id,
            account_name=result.get("account_name") or "", result=result)
        tokens.append(row["token"])

    _log_grade(session, graded[0][1], tokens[0])
    return redirect(f"{MOUNT}/r/{tokens[0]}")


def _log_grade(session: dict, result: dict, token: str) -> None:
    """One activity row per graded prospect. Never raises."""
    try:
        from hub import audit
        audit.log("ads_grader", "graded", actor="public",
                  company=session.get("company") or "",
                  lead=session.get("lead_id") or "",
                  score=result.get("score"), grade=result.get("grade"),
                  spend=result.get("spend"), report=_report_url(token))
    except Exception:                                    # noqa: BLE001
        pass


@app.get("/r/<token>")
def page_report(token):
    row = store.get_result(token, with_result=True)
    if not row or row["revoked"]:
        # Revoked, deleted and never-existed all answer the same page: a
        # public URL that says "that one expired" tells somebody probing which
        # tokens are real.
        return render_template("grader_gone.html"), 404
    return render_template("grader_report.html", mount=MOUNT, row=row,
                           r=row["result"], token=token,
                           weights=grading.WEIGHTS)


@app.get("/health")
def health():
    ok, missing = grading.configured()
    return jsonify({"status": "ok" if ok else "not_configured",
                    "module": "ads_grader", "version": VERSION,
                    "version_date": VERSION_DATE,
                    "missing": missing,
                    "redirect_uri": grading.redirect_uri(),
                    "accounts_graded_per_run": grading.MAX_ACCOUNTS_GRADED})
