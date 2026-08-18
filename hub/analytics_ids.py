"""GA and GTM identifiers, from Knack AND Google — and whether they agree.

Two sources exist and neither is sufficient alone:

  * **Knack (object_153)** records what we believe the client's GA and GTM
    accounts to be. It's there whether or not anyone has connected the Google
    account, which is why the Hub can show `GTM-TG6FPR8M` for Icon Solar even
    though the live lookup finds nothing.
  * **Google** reports what we can actually reach right now. It's authoritative
    about access — if it isn't here, we cannot administer it — but it only
    covers accounts somebody has connected.

An earlier version treated these as alternatives, preferring whichever
answered first. That threw away the most useful fact of all: **when the two
disagree.** A recorded container that doesn't match the one Google shows means
either the site is running the wrong container or the record is stale, and
both are worth knowing. So nothing is discarded; the comparison is the output.

Five states, each meaning something different operationally:

    match          recorded and reachable, same id      nothing to do
    mismatch       both known, different ids            investigate
    recorded_only  in Knack, no Google access           request access
    live_only      reachable, not recorded in Knack     update Knack
    missing        neither                              set one up
"""
from __future__ import annotations

import re


def _norm_ga(v: str) -> str:
    """Normalise a GA identifier for comparison.

    GA ids appear as `G-ABC123`, `properties/123456`, `UA-123456-1` and bare
    numeric property ids depending on where they came from. Comparing raw
    strings reports false mismatches, which is worse than no check at all
    because it trains people to ignore the warning.
    """
    s = str(v or "").strip().upper()
    if not s:
        return ""
    s = s.replace("PROPERTIES/", "").replace("ACCOUNTS/", "")
    m = re.search(r"\b(G-[A-Z0-9]+)\b", s)
    if m:
        return m.group(1)
    m = re.search(r"\b(UA-\d+-\d+)\b", s)
    if m:
        return m.group(1)
    m = re.search(r"\b(\d{6,})\b", s)          # bare property id
    return m.group(1) if m else s


def _norm_gtm(v: str) -> str:
    s = str(v or "").strip().upper()
    m = re.search(r"\b(GTM-[A-Z0-9]+)\b", s)
    if m:
        return m.group(1)
    m = re.search(r"\b(\d{6,})\b", s)          # container/account number
    return m.group(1) if m else s


def _state(recorded: str, live: str, norm) -> str:
    r, l = norm(recorded), norm(live)
    if r and l:
        return "match" if r == l else "mismatch"
    if r:
        return "recorded_only"
    if l:
        return "live_only"
    return "missing"


_ADVICE = {
    "match": "Recorded and reachable, and they agree.",
    "mismatch": ("The recorded id and the one we can reach are different. "
                 "Either the site is running a container we don't administer, "
                 "or the Knack record is out of date. Worth resolving — "
                 "reports built on the wrong property are silently wrong."),
    "recorded_only": ("Recorded in Knack but no connected Google account can "
                      "reach it. We can reference it, but we can't administer "
                      "it or pull data from it. Request access from the client."),
    "live_only": ("We have access but it isn't recorded in Knack, so nothing "
                  "else in the Hub knows about it. Add it to the website "
                  "record."),
    "missing": "Neither recorded nor reachable.",
}


def _live_google(client: str, domain: str) -> dict:
    """What the connected Google accounts can actually see for this client.

    Matches on domain first, then on name — a GA property is usually named
    after the site. Returns blanks rather than raising: an unreachable Google
    is a reason to show the Knack value, not a reason to fail.
    """
    out = {"ga": "", "gtm": "", "ga_name": "", "gtm_name": "",
           "accounts_connected": 0, "error": ""}
    try:
        from modules.google_finder import app as gf
        accounts = gf.connected_accounts() or []
        out["accounts_connected"] = len(accounts)
    except Exception as exc:                            # noqa: BLE001
        out["error"] = f"Google module unavailable ({type(exc).__name__})."
        return out

    want_dom = (domain or "").lower()
    want_name = re.sub(r"[^a-z0-9]+", "", (client or "").lower())

    def looks_like(text: str) -> bool:
        t = str(text or "").lower()
        if want_dom and want_dom.split(".")[0] in t:
            return True
        n = re.sub(r"[^a-z0-9]+", "", t)
        return bool(want_name) and (want_name in n or n in want_name)

    try:
        for acct in accounts:
            for item in (acct.get("items") or []):
                plat = str(item.get("platform") or "")
                label = " ".join(str(item.get(k) or "") for k in
                                 ("name", "account_name", "url", "container_id",
                                  "property_id", "measurement_id"))
                if not looks_like(label):
                    continue
                if "Analytics" in plat and not out["ga"]:
                    out["ga"] = str(item.get("measurement_id")
                                    or item.get("property_id") or "")
                    out["ga_name"] = str(item.get("name") or "")
                elif "Tag Manager" in plat and not out["gtm"]:
                    out["gtm"] = str(item.get("container_id")
                                     or item.get("public_id") or "")
                    out["gtm_name"] = str(item.get("name") or "")
    except Exception as exc:                            # noqa: BLE001
        out["error"] = f"Could not read connected accounts ({type(exc).__name__})."
    return out


def compare(client: str, domain: str = "") -> dict:
    """Both sources, side by side, with what the difference means."""
    recorded = {"ga": "", "gtm": "", "found": False}
    try:
        from hub.knack_websites import enrich
        reg = enrich(client, domain)
        if reg.get("found"):
            recorded = {"ga": reg.get("ga_account", ""),
                        "gtm": reg.get("gtm_account", ""), "found": True}
            domain = domain or reg.get("domain", "")
    except Exception:                                   # noqa: BLE001
        pass

    live = _live_google(client, domain)

    ga_state = _state(recorded["ga"], live["ga"], _norm_ga)
    gtm_state = _state(recorded["gtm"], live["gtm"], _norm_gtm)

    def row(kind, state, rec, liv, live_name):
        return {
            "kind": kind, "state": state,
            "recorded": rec, "live": liv, "live_name": live_name,
            # Show the recorded value when we have nothing live: it's still
            # the answer to "what's on their site", just not one we can touch.
            "display": rec or liv or "",
            "have_access": bool(liv),
            "advice": _ADVICE[state],
        }

    rows = [row("GA4", ga_state, recorded["ga"], live["ga"], live["ga_name"]),
            row("GTM", gtm_state, recorded["gtm"], live["gtm"], live["gtm_name"])]

    problems = [r for r in rows if r["state"] in ("mismatch", "recorded_only")]
    return {
        "client": client, "domain": domain,
        "in_knack": recorded["found"],
        "google_accounts_connected": live["accounts_connected"],
        "google_error": live["error"],
        "rows": rows,
        "needs_attention": len(problems),
        "note": ("Both sources are shown deliberately. Knack records what the "
                 "client uses; Google shows what we can actually administer. "
                 "Neither replaces the other, and where they disagree that is "
                 "itself the finding."),
    }


def audit_all(limit: int = 400) -> dict:
    """Every client where the two sources disagree, or we lack access.

    This is the report worth having: a mismatch means somebody's analytics
    reporting may be pointed at the wrong property, and nothing else surfaces
    that.
    """
    try:
        from hub import clients_registry
        clients = clients_registry.all_clients()[:limit]
    except Exception:                                   # noqa: BLE001
        clients = []
    mismatched, no_access, ok = [], [], 0
    for c in clients:
        name = c.get("name") or ""
        if not name:
            continue
        try:
            r = compare(name, c.get("domain") or c.get("url") or "")
        except Exception:                               # noqa: BLE001
            continue
        for row in r["rows"]:
            if row["state"] == "mismatch":
                mismatched.append({"client": name, **row})
            elif row["state"] == "recorded_only":
                no_access.append({"client": name, **row})
            elif row["state"] == "match":
                ok += 1
    return {"checked": len(clients), "in_agreement": ok,
            "mismatched": mismatched, "no_access": no_access,
            "note": "A mismatch usually means reporting is pointed at a "
                    "property we don't administer, or the Knack record is "
                    "stale. Either way the numbers can't be trusted until "
                    "it's resolved."}
