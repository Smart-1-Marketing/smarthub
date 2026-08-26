"""What a Simvoly project name is actually the name of.

The matcher joins a project to a client on **domain** wherever there is one,
because a domain is an identifier and a name is not. What is left over is
every project whose domain we hold no record of — and on this deployment that
is most of them, because the client's URL lives in the Knack website registry
(object_153) under a name and the project carries the domain under a label
somebody typed.

So the last resort is the name, and the name as Simvoly holds it is not the
business's name. Run against this deployment's own portfolio export, 548 of
1,021 project names carry a **media-partner prefix**:

    TMRG - JWS Pottery                 216 projects begin "TMRG - "
    FabLocal -  SERVPRO of Fresno NW    37 begin "FabLocal - "
    S1M - Hern Marine Summer            66 begin "S1M - "

`normalise_name()` on any of those produces "tmrg jws pottery", which matches
the client "WJS Pottery" at nothing at all — and worse, "FabLocal" is itself a
client in the registry, so a matcher that reached for a substring would file
thirty-seven other companies' websites under FabLocal. That is the failure
`hub/client_key.py` exists to refuse, and it is why this module produces
**candidates a human confirms** rather than a match.

The other two shapes in the same export:

  * A **variant marker** on the end. "Helena Valley Addiction Services - 2026
    Refresh", "Fina Med Spa Landing", "Hern Marine Summer". The business is
    the middle, and dropping the marker is what makes it findable.
  * A **placeholder**, which is 229 of the 1,021: "Anna's Website",
    "chatita521@yahoo.com's Website", "S1M Test", "Untitled". These identify
    nobody. They are named as placeholders rather than matched loosely,
    because a fuzzy pass over "Anna's Website" will eventually find an Anna
    and attach a stranger's site to her.

## The rules

* **A candidate is a proposal, never a write.** Every function here is pure
  and read-only. `sites_match.suggest()` shows what a person accepts.
* **Exactly or not at all.** `exact_matches()` compares normalised names for
  equality — never a substring, for the reason `client_key.resolve()` gives at
  length. "Riverside HVAC" must not collect "Riverside HVAC Supply".
* **Ambiguity is an answer.** A candidate matching two different clients
  returns *both*, and the caller must not pick one. Attributing one company's
  website to another is the worst outcome available to this tool.
* **Every candidate says how it was derived.** "the name with the media
  partner prefix removed" and "the whole project name" are different claims,
  and only the first needs an eyeball.
* **A generic remainder is not a name.** Dropping the prefix from "Elsie
  Consulting - Main Site" leaves "Main Site", which identifies nobody;
  matching on it would join every project called that. Those are refused here
  rather than at the far end.
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher

from hub.client_key import normalise_name

# The separators a project name uses between partner, business and marker.
_SPLIT = re.compile(r"\s*[-–—|]\s+|\s+[-–—|]\s*")

# A name that is about nobody. Kept explicit and short rather than clever:
# each of these is a real shape in the portfolio export, and the cost of
# missing one is a project that stays unmatched, while the cost of a loose
# rule here is a stranger's website filed against a client.
_PLACEHOLDER_PATTERNS = (
    (re.compile(r"^\S{1,60}['’]s\s+(website|site)$", re.I),
     "a trial project named after the person who opened it"),
    (re.compile(r"^(test|testing|demo|sample|untitled|new\s+(site|website|"
                r"project)|my\s+(site|website)|website|site|copy)$", re.I),
     "a placeholder project name"),
    (re.compile(r"\btest(ing)?\b", re.I), "a test project"),
    (re.compile(r"[^@\s]+@[^@\s]+\.[a-z]{2,}", re.I),
     "named after an email address, not a business"),
)

# Words that make a remainder mean nothing on its own. A candidate whose whole
# normalised form is built from these is refused: it is a label somebody put
# on a project, not the name of a company.
_EMPTY_WORDS = {
    "main", "site", "sites", "website", "websites", "page", "pages", "home",
    "landing", "lp", "new", "old", "copy", "draft", "refresh", "redesign",
    "rebuild", "update", "updated", "version", "v", "v2", "v3", "temp",
    "backup", "final", "live", "staging", "dev", "test", "demo", "client",
    "project", "web", "design", "build",
}

# Trailing markers that describe the *job*, not the business. Dropped only
# from the end, only while something is left, and the shortened form is
# offered beside the full one rather than replacing it.
_TRAILING_NOISE = {
    "refresh", "redesign", "rebuild", "revamp", "update", "updated",
    "landing", "page", "lp", "microsite", "site", "website", "new", "old",
    "copy", "draft", "final", "temp", "staging", "dev", "v2", "v3",
    "rebuilt", "relaunch", "phase", "round", "campaign", "promo",
    "spring", "summer", "fall", "autumn", "winter", "holiday",
}
_YEAR = re.compile(r"^(19|20)\d{2}$")

# Words that identify no business, so a shared one is no evidence at all —
# the same rule `hub/google_links.py` applies to its word index, for the same
# reason: on a book where a tenth of the names contain "heating", matching on
# it proposes a tenth of the book for every row.
_WEAK_WORDS = {
    "heating", "cooling", "air", "hvac", "plumbing", "roofing", "services",
    "service", "construction", "contracting", "group", "marketing", "media",
    "solutions", "systems", "auto", "automotive", "repair", "center",
    "centre", "clinic", "dental", "law", "insurance", "realty", "real",
    "estate", "restaurant", "cafe", "shop", "store", "supply", "supplies",
}


def is_placeholder(name: str) -> str:
    """Why this project name is about no business, or "" if it names one."""
    n = re.sub(r"\s+", " ", str(name or "")).strip()
    if not n:
        return "the project has no name"
    for pattern, why in _PLACEHOLDER_PATTERNS:
        if pattern.search(n):
            return why
    if len(normalise_name(n)) < 3:
        return "the project name is too short to identify anybody"
    return ""


def _is_empty_label(text: str) -> bool:
    """Is this remainder a label rather than a name?"""
    words = normalise_name(text).split()
    if not words:
        return True
    return all(w in _EMPTY_WORDS or _YEAR.match(w) for w in words)


def _drop_trailing_noise(text: str) -> str:
    """"Helena Valley Addiction Services 2026 Refresh" -> the services."""
    words = str(text or "").split()
    while len(words) > 1:
        last = re.sub(r"[^a-z0-9]+", "", words[-1].lower())
        if last in _TRAILING_NOISE or _YEAR.match(last):
            words = words[:-1]
            continue
        break
    return " ".join(words)


def candidates(name: str) -> list[dict]:
    """The business names one project name might be about, best guess first.

    Ordered most conservative first: the whole name, then the name with a
    media-partner prefix removed, then with a trailing job marker removed.
    Each carries `why`, because "the whole project name" and "the middle of a
    three-part label" are different claims about the same string.

    Returns [] for a placeholder — see `is_placeholder()` for the reason in
    words. Never raises; a name it cannot read produces no candidates rather
    than a bad one.
    """
    n = re.sub(r"\s+", " ", str(name or "")).strip()
    if not n or is_placeholder(n):
        return []

    out: list[dict] = []
    seen: set[str] = set()

    def add(text: str, kind: str, why: str) -> None:
        text = re.sub(r"\s+", " ", str(text or "")).strip(" -–—|")
        key = normalise_name(text)
        if not key or key in seen or _is_empty_label(text):
            return
        seen.add(key)
        out.append({"name": text, "key": key, "kind": kind, "why": why})

    add(n, "full", "the project name as Simvoly holds it")

    parts = [p.strip() for p in _SPLIT.split(n) if p.strip()]
    if len(parts) > 1:
        add(" ".join(parts[1:]), "no_prefix",
            f"the project name without the “{parts[0]}” prefix — that is the "
            "media partner on most projects, not the business")
        if len(parts) > 2:
            add(" ".join(parts[1:-1]), "middle",
                f"the middle of the name: “{parts[0]}” is the media partner "
                f"and “{parts[-1]}” describes the job")
        add(parts[-1], "tail", "the last part of the name")

    # The same names again with the job marker taken off the end. Done on the
    # parts rather than on the whole string, or trimming "Elsie Consulting -
    # Main Site" cuts the word "Site" off and leaves "Elsie Consulting - Main"
    # — a shorter version of the same wrong answer. A part that is *entirely*
    # a label ("Main Site", "2026 Refresh") is dropped whole.
    kept = [q for q in parts if not _is_empty_label(q)] or parts
    if kept:
        kept = kept[:-1] + [_drop_trailing_noise(kept[-1])]
    for item in list(out):
        base = kept if item["kind"] == "full" else None
        trimmed = (" ".join(base) if base
                   else _drop_trailing_noise(item["name"]))
        if normalise_name(trimmed) != item["key"]:
            add(trimmed, item["kind"] + "_trimmed",
                item["why"] + ", and without the trailing job marker")

    return out


# Which reading of a project name to hand a reader that takes only one. The
# middle of a three-part label is the business; failing that the part after
# the media partner; failing that the name itself.
_PREFERENCE = ("middle", "middle_trimmed", "no_prefix", "no_prefix_trimmed",
               "full_trimmed", "full", "tail", "tail_trimmed")


def best_name(name: str) -> str:
    """The single most business-like reading of a project name, or "".

    `knack_websites.suggest_for()` compares one string against the registry,
    and handing it the raw project name compares "FabLocal -  SERVPRO of
    Southwest San Antonio" against every client — which scores the *wrong*
    SERVPRO franchise above the right one, because half of what it is
    comparing is the media partner's name. It gets this instead.
    """
    found = {c["kind"]: c["name"] for c in candidates(name)}
    for kind in _PREFERENCE:
        if kind in found:
            return found[kind]
    return ""


# ---------------------------------------------------------------------------
# Matching a candidate against a book of client names
# ---------------------------------------------------------------------------
# The book is {normalised name: [client names]} — a list, because two client
# records can normalise to one string and picking either is the guess this
# module refuses to make.

def index_names(pairs) -> dict:
    """Build the book from (client name, source label) pairs."""
    book: dict[str, list[dict]] = {}
    for name, source in pairs or ():
        nm = str(name or "").strip()
        key = normalise_name(nm)
        if not key:
            continue
        row = book.setdefault(key, [])
        if not any(r["client"] == nm for r in row):
            row.append({"client": nm, "source": str(source or "")})
    return book


def exact_matches(name: str, book: dict) -> list[dict]:
    """Every client whose name *is* one of this project's candidate names.

    Exact on the normalised form — never a substring. A list, because more
    than one answer means the caller must not pick: two clients sharing a
    normalised name is exactly the ambiguity `client_key.resolve()` refuses.
    """
    out: list[dict] = []
    for cand in candidates(name):
        for hit in book.get(cand["key"], ()):
            if any(o["client"] == hit["client"] for o in out):
                continue
            out.append({**hit, "matched": cand["name"], "score": 1.0,
                        "why": f"“{hit['client']}” is {cand['why']}, "
                               "exactly — punctuation, LLC/Inc and filler "
                               "words aside."})
    return out


def _worth_comparing(a: str, b: str) -> bool:
    """A cheap gate before the expensive ratio, and a rule about evidence.

    Two names are worth comparing when they share a word that identifies
    somebody, or open the same way. A shared *weak* word — "heating",
    "services", "auto" — is no evidence: on a book where a tenth of the names
    carry it, matching on it proposes a tenth of the book for every project.
    """
    aw, bw = set(a.split()), set(b.split())
    if (aw & bw) - _WEAK_WORDS:
        return True
    return bool(a[:4] == b[:4])


def near_matches(name: str, book: dict, threshold: float = 0.82,
                 limit: int = 5) -> list[dict]:
    """Clients whose name resembles one of this project's candidate names.

    A suggestion and nothing else: `sites_match` puts these behind a "Yes —
    this is them" button, because an automatic fuzzy match writes the wrong
    client onto a site and a wrong `internal_client_name` is worse than a
    blank one.
    """
    best: dict[str, dict] = {}
    for cand in candidates(name):
        key = cand["key"]
        for other, rows in book.items():
            if other == key or not _worth_comparing(key, other):
                continue
            score = SequenceMatcher(None, key, other).ratio()
            if score < threshold:
                continue
            for hit in rows:
                prev = best.get(hit["client"])
                if prev and prev["score"] >= score:
                    continue
                best[hit["client"]] = {
                    **hit, "matched": cand["name"], "score": round(score, 3),
                    "why": f"“{hit['client']}” looks like {cand['why']} "
                           f"({int(score * 100)}%). Check before applying.",
                }
    out = sorted(best.values(), key=lambda r: -r["score"])
    return out[:limit]
