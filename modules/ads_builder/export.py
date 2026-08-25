"""Handing an approved campaign to Google Ads without the API.

The Google Ads API needs an approved **developer token**, which Google issues
on its own timetable — the application is made in the manager account under
Tools → API Center and can sit pending for days. Nothing else in this module
needs one: generation is OpenAI, review and approval are the Hub's own, and
the client proposal is a page. Only the last mile — reading live campaigns and
writing a new one — goes through Google's API.

So the last mile has a second route that needs no token at all. **Google Ads
Editor** imports a CSV and posts it with the account owner's own sign-in, so an
approved proposal can be built in the client's account today and the same
proposal can be deployed through the API later, unchanged, when the token
arrives.

Two rules this file exists to keep:

* **The CSV must describe the same campaign the API would create**, or the tool
  quietly builds two different things depending on which button someone
  pressed. Campaign name, PAUSED status, the daily budget arithmetic, match
  types, the RSA fallbacks and campaign-level broad negatives are all imported
  from ``google_ads`` rather than restated here.
* **What the CSV cannot carry is named, not dropped.** Ads Editor's asset
  import columns differ between versions, so guessing them produces an import
  Editor rejects in a way that reads as our bug. Sitelinks, callouts and
  structured snippets go into a plain build sheet to be typed in, and the
  sheet says so at the top. A missing final URL or a zero budget is likewise
  reported in the sheet and left blank in the CSV — Editor then refuses that
  row, which is the correct visible failure, rather than importing a campaign
  with a budget nobody chose.
"""
from __future__ import annotations

import csv
import io
import re

from .google_ads import _build_rsa, _clamp, normalise_url, parse_keyword

# Ads Editor reads a row by the columns it carries, so one file can hold
# campaign, ad group, keyword, negative and ad rows. The column names are
# Editor's own.
HEADLINES = 15
DESCRIPTIONS = 4

COLUMNS = (
    ["Campaign", "Campaign Type", "Campaign Daily Budget", "Networks",
     "Ad Group", "Max CPC", "Keyword", "Criterion Type", "Ad Type"]
    + [f"Headline {i}" for i in range(1, HEADLINES + 1)]
    + [f"Description {i}" for i in range(1, DESCRIPTIONS + 1)]
    + ["Final URL", "Status"]
)

CRITERION = {"EXACT": "Exact", "PHRASE": "Phrase", "BROAD": "Broad"}

# Matches deploy_proposal: a monthly budget spread over an average month.
DAYS_PER_MONTH = 30.4


def default_campaign_name(proposal: dict) -> str:
    """Deliberately without the timestamp ``deploy_proposal`` adds.

    A download that named the campaign differently every time would import as a
    second campaign on the second attempt, and the person doing it would have
    no way to tell the duplicate from the original.
    """
    return _clamp(f"{proposal.get('businessName') or 'Campaign'} | Search", 255)


def slug(value) -> str:
    out = re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")
    return out or "campaign"


def daily_budget(proposal: dict):
    """The daily figure, or None when the proposal carries no budget."""
    try:
        monthly = float(proposal.get("monthlyBudget") or 0)
    except (TypeError, ValueError):
        monthly = 0.0
    return round(monthly / DAYS_PER_MONTH, 2) if monthly > 0 else None


def negatives_of(proposal: dict) -> list:
    """Campaign-level, de-duplicated, in vault order — as ``deploy_proposal``."""
    out, seen = [], set()
    for bucket in (proposal.get("negativeKeywordVault") or {}).values():
        for term in bucket or []:
            kw = parse_keyword(term)
            if not kw or not kw["text"] or kw["text"].lower() in seen:
                continue
            seen.add(kw["text"].lower())
            out.append(kw["text"])
    return out


def problems(proposal: dict) -> list:
    """What Editor will refuse, said here first and in the same words."""
    found = []
    if not normalise_url(proposal.get("websiteUrl")):
        found.append(
            "No usable final URL on the proposal, so the ad rows carry no destination. "
            "Add the landing page in Ads Editor before posting, or Google rejects the ads."
        )
    if daily_budget(proposal) is None:
        found.append(
            "No monthly budget on the proposal, so the daily budget cell is blank. "
            "Editor will not accept the campaign row until it is filled in."
        )
    if not [g for g in (proposal.get("adGroups") or []) if g.get("keywords")]:
        found.append("No ad group carries any keywords, so the file has structure and nothing to bid on.")
    return found


def editor_csv(proposal: dict, campaign_name: str = None, search_partners: bool = False) -> str:
    """One Google Ads Editor import file: campaign, ad groups, keywords,
    campaign negatives and one responsive search ad per group.

    Everything is Paused, exactly as the API path creates it — importing this
    cannot start spending any more than deploying can.
    """
    name = _clamp(campaign_name or default_campaign_name(proposal), 255)
    final_url = normalise_url(proposal.get("websiteUrl"))
    budget = daily_budget(proposal)
    est_cpc = (proposal.get("costEstimation") or {}).get("avgCPC")

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=COLUMNS, extrasaction="ignore",
                            lineterminator="\r\n")
    writer.writeheader()

    def row(**cells):
        writer.writerow({c: cells.get(c, "") for c in COLUMNS})

    row(**{
        "Campaign": name,
        "Campaign Type": "Search",
        "Campaign Daily Budget": f"{budget:.2f}" if budget is not None else "",
        "Networks": "Google search" + ("; Search partners" if search_partners else ""),
        "Status": "Paused",
    })

    for group in proposal.get("adGroups") or []:
        if not group.get("keywords"):
            continue
        group_name = _clamp(group.get("name") or "Ad Group", 255)
        try:
            bid = float(group.get("avgCPC") or est_cpc or 2)
        except (TypeError, ValueError):
            bid = 2.0
        row(**{
            "Campaign": name,
            "Ad Group": group_name,
            "Max CPC": f"{max(bid, 0.05):.2f}",
            "Status": "Paused",
        })

        seen = set()
        for raw in group.get("keywords") or []:
            kw = parse_keyword(raw)
            if not kw or not kw["text"]:
                continue
            key = (kw["match_type"], kw["text"].lower())
            if key in seen:
                continue
            seen.add(key)
            row(**{
                "Campaign": name,
                "Ad Group": group_name,
                "Keyword": _clamp(kw["text"], 80),
                "Criterion Type": CRITERION.get(kw["match_type"], "Phrase"),
                "Status": "Enabled",   # inside a paused campaign, as the API path
            })

        headlines, descriptions = _build_rsa(proposal, group)
        if len(headlines) >= 3 and len(descriptions) >= 2:
            ad = {
                "Campaign": name,
                "Ad Group": group_name,
                "Ad Type": "Responsive search ad",
                "Final URL": final_url,
                "Status": "Paused",
            }
            for i, h in enumerate(headlines[:HEADLINES], start=1):
                ad[f"Headline {i}"] = h["text"]
            for i, d in enumerate(descriptions[:DESCRIPTIONS], start=1):
                ad[f"Description {i}"] = d["text"]
            row(**ad)

    for term in negatives_of(proposal)[:5000]:
        row(**{
            "Campaign": name,
            "Keyword": _clamp(term, 80),
            "Criterion Type": "Campaign Negative Broad",
            "Status": "Enabled",
        })

    return buf.getvalue()


def _wrap(lines) -> str:
    return "\n".join(lines).rstrip() + "\n"


def build_sheet(proposal: dict, campaign_name: str = None) -> str:
    """The half of the campaign the CSV deliberately does not carry."""
    name = _clamp(campaign_name or default_campaign_name(proposal), 255)
    assets = proposal.get("adAssets") or {}
    sitelinks = assets.get("sitelinks") or []
    callouts = assets.get("callouts") or []
    snippets = assets.get("structuredSnippets") or {}
    budget = daily_budget(proposal)
    final_url = normalise_url(proposal.get("websiteUrl"))

    if budget is None:
        budget_line = "not set on the proposal — fill it in before posting"
    else:
        monthly = float(proposal.get("monthlyBudget") or 0)
        budget_line = f"${budget:,.2f}  (${monthly:,.0f}/mo over {DAYS_PER_MONTH} days)"

    out = [
        f"{name}",
        "=" * len(name),
        "",
        "Google Ads Editor build sheet — Smart 1 Ads",
        "",
        "The CSV beside this file carries the campaign, ad groups, keywords, campaign",
        "negatives and one responsive search ad per group. It does NOT carry the assets",
        "below: Ads Editor's asset import columns differ between versions, and a guess",
        "produces an import Editor rejects. Add these by hand once the CSV is in.",
        "",
        "HOW TO POST THE CSV",
        "  1. Open Google Ads Editor and sign in to the client account.",
        "  2. Account → Import → Import from file, and choose the CSV.",
        "  3. Review the proposed changes — everything arrives Paused.",
        "  4. Post. Nothing spends until somebody enables the campaign.",
        "",
        "CAMPAIGN SETTINGS",
        f"  Campaign name    {name}",
        "  Type             Search — Google search only, no display, no search partners",
        "  Status           Paused",
        f"  Daily budget     {budget_line}",
        f"  Final URL        {final_url or 'not set on the proposal — the ads have no destination'}",
        "  Bidding          Manual CPC, per-ad-group max CPC in the CSV",
        "",
    ]

    problem_list = problems(proposal)
    if problem_list:
        out += ["BEFORE YOU POST", ""]
        out += [f"  * {p}" for p in problem_list]
        out += [""]

    out += [f"SITELINKS ({len(sitelinks)})", ""]
    if sitelinks:
        for s in sitelinks:
            out.append(f"  {s.get('title', '')}")
            if s.get("desc1"):
                out.append(f"    {s.get('desc1')}")
            if s.get("desc2"):
                out.append(f"    {s.get('desc2')}")
            out.append(f"    {s.get('url') or final_url or 'no URL'}")
            out.append("")
        out.append("  Both description lines or neither — Google refuses a sitelink with one.")
    else:
        out.append("  None on this proposal.")
    out += [""]

    out += [f"CALLOUTS ({len(callouts)})", ""]
    out.append("  " + (" · ".join(str(c) for c in callouts) if callouts else "None on this proposal."))
    out += [""]

    values = snippets.get("values") or []
    out += ["STRUCTURED SNIPPETS", ""]
    if snippets.get("header") and values:
        out.append(f"  Header: {snippets['header']}")
        out.append("  " + " · ".join(str(v) for v in values))
        if len(values) < 3:
            out.append("  Google wants at least 3 values — this has fewer, so it may not serve.")
    else:
        out.append("  None on this proposal.")
    out += [""]

    negatives = negatives_of(proposal)
    out += [
        f"NEGATIVE KEYWORDS ({len(negatives)})",
        "",
        "  In the CSV as campaign-level broad negatives. Nothing to do by hand.",
        "",
    ]
    return _wrap(out)
