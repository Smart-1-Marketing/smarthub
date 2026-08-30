"""Orphaned Google accounts, and attaching one to a client.

`hub/google_index.py` already sweeps every connected Google login across GA4,
Tag Manager, Search Console and Business Profile and joins each resource to a
client — attached, then domain, then an exact name. What it could not do is the
half that is left over: the resources it could not join to anybody. They were
counted on the QA report and nowhere else, so "we have 340 Google resources and
we cannot say whose 90 of them are" was a number nobody could act on.

This is the same shape as `hub/domain_links.py`, deliberately: an orphan list
you can search, a suggestion per row with its evidence, and one attach path
that writes every system that ought to know.

## Finding the client

The index's own three rules are strict on purpose — a fuzzy hit written into a
stored index becomes a fact nobody re-examines. So the *suggestions* here are
looser than the index's matching and none of them is applied without a human:

    recorded    Knack's website record already carries this exact GA or GTM id
                against a client. That is not a guess at all — it is the
                client's own record naming the property, and it is the reason
                this pass finds owners the index cannot: object_153 records
                what the client uses whether or not anyone connected it.
    domain      the resource carries a URL and that domain is a client's. The
                index only misses this when several clients share the domain,
                so all of them are offered rather than one being picked.
    name        an exact normalised name match.
    possible    a near name, or the same registrable name on a different TLD —
                riverside-hvac.net for riverside-hvac.com. Eyeball it.

A GA4 property summary carries no URL at all, so most of them can only ever be
name-matched or recognised by their recorded id, and the rows say which it was.
Ranking is by that order and nothing else; a row with no suggestion says so
rather than offering the nearest thing on the page.

## Attaching

`attach()` writes three places and reports each separately, for the reason
`domain_links` gives at length — "attached" and "attached in two of three
places" are different outcomes:

1. the **client record's attachment**, which is the index's own strongest rule,
   so the next sweep re-applies it without this file being involved;
2. the **stored index row**, so the resource leaves the orphan list now instead
   of at the next sweep — a button that appears to do nothing gets clicked
   again;
3. the **Knack website record** (object_153 `field_2929` / `field_2930`), which
   is what `hub/analytics_ids.py` compares against and what every report that
   never touches Google reads. A recorded id that *differs* is never
   overwritten without being asked: that disagreement is a finding, not a
   typo. Search Console and Business Profile have no field on that object, so
   they say so instead of being written somewhere they do not belong.
"""
from __future__ import annotations

from hub.analytics_ids import _norm_ga, _norm_gtm                # noqa: SLF001

# The three the orphan list is about. Business Profile is swept by the same
# index and is included behind a toggle rather than dropped — but it is not
# what was asked for, and "we checked everything" and "we checked the three"
# are different claims, so what is left out is counted and named.
ASKED_FOR = ("ga4", "gtm", "gsc")

PLATFORM_LABELS = {
    "ga4": "Google Analytics", "gtm": "Google Tag Manager",
    "gsc": "Search Console", "gbp": "Google Business Profile",
    "other": "Other Google resource",
}

# Where an attachment is filed on the client record. `analytics` rather than
# `ga4` because Client 360's own attach button has written that kind since
# before this file existed, and two kinds for one thing is two lists to keep
# in step. The index reads every kind, so the name only matters for display.
LINK_KINDS = {"ga4": "analytics", "gtm": "gtm", "gsc": "search_console",
              "gbp": "business_profile", "other": "google"}

# Which object_153 field records this platform's id, where one exists.
KNACK_FIELDS = {"ga4": "ga_account", "gtm": "gtm_account"}

CONFIDENCE_RANK = {"recorded": 4, "domain": 3, "name": 2, "possible": 1}

SYSTEMS = {
    "client": "Client record attachment",
    "index": "Google account index",
    "knack": "Knack website registry (object_153)",
}


def platform_key(item: dict) -> str:
    """The index's own short key for a resource's platform."""
    from hub import google_index
    return google_index.PLATFORM_KEYS.get(
        str((item or {}).get("platform") or ""), "other")


def _norm_id(kind: str, value: str) -> str:
    if kind == "ga4":
        return _norm_ga(value)
    if kind == "gtm":
        return _norm_gtm(value)
    return str(value or "").strip().lower()


def _stem(domain: str) -> str:
    """The registrable name without its TLD: riverside-hvac.com -> riverside-hvac."""
    parts = str(domain or "").split(".")
    return parts[0] if parts else ""


# A company suffix is not a word that identifies anybody. "Riverstone Heating
# LLC" and "Buckeye Lake Marina LLC" share LLC and nothing else, and a match on
# it would offer every incorporated client as a possible owner of everything.
LEGAL_WORDS = {
    "llc", "inc", "incorporated", "corp", "corporation", "ltd", "limited",
    "company", "co", "the", "and", "of", "for", "plc", "lp", "llp", "pllc",
    "com", "net", "org", "www", "http", "https", "site", "website", "web",
    "ga4", "gtm", "tag", "manager", "analytics", "property", "container",
    "account", "google", "search", "console", "main", "new", "old", "test",
}

# A word shared by more clients than this identifies none of them. Computed
# against the book rather than guessed: on a client list where half the names
# contain "heating", matching on it proposes half the book for every resource,
# which is worse than proposing nobody.
COMMON_TOKEN_SHARE = 0.05
COMMON_TOKEN_FLOOR = 8

# How many clients a shared word may propose for one resource.
WORD_MATCH_LIMIT = 3


def _words(text: str) -> set:
    """The words in a name that could identify a business.

    Splits on anything that is not a letter or a digit, so "riverstone-heating",
    "Riverstone Heating" and "riverstoneheating.com" all give up `riverstone`
    and `heating` — except the last, which gives one word, which is why the
    domain stem is tokenised separately by the caller.
    """
    import re as _re
    out = set()
    for w in _re.split(r"[^a-z0-9]+", str(text or "").lower()):
        if len(w) >= 3 and w not in LEGAL_WORDS and not w.isdigit():
            out.add(w)
    return out


# ---------------------------------------------------------------------------
# What the suggestions are built from, read once
# ---------------------------------------------------------------------------
def _context() -> dict:
    """Every lookup the suggestions need, built once for the whole list."""
    ctx = {"recorded": {}, "by_domain": {}, "shared_domain": {}, "by_stem": {},
           "by_word": {}, "common_words": set(), "sources": []}

    # Knack's recorded GA and GTM ids. The strongest signal available, because
    # it is the client's own record naming the property rather than anything
    # inferred from a name.
    try:
        from hub import knack_websites
        rows = knack_websites.rows()
        for r in rows:
            client = r.get("client") or ""
            if not client:
                continue
            for kind, field in (("ga4", "ga_account"), ("gtm", "gtm_account")):
                norm = _norm_id(kind, r.get(field) or "")
                if norm:
                    ctx["recorded"].setdefault((kind, norm), {
                        "client": client, "record_id": r.get("id", ""),
                        "domain": r.get("domain", ""),
                        "recorded_as": r.get(field) or ""})
        err = knack_websites.last_error()
        ctx["sources"].append({"source": "knack", "ok": not err,
                               "label": "Knack website registry (object_153)",
                               "rows": len(rows), "error": err})
    except Exception as exc:                            # noqa: BLE001
        ctx["sources"].append({"source": "knack", "ok": False,
                               "label": "Knack website registry (object_153)",
                               "error": f"{type(exc).__name__}: {exc}"[:200]})

    # The registry's own domain map, and the domains two clients share — which
    # is the usual reason the index left a resource unmapped despite it
    # carrying a perfectly good domain.
    try:
        from hub import client_key
        idx = client_key.alias_index()
        conflicts = idx.get("domain_conflicts") or {}
        for dom, entry in (idx.get("by_domain") or {}).items():
            if not dom:
                continue
            names = list(entry.get("names") or ([entry["name"]]
                                                if entry.get("name") else []))
            if dom in conflicts or len(names) > 1:
                ctx["shared_domain"][dom] = names
            elif entry.get("name"):
                ctx["by_domain"][dom] = entry["name"]
            stem = _stem(dom)
            if stem and entry.get("name"):
                ctx["by_stem"].setdefault(stem, set()).add(entry["name"])
        ctx["sources"].append({"source": "registry", "ok": True,
                               "label": "Client registry",
                               "rows": len(idx.get("by_domain") or {})})
    except Exception as exc:                            # noqa: BLE001
        ctx["sources"].append({"source": "registry", "ok": False,
                               "label": "Client registry",
                               "error": f"{type(exc).__name__}: {exc}"[:200]})

    # Every word in every client name, so a resource can be matched on a word
    # it shares with one. Deliberately looser than anything the index itself
    # will act on: these are proposals a human ticks, and the row says which
    # word did it so the guess can be judged rather than trusted.
    try:
        from hub import clients_registry
        rows = clients_registry.all_clients() or []
        names = set()
        for row in rows:
            name = str(row.get("name") or "").strip()
            if not name:
                continue
            names.add(name)
            words = _words(name) | _words(_stem(str(row.get("domain") or "")))
            for word in words:
                ctx["by_word"].setdefault(word, set()).add(name)
        # The ceiling is computed against this book, not guessed at. On a list
        # where a tenth of the names contain "heating", matching on it proposes
        # a tenth of the book for every resource — which is worse than
        # proposing nobody, because it buries the two rows that meant something.
        ceiling = max(COMMON_TOKEN_FLOOR, int(len(names) * COMMON_TOKEN_SHARE))
        ctx["common_words"] = {w for w, owners in ctx["by_word"].items()
                               if len(owners) > ceiling}
        ctx["word_ceiling"] = ceiling
    except Exception as exc:                            # noqa: BLE001
        ctx["sources"].append({"source": "words", "ok": False,
                               "label": "Client name words",
                               "error": f"{type(exc).__name__}: {exc}"[:200]})
    return ctx


def _add(out: list, client: str, confidence: str, why: str, **extra) -> None:
    """Keep one row per client, at the best confidence anything gave it."""
    client = str(client or "").strip()
    if not client:
        return
    for row in out:
        if row["client"].lower() == client.lower():
            if CONFIDENCE_RANK[confidence] > CONFIDENCE_RANK[row["confidence"]]:
                row.update(confidence=confidence, why=why, **extra)
            return
    out.append({"client": client, "confidence": confidence, "why": why, **extra})


def suggest_for(item: dict, ctx: dict | None = None) -> list[dict]:
    """Which clients this Google resource might belong to, with the evidence."""
    ctx = _context() if ctx is None else ctx
    kind = platform_key(item)
    out: list[dict] = []

    rid = str(item.get("resource_id") or "")
    if kind in KNACK_FIELDS:
        hit = ctx["recorded"].get((kind, _norm_id(kind, rid)))
        if hit:
            _add(out, hit["client"], "recorded",
                 f"The Knack website record for {hit['client']} already "
                 f"records this {PLATFORM_LABELS[kind]} id "
                 f"(“{hit['recorded_as']}”). It is their property; nothing "
                 "in the Hub had joined the two.",
                 record_id=hit.get("record_id", ""),
                 domain=hit.get("domain", ""))

    for dom in item.get("domains") or []:
        name = ctx["by_domain"].get(dom)
        if name:
            _add(out, name, "domain",
                 f"This resource carries {dom}, which is that client's domain.",
                 domain=dom)
            continue
        shared = ctx["shared_domain"].get(dom)
        if shared:
            for name in shared[:5]:
                _add(out, name, "possible",
                     f"{len(shared)} client records share {dom}, so the domain "
                     "cannot say which — that is why the index left this "
                     "unmatched. Pick the one that is right.", domain=dom)
            continue
        for name in sorted(ctx["by_stem"].get(_stem(dom), set()))[:5]:
            _add(out, name, "possible",
                 f"{dom} is the same name as that client's domain on a "
                 "different suffix. Often the same business; check.",
                 domain=dom)

    label = str(item.get("name") or "").strip()
    if label:
        try:
            from hub import client_key
            hit = client_key.resolve(name=label, allow_fuzzy=True)
        except Exception:                               # noqa: BLE001
            hit = None
        if hit and hit.get("known"):
            _add(out, hit["client"],
                 "name" if hit.get("confidence") == "exact" else "possible",
                 hit.get("why") or "The name matches this client.")
        for cand in (hit or {}).get("candidates") or []:
            _add(out, cand, "possible",
                 "More than one client could be meant by this name, so nothing "
                 "was matched automatically.")

    # Last and loosest: a word shared between this resource and a client name.
    # A GTM container called "Buckeye Marina - new" and a client filed as
    # "Buckeye Lake Marina" match on nothing above — not the domain (a
    # container often carries none), not an exact name, not a near name — and
    # a person reading the row can see in a second that they are the same
    # business. So the shared word is offered, `possible`, with the word
    # itself named so the guess can be judged rather than trusted.
    for client, words in _word_matches(item, ctx).items():
        # Short on purpose. Several clients can share a word, so this line can
        # repeat three times in one row, and three copies of a paragraph
        # explaining what a shared word means is a wall nobody reads — the
        # page says that once, above the table. What differs per row is the
        # word, so that is what the row carries.
        _add(out, client, "possible",
             "Shares the word" + ("s " if len(words) > 1 else " ")
             + ", ".join(sorted(words)[:3]) + ".")

    out.sort(key=lambda r: -CONFIDENCE_RANK[r["confidence"]])
    return out[:8]


def _word_matches(item: dict, ctx: dict) -> dict:
    """{client: {shared words}} for clients sharing a word with this resource.

    Every word the resource offers is used — its own name, the Google account
    it sits in, and the stem of any domain on it — because which of the three
    carries the business name varies by platform: a GA4 property is usually
    named for the client, a GTM container often for the site, and a Search
    Console property is a URL and nothing else.
    """
    words = _words(item.get("name") or "") | _words(item.get("account_name") or "")
    for dom in item.get("domains") or []:
        words |= _words(_stem(dom))
    out: dict = {}
    common = ctx.get("common_words") or set()
    by_word = ctx.get("by_word") or {}
    for word in words:
        if word in common:
            continue
        for client in by_word.get(word, ()):
            out.setdefault(client, set()).add(word)
    # Most words first: two shared words is a much better guess than one, and
    # the caller's cap would otherwise cut by client name rather than by how
    # much they actually have in common.
    #
    # Three, not six. This is the weakest rule here, and a row where it fills
    # every slot has buried whatever the stronger rules found under a list of
    # near-identical guesses — which is how a suggestion column stops being
    # read at all.
    ranked = sorted(out.items(), key=lambda kv: (-len(kv[1]), kv[0].lower()))
    return dict(ranked[:WORD_MATCH_LIMIT])


# ---------------------------------------------------------------------------
# The orphan list
# ---------------------------------------------------------------------------
def _orphan_book() -> dict:
    """Every unmapped resource with its suggested owner — today's copy.

    The suggestions are the expensive half, and this module already says so:
    a Knack read, the client alias index and a word index per resource. What
    it did not say is that the cost was paid *per page* — `suggest_for()` runs
    over the whole book before the sort, because the rows a person can act on
    have to come first, so walking a book of two thousand orphans 25 at a time
    ran two thousand suggestions eighty times over.

    So the annotated book is built once a day. Everything that depends on what
    somebody typed or ticked — the platform filter, the search box, the page
    cut — runs over it per request, because `q=acme` and `q=acm` are two cache
    files and a search box types one per keystroke.

    It is dropped by `google_index._forget_reports()`, which runs on the
    sweep, on an attachment and on a domain re-match: those are the three
    things that take a resource off this list.
    """
    def build() -> dict:
        from hub import google_index
        ctx = _context()
        rows = []
        for r in google_index.rows():
            if r.get("client"):
                continue
            key = google_index.PLATFORM_KEYS.get(r.get("platform"), "other")
            rows.append({**r, "key": key,
                         "platform_label": PLATFORM_LABELS.get(
                             key, r.get("platform")),
                         "suggestions": suggest_for(r, ctx)})
        # An index that has never been built has no orphans and no resources
        # either, and storing that as "nothing to do" would keep the page
        # saying so for the rest of the day — with the Build button on it
        # doing nothing visible. Not an answer.
        return {"rows": rows, "sources": ctx["sources"],
                "measured": not google_index.status().get("never_built")}

    try:
        from hub import report_cache
    except Exception:                                   # noqa: BLE001
        return build()
    return report_cache.serve("google-orphans", build)


def orphans(q: str = "", platform: str = "", include_other: bool = False,
            limit: int = 25, offset: int = 0, platforms=None) -> dict:
    """Every Google resource the index could not join to a client, one page.

    Paged on the server rather than in the browser. The suggestions are the
    expensive half — a Knack read, the client alias index and a word index per
    resource — and a book of two thousand orphans is two thousand of those
    before the first row is drawn. `offset`/`limit` walk the same sorted list,
    and every count in the reply (`count`, `orphans_total`, `by_platform`) is
    of the WHOLE list, not the page: a page that reports its own length as the
    total is how somebody concludes there are 25 orphans.

    Filtering and searching happen before the page is cut, so a search
    genuinely searches the book rather than whichever 25 rows are on screen.
    That is the reason this moved off the browser at all.
    """
    from hub import google_index

    # A client that gained a URL since the last sweep makes its resources
    # joinable now; leaving them here until the next sweep lists them as
    # orphans nobody can explain beside the client whose domain they carry.
    # Idempotent and writes nothing when nothing changed — and when it does
    # change something it drops the book below, so the row it just joined is
    # gone from this page rather than sitting on it until tomorrow.
    rejoined = google_index.apply_domain_matches()

    data = google_index.load()
    status = google_index.status()
    book = _orphan_book()
    unmapped = book["rows"]

    # `platforms` is the page's three tickboxes; `platform` is the older
    # single-value parameter, kept because /api/google/orphans?platform=ga4 is
    # a URL somebody may have bookmarked and a test asserts it.
    picked = tuple(p for p in (platforms or ()) if p in PLATFORM_LABELS)
    if platform:
        wanted = (platform,)
    elif picked:
        wanted = picked + (("gbp", "other") if include_other else ())
    else:
        wanted = tuple(PLATFORM_LABELS) if include_other else ASKED_FOR
    by_key: dict[str, int] = {}
    kept, skipped_other = [], 0
    for r in unmapped:
        key = r["key"]
        by_key[key] = by_key.get(key, 0) + 1
        if key not in wanted:
            skipped_other += 1
            continue
        kept.append(r)

    ctx = {"sources": book["sources"]}

    needle = str(q or "").strip().lower()
    if needle:
        def hay(r):
            return " ".join([str(r.get("name") or ""), str(r.get("resource_id") or ""),
                             str(r.get("account_name") or ""),
                             str(r.get("google_login") or ""),
                             " ".join(r.get("domains") or []),
                             " ".join(s["client"] for s in r["suggestions"])]).lower()
        kept = [r for r in kept if needle in hay(r)]

    # The ones we can propose an owner for come first: those are the rows
    # somebody can actually close.
    kept.sort(key=lambda r: (-CONFIDENCE_RANK.get(
        (r["suggestions"] or [{}])[0].get("confidence", ""), 0),
        r["platform_label"], str(r.get("name") or "").lower()))

    start = max(0, int(offset or 0))
    page = kept[start:start + max(1, int(limit or 25))]

    note = ("Every Google account, property and container we can reach that no "
            "client is attached to. Attaching one writes the client record, "
            "the account index and the Knack website record — each reported "
            "separately.")
    if status.get("never_built"):
        note = ("The Google account index has never been built, so this is not "
                "a list of orphans — it is nothing at all. Build it with "
                "Rebuild the index above; it sweeps every connected Google "
                "login and takes about a minute.")
    elif status.get("stale"):
        note += (" The index is stale, so this reflects the last successful "
                 "sweep rather than right now.")
    if rejoined.get("mapped"):
        note += (f" {rejoined['mapped']} resource(s) were joined to a client "
                 "by domain just now — their client had gained that domain "
                 "since the last sweep — so they have left this list without "
                 "anybody clicking anything.")
    if status.get("last_error"):
        note += f" The last sweep reported: {status['last_error']}"
    problems = status.get("sweep_problems") or []
    if problems:
        plats = sorted({str(n.get("platform") or "?") for n in problems})
        note += (f" {len(problems)} login/platform combination(s) could not be "
                 f"swept ({', '.join(plats)}), so those rows are missing "
                 f"rather than absent — see the sweep table below.")
    elif status.get("sweep") == [] and not status.get("never_built"):
        note += (" This index was built before the sweep reported per "
                 "platform, so what each login could be read for is not "
                 "measured here. Rebuild it to find out.")
    if skipped_other:
        note += (f" {skipped_other} unmatched resource(s) on other Google "
                 "platforms are not shown — tick to include them.")
    unreadable = [s for s in ctx["sources"] if not s.get("ok")]
    if unreadable:
        note += (" " + ", ".join(s["label"] for s in unreadable) +
                 " could not be read, so the suggestions are a floor, not a "
                 "total.")

    return {
        "q": q, "platform": platform, "include_other": include_other,
        "platforms": list(picked),
        # `count` is the whole filtered list; `rows` is one page of it.
        "count": len(kept), "offset": start, "limit": limit,
        "shown": len(page), "has_more": start + len(page) < len(kept),
        "next_offset": start + len(page),
        "rows": page,
        "with_suggestion": sum(1 for r in kept if r["suggestions"]),
        "orphans_total": len(unmapped),
        "by_platform": {PLATFORM_LABELS.get(k, k): v for k, v in
                        sorted(by_key.items(), key=lambda kv: -kv[1])},
        "resources": status.get("resources", 0),
        "mapped": status.get("mapped", 0),
        "built_at": status.get("built_at"),
        "never_built": status.get("never_built"),
        "stale": status.get("stale"),
        "accounts": len(data.get("accounts") or []),
        "account_emails": list(data.get("accounts") or []),
        "sources": ctx["sources"],
        # What each connected login could actually be swept for. A platform
        # that refused is the reason its rows are missing, and it has to be on
        # the page: "this login has no Tag Manager containers" and "Tag
        # Manager refused this token" are the same empty list otherwise.
        "sweep": status.get("sweep") or [],
        "sweep_problems": status.get("sweep_problems") or [],
        # When the suggestions were worked out. The index carries its own
        # `built_at` for when Google was last swept, and these are two
        # different ages: the sweep is what we can see, this is what we have
        # made of it. A page that printed one and meant the other would be
        # exactly the kind of confident wrong date this module avoids.
        "cache": book.get("cache") or {},
        "note": note,
    }


# ---------------------------------------------------------------------------
# Attaching
# ---------------------------------------------------------------------------
def _find(resource_id: str) -> dict:
    from hub import google_index
    rid = str(resource_id or "").strip().lower()
    for it in google_index.load().get("items") or []:
        if str(it.get("resource_id") or "").strip().lower() == rid:
            return it
    return {}


def _wrote(report: dict, system: str, detail: str) -> None:
    report["written"].append({"system": system, "label": SYSTEMS[system],
                              "detail": detail})


def _skipped(report: dict, system: str, why: str) -> None:
    report["skipped"].append({"system": system, "label": SYSTEMS[system],
                              "why": why})


def attach(resource_id: str, client: str, *, actor: str = "",
           force: bool = False) -> dict:
    """Attach one Google resource to one client, everywhere it belongs."""
    client = str(client or "").strip()
    rid = str(resource_id or "").strip()
    report = {"resource_id": rid, "client": client, "written": [],
              "skipped": [], "ok": False}
    if not client:
        return {**report, "error": "No client was chosen."}
    if not rid:
        return {**report, "error": "No Google resource was given."}

    item = _find(rid)
    if not item:
        # Never invented. A resource that is not in the index is one no
        # connected Google login can see, and attaching it would file an id
        # nobody can administer against a client.
        return {**report,
                "error": f"“{rid}” is not in the Google account index. Only "
                         "resources a connected Google login can reach can be "
                         "attached; rebuild the index if it was created today."}

    kind = platform_key(item)
    label = str(item.get("name") or rid)
    report.update(platform=PLATFORM_LABELS.get(kind, kind), name=label)

    # 1. the client record — the index's own strongest rule
    try:
        from hub import seo
        seo.set_link(client, LINK_KINDS.get(kind, "google"), {
            "name": label, "resource_id": item.get("resource_id", ""),
            "google_login": item.get("google_login", ""),
            "account_id": item.get("account_id", ""),
            "platform": item.get("platform", ""),
        })
        _wrote(report, "client",
               f"{PLATFORM_LABELS.get(kind, kind)} “{label}” attached to "
               f"{client}.")
    except Exception as exc:                            # noqa: BLE001
        _skipped(report, "client", f"{type(exc).__name__}: {exc}"[:160])

    # 2. the stored index, so it leaves the orphan list now
    try:
        from hub import google_index
        out = google_index.set_client(
            item.get("resource_id", ""), client,
            detail=f"Attached to {client} by "
                   f"{actor or 'somebody'} from the orphan list.")
        if out.get("ok"):
            _wrote(report, "index", "No longer listed as an orphan.")
        else:
            _skipped(report, "index", out.get("error", "could not be updated"))
    except Exception as exc:                            # noqa: BLE001
        _skipped(report, "index", f"{type(exc).__name__}: {exc}"[:160])

    # 3. Knack's website record — what analytics_ids compares against
    report_knack = _write_knack(kind, item, client, actor=actor, force=force)
    for line in report_knack["written"]:
        _wrote(report, "knack", line)
    for line in report_knack["skipped"]:
        _skipped(report, "knack", line)

    report["ok"] = bool(report["written"])
    wrote = len(report["written"])
    report["note"] = (
        f"{label} is now attached to {client} in {wrote} of {len(SYSTEMS)} "
        "systems." + (" The rest are listed with the reason: a system that "
                      "could not be written to must not read as one that was."
                      if report["skipped"] else "")
        if wrote else
        "Nothing was written — see the reasons below.")
    try:
        from hub import audit
        audit.log("google_index", "resource_attached", actor=actor or None,
                  client=client, detail=f"{kind}:{rid}", wrote=wrote,
                  skipped=len(report["skipped"]))
    except Exception:                                   # noqa: BLE001
        pass
    return report


def _write_knack(kind: str, item: dict, client: str, *, actor: str = "",
                 force: bool = False) -> dict:
    out = {"written": [], "skipped": []}
    field = KNACK_FIELDS.get(kind)
    if not field:
        out["skipped"].append(
            f"{PLATFORM_LABELS.get(kind, kind)} has no field on the website "
            "record, so there is nowhere on object_153 to record it. The "
            "client record holds it.")
        return out
    try:
        from hub import knack_websites
    except Exception as exc:                            # noqa: BLE001
        out["skipped"].append(f"{type(exc).__name__}: {exc}"[:160])
        return out

    domain = ""
    for d in item.get("domains") or []:
        domain = d
        break
    rec = knack_websites.for_client(client, domain)
    if not rec:
        out["skipped"].append(
            f"No website record in object_153 is filed under “{client}”, so "
            "there is nothing to record the id on.")
        return out

    rid = str(item.get("resource_id") or "")
    current = str(rec.get(field) or "")
    if current and _norm_id(kind, current) == _norm_id(kind, rid):
        out["written"].append(f"Already recorded as “{current}”.")
        return out
    if current and not force:
        # A recorded id that differs is a finding — the site may be running a
        # property we do not administer, or the record may be stale. Either
        # way it is not this button's business to decide which.
        out["skipped"].append(
            f"That record already carries “{current}”, which is a different "
            f"{PLATFORM_LABELS.get(kind, kind)} id. Nothing was overwritten — "
            "a disagreement between what is recorded and what we can reach is "
            "worth resolving rather than flattening.")
        return out
    if not knack_websites.configured():
        out["skipped"].append(
            "KNACK_APP_ID / KNACK_API_KEY are not set on this deployment, so "
            "Knack cannot be written to.")
        return out

    res = knack_websites.set_analytics_ids(
        rec.get("id", ""), **{kind_field(kind): rid}, actor=actor)
    if res.get("ok"):
        out["written"].append(
            f"Recorded on website record {rec.get('id', '')}."
            + (f" Refused: {'; '.join(res['rejected'])}."
               if res.get("rejected") else ""))
    else:
        out["skipped"].append(res.get("error", "the write did not go through")
                              + (f" ({'; '.join(res['rejected'])})"
                                 if res.get("rejected") else ""))
    return out


def kind_field(kind: str) -> str:
    """The keyword `knack_websites.set_analytics_ids` takes for this platform."""
    return {"ga4": "ga", "gtm": "gtm"}.get(kind, kind)


def attach_many(links, *, actor: str = "", force: bool = False) -> dict:
    """Attach several resources — usually one client's GA, GTM and GSC at once."""
    done, failed = [], []
    for item in links or []:
        rep = attach(str((item or {}).get("resource_id") or ""),
                     str((item or {}).get("client") or ""),
                     actor=actor, force=force)
        (done if rep.get("ok") else failed).append(rep)
    return {"ok": bool(done), "attached": len(done), "items": done,
            "failed": failed,
            "note": (f"{len(done)} resource(s) attached."
                     + (f" {len(failed)} could not be." if failed else ""))}
