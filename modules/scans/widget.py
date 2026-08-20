"""The free pre-check behind the public scan widget.

A full Insites audit costs a credit and takes one to four minutes. Neither is
acceptable for an anonymous visitor who has typed a domain into a box on
someone else's website, so this module answers the same question badly but
instantly and for nothing: **can AI assistants see this business?**

Everything here is read with three or four plain HTTP GETs of the prospect's
own site — robots.txt, llms.txt, the homepage, sitemap.xml. No third-party
API, no key, no credit, no row in anybody's billing. It runs in about five
seconds and it is the only thing a visitor sees before they identify
themselves.

The checks are deliberately a **subset of the Insites GEO audit**, worded the
same way. When the visitor converts and the real scan runs, the full report
must read as *more* of the same story, never as a contradiction of it. If a
check here can't be answered confidently it is reported as unknown and left
out of the score — an anonymous visitor being told they're fine when nobody
looked is the one outcome that would make the paid report look like a lie.
"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from typing import Optional
from urllib.parse import urlparse

import requests

UA = {"User-Agent": "Mozilla/5.0 (compatible; Smart1Hub/1.0; "
                    "+https://smart1marketing.com)"}
TIMEOUT = 8

# The crawlers that feed the assistants people actually ask. Blocking any of
# these is the difference between being quotable and being invisible.
AI_AGENTS = [
    ("GPTBot", "ChatGPT"),
    ("OAI-SearchBot", "ChatGPT search"),
    ("ChatGPT-User", "ChatGPT browsing"),
    ("ClaudeBot", "Claude"),
    ("anthropic-ai", "Claude"),
    ("PerplexityBot", "Perplexity"),
    ("Google-Extended", "Gemini"),
    ("Applebot-Extended", "Apple Intelligence"),
    ("CCBot", "Common Crawl (feeds several models)"),
]

_SCHEME = re.compile(r"^https?://", re.I)


class PrecheckError(Exception):
    """The site couldn't be read at all — bad domain, dead host, hard block."""


# ---------------------------------------------------------------- helpers

def normalise_domain(raw: str) -> str:
    """'HTTPS://WWW.Example.com/pricing?x=1' -> 'example.com'.

    Returns "" for anything that isn't plausibly a domain. The widget is a
    public form; the single most common input after a real domain is an email
    address, and spending a lookup on 'bob@gmail.com' helps nobody.
    """
    s = (raw or "").strip().lower()
    if not s:
        return ""
    if "@" in s and not _SCHEME.match(s):
        return ""
    if not _SCHEME.match(s):
        s = "https://" + s
    host = (urlparse(s).hostname or "").strip(".")
    if not host or "." not in host or " " in host:
        return ""
    if host.startswith("www."):
        host = host[4:]
    # Reject obvious non-sites early rather than after four timeouts.
    if host in ("localhost",) or re.match(r"^\d+\.\d+\.\d+\.\d+$", host):
        return ""
    tld = host.rsplit(".", 1)[-1]
    if len(tld) < 2 or not tld.isalpha():
        return ""
    return host


def _get(url: str, timeout: int = TIMEOUT) -> Optional[requests.Response]:
    try:
        r = requests.get(url, headers=UA, timeout=timeout, allow_redirects=True)
        return r
    except requests.RequestException:
        return None


def _homepage(domain: str) -> tuple[str, str]:
    """(final_url, html). Tries https then http. Raises if neither answers."""
    for base in (f"https://{domain}", f"http://{domain}"):
        r = _get(base, TIMEOUT + 4)
        if r is not None and r.status_code < 400 and r.text:
            return r.url, r.text
    raise PrecheckError(
        "We couldn't load that website. Check the address and try again — and "
        "if it's behind a firewall or password, that's worth knowing too: "
        "search engines and AI assistants hit the same wall.")


def _text_of(html: str) -> str:
    t = re.sub(r"<script.*?</script>|<style.*?</style>|<!--.*?-->", " ", html,
               flags=re.S | re.I)
    t = re.sub(r"<[^>]+>", " ", t)
    return re.sub(r"\s+", " ", t).strip()


# ---------------------------------------------------------------- checks

def _base_of(final_url: str) -> str:
    """Scheme + host the site actually served from, after redirects.

    Reading ``https://domain.com/robots.txt`` when the site lives on
    ``www.domain.com`` reads a different file — often a missing one — and
    reports "nothing blocked" for a site that blocks everything. Aux files are
    fetched relative to where the homepage really landed.
    """
    p = urlparse(final_url)
    return f"{p.scheme}://{p.netloc}" if p.scheme and p.netloc else final_url


def _check_robots(base: str) -> dict:
    """Is anything in robots.txt shutting the AI crawlers out?

    Parsed properly rather than by substring: a naive ``"GPTBot" in text``
    reports a block on a file whose only mention of GPTBot is an *Allow*.
    """
    r = _get(f"{base}/robots.txt")
    if r is None or r.status_code >= 400 or not r.text:
        # No robots.txt means nothing is blocked. That is a pass, not a gap.
        return {"blocked": [], "measured": r is not None, "has_robots": False}

    blocked, agent, disallows = [], None, []
    groups: dict[str, list[str]] = {}
    for line in r.text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        field, _, value = line.partition(":")
        field, value = field.strip().lower(), value.strip()
        if field == "user-agent":
            agent = value.lower()
            groups.setdefault(agent, [])
        elif field == "disallow" and agent is not None:
            groups.setdefault(agent, []).append(value)

    for token, label in AI_AGENTS:
        rules = groups.get(token.lower())
        if rules is None:
            rules = groups.get("*") if token.lower() not in groups else None
        if rules and any(v == "/" for v in rules):
            blocked.append(label)
    # De-duplicate labels (Claude appears twice by design).
    seen, out = set(), []
    for b in blocked:
        if b not in seen:
            seen.add(b)
            out.append(b)
    return {"blocked": out, "measured": True, "has_robots": True,
            "disallows": disallows}


def _check_llms(base: str) -> bool:
    r = _get(f"{base}/llms.txt", 6)
    if r is None or r.status_code >= 400:
        return False
    body = (r.text or "").strip()
    # Plenty of sites answer 200 with their HTML 404 page. An llms.txt is
    # markdown; if it opens with a tag it isn't one.
    return bool(body) and not body.lstrip().startswith("<")


def _check_sitemap(base: str) -> bool:
    r = _get(f"{base}/sitemap.xml", 6)
    if r is None or r.status_code >= 400:
        return False
    head = (r.text or "")[:400].lstrip().lower()
    return "<urlset" in head or "<sitemapindex" in head


def _schema_types(html: str) -> set[str]:
    """Every schema.org @type on the page, JSON-LD and microdata."""
    import html as _html
    import json as _json
    types: set[str] = set()
    for block in re.findall(
            r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
            html, re.S | re.I):
        try:
            data = _json.loads(_html.unescape(block.strip()))
        except ValueError:
            continue
        stack = [data]
        while stack:
            node = stack.pop()
            if isinstance(node, list):
                stack.extend(node)
            elif isinstance(node, dict):
                t = node.get("@type")
                if isinstance(t, list):
                    types.update(str(x) for x in t)
                elif t:
                    types.add(str(t))
                stack.extend(v for v in node.values()
                            if isinstance(v, (dict, list)))
    types.update(re.findall(r'itemtype=["\']https?://schema\.org/(\w+)',
                            html, re.I))
    return types


# schema.org LocalBusiness subtypes worth recognising. Not exhaustive — the
# fallback below catches anything ending in a business-ish word — but these
# are the ones that actually turn up on the sites Smart 1 sells to.
_LOCAL_TYPES = {
    "localbusiness", "organization", "corporation", "store", "restaurant",
    "hvacbusiness", "plumber", "electrician", "roofingcontractor",
    "generalcontractor", "homeandconstructionbusiness", "autorepair",
    "automotivebusiness", "dentist", "physician", "medicalclinic",
    "medicalbusiness", "legalservice", "attorney", "insuranceagency",
    "financialservice", "accountingservice", "realestateagent",
    "professionalservice", "healthandbeautybusiness", "dayspa", "hairsalon",
    "foodestablishment", "cafeorcoffeeshop", "bakery", "bar", "lodgingbusiness",
    "hotel", "childcare", "veterinarycare", "movingcompany", "selfstorage",
    "pestcontrolservice", "housepainter", "locksmith", "landscaper",
    "cleaningservice", "travelagency", "sportsactivitylocation", "gym",
}


def _looks_local(types: set[str]) -> bool:
    low = {t.lower() for t in types}
    if low & _LOCAL_TYPES:
        return True
    return any(t.endswith(("business", "service", "contractor", "agency",
                           "store", "clinic", "practice"))
               for t in low)


def _finding(key, label, state, detail, fix="", weight=1) -> dict:
    return {"key": key, "label": label, "state": state, "detail": detail,
            "fix": fix, "weight": weight}


def precheck(domain: str) -> dict:
    """Everything the widget knows before a credit is spent.

    Returns ``{"domain", "url", "business", "score", "band", "headline",
    "findings": [...], "counts": {...}}``. Raises ``PrecheckError`` only when
    the site itself couldn't be loaded — every individual check degrades to
    "unknown" rather than taking the whole thing down.
    """
    domain = normalise_domain(domain)
    if not domain:
        raise PrecheckError(
            "That doesn't look like a website address. Enter it like "
            "yourbusiness.com.")

    # The homepage comes first because its final URL decides where the aux
    # files live; the three that follow are independent, so pay for the
    # slowest rather than the sum.
    final_url, html = _homepage(domain)            # raises PrecheckError
    base = _base_of(final_url)
    with ThreadPoolExecutor(max_workers=3) as pool:
        f_robots = pool.submit(_check_robots, base)
        f_llms = pool.submit(_check_llms, base)
        f_sitemap = pool.submit(_check_sitemap, base)
        try:
            robots = f_robots.result()
        except Exception:                          # noqa: BLE001
            robots = {"blocked": [], "measured": False, "has_robots": False}
        try:
            has_llms = f_llms.result()
        except Exception:                          # noqa: BLE001
            has_llms = False
        try:
            has_sitemap = f_sitemap.result()
        except Exception:                          # noqa: BLE001
            has_sitemap = False

    types = _schema_types(html)
    text = _text_of(html)
    words = len(text.split())
    title_m = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
    title = re.sub(r"\s+", " ", title_m.group(1)).strip() if title_m else ""
    desc_m = re.search(
        r'<meta[^>]+name=["\']description["\'][^>]+content=["\'](.*?)["\']',
        html, re.I) or re.search(
        r'<meta[^>]+content=["\'](.*?)["\'][^>]+name=["\']description["\']',
        html, re.I)
    description = (desc_m.group(1).strip() if desc_m else "")
    has_tel = bool(re.search(r'href=["\']tel:', html, re.I))

    findings: list[dict] = []

    # 1. AI crawler access — the one that makes everything else moot.
    if not robots["measured"]:
        findings.append(_finding(
            "ai_crawlers", "AI crawler access", "unknown",
            "We couldn't read this site's robots.txt file.",
            "Worth checking by hand — it's the file that decides whether AI "
            "assistants may read the site at all.", weight=3))
    elif robots["blocked"]:
        findings.append(_finding(
            "ai_crawlers", "AI assistants are blocked from reading this site",
            "bad",
            "robots.txt blocks " + ", ".join(robots["blocked"]) + ". Those "
            "assistants cannot read a word of this website, so they can never "
            "recommend this business.",
            "Remove the Disallow rules for those crawlers in robots.txt. It's "
            "a one-line change and it's the single highest-value fix here.",
            weight=3))
    else:
        findings.append(_finding(
            "ai_crawlers", "AI assistants can read this site", "ok",
            "ChatGPT, Claude, Perplexity and Gemini are all allowed to crawl "
            "this website.",
            "", weight=3))

    # 2. Local structured data — how AI knows what and where this business is.
    if _looks_local(types):
        findings.append(_finding(
            "local_schema", "Business structured data found", "ok",
            "The site tells AI and search engines what kind of business this "
            "is (" + ", ".join(sorted(types)[:4]) + ").", "", weight=3))
    elif types:
        findings.append(_finding(
            "local_schema", "No business structured data", "bad",
            "There is structured data on this site, but none of it identifies "
            "the business itself — no name, address, phone or category that a "
            "machine can read.",
            "Add LocalBusiness (or the specific subtype — Plumber, Dentist, "
            "Restaurant) markup with the name, address, phone and hours.",
            weight=3))
    else:
        findings.append(_finding(
            "local_schema", "No structured data at all", "bad",
            "Nothing on this site is marked up for machines. AI assistants and "
            "search engines are left to guess what this business does and "
            "where it is.",
            "Add LocalBusiness structured data. It is the cheapest and "
            "largest single improvement available to most sites.", weight=3))

    # 3. FAQ markup — the format assistants quote from most.
    if any(t.lower() in ("faqpage", "question") for t in types):
        findings.append(_finding(
            "faq_schema", "FAQ content is marked up", "ok",
            "Questions and answers on this site are in the format AI "
            "assistants quote directly.", "", weight=2))
    else:
        findings.append(_finding(
            "faq_schema", "No FAQ markup", "bad",
            "AI answers are assembled from questions and answers. Without FAQ "
            "markup this site has nothing in the shape an assistant wants.",
            "Publish an FAQ built from what customers actually ask, marked up "
            "as FAQPage.", weight=2))

    # 4. llms.txt — no score weight, but a strong differentiator to talk about.
    findings.append(_finding(
        "llms_txt", "llms.txt published" if has_llms else "No llms.txt file",
        "ok" if has_llms else "warn",
        "This site publishes an llms.txt — a guide telling large language "
        "models what matters on it." if has_llms else
        "An llms.txt tells AI models which pages matter and how to describe "
        "this business. Very few sites have one yet, which is exactly why it "
        "is worth having.",
        "" if has_llms else "Publish an llms.txt at the root of the domain.",
        weight=1))

    # 5. Sitemap.
    findings.append(_finding(
        "sitemap", "Sitemap found" if has_sitemap else "No sitemap found",
        "ok" if has_sitemap else "bad",
        "Search engines and AI crawlers are given a map of every page."
        if has_sitemap else
        "Without a sitemap, crawlers have to discover pages by following "
        "links — and anything they miss cannot be quoted or ranked.",
        "" if has_sitemap else "Publish sitemap.xml and reference it in "
                               "robots.txt.", weight=2))

    # 6. Title and description.
    if title and description:
        state, label, detail, fix = ("ok", "Title and description are set",
                                     "The homepage tells search engines and "
                                     "assistants what it is.", "")
    elif title:
        state, label = "warn", "No meta description"
        detail = ("The homepage has a title but no description, so Google and "
                  "AI assistants write their own summary of this business.")
        fix = "Write a 150-character description of what the business does."
    else:
        state, label = "bad", "The homepage has no title tag"
        detail = ("There is no title on the homepage. It is the first thing "
                  "every search engine and assistant reads.")
        fix = "Add a title tag naming the business and what it does."
    findings.append(_finding("titles", label, state, detail, fix, weight=2))

    # 7. Enough content to summarise.
    if words >= 500:
        findings.append(_finding(
            "content", "Enough content to summarize", "ok",
            f"About {words:,} words on the homepage — enough for an assistant "
            "to understand and describe the business.", "", weight=2))
    else:
        findings.append(_finding(
            "content", "Very little content for AI to read", "bad",
            f"Only about {words:,} words on the homepage. Assistants "
            "summarize what they can read, and there isn't much here.",
            "Add real written detail about the services, the area served and "
            "the questions customers ask.", weight=2))

    # 8. Tap-to-call — the conversion check that pays for the rest.
    findings.append(_finding(
        "click_to_call", "Tap-to-call is set up" if has_tel else
                         "The phone number isn't tappable", "ok" if has_tel else "warn",
        "Visitors on a phone can call in one tap." if has_tel else
        "On a phone, a number that isn't a link is a number that doesn't get "
        "dialed.",
        "" if has_tel else "Wrap the phone number in a tel: link.", weight=1))

    scored = [f for f in findings if f["state"] in ("ok", "bad", "warn")]
    total = sum(f["weight"] for f in scored) or 1
    earned = sum(f["weight"] * (1.0 if f["state"] == "ok"
                                else 0.5 if f["state"] == "warn" else 0.0)
                 for f in scored)
    score = int(round(100 * earned / total))

    # A site that blocks the AI crawlers cannot score well no matter how good
    # its markup is — none of that markup is readable. Without this cap the
    # blocked case scores in the seventies and sits under a red verdict, which
    # reads as a broken widget rather than an urgent problem.
    if robots.get("blocked"):
        score = min(score, 20)

    band, headline = _band(score, robots.get("blocked"))
    counts = {
        "bad": sum(1 for f in findings if f["state"] == "bad"),
        "warn": sum(1 for f in findings if f["state"] == "warn"),
        "ok": sum(1 for f in findings if f["state"] == "ok"),
        "unknown": sum(1 for f in findings if f["state"] == "unknown"),
    }
    # Worst first — the teaser only shows the top three, and they should be
    # the three that hurt.
    order = {"bad": 0, "warn": 1, "unknown": 2, "ok": 3}
    findings.sort(key=lambda f: (order.get(f["state"], 9), -f["weight"]))

    return {
        "domain": domain,
        "url": final_url,
        "business": title.split("|")[0].split("-")[0].strip()[:80] if title else "",
        "title": title,
        "description": description,
        "score": score,
        "band": band,
        "headline": headline,
        "findings": findings,
        "counts": counts,
        "words": words,
        "schema_types": sorted(types)[:12],
    }


def _band(score: int, blocked) -> tuple[str, str]:
    if blocked:
        return ("bad",
                "AI assistants are being blocked from this website entirely. "
                "Until that changes, nothing else here can help.")
    if score >= 85:
        return ("ok", "This site is in good shape for AI search. There's still "
                      "room at the top — most of the competition isn't here yet.")
    if score >= 60:
        return ("warn", "This site is partly ready for AI search. The gaps "
                        "below are the ones costing visibility today.")
    if score >= 35:
        return ("bad", "AI assistants would struggle to describe this business "
                       "accurately, and are unlikely to recommend it.")
    return ("bad", "As far as ChatGPT, Gemini and Perplexity are concerned, "
                   "this business is close to invisible.")


def teaser(result: dict, show: int = 3) -> dict:
    """What an anonymous visitor is allowed to see.

    The gate is here, on the server. Nothing withheld is serialised into the
    page — a CSS blur over the full answer is not a gate, it is a delay with
    extra steps, and the Suite audit found one of those bypassable with a
    single devtools click.
    """
    shown = [{"label": f["label"], "state": f["state"], "detail": f["detail"]}
             for f in result["findings"][:show]]
    held = max(0, len(result["findings"]) - len(shown))
    return {
        "domain": result["domain"],
        "business": result["business"],
        "score": result["score"],
        "band": result["band"],
        "headline": result["headline"],
        "findings": shown,
        "locked_count": held,
        "counts": result["counts"],
    }
