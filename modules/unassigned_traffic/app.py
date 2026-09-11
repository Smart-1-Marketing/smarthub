"""SmartHub Unassigned Traffic Resolver.

Read-only GA4 diagnostic. It uses SmartHub's existing Google account index and
OAuth connection, measures exactly how much traffic GA4 currently places in
Unassigned, and turns the largest source/medium rows into a remediation queue.
Historical attribution is never rewritten; recommendations improve future
measurement.
"""
from __future__ import annotations

import os
import re
from collections import Counter
from urllib.parse import urlparse

import requests
from flask import Flask, jsonify, render_template_string, request

app = Flask(__name__)

RECOGNIZED_MEDIA = {
    "affiliate", "cpc", "cpm", "cpv", "display", "email", "organic",
    "paid_social", "paid-social", "referral", "social", "video", "(none)",
}
PAID_HINTS = {
    "cpc", "ppc", "paid", "paidsearch", "paid_search", "paid-search",
    "sem", "display", "cpm", "cpv",
}
PAYMENT_HOSTS = (
    "paypal.", "stripe.", "squareup.", "authorize.net", "afterpay.", "klarna.",
)
EMPTY = {"", "(not set)", "not set", "(none)"}
DETAIL_LIMIT = 250


def _clean(value) -> str:
    return str(value or "").strip()


def _host(value) -> str:
    raw = _clean(value).lower()
    if not raw:
        return ""
    if "://" not in raw:
        raw = "https://" + raw
    try:
        return (urlparse(raw).hostname or "").lower().removeprefix("www.")
    except Exception:  # noqa: BLE001
        return ""


def _suggest_utm(source: str, medium: str, campaign: str) -> str:
    src = re.sub(r"[^a-z0-9_-]+", "-", source or "source").strip("-") or "source"
    if medium in RECOGNIZED_MEDIA and medium != "(none)":
        med = medium.replace("paid-social", "paid_social")
    elif medium in PAID_HINTS:
        med = "cpc"
    else:
        med = "referral"
    camp = re.sub(r"[^a-zA-Z0-9_-]+", "-", campaign or "campaign-name").strip("-")
    return f"utm_source={src}&utm_medium={med}&utm_campaign={camp or 'campaign-name'}"


def diagnose_row(row: dict, client_domain: str = "") -> dict:
    """Classify one GA4 Unassigned row without pretending certainty we lack."""
    source = _clean(row.get("source")).lower()
    medium = _clean(row.get("medium")).lower()
    campaign = _clean(row.get("campaign"))
    landing = _clean(row.get("landing_page"))
    domain = _host(client_domain)

    kind = "channel_rules"
    title = "Source/medium is not matching GA4's channel rules"
    why = "GA4 received attribution values, but they did not map to a standard default channel."
    fix = "Standardize the campaign source and medium, then use the UTM Builder for future links."
    confidence = "medium"

    if source in EMPTY and medium in EMPTY:
        kind, confidence = "missing_attribution", "high"
        title = "Source and medium are missing"
        why = "GA4 has no usable campaign attribution for these sessions."
        fix = "Add UTMs to campaign links and check redirects, consent handling, and links that strip query parameters."
    elif medium in EMPTY:
        kind, confidence = "missing_medium", "high"
        title = "Campaign medium is missing"
        why = f"GA4 received source “{source or '(not set)'}” without a usable medium."
        fix = "Add a standard utm_medium and keep it consistent across every link in this campaign."
    elif source in EMPTY:
        kind, confidence = "missing_source", "high"
        title = "Campaign source is missing"
        why = f"GA4 received medium “{medium}” without a usable source."
        fix = "Add utm_source so GA4 can identify the platform or publisher sending the visit."
    elif domain and (source == domain or source.endswith("." + domain)):
        kind, confidence = "self_referral", "high"
        title = "Your own domain is appearing as a traffic source"
        why = "This usually points to cross-domain/session continuity problems or a redirect that starts a new session."
        fix = "Review cross-domain measurement, unwanted referrals, checkout/subdomain transitions, and tag coverage."
    elif any(h in source for h in PAYMENT_HOSTS):
        kind, confidence = "payment_referral", "high"
        title = "A payment provider is taking credit for the session"
        why = "Visitors are returning from checkout and GA4 is treating that return as a new referral."
        fix = "Add the payment domain to unwanted referrals and verify cross-domain measurement through checkout."
    elif medium not in RECOGNIZED_MEDIA and medium not in PAID_HINTS:
        kind, confidence = "nonstandard_medium", "high"
        title = f"Non-standard medium: {medium}"
        why = "Custom medium values often fall outside GA4's default channel definitions."
        fix = "Normalize utm_medium to a controlled value such as cpc, email, referral, paid_social, display, or video."
    elif medium in PAID_HINTS and not campaign:
        kind = "paid_missing_campaign"
        title = "Paid traffic is missing a campaign name"
        why = "The source/medium looks paid, but the campaign value is blank."
        fix = "Verify ad-platform auto-tagging and add utm_campaign to manually tagged paid links."
    elif source == "google" and medium in PAID_HINTS:
        kind = "google_paid_rules"
        title = "Google paid traffic is not landing in a paid-search channel"
        why = "The values look like paid Google traffic, so tagging or the Ads/GA4 link may be inconsistent."
        fix = "Verify Google Ads auto-tagging, the GA4↔Google Ads link, redirects preserving gclid/UTMs, and medium consistency."

    try:
        sessions = int(float(row.get("sessions") or 0))
    except (TypeError, ValueError):
        sessions = 0
    return {
        **row,
        "sessions": sessions,
        "issue": kind,
        "issue_title": title,
        "why": why,
        "fix": fix,
        "confidence": confidence,
        "suggested_utm": _suggest_utm(source, medium, campaign),
        "landing_page": landing,
    }


def summarize(rows: list[dict], total_sessions: int, unassigned_sessions: int | None = None) -> dict:
    """Shape detail rows while keeping GA4's exact Unassigned total separate.

    The detailed report is deliberately capped so a pathological property does
    not make a page fetch thousands of attribution combinations. Therefore its
    row sum is *diagnosed coverage*, never the headline Unassigned total.
    """
    diagnosed = [diagnose_row(r, r.get("client_domain", "")) for r in rows]
    diagnosed_sessions = sum(r["sessions"] for r in diagnosed)
    exact_unassigned = diagnosed_sessions if unassigned_sessions is None else int(unassigned_sessions or 0)
    issues = Counter()
    for row in diagnosed:
        issues[row["issue"]] += row["sessions"]
    diagnosed.sort(key=lambda r: r["sessions"], reverse=True)
    total_sessions = int(total_sessions or 0)
    return {
        "total_sessions": total_sessions,
        "unassigned_sessions": exact_unassigned,
        "unassigned_rate": round(exact_unassigned / total_sessions * 100, 2) if total_sessions else 0,
        "diagnosed_sessions": diagnosed_sessions,
        "diagnostic_coverage_pct": (
            round(diagnosed_sessions / exact_unassigned * 100, 1) if exact_unassigned else 100.0
        ),
        "detail_limited": diagnosed_sessions < exact_unassigned,
        "rows": diagnosed,
        "issues": [{"issue": k, "sessions": v} for k, v in issues.most_common()],
        "note": "GA4 historical session attribution is not rewritten. These fixes reduce future Unassigned traffic.",
    }


def _gf():
    import sys
    mod = sys.modules.get("gf_app")
    if mod is not None:
        return mod
    from modules.google_finder import app as mod
    return mod


def _property_for_client(client: str, domain: str = "") -> dict:
    from hub import google_index
    found = google_index.for_client(client, domain)
    props = found.get("ga4") or []
    if not props:
        return {"ok": False, "error": "No mapped GA4 property was found for this client.", "index": found}
    # Human attachment wins where several properties map to the same client.
    item = next((p for p in props if p.get("match") == "attached"), props[0])
    rid = _clean(item.get("resource_id"))
    prop = rid.split("/")[-1]
    domains = item.get("domains") or []
    return {
        "ok": True,
        "property_id": prop,
        "property_name": item.get("name") or prop,
        "google_login": item.get("google_login") or "",
        "domain": _clean(domain) or (_clean(domains[0]) if domains else ""),
        "index": found,
    }


def _access_token(login: str) -> str:
    gf = _gf()
    accounts, err = gf.connected_accounts_result()
    if err and not accounts:
        raise RuntimeError(err)
    wanted = _clean(login).lower()
    candidates = [a for a in accounts if a.get("status") == "ACTIVE"]
    account = next((a for a in candidates if _clean(a.get("email")).lower() == wanted), None)
    if account is None and len(candidates) == 1:
        account = candidates[0]
    if account is None:
        raise RuntimeError("The Google login for this GA4 property is not connected or needs reauthorization.")
    return gf.refresh_access_token(account["email"], account["refresh_token"])


def _run_report(token: str, property_id: str, payload: dict) -> dict:
    url = f"https://analyticsdata.googleapis.com/v1beta/properties/{property_id}:runReport"
    r = requests.post(url, headers={"Authorization": f"Bearer {token}"}, json=payload, timeout=25)
    try:
        _gf()._note_google(url, ok=r.ok)
    except Exception:  # noqa: BLE001
        pass
    if not r.ok:
        detail = ""
        try:
            detail = (r.json().get("error") or {}).get("message") or ""
        except Exception:  # noqa: BLE001
            pass
        raise RuntimeError(f"GA4 Data API answered {r.status_code}: {detail or r.text[:180]}")
    return r.json()


def _metric(report: dict, idx: int = 0) -> int:
    rows = report.get("rows") or []
    if not rows:
        return 0
    try:
        return int(float(rows[0]["metricValues"][idx]["value"]))
    except (KeyError, IndexError, TypeError, ValueError):
        return 0


def _unassigned_filter() -> dict:
    return {
        "filter": {
            "fieldName": "sessionDefaultChannelGroup",
            "stringFilter": {
                "matchType": "EXACT",
                "value": "Unassigned",
                "caseSensitive": False,
            },
        }
    }


def live_analysis(client: str, domain: str = "", days: int = 30) -> dict:
    mapped = _property_for_client(client, domain)
    if not mapped.get("ok"):
        return mapped
    days = max(7, min(int(days or 30), 365))
    token = _access_token(mapped["google_login"])
    date_ranges = [{"startDate": f"{days}daysAgo", "endDate": "yesterday"}]

    total = _run_report(token, mapped["property_id"], {
        "dateRanges": date_ranges,
        "metrics": [{"name": "sessions"}],
    })
    # Exact headline total: never derive this from the capped detail table.
    unassigned_total = _run_report(token, mapped["property_id"], {
        "dateRanges": date_ranges,
        "metrics": [{"name": "sessions"}],
        "dimensionFilter": _unassigned_filter(),
    })
    detail = _run_report(token, mapped["property_id"], {
        "dateRanges": date_ranges,
        "dimensions": [
            {"name": "sessionSource"},
            {"name": "sessionMedium"},
            {"name": "sessionCampaignName"},
            {"name": "landingPagePlusQueryString"},
        ],
        "metrics": [{"name": "sessions"}],
        "dimensionFilter": _unassigned_filter(),
        "orderBys": [{"metric": {"metricName": "sessions"}, "desc": True}],
        "limit": DETAIL_LIMIT,
    })

    effective_domain = mapped.get("domain") or domain
    rows = []
    for raw in detail.get("rows") or []:
        dims = [v.get("value", "") for v in raw.get("dimensionValues") or []]
        mets = raw.get("metricValues") or []
        rows.append({
            "source": dims[0] if len(dims) > 0 else "",
            "medium": dims[1] if len(dims) > 1 else "",
            "campaign": dims[2] if len(dims) > 2 else "",
            "landing_page": dims[3] if len(dims) > 3 else "",
            "sessions": mets[0].get("value", "0") if mets else "0",
            "client_domain": effective_domain,
        })

    out = summarize(rows, _metric(total), _metric(unassigned_total))
    out.update({
        "ok": True,
        "client": client,
        "client_domain": effective_domain,
        "days": days,
        "property_id": mapped["property_id"],
        "property_name": mapped["property_name"],
        "google_login": mapped["google_login"],
        "index_stale": mapped["index"].get("stale", False),
        "detail_limit": DETAIL_LIMIT,
    })
    return out


def _available_clients() -> list[dict]:
    from hub import google_index
    seen = {}
    for item in google_index.load().get("items") or []:
        if item.get("platform") != "Google Analytics" or not item.get("client"):
            continue
        name = _clean(item.get("client"))
        key = name.lower()
        if not name or key in seen:
            continue
        domains = item.get("domains") or []
        seen[key] = {
            "name": name,
            "property": item.get("name") or item.get("resource_id") or "",
            "domain": _clean(domains[0]) if domains else "",
        }
    return sorted(seen.values(), key=lambda x: x["name"].lower())


@app.get("/")
def page():
    return render_template_string(PAGE)


@app.get("/api/clients")
def api_clients():
    return jsonify({"clients": _available_clients()})


@app.get("/api/analyze")
def api_analyze():
    client = _clean(request.args.get("client"))
    domain = _clean(request.args.get("domain"))
    if not client:
        return jsonify({"ok": False, "error": "Choose a client."}), 400
    try:
        days = int(request.args.get("days") or 30)
    except ValueError:
        days = 30
    try:
        out = live_analysis(client, domain, days)
        return jsonify(out), 200 if out.get("ok") else 404
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)[:500]}), 502


PAGE = r'''<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Unassigned Traffic Resolver — Smart 1 Hub</title>
<style>
:root{font-family:Inter,system-ui,sans-serif;color:#17233c;background:#f4f7fb}.wrap{max-width:1400px;margin:auto;padding:32px}.head{display:flex;justify-content:space-between;gap:20px;align-items:end;flex-wrap:wrap}.head h1{margin:0;font-size:30px;color:#142b54}.head p{margin:7px 0 0;color:#64748b}.controls,.card{background:white;border:1px solid #dde5ef;border-radius:14px;box-shadow:0 3px 14px #19355c0d}.controls{padding:16px;display:flex;gap:10px;flex-wrap:wrap;margin:22px 0}select,input,button{border:1px solid #cfd8e6;border-radius:9px;padding:10px 12px;font:inherit}select{min-width:280px}button{background:#183b6b;color:white;border-color:#183b6b;font-weight:700;cursor:pointer}.stats{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px}.stat{padding:18px}.num{font-size:29px;font-weight:800;color:#142b54}.label{font-size:12px;text-transform:uppercase;letter-spacing:.06em;color:#74839a}.section{margin-top:18px;padding:18px}.section h2{margin:0 0 12px;font-size:18px}.issues{display:flex;gap:8px;flex-wrap:wrap}.pill{background:#eef3f9;border-radius:999px;padding:7px 10px;font-size:12px}.table-wrap{overflow:auto}table{width:100%;border-collapse:collapse;font-size:13px}th,td{text-align:left;padding:11px 9px;border-bottom:1px solid #e7edf5;vertical-align:top}th{font-size:11px;text-transform:uppercase;color:#64748b}.sessions{font-weight:800}.fix{min-width:260px}.utm{font-family:ui-monospace,monospace;font-size:11px;background:#f5f7fa;padding:5px 7px;border-radius:5px;word-break:break-all}.muted{color:#738096}.status{padding:20px;text-align:center;color:#64748b}.warn{background:#fff8e7;border:1px solid #f1d79c;padding:12px;border-radius:9px;color:#75520b}.error{background:#fff0f0;border:1px solid #f2b6b6;color:#8b2525;padding:12px;border-radius:9px}.hidden{display:none}@media(max-width:760px){.wrap{padding:18px}.stats{grid-template-columns:1fr}.controls>*{width:100%}select{min-width:0}}
</style></head><body><main class="wrap">
<div class="head"><div><h1>Unassigned Traffic Resolver</h1><p>Find the sessions GA4 cannot classify, identify likely causes, and give the team a fix list.</p></div></div>
<div class="controls"><select id="client"><option value="">Loading Analytics clients…</option></select><input id="domain" placeholder="Client domain"><select id="days"><option value="30">Last 30 days</option><option value="60">Last 60 days</option><option value="90">Last 90 days</option><option value="180">Last 180 days</option></select><button id="run">Analyze Unassigned Traffic</button></div>
<div id="message" class="status">Choose a client with a mapped GA4 property.</div>
<div id="result" class="hidden">
<div class="stats"><div class="card stat"><div class="label">All Sessions</div><div class="num" id="total">—</div></div><div class="card stat"><div class="label">Unassigned Sessions</div><div class="num" id="unassigned">—</div></div><div class="card stat"><div class="label">Unassigned Rate</div><div class="num" id="rate">—</div></div></div>
<div class="card section"><h2>What is causing it</h2><div id="coverage" class="muted" style="margin-bottom:10px"></div><div class="issues" id="issues"></div></div>
<div class="card section"><h2>Remediation Queue</h2><div class="table-wrap"><table><thead><tr><th>Sessions</th><th>Source / Medium</th><th>Campaign</th><th>Landing Page</th><th>Likely Issue</th><th>Recommended Fix</th><th>Suggested Tagging</th></tr></thead><tbody id="rows"></tbody></table></div></div>
<div class="warn" style="margin-top:16px">GA4 does not let this tool rewrite historical attribution. Fixes here are intended to prevent future traffic from becoming Unassigned.</div>
</div></main>
<script>
const $=id=>document.getElementById(id),esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));let clients=[];
async function loadClients(){try{const d=await fetch('api/clients').then(r=>r.json());clients=d.clients||[];$('client').innerHTML='<option value="">Choose Analytics client…</option>'+clients.map(c=>`<option value="${esc(c.name)}">${esc(c.name)} — ${esc(c.property)}</option>`).join('');}catch(e){$('client').innerHTML='<option>Could not load clients</option>';}}
$('client').onchange=()=>{const c=clients.find(x=>x.name===$('client').value);if(c&&c.domain&&!$('domain').value)$('domain').value=c.domain;};
$('run').onclick=async()=>{const client=$('client').value;if(!client)return;$('result').classList.add('hidden');$('message').className='status';$('message').textContent='Reading GA4 and diagnosing Unassigned sessions…';try{const q=new URLSearchParams({client,domain:$('domain').value,days:$('days').value});const r=await fetch('api/analyze?'+q);const d=await r.json();if(!r.ok||!d.ok)throw new Error(d.error||'Analysis failed');$('message').textContent=`${d.property_name} · ${d.days} days${d.index_stale?' · Google mapping index is stale':''}`;$('total').textContent=d.total_sessions.toLocaleString();$('unassigned').textContent=d.unassigned_sessions.toLocaleString();$('rate').textContent=d.unassigned_rate.toFixed(2)+'%';$('coverage').textContent=d.detail_limited?`Top ${d.detail_limit} attribution combinations explain ${d.diagnostic_coverage_pct.toFixed(1)}% of Unassigned sessions.`:`Diagnostic rows explain ${d.diagnostic_coverage_pct.toFixed(1)}% of Unassigned sessions.`;$('issues').innerHTML=d.issues.length?d.issues.map(x=>`<span class="pill">${esc(x.issue.replaceAll('_',' '))}: <b>${x.sessions.toLocaleString()}</b></span>`).join(''):'<span class="muted">No Unassigned sessions found.</span>';$('rows').innerHTML=d.rows.map(x=>`<tr><td class="sessions">${x.sessions.toLocaleString()}</td><td><b>${esc(x.source||'(not set)')}</b><br><span class="muted">${esc(x.medium||'(not set)')}</span></td><td>${esc(x.campaign||'(not set)')}</td><td>${esc(x.landing_page||'—')}</td><td><b>${esc(x.issue_title)}</b><br><span class="muted">${esc(x.why)} · ${esc(x.confidence)} confidence</span></td><td class="fix">${esc(x.fix)}</td><td><span class="utm">${esc(x.suggested_utm)}</span></td></tr>`).join('');$('result').classList.remove('hidden');}catch(e){$('message').className='error';$('message').textContent=e.message;}};
loadClients();
</script></body></html>'''


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")), debug=True)
