"""One search box, over the clients and over the Hub itself.

## Why this exists

The box at the top of every page was a `GET` form pointed at `/client360`, so
whatever you typed became a client lookup and nothing else. That is right for
the thing people search for most and wrong for everything else: a tool nobody
can remember the name of, a QA report, the panel that explains what "addressable
audience" counts. Twenty-two modules, three index pages and several hundred
help topics, and the only way to reach any of it was to know where it lived.

`search()` answers across three books at once, and says which book each answer
came from.

## The rules, each of which is a way a search box goes wrong

**A client is always the first answer.** Not a scoring nudge -- a hard
ordering. The box sits on every screen in a Hub whose entire subject is a book
of clients, and a page result above the client somebody typed the name of is
the search box being clever at the reader's expense. `client_first()` is that
rule, on its own, so it cannot be lost in a tie-break.

**Clients are matched live, never from a stored index.** `clients_registry`
already caches for two minutes and `search_clients()` is already the matcher
the rest of the Hub uses. A second, stored copy of the client book would go
stale the day somebody is added, and a search box that cannot find a client we
signed last week is one people stop using. What *is* indexed here is the part
that only changes when this repo changes: the tools, the reports and the help.

**A source that could not be read is named.** `(results, errors)` again, for
the reason `connected_accounts_result()` gives in Google Finder: "no client
called that" and "we could not read the client book" are different answers and
only the first means check the spelling. A search that quietly returns the
pages when the client book is down is the worst of the two.

**Page text is what the page says about itself, not a crawl.** The tiles, the
nav, the report descriptions and the help registry are the Hub describing its
own screens, and they are in this repo -- so the index is built from code,
holds no client data, and cannot go stale against a page it does not match.
Crawling the app for text would need a session, would cost a request per page,
and is what `hub/no_crawl.py` spends its whole existence refusing on the other
side of the door.

**Nothing is stored.** The index is derived at first use and kept in the
process. Two gunicorn workers each build their own; it is read-only and built
from the same source, so they cannot disagree in a way anybody could observe.
"""
from __future__ import annotations

import html as _html
import re
import threading

# What a result is. `kind` decides the group a result is drawn under, and
# `client` is the only kind that can ever be first -- see client_first().
KIND_LABELS = {
    "client": "Clients",
    "tool": "Tools and pages",
    "report": "QA reports",
    "help": "How this works",
}

# How many of the looser client hits survive a long page list. Small on
# purpose: they are below the pages because the query did not name them, and
# a page of them would be the substring matching this split exists to demote.
LOOSE_CLIENT_SLOTS = 3

_lock = threading.Lock()
_pages: list[dict] | None = None

_WORD = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> list[str]:
    return _WORD.findall(str(text or "").lower())


def _norm(text: str) -> str:
    return " ".join(_tokens(text))


# ---------------------------------------------------------------------------
# The Hub's own pages
# ---------------------------------------------------------------------------

def _tiles(template: str) -> list[dict]:
    """Every tool tile on one of the three index pages.

    Read out of the template rather than kept as a second list beside it.
    `hub/templates/creative.html` and `tools.html` are where a tool's tile
    actually is -- CLAUDE.md counts six tools that were invisible for weeks
    for want of one -- so a tool tiled tomorrow is searchable tomorrow, and a
    parallel list here would be a seventh way to be missing.
    """
    import pathlib
    path = pathlib.Path(__file__).resolve().parent / "templates" / template
    try:
        html = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []
    out = []
    for href, body in re.findall(
            r'<a class="tool-tile"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
            html, re.S):
        title = re.search(r"<h3>(.*?)</h3>", body, re.S)
        if not title:
            continue
        blurb = re.search(r"<p>(.*?)</p>", body, re.S)
        # Unescaped: the template says "Image Optimizer &amp; Resizer", and a
        # result reading "&amp;" is the search box looking broken on the one
        # row somebody was most likely searching for.
        out.append({
            "kind": "tool",
            "title": _html.unescape(
                re.sub(r"<[^>]+>", "", title.group(1))).strip(),
            "subtitle": _html.unescape(re.sub(r"\s+", " ", re.sub(
                r"<[^>]+>", "", blurb.group(1)))).strip() if blurb else "",
            "url": href,
        })
    return out


def _nav() -> list[dict]:
    """The sidebar. Its entries are pages the tiles do not always repeat."""
    try:
        from .sidebar import _ITEMS
    except Exception:                                     # noqa: BLE001
        return []
    return [{"kind": "tool", "title": label, "subtitle": "", "url": href}
            for key, href, _ico, label, *_ in _ITEMS
            if href and not key.startswith("_sec")]


def _reports() -> list[dict]:
    """The QA reports and the tools filed beside them.

    Both, because the QA page draws both and somebody searching for "domain
    renewals" does not know or care that one is a table-returning function
    and the other is a whole tool. `qa.EXTRAS` is module-level for exactly
    this reason: it was inline in the route that drew it, so the only list of
    those seven tools was one nothing else could read, and every one of them
    answered nothing typed into the search box.
    """
    try:
        from . import qa
    except Exception:                                     # noqa: BLE001
        return []
    out = []
    for slug, rep in (getattr(qa, "REPORTS", {}) or {}).items():
        out.append({"kind": "report",
                    "title": str(rep.get("title") or slug),
                    "subtitle": str(rep.get("desc") or ""),
                    "url": f"/qa/{slug}"})
    for _group, _key, meta in (getattr(qa, "EXTRAS", []) or []):
        out.append({"kind": "report",
                    "title": str(meta.get("title") or ""),
                    "subtitle": str(meta.get("desc") or ""),
                    "url": str(meta.get("href") or "")})
    return out


def _help_topics() -> list[dict]:
    """The help registry -- the Hub explaining its own screens.

    This is the closest thing the Hub has to page *content*: a topic is
    written against the panel it sits on, so searching it finds the screen as
    well as the answer. The link is the topic's own where it has one, and the
    screen it belongs to otherwise.
    """
    try:
        from . import help as help_mod
    except Exception:                                     # noqa: BLE001
        return []
    out = []
    for h in getattr(help_mod, "REGISTRY", []) or []:
        out.append({"kind": "help", "title": h.title,
                    "subtitle": re.sub(r"\s+", " ", h.body)[:220],
                    "url": h.link or "", "key": h.key,
                    "body": h.body})
    return out


def pages(rebuild: bool = False) -> list[dict]:
    """Everything in the Hub that is not a client, indexed once per process."""
    global _pages
    with _lock:
        if _pages is not None and not rebuild:
            return _pages
        docs: list[dict] = []
        seen_urls: set[str] = set()
        for group in (_tiles("creative.html"), _tiles("tools.html"),
                      _reports(), _nav(), _help_topics()):
            for doc in group:
                # A tool tiled on an index page and named in the nav is one
                # page. First writer wins, and the tiles run first because a
                # tile carries the description the nav entry does not.
                url = doc.get("url") or ""
                if url and doc["kind"] == "tool":
                    if url in seen_urls:
                        continue
                    seen_urls.add(url)
                doc["_hay"] = _norm(" ".join(
                    str(doc.get(k) or "") for k in
                    ("title", "subtitle", "body", "key", "url")))
                doc["_title"] = _norm(doc.get("title"))
                docs.append(doc)
        _pages = docs
        return _pages


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def _score(doc: dict, query: str, words: list[str]) -> int:
    """How well one indexed page answers this query.

    Whole-title match beats a title that starts with it, which beats a word
    in the title, which beats a word in the body. Deliberately coarse: the
    thing that decides whether this box is useful is the client rule below,
    not a tuned relevance model nobody can explain when it puts the wrong
    row first.
    """
    title, hay = doc["_title"], doc["_hay"]
    if not words:
        return 0
    # Every word has to appear somewhere. Without this, "zzzqqq nothing here"
    # came back with thirteen help topics -- none of them containing "zzzqqq",
    # all of them scoring on the word "nothing". A search box that answers a
    # query it plainly does not match teaches people not to read its answers,
    # and the one query where that matters is the one where somebody has
    # mistyped a client's name and needs to be told so.
    hay_words = hay.split()
    if not all(w in hay for w in words):
        return 0
    score = 0
    if title == query:
        score += 100
    elif title.startswith(query):
        score += 60
    elif query in title:
        score += 40
    elif query in hay:
        score += 12
    title_words = title.split()
    for w in words:
        if w in title_words:
            score += 8
        elif title.startswith(w) or f" {w}" in title:
            score += 5
        elif w in hay_words:
            score += 2
        else:
            score += 1                   # present, but inside a longer word
    return score


def _clients(query: str, limit: int) -> tuple[list[dict], list[dict], str]:
    """Matching clients, live from the registry. `(named, loose, error)`.

    Split, because `search_clients()` matches a *substring* of the name and
    of the domain -- right for a type-ahead, and the reason a search for
    "image" returned a bridal shop whose domain happens to contain the word.
    Promoted unconditionally to the top of every result list, one such hit
    would sit above Image Creator on the query "image" for ever.

    So `named` is a client the query actually names -- the whole name, the
    start of it, a whole word in it, or their domain exactly -- and those are
    the ones the client-first rule promotes. A looser hit is still an answer
    and still shown, underneath the pages, where it costs nobody anything.
    """
    try:
        from . import clients_registry
        rows = clients_registry.search_clients(query, limit=limit) or []
    except Exception as exc:                              # noqa: BLE001
        return [], [], ("The client book could not be read "
                        f"({type(exc).__name__}).")
    named, loose = [], []
    for r in rows:
        name = str(r.get("name") or "").strip()
        if not name:
            continue
        bits = [b for b in (str(r.get("url") or r.get("domain") or ""),
                            str(r.get("city") or "")) if b]
        row = {
            "kind": "client", "title": name,
            "subtitle": " · ".join(bits),
            "url": "/client360?q=" + name.replace(" ", "+"),
            "live": bool(r.get("live")),
        }
        norm = _norm(name)
        domain = _norm(r.get("domain") or "")
        if (norm == query or norm.startswith(query) or domain == query
                or query in norm.split()):
            named.append(row)
        else:
            loose.append(row)
    return named, loose, ""


def client_first(named: list[dict], others: list[dict],
                 loose: list[dict] | None = None) -> list[dict]:
    """A client the query names, then everything else, then looser clients.

    Written as its own function rather than as a sort key so it cannot be
    lost in a tie-break: this Hub's subject is a book of clients, the box is
    on every screen, and a page ranked above the client whose name was typed
    is the search being clever at the reader's expense.

    `loose` is the other half of that -- see `_clients()`. A client whose
    *domain* merely contains the word somebody typed has not been named, and
    promoting one puts a bridal shop above Image Creator on the query
    "image". It is still an answer; it is just not the first one.
    """
    return list(named) + list(others) + list(loose or [])


def search(query: str, limit: int = 12) -> dict:
    """Clients first, then the Hub's own pages, reports and help.

    Never raises, and never returns fewer results in silence: `errors` names
    a book that could not be read, because "no client called that" and "we
    could not look" send somebody to two different places.
    """
    query = _norm(query)
    if not query:
        return {"query": "", "results": [], "counts": {}, "errors": [],
                "note": "Type a client, a tool, a report or a question."}

    words = query.split()
    named, loose, client_error = _clients(query, max(3, limit // 2))

    scored = []
    for doc in pages():
        s = _score(doc, query, words)
        if s > 0:
            scored.append((s, doc))
    scored.sort(key=lambda pair: (-pair[0], pair[1]["_title"]))

    # Room is reserved for the looser client hits before the pages are cut to
    # length. Without it they are appended and then truncated away by a long
    # page list, so a client whose domain carries the word somebody typed
    # disappears entirely -- which is worse than ranking them below the pages,
    # because it is indistinguishable from us not holding them at all.
    keep_loose = loose[:LOOSE_CLIENT_SLOTS]
    room = max(1, limit - len(named) - len(keep_loose))
    others = [{k: v for k, v in doc.items() if not k.startswith("_")}
              for _s, doc in scored[:room]]

    results = client_first(named, others, keep_loose)[:limit]
    counts: dict[str, int] = {}
    for r in results:
        counts[r["kind"]] = counts.get(r["kind"], 0) + 1

    errors = [client_error] if client_error else []
    note = ""
    if not results and not errors:
        note = f"Nothing in the Hub matches “{query}”."
    elif not named and not client_error:
        note = "No client of that name — these are pages and reports."
    return {"query": query, "results": results, "counts": counts,
            "errors": errors, "note": note,
            "labels": KIND_LABELS}
