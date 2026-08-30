"""Keep search engines and AI crawlers out of the Hub entirely.

Every page here is internal: client names, budgets, proposals, invoices, the
activity log. None of it should appear in a search result or inside a model's
training set, and the Hub is on a public hostname
(`smart1-hub.onrender.com`) where anything reachable is fair game to a crawler
that finds the URL.

## Three layers, because each one alone has a hole

1. **robots.txt** — obeyed by the well-behaved crawlers, which is most of the
   traffic that matters. It is a *request*, not a control: nothing enforces it.
2. **An `X-Robots-Tag` header on every response**, added as WSGI middleware in
   `wsgi.py` so it covers the mounted modules too. This is the layer that
   actually removes a page from an index, and unlike a `<meta>` tag it applies
   to the PDFs, the CSV exports and the JSON as well — a proposal PDF is the
   single most sensitive thing this Hub serves and a meta tag cannot reach it.
3. **`<meta name="robots">` in the page head**, for the crawler that fetched
   a page and dropped the headers.

The header is the one that does the work; the other two exist because a
crawler that ignores one may honour another, and none of the three costs
anything.

## Why the AI crawlers are named individually

`User-agent: *` plus `Disallow: /` already covers everything that reads
robots.txt properly. Several AI crawlers read it *by name only* — Google's
`Google-Extended` and Apple's `Applebot-Extended` are opt-outs that exist as
their own tokens precisely so a site can refuse AI training while staying in
the search index, and a wildcard block does not always register with them.
Naming them is the difference between opting out and assuming you did.

The list is data rather than a paragraph in a template, so adding next year's
crawler is one line here and appears in robots.txt, the diagnostics page and
the test at once.

## What this does NOT do

It does not stop a crawler that ignores robots.txt and the header, and it
cannot: the real control for that is the login, which is already in front of
every page here. This closes the gap where a page is *reachable* — the sign-in
page itself, an error page, a public landing page — not the gap where somebody
is determined.
"""
from __future__ import annotations

# The crawlers that read robots.txt under their own name. Grouped by who
# operates them so a line that stops being real is easy to find; the grouping
# is a comment, not structure, because robots.txt is a flat list.
AI_CRAWLERS = (
    # OpenAI
    "GPTBot", "ChatGPT-User", "OAI-SearchBot",
    # Anthropic
    "ClaudeBot", "Claude-User", "Claude-SearchBot", "anthropic-ai",
    # Google — Google-Extended is the AI-training opt-out and is honoured
    # *only* under its own name, which is the whole reason this list exists.
    "Google-Extended",
    # Apple — same arrangement as Google-Extended.
    "Applebot-Extended",
    # Perplexity
    "PerplexityBot", "Perplexity-User",
    # Meta
    "meta-externalagent", "Meta-ExternalAgent", "Meta-ExternalFetcher",
    "FacebookBot",
    # Amazon, ByteDance, Common Crawl and the rest of the training scrapers
    "Amazonbot", "Bytespider", "CCBot", "cohere-ai", "Diffbot",
    "ImagesiftBot", "Omgilibot", "omgili", "Timpibot", "YouBot",
    "AI2Bot", "Ai2Bot-Dolma", "DuckAssistBot", "PanguBot", "Kangaroo Bot",
    "Webzio-Extended", "Scrapy", "SemrushBot-OCOB", "Firecrawl",
)

# The ordinary search indexers. Covered by the wildcard already; named so the
# file reads as a decision rather than an oversight, and so a crawler that
# only honours its own name has one.
SEARCH_CRAWLERS = (
    "Googlebot", "Googlebot-Image", "Googlebot-News", "Storebot-Google",
    "Bingbot", "msnbot", "Slurp", "DuckDuckBot", "Baiduspider",
    "YandexBot", "Sogou", "Exabot", "facebot", "ia_archiver",
    "AhrefsBot", "SemrushBot", "MJ12bot", "DotBot", "Screaming Frog SEO Spider",
)

# Sent on every response. `noindex` and `nofollow` are the two that matter;
# the rest close the ways a page can survive a noindex — a cached copy, a
# snippet in a result page, an image lifted out of it.
ROBOTS_TAG = ("noindex, nofollow, noarchive, nosnippet, noimageindex, "
              "notranslate, noai, noimageai")

# The same statement in a form a page head can carry.
META_TAG = '<meta name="robots" content="' + ROBOTS_TAG + '">'


def robots_txt() -> str:
    """The whole file. One wildcard block, then every crawler by name."""
    lines = [
        "# Smart 1 Hub is an internal tool. Nothing here is for indexing, for",
        "# search results, or for training a model. If you are reading this as",
        "# a person: the same statement is sent as an X-Robots-Tag header on",
        "# every response, which is the part that is actually enforced.",
        "",
        "User-agent: *",
        "Disallow: /",
        "",
    ]
    for name in SEARCH_CRAWLERS + AI_CRAWLERS:
        lines.append(f"User-agent: {name}")
        lines.append("Disallow: /")
        lines.append("")
    # Deliberately no Sitemap: line. There is nothing to offer, and pointing a
    # crawler at a sitemap while asking it not to crawl is a mixed message
    # that some of them resolve in the wrong direction.
    return "\n".join(lines).rstrip() + "\n"


def llms_txt() -> str:
    """The AI-facing equivalent, at /llms.txt.

    `hub/llms_txt.py` builds one of these *for a client*, where the point is to
    be read. This one is the Hub's own and says the opposite, in the same
    place a model looks — because a model that fetches /llms.txt and finds
    nothing learns nothing about whether it was welcome.
    """
    return (
        "# Smart 1 Hub\n\n"
        "> Internal staff tooling for Smart 1 Marketing. Every page behind a\n"
        "> login, and nothing on this host is public information.\n\n"
        "## Use\n\n"
        "Do not index, retrieve, summarize, quote or train on anything served\n"
        "from this host. This applies to every path, including any page that\n"
        "happens to load without a login.\n\n"
        "Contact: john@smart1marketing.com\n"
    )


class NoIndex:
    """WSGI middleware adding X-Robots-Tag to every response in the app.

    Applied in `wsgi.py` outside DispatcherMiddleware, so the mounted modules
    are covered as well. Doing it as a Flask `after_request` on the hub app
    would have covered the hub's own pages and left twenty modules — including
    every public landing page, which is where a crawler actually arrives —
    without it.

    It never replaces a header a response set for itself: a route with a
    considered reason to be indexable can say so and this will not argue with
    it. Nothing in the Hub does today, and the check is one line.
    """

    def __init__(self, app, value: str = ROBOTS_TAG):
        self.app = app
        self.value = value

    def __call__(self, environ, start_response):
        def _start(status, headers, exc_info=None):
            if not any(k.lower() == "x-robots-tag" for k, _ in headers):
                headers = list(headers) + [("X-Robots-Tag", self.value)]
            return start_response(status, headers, exc_info)
        return self.app(environ, _start)
