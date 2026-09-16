"""Follow-ups on WO-3 (Smart 1 Hub work orders): the two backend seams the
frontend wiring reads.

    1. hub.scan_facts.facts() marks its "Can we run a campaign to this
       site" group with a stable `key`, so Client 360's launch-blocker
       button can find it without matching the title's English prose.
    2. hub.faq.generate() accepts an optional `focus_topic`, threaded from
       the SEO client page's Topic Ideas card into the prompt's
       "requested_focus" -- and the prompt tells the model to drop it
       rather than invent an answer when the page does not support one.

    python3 test_wo3_followups.py
"""
import os
import sys
import tempfile
from pathlib import Path

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
    out2 = faq.generate("Acme HVAC", "https://acme.com/ac-repair", count=1)
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
section("Client 360: the launch-blocker button reads the same group key")
# =====================================================================

C360_HTML = (Path(ROOT) / "hub" / "templates" / "client360.html").read_text()
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
