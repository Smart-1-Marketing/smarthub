"""FAQ Builder — AI-written Frequently Asked Questions for a client page.

Give it one URL. It reads that page in full, samples the rest of the site for
context (services, locations, tone), and drafts 5-8 questions with answers.
Each one is approved, edited, skipped, or swapped for a fresh question. When
every question has been dealt with, the page is saved to the client and stays
with them — listed in a table with the date it was created, the date it was
added to the site (editable, because that rarely happens the same day), the
page URL and the question count.

Saved pages also carry ready-to-paste FAQPage JSON-LD, so approved answers
earn FAQ rich results in Google, and they can be downloaded as a Word
document formatted for a customer to review.
"""
import datetime as _dt
import html as _html
import json
import re

import requests

from . import seo

MIN_Q, MAX_Q = 5, 8

_FAQ_PROMPT = """You are an SEO content strategist writing the FAQ section for a local business website.

You are given the full text of ONE page plus context from the rest of the site.
Write the Frequently Asked Questions a real customer would actually type into Google
or ask on the phone before buying from this business.

Rules:
- Output JSON only: {"faqs": [{"question": str, "answer": str}]}.
- Produce EXACTLY the number of questions requested.
- Questions must be specific to THIS page's subject and this business — never generic
  filler like "What are your hours?" unless the page is about hours. Favor the
  questions that carry search intent: cost, process, timeline, what's included,
  who it's for, service area, what makes them different, what to do first.
- Answers: 40-90 words, plain English, second person, no marketing fluff, and they
  must answer the question in the FIRST sentence (that is what Google pulls).
- NEVER invent prices, guarantees, certifications, awards, years in business,
  staff counts or service claims that are not in the supplied content. When a
  specific number is unknown, answer in general terms instead of guessing.
- Work the business name and city in naturally where it fits — do not stuff them.
- Do not repeat a question already listed in "questions_to_avoid", and do not
  rephrase one of them into a near-duplicate."""


# ------------------------------------------------------------------ storage
def _pages(store: dict) -> dict:
    faq = store.setdefault("faq_pages", {})
    return faq


def _norm_url(url: str) -> str:
    url = str(url or "").strip()
    if not url:
        return ""
    if not url.startswith("http"):
        url = "https://" + url
    return url.split("#")[0].rstrip("/") or url


def list_pages(client: str) -> list[dict]:
    """Saved FAQ pages, newest first — the rows behind the FAQ table."""
    store = seo.load_store(client)
    out = []
    for url, page in _pages(store).items():
        items = page.get("questions", [])
        out.append({
            "url": url,
            "title": page.get("title", ""),
            "created": page.get("created", ""),
            "added_to_site": page.get("added_to_site", ""),
            "updated": page.get("updated", ""),
            "count": len(items),
            "questions": items,
        })
    out.sort(key=lambda p: str(p.get("created") or ""), reverse=True)
    return out


def get_page(client: str, url: str) -> dict | None:
    return _pages(seo.load_store(client)).get(_norm_url(url))


def save_page(client: str, url: str, questions: list[dict],
              added_to_site: str = "", title: str = "",
              style: dict | None = None) -> dict:
    """Create or replace one saved FAQ page. Approved answers only."""
    url = _norm_url(url)
    if not url:
        raise ValueError("A page URL is required.")
    clean = []
    for q in questions or []:
        if not isinstance(q, dict):
            continue
        question = str(q.get("question") or "").strip()
        answer = str(q.get("answer") or "").strip()
        if question and answer:
            clean.append({"question": question[:300], "answer": answer[:2000]})
    if not clean:
        raise ValueError("Nothing to save — every question was skipped.")

    store = seo.load_store(client)
    pages = _pages(store)
    existing = pages.get(url) or {}
    page = {
        "url": url,
        "title": str(title or existing.get("title") or "").strip(),
        "questions": clean,
        "created": existing.get("created") or _dt.date.today().isoformat(),
        "added_to_site": seo_date(added_to_site, existing.get("added_to_site", "")),
        "updated": _dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    # keep the styling captured when the page was read, so the embeddable
    # accordion always matches the site it came from
    style = style if isinstance(style, dict) and style else existing.get("style")
    if not style:
        try:
            style = page_style(url)
        except Exception:                        # noqa: BLE001
            style = dict(_DEFAULT_STYLE)
    page["style"] = {k: str(v) for k, v in (style or {}).items() if v}
    page["schema"] = faq_schema(page)
    pages[url] = page
    seo.save_store(client, store)
    return page


def seo_date(value, fallback="") -> str:
    """Normalise a date the user typed to YYYY-MM-DD (blank stays blank)."""
    s = str(value or "").strip()
    if not s:
        return fallback if value is None else ""
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y"):
        try:
            return _dt.datetime.strptime(s[:10], fmt).date().isoformat()
        except ValueError:
            continue
    return fallback


def update_page(client: str, url: str, updates: dict) -> dict | None:
    """Edit the added-to-site date, or the questions themselves after a
    customer asks for a change."""
    url = _norm_url(url)
    store = seo.load_store(client)
    pages = _pages(store)
    page = pages.get(url)
    if page is None:
        return None
    if "added_to_site" in updates:
        page["added_to_site"] = seo_date(updates["added_to_site"], "")
    if isinstance(updates.get("questions"), list):
        clean = []
        for q in updates["questions"]:
            if not isinstance(q, dict):
                continue
            question = str(q.get("question") or "").strip()
            answer = str(q.get("answer") or "").strip()
            if question and answer:
                clean.append({"question": question[:300], "answer": answer[:2000]})
        if clean:
            page["questions"] = clean
    page["updated"] = _dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    page["schema"] = faq_schema(page)
    seo.save_store(client, store)
    return page


def delete_page(client: str, url: str) -> bool:
    url = _norm_url(url)
    store = seo.load_store(client)
    pages = _pages(store)
    if url not in pages:
        return False
    pages.pop(url)
    seo.save_store(client, store)
    return True


# --------------------------------------------------------------- generation
def _site_context(client: str, url: str, store: dict) -> dict:
    """The target page in full, plus a light sample of the rest of the site."""
    facts = seo._page_facts(url)
    if facts.get("error"):
        raise ValueError(f"Could not read {url} — {facts['error']}")

    others, sitemap = [], store.get("sitemap") or []
    if not sitemap:
        try:
            sitemap = seo.sitemap_pages(url, cap=60)
            store["sitemap"] = sitemap
        except Exception:                        # noqa: BLE001 — context is optional
            sitemap = []
    target = _norm_url(url)
    for other in [u for u in sitemap if _norm_url(u) != target][:4]:
        try:
            f = seo._page_facts(other)
        except Exception:                        # noqa: BLE001
            continue
        if f.get("error"):
            continue
        others.append({"url": other, "title": f.get("title", ""),
                       "h1": f.get("h1", []), "h2": f.get("h2", []),
                       "text": str(f.get("text", ""))[:900]})

    return {
        "client_name": client,
        "business_info": seo.master_business_info(client, store),
        "page": {k: v for k, v in facts.items() if k != "error"},
        "other_pages_on_site": others,
        "all_page_urls": sitemap[:40],
    }


def _fallback_faqs(ctx: dict, count: int, avoid: list[str]) -> list[dict]:
    """No OPENAI_API_KEY — draft the skeleton so the workflow still runs and
    the team can write the answers themselves."""
    bi = ctx.get("business_info", {})
    name = bi.get("name") or ctx.get("client_name") or "this business"
    what = bi.get("category") or (ctx.get("page", {}).get("title") or "these services")
    city = bi.get("city") or "your area"
    seeds = [
        (f"What does {name} charge for {what}?",
         f"Pricing for {what} depends on the size and scope of the job. "
         f"Contact {name} for a straightforward quote before any work begins."),
        (f"How long does {what} take?",
         "Timelines vary with the scope of work. Most projects are scheduled after "
         "a short assessment so you get a realistic date up front."),
        (f"Does {name} serve {city}?",
         f"Yes — {name} works throughout {city} and the surrounding communities. "
         "Call to confirm coverage for your exact address."),
        (f"What is included with {what}?",
         "Every job includes an up-front assessment, the agreed scope of work, and a "
         "walkthrough at completion so you know exactly what was done."),
        (f"How do I get started with {name}?",
         f"Reach out by phone or through the contact form. {name} will review what you "
         "need, answer your questions, and schedule the work."),
        (f"What makes {name} different from other {what} companies?",
         "Clear communication, work performed to the agreed scope, and a team that "
         "answers the phone when you call."),
        (f"Do I need an appointment for {what}?",
         "Scheduling ahead is best so the right people and materials are on hand, "
         "though urgent requests are handled as quickly as possible."),
        (f"What should I have ready before contacting {name} about {what}?",
         "A short description of the problem, the address, and any photos you have. "
         "That is usually enough to get you an accurate answer quickly."),
    ]
    seen = {a.strip().lower() for a in avoid}
    out = []
    for q, a in seeds:
        if q.strip().lower() in seen:
            continue
        out.append({"question": q, "answer": a})
        if len(out) >= count:
            break
    return out


def generate(client: str, url: str, count: int = 6, avoid: list[str] | None = None) -> dict:
    """Draft `count` questions for one page. Called for the first batch and,
    with count=1, for 'generate another question'."""
    url = _norm_url(url)
    if not url:
        raise ValueError("Enter the page URL you want FAQs for.")
    count = max(1, min(int(count or MIN_Q), MAX_Q))
    avoid = [str(a).strip() for a in (avoid or []) if str(a).strip()]

    store = seo.load_store(client)
    ctx = _site_context(client, url, store)
    seo.save_store(client, store)                # keep the sitemap we just built

    payload = dict(ctx)
    payload["question_count"] = count
    payload["questions_to_avoid"] = avoid

    faqs, ai_error = [], ""
    try:
        out = seo._openai_json(_FAQ_PROMPT, payload) or {}
        for item in (out.get("faqs") or []):
            if not isinstance(item, dict):
                continue
            q = str(item.get("question") or "").strip()
            a = str(item.get("answer") or "").strip()
            if q and a and q.lower() not in {x.lower() for x in avoid}:
                faqs.append({"question": q[:300], "answer": a[:2000]})
    except Exception as exc:                     # noqa: BLE001 — fall back below
        ai_error = str(exc)

    if len(faqs) < count:
        faqs += _fallback_faqs(ctx, count - len(faqs),
                               avoid + [f["question"] for f in faqs])

    import os
    result = {
        "url": url,
        "title": ctx["page"].get("title", ""),
        "faqs": faqs[:count],
        "ai": bool(os.environ.get("OPENAI_API_KEY")) and not ai_error,
        "ai_error": ai_error,
    }
    if count > 1:            # first batch — also learn how the page is styled
        try:
            result["style"] = page_style(url)
        except Exception:                        # noqa: BLE001 — defaults are fine
            result["style"] = dict(_DEFAULT_STYLE)
    return result


# ------------------------------------------- match the site's look and feel
# The embeddable accordion has to look like it belongs on the client's site,
# so when a page is read we also record the fonts and colours it actually
# uses — from its own stylesheets, not a guess.
_DEFAULT_STYLE = {
    "font": "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif",
    "heading_font": "",
    "text": "#1e293b",
    "heading": "#111827",
    "accent": "#2563eb",
    "border": "#e5e7eb",
    "panel": "#f8fafc",
}
_NAMED = {"white": "#ffffff", "black": "#000000"}


def _clean_font(value: str) -> str:
    v = re.sub(r"\s+", " ", str(value or "")).strip().rstrip(";").strip()
    v = re.sub(r"\s*!important$", "", v, flags=re.I)
    if not v or v.lower() in ("inherit", "initial", "unset", "revert"):
        return ""
    return v[:220]


def _clean_color(value: str) -> str:
    v = str(value or "").strip().rstrip(";").strip().lower()
    v = re.sub(r"\s*!important$", "", v)
    if v in _NAMED:
        return _NAMED[v]
    if re.match(r"^#[0-9a-f]{3}$", v):
        return "#" + "".join(c * 2 for c in v[1:])
    if re.match(r"^#[0-9a-f]{6}$", v):
        return v
    m = re.match(r"^rgba?\(\s*(\d+)[,\s]+(\d+)[,\s]+(\d+)", v)
    if m:
        return "#" + "".join(f"{min(255, int(x)):02x}" for x in m.groups())
    return ""


def _decl(css: str, selector_re: str, prop: str) -> str:
    """First value of `prop` inside the first rule whose selector matches."""
    for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
        sel, body = m.group(1), m.group(2)
        if not re.search(selector_re, sel, re.I):
            continue
        p = re.search(re.escape(prop) + r"\s*:\s*([^;}]+)", body, re.I)
        if p:
            return p.group(1)
    return ""


def page_style(url: str, html_text: str = "") -> dict:
    """Fonts and colours pulled from the page and its stylesheets."""
    style = dict(_DEFAULT_STYLE)
    try:
        if not html_text:
            html_text = seo._fetch(url)
    except Exception:                            # noqa: BLE001 — defaults are fine
        return style

    css = " ".join(re.findall(r"<style[^>]*>(.*?)</style>", html_text, re.S | re.I))
    origin = seo._origin(url)
    hrefs = re.findall(r'<link[^>]+rel=["\']stylesheet["\'][^>]*>', html_text, re.I)
    sheets = []
    for tag in hrefs[:4]:
        m = re.search(r'href=["\'](.*?)["\']', tag, re.I)
        if not m:
            continue
        href = _html.unescape(m.group(1))
        if href.startswith("//"):
            href = "https:" + href
        elif href.startswith("/"):
            href = origin + href
        elif not href.startswith("http"):
            href = origin + "/" + href.lstrip("./")
        sheets.append(href)
    for href in sheets[:3]:
        try:
            r = requests.get(href, headers=seo.UA, timeout=10)
            if r.ok:
                css += " " + r.text[:400_000]
        except Exception:                        # noqa: BLE001
            continue
    if not css:
        return style

    body_font = _clean_font(_decl(css, r"(^|,)\s*(html|body)\s*$", "font-family")) \
        or _clean_font(_decl(css, r"\bbody\b", "font-family"))
    if body_font:
        style["font"] = body_font
    head_font = _clean_font(_decl(css, r"\bh[12]\b", "font-family"))
    if head_font and head_font != body_font:
        style["heading_font"] = head_font

    for key, selector, prop in (("text", r"\bbody\b", "color"),
                                ("heading", r"\bh[12]\b", "color"),
                                ("accent", r"(^|,)\s*a\s*(:link)?\s*$", "color")):
        c = _clean_color(_decl(css, selector, prop))
        if c and c not in ("#ffffff",):
            style[key] = c
    if style["accent"] == "#ffffff" or not style["accent"]:
        style["accent"] = _DEFAULT_STYLE["accent"]
    return style


def _contrast_text(hex_color: str) -> str:
    """Readable text colour on top of the given background."""
    c = _clean_color(hex_color) or "#ffffff"
    r, g, b = (int(c[i:i + 2], 16) for i in (1, 3, 5))
    return "#111111" if (r * 299 + g * 587 + b * 114) / 1000 > 150 else "#ffffff"


def accordion_html(client: str, urls: list[str] | None = None,
                   standalone: bool = False) -> str:
    """Copy-paste accordion FAQ for the client's site.

    Self-contained: scoped CSS, no JavaScript (native <details>), styled with
    the fonts and colours of the page the questions came from, and it carries
    its own FAQPage JSON-LD so the markup travels with the content. Drops
    straight into a code / embed block on any site builder.
    """
    pages = list_pages(client)
    if urls:
        wanted = {_norm_url(u) for u in urls}
        pages = [p for p in pages if p["url"] in wanted]
    if not pages:
        return "<!-- No FAQ pages selected. -->"

    esc = _html.escape
    out = []
    if standalone:
        out.append('<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
                   '<meta name="viewport" content="width=device-width,initial-scale=1">'
                   f"<title>FAQ — {esc(client)}</title></head><body>")

    for idx, row in enumerate(pages):
        page = get_page(client, row["url"]) or row
        st = page.get("style") or _DEFAULT_STYLE
        # stable, collision-free class prefix so several snippets can live on
        # the same site without their styles bleeding into each other
        import hashlib
        ns = "s1faq-" + hashlib.md5(
            (page.get("url") or str(idx)).encode()).hexdigest()[:8]
        accent = st.get("accent") or _DEFAULT_STYLE["accent"]
        head_font = st.get("heading_font") or st.get("font")

        out.append(f"\n<!-- ===== Smart 1 Marketing · FAQ for {esc(page.get('url', ''))} ===== -->")
        out.append(f'<section class="{ns}" aria-label="Frequently asked questions">')
        out.append(f"""<style>
.{ns}{{font-family:{st.get('font')};color:{st.get('text')};max-width:860px;margin:32px auto;
 line-height:1.6;-webkit-font-smoothing:antialiased}}
.{ns} .{ns}-h{{font-family:{head_font};color:{st.get('heading')};font-size:1.6em;
 margin:0 0 18px;font-weight:700}}
.{ns} details{{border:1px solid {st.get('border')};border-radius:10px;margin-bottom:10px;
 background:#fff;overflow:hidden;transition:border-color .15s ease}}
.{ns} details[open]{{border-color:{accent}}}
.{ns} summary{{font-family:{head_font};font-weight:600;font-size:1.02em;color:{st.get('heading')};
 padding:16px 46px 16px 18px;cursor:pointer;position:relative;list-style:none;
 background:{st.get('panel')}}}
.{ns} summary::-webkit-details-marker{{display:none}}
.{ns} summary:hover{{color:{accent}}}
.{ns} summary:focus-visible{{outline:2px solid {accent};outline-offset:-2px}}
.{ns} summary::after{{content:"";position:absolute;right:18px;top:50%;width:9px;height:9px;
 border-right:2px solid {accent};border-bottom:2px solid {accent};
 transform:translateY(-70%) rotate(45deg);transition:transform .2s ease}}
.{ns} details[open] summary::after{{transform:translateY(-30%) rotate(225deg)}}
.{ns} .{ns}-a{{padding:14px 18px 18px;margin:0;border-top:1px solid {st.get('border')}}}
@media (max-width:600px){{.{ns} summary{{padding:14px 40px 14px 14px}}
 .{ns} .{ns}-a{{padding:12px 14px 16px}}}}
</style>""")
        out.append(f'  <h2 class="{ns}-h">Frequently Asked Questions</h2>')
        for q in page.get("questions", []):
            out.append("  <details>")
            out.append(f"    <summary>{esc(q['question'])}</summary>")
            out.append(f"    <p class=\"{ns}-a\">{esc(q['answer'])}</p>")
            out.append("  </details>")
        out.append('  <script type="application/ld+json">')
        out.append(json.dumps(page.get("schema") or faq_schema(page), indent=1))
        out.append("  </script>")
        out.append("</section>")

    if standalone:
        out.append("</body></html>")
    return "\n".join(out)


# ------------------------------------------------------------------ outputs
def faq_schema(page: dict) -> dict:
    """FAQPage JSON-LD for one saved page — copy-paste ready."""
    url = page.get("url", "")
    return {
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "@id": url + "#faq",
        **({"url": url} if url else {}),
        **({"name": page["title"]} if page.get("title") else {}),
        "mainEntity": [
            {"@type": "Question", "name": q["question"],
             "acceptedAnswer": {"@type": "Answer", "text": q["answer"]}}
            for q in page.get("questions", [])
        ],
    }


def schema_html(client: str, urls: list[str] | None = None) -> str:
    """One <script type="application/ld+json"> block per saved FAQ page."""
    pages = list_pages(client)
    if urls:
        wanted = {_norm_url(u) for u in urls}
        pages = [p for p in pages if p["url"] in wanted]
    out = [f"<!-- FAQPage schema for {client} — generated by Smart 1 Hub -->"]
    for p in pages:
        full = get_page(client, p["url"]) or p
        out.append(f"\n<!-- ===== {p['url']} ===== -->")
        out.append('<script type="application/ld+json">')
        out.append(json.dumps(full.get("schema") or faq_schema(full), indent=1))
        out.append("</script>")
    return "\n".join(out)


def review_doc(client: str, urls: list[str] | None = None) -> str:
    """Word-openable document of the selected FAQ pages, laid out for a
    customer to read and mark up."""
    pages = list_pages(client)
    if urls:
        wanted = {_norm_url(u) for u in urls}
        pages = [p for p in pages if p["url"] in wanted]
    esc = _html.escape
    today = _dt.date.today().strftime("%B %-d, %Y")
    parts = ["""<html xmlns:w="urn:schemas-microsoft-com:office:word"><head><meta charset="utf-8">
<title>Frequently Asked Questions — """ + esc(client) + """</title>
<style>
body{font-family:Calibri,Segoe UI,sans-serif;max-width:760px;margin:24px auto;color:#1e293b;line-height:1.55}
h1{color:#1a2e58;font-size:22pt;margin-bottom:2px}
h2{color:#1a2e58;font-size:14pt;margin:26px 0 4px}
.meta{color:#64748b;font-size:10pt}
.pagehead{background:#f4f6fa;border-left:4px solid #1a2e58;padding:10px 14px;margin:26px 0 14px}
.pagehead .u{font-size:10.5pt;color:#2563eb;word-break:break-all}
.q{font-weight:700;color:#1a2e58;margin:16px 0 3px;font-size:12pt}
.a{margin:0 0 4px}
.note{color:#94a3b8;font-size:9.5pt;font-style:italic}
.pagebreak{page-break-before:always}
</style></head><body>"""]
    parts.append(f"<h1>Frequently Asked Questions</h1>"
                 f"<p class='meta'><b>{esc(client)}</b> &middot; prepared by Smart 1 Marketing "
                 f"&middot; {esc(today)}</p>"
                 f"<p class='meta'>Please review the questions and answers below. "
                 f"Mark any changes directly on this document and send it back — "
                 f"we'll update them and publish.</p>")
    if not pages:
        parts.append("<p><i>No FAQ pages saved yet.</i></p>")
    for i, p in enumerate(pages):
        cls = " pagebreak" if i else ""
        parts.append(f"<div class='pagehead{cls}'><b>{esc(p.get('title') or p['url'])}</b>"
                     f"<div class='u'>{esc(p['url'])}</div>"
                     f"<div class='meta'>{len(p['questions'])} questions"
                     + (f" &middot; added to site {esc(p['added_to_site'])}"
                        if p.get("added_to_site") else "") + "</div></div>")
        for q in p["questions"]:
            parts.append(f"<div class='q'>{esc(q['question'])}</div>"
                         f"<p class='a'>{esc(q['answer'])}</p>")
    parts.append("<p class='note'>Approved answers are published with FAQ schema "
                 "so they can appear as expandable results in Google.</p>")
    parts.append("</body></html>")
    return "\n".join(parts)
