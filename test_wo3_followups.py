"""Follow-ups on WO-3 (Smart 1 Hub work orders): the backend seams the
frontend wiring reads.

    1. hub.scan_facts.facts() marks its "Can we run a campaign to this
       site" group with a stable `key`, so Client 360's launch-blocker
       button can find it without matching the title's English prose.
    2. hub.faq.generate() accepts an optional `focus_topic`, threaded from
       the SEO client page's Topic Ideas card into the prompt's
       "requested_focus" -- and the prompt tells the model to drop it
       rather than invent an answer when the page does not support one.
    3. hub.proposal_scan_insights (WO-3e) reaches the proposal document
       itself now, not only its own API route: "Where you stand" leads the
       ROI section on the PDF, the Word export and the live preview, and
       the SEO & AEO scope's derivation rides in the media-plan row's own
       description for whichever line is the "SEO & AEO Scope Package" --
       the same mechanism the consulting catch-all's description already
       uses, so no fourth renderer had to learn a new shape.

    python3 test_wo3_followups.py
"""
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

TMP = tempfile.mkdtemp(prefix="s1wo3followups_test_")
os.environ["HUB_DATA_DIR"] = TMP
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "t.db")

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

_passed = _failed = 0


def check(label, ok, detail=""):
    global _passed, _failed
    if ok:
        _passed += 1
        print(f"  ok    {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}  {detail}")


def section(title):
    print(f"\n{'=' * 70}\n{title}\n{'=' * 70}")


# =====================================================================
section("hub.scan_facts: the campaign-readiness group carries a stable key")
# =====================================================================

from hub import scan_facts                                          # noqa: E402

REPORT = {
    "google_ads_readiness": {"is_google_ads_ready": True, "has_google_tag": True,
                             "uses_consent_mode_v2": False},
    "analytics": {"analytics_tool": "GA4", "uses_universal_ga": False},
    "retargeting": {"has_facebook_pixel": False, "has_google_pixel": False},
    "click_to_contact": {"tel_links_found_count": 2},
}
ROW = {"public_id": "sc900", "domain_key": "acme.com", "overall_score": 71,
       "tier": "B", "completed_at": "2026-09-01T09:00:00", "created_at": None}

_real_latest = scan_facts._latest
scan_facts._latest = lambda d: (REPORT, ROW, "")
try:
    f = scan_facts.facts("acme.com")
    groups = f.get("groups") or []
    campaign_groups = [g for g in groups if g.get("key") == "campaign_readiness"]
    check("exactly one group carries the campaign_readiness key",
          len(campaign_groups) == 1, groups)
    check("it is the 'Can we run a campaign to this site' group",
          campaign_groups and campaign_groups[0]["title"]
          == "Can we run a campaign to this site",
          campaign_groups)
    other_groups = [g for g in groups if g.get("key")]
    check("no other group was accidentally keyed",
          len(other_groups) == 1, other_groups)
finally:
    scan_facts._latest = _real_latest


# =====================================================================
section("hub.faq.generate(): focus_topic reaches the prompt payload, and only that")
# =====================================================================

from hub import faq, seo                                            # noqa: E402

_real_page_facts = seo._page_facts
_real_sitemap_pages = seo.sitemap_pages
_real_openai_json = seo._openai_json

seo._page_facts = lambda url: {
    "url": url, "title": "AC Repair", "h1": ["AC Repair in Dublin"],
    "h2": [], "text": "We fix air conditioners fast.", "tel": "", "mail": "",
}
seo.sitemap_pages = lambda url, cap=60: [url]

captured_payloads = []


def _fake_openai_json(system, payload, timeout=120, client=None):
    captured_payloads.append(payload)
    return {"faqs": [{"question": "How fast can you fix my AC?",
                      "answer": "Most repairs are same-day if we have the part in stock."}]}


seo._openai_json = _fake_openai_json

try:
    out = faq.generate("Acme HVAC", "https://acme.com/ac-repair", count=1,
                       focus_topic="  how much does ac repair cost  ")
    check("generate() succeeds with a focus_topic", bool(out.get("faqs")), out)
    check("the payload sent to the model carries the trimmed focus",
          captured_payloads and captured_payloads[-1].get("requested_focus")
          == "how much does ac repair cost",
          captured_payloads)

    captured_payloads.clear()
    faq.generate("Acme HVAC", "https://acme.com/ac-repair", count=1)
    check("omitting focus_topic sends an empty string, never None or absent",
          captured_payloads and captured_payloads[-1].get("requested_focus") == "",
          captured_payloads)

    captured_payloads.clear()
    long_topic = "x" * 500
    faq.generate("Acme HVAC", "https://acme.com/ac-repair", count=1,
                focus_topic=long_topic)
    check("an unreasonably long focus is capped rather than sent whole",
          len(captured_payloads[-1].get("requested_focus", "")) <= 200,
          len(captured_payloads[-1].get("requested_focus", "")))
finally:
    seo._page_facts = _real_page_facts
    seo.sitemap_pages = _real_sitemap_pages
    seo._openai_json = _real_openai_json

check("the prompt tells the model requested_focus is optional and may be dropped",
      "requested_focus" in faq._FAQ_PROMPT
      and "ignore" in faq._FAQ_PROMPT.lower(),
      True)
check("the prompt never tells the model to invent an answer to fit the focus",
      "invent" in faq._FAQ_PROMPT.lower(),
      True)


# =====================================================================
section("The route reads focus_topic and passes it through, by name")
# =====================================================================

HUB_SRC = (Path(ROOT) / "hub" / "__init__.py").read_text()
FAQ_ROUTE = HUB_SRC[HUB_SRC.index('@app.route("/api/seo/faq/generate"'):]
FAQ_ROUTE = FAQ_ROUTE[:FAQ_ROUTE.index("@app.route", 1)]
check("the route reads focus_topic off the POST body",
      'body.get("focus_topic")' in FAQ_ROUTE, True)
check("and forwards it to faq.generate() by name, not positionally",
      "focus_topic=focus_topic" in FAQ_ROUTE, True)


# =====================================================================
section("The SEO client page: the Topic Ideas card and the FAQ focus field exist")
# =====================================================================

SEO_HTML = (Path(ROOT) / "hub" / "templates" / "seo_client.html").read_text()
check("the Topic Ideas card is in the template",
      'id="cardTopics"' in SEO_HTML, True)
check("it starts hidden -- absent topics must not show an empty card",
      '<div class="card seoc-card" id="cardTopics" hidden>' in SEO_HTML, True)
check("it reads /api/seo/queue, not a second copy of topic_ideas()",
      "/api/seo/queue?client=" in SEO_HTML, True)
check("a topic click switches to the FAQ Builder view",
      "showSection('faqs')" in SEO_HTML, True)
check("the FAQ Builder has a focus field",
      'id="faqFocus"' in SEO_HTML, True)
check("Build FAQs sends the focus field's value as focus_topic",
      "focus_topic:focus" in SEO_HTML, True)
check("a topic click never claims the URL for the rep -- they still choose the page",
      "urlInp.focus()" in SEO_HTML, True)


# =====================================================================
section("The proposal's ROI section leads with 'Where you stand today', "
        "measured only")
# =====================================================================

import modules.sales_builder.app as sb                              # noqa: E402


def _fact(v):
    return {"value": v, "source": "scan"}


MEASURED_BRIEF = {
    "proof": {"review_count": _fact(40), "review_rating": _fact(4.5)},
    "seo": {"average_monthly_traffic": _fact(1200), "num_keywords_ranked_for": _fact(85)},
}
UNMEASURED_BRIEF = {"proof": {}, "seo": {}}

with mock.patch("hub.client_brief.build", return_value=MEASURED_BRIEF):
    standing = sb._proposal_scan_insights.where_you_stand("Acme Plumbing", "acme.com")
check("where_you_stand() found something to lead the ROI section with",
      standing["measured"] and standing["lines"], standing)

with mock.patch("hub.client_brief.build", return_value=UNMEASURED_BRIEF):
    standing_empty = sb._proposal_scan_insights.where_you_stand("Nobody Scanned", "")
check("and reports nothing measured when the scan has nothing to say",
      standing_empty == {"lines": [], "measured": False}, standing_empty)

_PDF_TEXT = Path(ROOT, "modules", "sales_builder", "app.py").read_text()
_ROI_PDF = _PDF_TEXT[_PDF_TEXT.index('elif kind == "roi":'):]
_ROI_PDF = _ROI_PDF[:_ROI_PDF.index('elif kind ==', _ROI_PDF.index("plan = hub_kpi.framework"))]
check("the PDF's ROI branch reads where_you_stand() before the KPI framework",
      ("where_you_stand(q.client, q.website)" in _ROI_PDF
       and _ROI_PDF.index("where_you_stand")
       < _ROI_PDF.index("hub_kpi.framework")),
      True)
check("and only prints it when measured -- never a placeholder block",
      'if standing["measured"]:' in _ROI_PDF, True)

# The Word export's own "roi" branch is the second copy of that arithmetic,
# by construction rather than name (its own docstring in build_proposal_docx
# opens with "elif kind == "roi":" a second time in the file), so the same
# window has to be re-sliced from its own occurrence.
_ROI_DOCX = _PDF_TEXT[_PDF_TEXT.rindex('elif kind == "roi":'):]
_ROI_DOCX = _ROI_DOCX[:_ROI_DOCX.index("plan = hub_kpi.framework") + 40]
check("the Word export's ROI branch reads it the same way",
      "where_you_stand(q.client, q.website)" in _ROI_DOCX, True)
check("that occurrence is a different branch from the PDF's own",
      _ROI_PDF != _ROI_DOCX, True)

WIZARD_HTML = Path(ROOT, "modules", "sales_builder", "templates",
                    "index.html").read_text()
check("the live preview's ROI table reads the same server-computed standing "
      "-- never a client-side re-derivation",
      "const standing=S._standing;" in WIZARD_HTML, True)
check("and draws it only when measured, same as both documents",
      "standing&&standing.measured&&(standing.lines||[]).length" in WIZARD_HTML,
      True)
check("editLoaded() reads it off the quote payload by name",
      "S._standing=q.where_you_stand||null;" in WIZARD_HTML, True)


# =====================================================================
section("The SEO & AEO scope's derivation rides in its own media-plan row")
# =====================================================================

SEO_AEO_PRODUCT = "SEO & AEO Scope Package"
check("the rate card's placeholder product string is exactly what the join reads",
      sb.SEO_AEO_SCOPE_PRODUCT == SEO_AEO_PRODUCT, sb.SEO_AEO_SCOPE_PRODUCT)
check("is_seo_aeo_scope() keys on the product string, never a substring",
      sb.is_seo_aeo_scope({"product": SEO_AEO_PRODUCT})
      and not sb.is_seo_aeo_scope({"product": "SEO & AEO Scope Package Plus"})
      and not sb.is_seo_aeo_scope({"product": "SEO"})
      and not sb.is_seo_aeo_scope({}), True)


def _scope_item(**over):
    row = {"product": SEO_AEO_PRODUCT, "category": "SEARCH ENGINE OPTIMIZATION",
           "label": "SEARCH ENGINE OPTIMIZATION — " + SEO_AEO_PRODUCT,
           "basis": "monthly", "termMonths": 6, "dollars": 1200,
           "rate": "Managed"}
    row.update(over)
    return row


MEASURED_SCOPE_BRIEF = {
    "proof": {}, "seo": {
        "pages_missing_title_count": _fact(3),
        "pages_missing_description_count": _fact(2),
        "missing_schema_items": _fact(1),
    },
}

with mock.patch("hub.client_brief.build", return_value=MEASURED_SCOPE_BRIEF), \
     mock.patch("hub.seo_queue.broken_link_count", return_value=0):
    state = {"months": 6, "client": "Acme Plumbing", "url": "acme.com",
             "items": [_scope_item()]}
    plan = sb.media_plan_rows(state)

row = plan["rows"][0]
check("the row's description carries the scope's own derivation",
      row["description"] and "small scope" in row["description"], row)
check("and the same figures scope_for() itself would have produced",
      "3 pages missing a title" in row["description"], row["description"])

with mock.patch("hub.client_brief.build", side_effect=RuntimeError("no scan")):
    state_unmeasured = {"months": 6, "client": "Never Scanned", "url": "",
                        "items": [_scope_item()]}
    plan_unmeasured = sb.media_plan_rows(state_unmeasured)
check("an unmeasured scope leaves the description blank -- never a placeholder "
      "line invented to fill the space",
      plan_unmeasured["rows"][0]["description"] == "", plan_unmeasured["rows"][0])

with mock.patch("hub.client_brief.build") as m_brief:
    state_other = {"months": 6, "client": "Acme", "url": "acme.com",
                   "items": [{"product": "RON (Run of Network)",
                             "category": "DISPLAY", "label": "RON",
                             "basis": "monthly", "termMonths": 6, "dollars": 500}]}
    plan_other = sb.media_plan_rows(state_other)
check("a plan with no SEO & AEO Scope Package line never asks the audit at all",
      not m_brief.called, m_brief.call_args_list)
check("and that row's own description stays exactly as it always was: empty",
      plan_other["rows"][0]["description"] == "", plan_other["rows"][0])

check("the PDF's mediaplan branch draws r['description'] generically -- the "
      "same cell the consulting catch-all's own description already uses",
      'r["description"]' in _PDF_TEXT and "elif kind == \"mediaplan\":" in _PDF_TEXT,
      True)
check("the Word export's mediaplan branch draws it the same way",
      'r["description"]' in _PDF_TEXT.split('elif kind == "mediaplan" and state.get("items"):')[1][:900],
      True)
check("the live preview's editable media-plan row draws the server row's own "
      "description too, never a client-side copy of the derivation",
      "(pr&&pr.description)" in WIZARD_HTML, True)


# =====================================================================
section("Client 360: the launch-blocker button reads the same group key")
# =====================================================================

def _c360_source():
    """The Client 360 record as one text: the template plus its script modules
    (hub/client360_assets.MODULES), because the record's JavaScript lives in
    files now and a check that asks what the record does reads all of it."""
    _os, _sys = __import__("os"), __import__("sys")
    _root = _os.path.dirname(_os.path.abspath(__file__))
    if _root not in _sys.path:
        _sys.path.insert(0, _root)
    return __import__("hub.client360_assets", fromlist=["source_text"]).source_text()

C360_HTML = _c360_source()
check("Client 360 matches the group by key, never by the title string",
      "gp.key==='campaign_readiness'" in C360_HTML, True)
check("the mount only appears once, inside that one group",
      C360_HTML.count('id="lb-box"') == 1, True)
check("it reads the dedicated launch-blockers endpoint",
      "/api/client/launch-blockers?client=" in C360_HTML, True)
check("creating a ticket confirms first",
      "confirm('Create one web ticket" in C360_HTML, True)
check("creating a ticket posts to the dedicated endpoint",
      "/api/client/launch-blockers/ticket" in C360_HTML, True)
check("a successful create disables the button so it cannot be pressed twice",
      "btn.disabled=true; btn.textContent='Ticket created'" in C360_HTML, True)


print(f"\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
