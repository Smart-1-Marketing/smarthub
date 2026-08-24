"""The blog specification — taxonomy, the approved-topic list, the default
author and the client's guardrails, as data.

Read by `hub/seo.py` (the planner and the writer), by the blog document
export, by `hub/cms_publish.py` (which turns a post into paste-ready CMS
fields) and by the AI prompt, so changing what a blog post carries is one
edit rather than four. Same shape as `hub/proposal_spec.py` and
`hub/social_plan.py`.

## Why the taxonomy is clamped in code rather than asked for in the prompt

A model asked for "categories and tags" invents a fresh category almost every
time: twelve posts arrive under twelve categories, and the client's blog
sidebar becomes a list of one-post categories that helps nobody and dilutes
every internal link. Categories are the site's *structure*, so they are a
small set the client keeps; tags are the per-post detail.

So the model is told the categories that already exist on this client, and
whatever it returns is passed through `clamp_taxonomy()`, which keeps the
known ones, allows at most one genuinely new category per post, dedupes
case-insensitively and caps the counts. `blog_categories` on the client store
is the set that grows — deliberately, slowly.

## Why "never mention" is a check, not a sentence in the prompt

The Proposal Builder learned this: copy mentioning Smart 1 Labs is *discarded*
before a rep sees it, because a prompt is a request and "the model was told
not to" is not evidence that it did not. A client's list of things not to say
is the same shape and higher stakes — it is usually there for a legal reason.

`scan_forbidden()` reads the finished copy and reports every hit with the
sentence around it. The post keeps its flags until someone clears them; the
UI shows a written post carrying a flag as *not* ready to publish. The free
text in `guidance` still goes to the model, because most of it is context
rather than prohibition; the `avoid` list goes to the model **and** is
checked afterwards.
"""
from __future__ import annotations

import html as _html
import re

# One post belongs in one or two places on a site. Three is a taxonomy nobody
# maintains, and WordPress's own "uncategorised" problem starts at four.
MAX_CATEGORIES = 2
# A new category per post is how a blog ends up with 12 categories of one post
# each. One per post, at most, and only when the model could not use an
# existing one.
MAX_NEW_CATEGORIES_PER_POST = 1
MIN_TAGS = 3
MAX_TAGS = 8
MAX_TERM_LEN = 40

# An uploaded approved-topics document is a client deliverable, not a database
# dump: past a couple of hundred lines it is something else that was uploaded
# by mistake, and reading all of it into an AI payload is the expensive way to
# find that out.
MAX_APPROVED_TOPICS = 200
# A line longer than this is prose — a paragraph of notes under a topic, or a
# whole approved post — not a title.
TOPIC_TITLE_MAX = 120

# Lines that are page furniture in every document a client sends back.
_TOPIC_NOISE = re.compile(
    r"^(approved\s+(blog\s+)?(topics?|posts?|articles?)|blog\s+topics?|"
    r"topics?\s+for\s+approval|content\s+calendar|proposed\s+topics?|"
    r"page\s*\d+|\d+\s*$|smart\s*1\s*marketing|confidential)\W*$",
    re.I)
# "1." "1)" "- " "•" "*" "a)" — the shapes a list arrives in.
_TOPIC_BULLET = re.compile(r"^\s*(?:[-*•–—●▪]+|"
                           r"\(?\d{1,3}[.)]|\(?[a-z][.)])\s+", re.I)


# ------------------------------------------------------------------ terms
def _collapse(s: str) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip()


def normalise_category(value: str) -> str:
    """A category as it should read in a CMS sidebar."""
    s = _collapse(value).strip(" \t-–—•*#:;,.")
    s = re.sub(r"[^\w &'/-]+", " ", s, flags=re.UNICODE)
    s = _collapse(s)[:MAX_TERM_LEN].strip()
    if not s:
        return ""
    # Title Case, but leave a word the writer capitalised oddly alone (HVAC,
    # AC, SEO) — lowercasing those is how "HVAC Repair" became "Hvac Repair"
    # on every category page.
    words = [w if (w.isupper() and len(w) <= 5) else w[:1].upper() + w[1:]
             for w in s.split(" ")]
    return " ".join(words)


def normalise_tag(value: str) -> str:
    """A tag: lower case, no hash, no punctuation run."""
    s = _collapse(value).lstrip("#").strip(" \t-–—•*:;,.")
    s = re.sub(r"[^\w &'/-]+", " ", s, flags=re.UNICODE)
    return _collapse(s).lower()[:MAX_TERM_LEN].strip()


def slugify_title(title: str) -> str:
    """The URL slug a post should be given.

    Both CMSes generate one from the title, and both keep the first one they
    generated when the title is later edited — so the slug is worth handing
    over explicitly rather than letting the editor guess twice.
    """
    s = re.sub(r"[^a-z0-9]+", "-", str(title or "").lower()).strip("-")
    # Stop at a word boundary rather than mid-word: a truncated slug reads as
    # a typo in the address bar.
    if len(s) > 70:
        s = s[:70].rsplit("-", 1)[0]
    return s or "blog-post"


def _dedupe(values, key=lambda v: v.lower()):
    seen, out = set(), []
    for v in values:
        if not v:
            continue
        k = key(v)
        if k in seen:
            continue
        seen.add(k)
        out.append(v)
    return out


def clamp_taxonomy(categories, tags, known: list | None = None) -> dict:
    """What a post is actually allowed to carry.

    Returns {"categories", "tags", "new_categories", "dropped"} — `dropped`
    so the caller can say what was trimmed rather than silently trimming it.
    """
    known_list = [normalise_category(c) for c in (known or [])]
    known_list = [c for c in known_list if c]
    known_lower = {c.lower(): c for c in known_list}

    wanted = _dedupe([normalise_category(c) for c in (categories or [])])
    kept, fresh, dropped = [], [], []
    for cat in wanted:
        if len(kept) >= MAX_CATEGORIES:
            dropped.append(cat)
            continue
        match = known_lower.get(cat.lower())
        if match:
            kept.append(match)
        elif len(fresh) < MAX_NEW_CATEGORIES_PER_POST:
            fresh.append(cat)
            kept.append(cat)
        else:
            dropped.append(cat)

    if not kept and known_list:      # never publish an uncategorised post
        kept = [known_list[0]]

    clean_tags = _dedupe([normalise_tag(t) for t in (tags or [])])
    if len(clean_tags) > MAX_TAGS:
        dropped.extend(clean_tags[MAX_TAGS:])
        clean_tags = clean_tags[:MAX_TAGS]
    return {"categories": kept, "tags": clean_tags,
            "new_categories": fresh, "dropped": dropped}


def merge_categories(known, added) -> list:
    """The client's category set after a plan or a write added to it."""
    return _dedupe([normalise_category(c) for c in list(known or []) + list(added or [])])


# -------------------------------------------------------- approved topics
def parse_approved_topics(text: str) -> list[dict]:
    """Topics out of a document the client approved.

    Accepts what a client actually sends: a numbered list, a bulleted list, a
    table pasted into Word, or full approved posts with a title line and
    paragraphs under it. A long line is treated as notes belonging to the
    topic above rather than as a topic of its own, so a document of approved
    *posts* produces one entry per post with its body as notes, not forty
    entries of stray sentences.
    """
    lines = []
    for raw in str(text or "").splitlines():
        line = _collapse(raw)
        if not line or _TOPIC_NOISE.match(line):
            continue
        stripped = _TOPIC_BULLET.sub("", line).strip()
        if stripped:
            lines.append((stripped, stripped != line))

    # A document that numbers or bullets its topics has told us exactly which
    # lines are topics, and every other line is the note under one. Only a
    # document with no list markers at all falls back to guessing by length —
    # which is what read a 118-character sentence of notes as a topic of its
    # own until this two-pass form replaced it.
    marked = sum(1 for _, bullet in lines if bullet) >= 2

    out: list[dict] = []
    for stripped, bullet in lines:
        is_topic = bullet if marked else len(stripped) <= TOPIC_TITLE_MAX
        if not is_topic:
            if out:                       # prose under the previous title
                out[-1]["notes"] = _collapse(out[-1]["notes"] + " " + stripped)[:1200]
            continue
        if len(out) >= MAX_APPROVED_TOPICS:
            break
        title = stripped[:200].rstrip(" .;:")
        if len(title) < 6:            # "Q1", "Fall" — a heading, not a topic
            continue
        out.append({"title": title, "notes": ""})
    return _dedupe(out, key=lambda d: d["title"].lower())


# ------------------------------------------------------------- guardrails
def normalise_avoid(value) -> list[str]:
    """The never-mention list, from a textarea or a list."""
    if isinstance(value, str):
        parts = re.split(r"[\r\n]+|(?<!\d),(?!\d)", value)
    else:
        parts = list(value or [])
    return _dedupe([_collapse(p).strip(" -–—•*") for p in parts if _collapse(p)])[:60]


def normalise_author(value) -> dict:
    """The default author. Name is the only field a CMS insists on."""
    if isinstance(value, str):
        value = {"name": value}
    d = value if isinstance(value, dict) else {}
    return {"name": _collapse(d.get("name"))[:80],
            "title": _collapse(d.get("title"))[:80],
            "email": _collapse(d.get("email"))[:120],
            "url": _collapse(d.get("url"))[:200],
            "bio": _collapse(d.get("bio"))[:600]}


def strip_html(html: str) -> str:
    """The words a reader sees — no tags, no attributes.

    Scanning raw HTML for a forbidden term matches attribute names and class
    names, so "guarantee" inside `class="guarantee-band"` would flag a post
    whose copy never says it.
    """
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", str(html or ""))
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    return _collapse(_html.unescape(text))


def scan_forbidden(html: str, avoid) -> list[dict]:
    """Every never-mention term that made it into the finished copy.

    Word-boundary matched where the term is a single word, so "ADA" does not
    fire on "Canada"; substring matched for a phrase, because a phrase with a
    comma or an apostrophe in it will not survive a \\b anchor.
    """
    text = strip_html(html)
    if not text:
        return []
    hits = []
    for term in normalise_avoid(avoid):
        pattern = (r"\b" + re.escape(term) + r"\b") if re.fullmatch(r"[\w'-]+", term) \
            else re.escape(term)
        m = re.search(pattern, text, re.I)
        if not m:
            continue
        start = max(0, m.start() - 60)
        hits.append({"term": term,
                     "context": ("…" if start else "") +
                                text[start:m.end() + 60].strip() + "…"})
    return hits


def guidance_payload(settings: dict) -> dict:
    """What the writer is told about this client, beyond the site facts.

    Kept as one block so the planner and the writer cannot drift: a topic
    planned against one set of guardrails and written against another is the
    failure this exists to prevent.
    """
    s = settings or {}
    return {
        "company_guidance": _collapse(s.get("guidance"))[:4000],
        "never_mention": normalise_avoid(s.get("avoid")),
        "existing_categories": [normalise_category(c) for c in (s.get("categories") or [])],
        "author": normalise_author(s.get("author")).get("name", ""),
        "taxonomy_rules": (
            f"Give every post 1-{MAX_CATEGORIES} categories and "
            f"{MIN_TAGS}-{MAX_TAGS} tags. Prefer a category from "
            "existing_categories; invent a new one only when none of them fits."),
    }
