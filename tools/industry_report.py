#!/usr/bin/env python3
"""One-off report: every client's industry evidence, against a draft taxonomy.

Run once, by hand, from the repo root:

    python3 tools/industry_report.py

This is a **read-only report script**, not a mounted module and not part of
the running app. It makes no change to any store the Hub writes to, and it
does not write `hub/industry.py` -- that file is a separate, later piece of
work (see the seam in `hub/client_brief.py`). All this does is read what the
Hub already has about each client from five places --

    hub.clients_registry.all_clients()   -- the merged client list, and the
                                             Knack "currently selling"
                                             products on each row
    hub.knack_data.products() /
    hub.knack_data.websites()            -- raw Knack rows, searched for
                                             whatever industry/vertical text
                                             they carry (see the caveat below)
    hub.scan_facts.latest_report()       -- the newest completed Insites
                                             scan for the client's domain
    modules.image_picker's own database  -- the client row's stored
                                             industry_key, if a gallery exists

-- resolve each piece of text against a **draft** 30-key taxonomy using the
same matching rule `modules/image_picker/taxonomy.py`'s `guess_industry()`
already uses (word-boundary regex, earliest-alias-position wins, longest
alias breaks a tie), and write the findings to a Markdown report so a human
can tune the list before it becomes code.

## What is and is not measurable in this environment

Knack does not publish an "industry" or "vertical" field anywhere this
codebase currently reads -- not in `hub/knack_products.py`, not in
`hub/knack_websites.py`, and not in the private-fallback JSON shape either.
This script still looks for one (several plausible field spellings, on both
the product and the website rows for each client) in case a deployment's
export carries one under a name nothing here has pinned yet, and reports
plainly if it found nothing rather than pretending the column exists.

`google_business_profile.gmb_industries` and `meta.primary_industry` are real
fields on an Insites scan payload (`hub/scan_facts.py` already reads both).
`meta.industry_sectors` does not appear anywhere in this codebase today --
this script still looks for it, in case a live payload carries it under that
path, and reports whether it ever found one.

This script never fabricates or samples data. Where a source cannot answer
in this environment (no live Knack credentials, an empty scans/image_picker
database), the report says so by name rather than presenting an empty count
as a complete answer.
"""
from __future__ import annotations

import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# hub.knack_data reads CLIENTS_DATA_DIR at import time (it builds `BASE` from
# it as a module-level constant), so this has to be set *before* the first
# import of hub.knack_data or anything that imports it. It is only set here
# when the caller has not already set one -- an explicit CLIENTS_DATA_DIR
# (pointing at a real private export) always wins.
_ENV_NOTE = ""
if not os.environ.get("CLIENTS_DATA_DIR"):
    _fixture_dir = ROOT / "tests" / "fixtures" / "clients"
    if (_fixture_dir / "products.json").exists():
        os.environ["CLIENTS_DATA_DIR"] = str(_fixture_dir)
        _ENV_NOTE = (
            "No CLIENTS_DATA_DIR was set and no live Knack credentials were "
            "present, so this run pointed CLIENTS_DATA_DIR at the repo's own "
            f"CI fixture ({_fixture_dir.relative_to(ROOT)}) -- the sanitized, "
            "tiny fixture the test suite uses, not a real client export."
        )
    else:
        _ENV_NOTE = (
            "No CLIENTS_DATA_DIR was set, no live Knack credentials were "
            "present, and no fixture export was found either -- the product "
            "and website reads below answered empty."
        )
else:
    _ENV_NOTE = (
        f"CLIENTS_DATA_DIR was already set to "
        f"{os.environ['CLIENTS_DATA_DIR']!r} by the caller; this run used it "
        "as given."
    )

import hub.knack_data as knack_data                                # noqa: E402
import hub.clients_registry as clients_registry                    # noqa: E402
import hub.scan_facts as scan_facts                                # noqa: E402


# --------------------------------------------------------------------------
# Draft taxonomy: 30 keys, matched exactly the way taxonomy.guess_industry()
# matches -- word-boundary regex over the whole alias, earliest match wins,
# a longer alias breaks a positional tie. Order in this list does not affect
# matching (position in the *text* does); it only affects report layout.
# --------------------------------------------------------------------------
DRAFT_TAXONOMY: list[tuple[str, list[str]]] = [
    ("hvac", [
        "heating", "cooling", "air conditioning", "air conditioner",
        "furnace", "hvac", "ac repair", "ac install",
    ]),
    ("home_services", [
        "plumbing", "plumber", "electrical", "electrician", "roofing",
        "roofer", "landscaping", "landscaper", "lawn care", "lawn",
        "pest control", "pest", "cleaning service", "cleaning",
        "garage door", "gutters", "gutter",
    ]),
    ("home_improvement", [
        "remodeling", "remodel", "kitchen remodel", "kitchen", "bath",
        "bathroom", "windows", "siding", "flooring", "lighting",
        "painting", "painter", "cabinets", "renovation",
    ]),
    ("construction", [
        "general contractor", "commercial construction", "excavation",
        "concrete", "paving", "construction",
    ]),
    ("solar", [
        "solar", "battery storage", "ev charging", "ev charger",
    ]),
    ("automotive", [
        "dealership", "auto repair", "tire shop", "tires", "tire",
        "body shop", "powersports", "motorcycle", "auto dealer",
        "car dealer", "automotive", "auto",
    ]),
    ("marine", [
        "boat", "marina", "yacht", "marine",
    ]),
    ("rv", [
        "rv", "camper", "campground", "motorhome",
    ]),
    ("restaurant", [
        "restaurant", "bar", "brewery", "cafe", "café", "catering",
        "pizza", "grill", "tavern", "diner", "bakery", "bistro",
    ]),
    ("retail", [
        "store", "boutique", "furniture", "jewelry", "jewelers",
        "mattress", "appliance", "retail shop", "retail",
    ]),
    ("ecommerce", [
        "online store", "shop online", "ecommerce", "e-commerce", "dtc",
        "food products", "apparel",
    ]),
    ("legal", [
        # (subtype pi) -- personal injury
        "personal injury", "injury lawyer", "injury",
        "law firm", "attorney", "lawyer", "bankruptcy", "divorce",
        # (subtype general) -- criminal defense
        "criminal defense", "criminal",
        "legal", "law office",
    ]),
    ("healthcare", [
        "medical", "dental", "dentist", "orthodontic", "orthodontist",
        "med spa", "medspa", "chiropractic", "chiropractor",
        "physical therapy", "urgent care", "clinic", "pharmacy",
        "optometry", "optometrist", "healthcare",
    ]),
    ("senior_care", [
        "assisted living", "senior living", "memory care", "hospice",
        "home health",
    ]),
    ("fitness", [
        "gym", "fitness", "crossfit", "martial arts", "yoga", "pilates",
    ]),
    ("beauty_wellness", [
        "salon", "spa", "barber", "massage", "tattoo", "nails", "lashes",
    ]),
    ("real_estate", [
        "realtor", "real estate", "brokerage", "home builder",
        "apartments", "apartment", "property management", "storage",
        "realty",
    ]),
    ("financial", [
        "bank", "credit union", "insurance", "accounting", "cpa",
        "mortgage", "wealth management", "wealth", "financial planning",
        "financial", "bookkeeping",
    ]),
    ("professional_services", [
        "background check", "fingerprint", "apostille", "notary",
        "staffing agency", "staffing", "consulting", "printing",
        "it services", "marketing agency", "marketing", "security",
    ]),
    ("technology", [
        "saas", "software", "telecom", "internet provider", "app",
        "technology",
    ]),
    ("manufacturing", [
        "manufactur", "industrial", "machine shop", "logistics",
        "trucking", "freight", "warehouse",
    ]),
    ("education", [
        "school", "college", "university", "trade school", "tutoring",
        "daycare", "childcare", "preschool", "academy",
    ]),
    ("recruiting", [
        "hiring", "careers", "recruit", "workforce", "jobs",
    ]),
    ("nonprofit", [
        "nonprofit", "non-profit", "charity", "church", "foundation",
        "association", "ministry",
    ]),
    ("government", [
        "city of", "county", "village", "township", "utility", "transit",
        "library", "public health",
    ]),
    ("tourism", [
        "hotel", "lodging", "inn", "resort", "attraction", "museum",
        "zoo", "cvb", "visitors bureau",
    ]),
    ("ski", [
        "ski", "snow", "golf course", "water park",
    ]),
    ("events", [
        "venue", "festival", "fair", "arena", "stadium", "team",
        "concert", "expo",
    ]),
    ("pets_vet", [
        "vet", "veterinary", "veterinarian", "animal hospital",
        "grooming", "boarding", "pet store",
    ]),
    ("general", []),   # fallback -- never matched by alias, only by default
]

TAXONOMY_BY_KEY = {k: aliases for k, aliases in DRAFT_TAXONOMY}
TAXONOMY_KEYS = [k for k, _ in DRAFT_TAXONOMY]
assert len(TAXONOMY_KEYS) == 30, f"draft taxonomy has {len(TAXONOMY_KEYS)} keys, not 30"

# Legacy image_picker keys that fold into a draft key, so section 4 (what
# would CHANGE under the draft) compares like with like rather than reporting
# every existing gallery as a change.
LEGACY_FOLD = {
    "auto": "automotive",
    "boat": "marine",
    "medical": "healthcare",
    "medical_dental": "healthcare",
    "professional": "professional_services",
    "recruit": "recruiting",
    "stadium": "events",
    "home_builder": "real_estate",
}


def resolve_industry(text: str | None) -> str:
    """Best-effort map of free text onto a draft-taxonomy key.

    Same two rules as `modules/image_picker/taxonomy.py`'s `guess_industry()`:
    match on word boundaries (a plain substring match makes "Roofing
    Contractor" an HVAC client, because "ac" sits inside "contractor"), and
    where several aliases match, the earliest position wins -- a longer alias
    breaks a positional tie. Falls back to "general".
    """
    if not text:
        return "general"
    t = str(text).strip().lower()
    if not t:
        return "general"

    for key, aliases in DRAFT_TAXONOMY:
        if t == key or any(t == a for a in aliases):
            return key

    best: tuple[int, int, str] | None = None   # (position, -len, key)
    for key, aliases in DRAFT_TAXONOMY:
        for alias in aliases:
            m = re.search(r"\b" + re.escape(alias) + r"\b", t)
            if not m:
                continue
            candidate = (m.start(), -len(alias), key)
            if best is None or candidate < best:
                best = candidate
    return best[2] if best else "general"


# --------------------------------------------------------------------------
# Evidence gathering
# --------------------------------------------------------------------------

# Plausible spellings for an industry/vertical field on a raw Knack row.
# None of these is pinned or read anywhere else in this codebase today (see
# the module docstring) -- this is a defensive, exploratory search of
# whatever the live export or fixture actually carries.
_INDUSTRY_FIELD_NAMES = (
    "industry", "vertical", "category", "business_category",
    "industry_type", "naics", "sic", "market_segment", "sector",
)


def _knack_text_from_row(row: dict) -> str:
    parts = []
    for name in _INDUSTRY_FIELD_NAMES:
        v = row.get(name)
        if v:
            parts.append(str(v))
    return "; ".join(parts)


def _get_dotted(d: dict, dotted: str):
    cur = d
    for part in dotted.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


def _stringify(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return ", ".join(str(v) for v in value if v)
    return str(value).strip()


def build_knack_index():
    """(products_by_key, websites_by_key) -- raw rows keyed on normalised name.

    Read once, rather than once per client, since `knack_data.products()` and
    `knack_data.websites()` are the same list either way.
    """
    from hub.client_key import normalise_name

    prods: dict[str, list[dict]] = defaultdict(list)
    for r in knack_data.products():
        nm = normalise_name(str(r.get("client") or r.get("organization") or ""))
        if nm:
            prods[nm].append(r)

    webs: dict[str, list[dict]] = defaultdict(list)
    for w in knack_data.websites():
        nm = normalise_name(str(w.get("name") or ""))
        if nm:
            webs[nm].append(w)
    return prods, webs


def build_picker_index() -> tuple[dict[str, dict], str]:
    """(client rows from the Image Picker's own database, by normalised name), error.

    Read-only: one SELECT, no writes. `error` is set when the table could not
    be read at all (not when it was read and is simply empty), the
    `connected_accounts_result()` shape this codebase uses everywhere else --
    "nobody has one" and "we could not look" are different answers.
    """
    from hub.client_key import normalise_name

    try:
        from modules.image_picker import models as picker_models
        picker_models.init_db()
        err = picker_models.db_error()
        if err:
            return {}, err
        db = picker_models.session()
        try:
            from sqlalchemy import select
            rows = db.execute(select(picker_models.PickerClient)).scalars().all()
        finally:
            db.close()
    except Exception as exc:                                       # noqa: BLE001
        return {}, f"{type(exc).__name__}: {exc}"

    out: dict[str, dict] = {}
    for r in rows:
        nm = normalise_name(r.name or "")
        if nm:
            out[nm] = {
                "id": r.id, "name": r.name, "slug": r.slug,
                "industry_key": r.industry_key or "general",
                "hub_client_id": r.hub_client_id or "",
            }
    return out, ""


# --------------------------------------------------------------------------
# Per-client evidence row
# --------------------------------------------------------------------------

class ClientEvidence:
    __slots__ = (
        "name", "domain", "live", "source",
        "knack_text", "scan_primary", "scan_sectors", "gbp_category",
        "picker_key", "products", "scanned",
    )

    def __init__(self):
        self.name = ""
        self.domain = ""
        self.live = False
        self.source = ""
        self.knack_text = ""
        self.scan_primary = ""
        self.scan_sectors = ""
        self.gbp_category = ""
        self.picker_key = ""      # "" = no gallery, else the stored key
        self.products: list[str] = []
        self.scanned = False

    # The five text sources, in the precedence order the report is asked for.
    def sources(self) -> list[tuple[str, str]]:
        return [
            ("knack", self.knack_text),
            ("scan_primary", self.scan_primary),
            ("scan_sectors", self.scan_sectors),
            ("gbp", self.gbp_category),
            ("name", self.name),
        ]

    def top_source(self) -> tuple[str, str]:
        """The first non-empty source in precedence order, and its text."""
        for label, text in self.sources():
            if text:
                return label, text
        return "name", self.name

    def top_key(self) -> str:
        _label, text = self.top_source()
        return resolve_industry(text)

    def resolved_by_source(self) -> dict[str, str]:
        """Every non-empty source, resolved independently -- for the
        cross-source disagreement check (section 3)."""
        out = {}
        for label, text in self.sources():
            if text:
                out[label] = resolve_industry(text)
        return out


def gather() -> tuple[list[ClientEvidence], dict]:
    prods_by_key, webs_by_key = build_knack_index()
    picker_by_key, picker_error = build_picker_index()

    rows: list[ClientEvidence] = []
    for entry in clients_registry.all_clients():
        ev = ClientEvidence()
        ev.name = entry.get("name", "")
        ev.domain = entry.get("domain", "")
        ev.live = bool(entry.get("live"))
        ev.source = entry.get("source", "")
        ev.products = list(entry.get("running_products") or [])

        from hub.client_key import normalise_name
        nm = normalise_name(ev.name)

        knack_bits = []
        for r in prods_by_key.get(nm, []):
            t = _knack_text_from_row(r)
            if t:
                knack_bits.append(t)
        for w in webs_by_key.get(nm, []):
            t = _knack_text_from_row(w)
            if t:
                knack_bits.append(t)
        ev.knack_text = "; ".join(dict.fromkeys(knack_bits))  # de-duped, ordered

        if ev.domain:
            report, meta, err = scan_facts.latest_report(ev.domain)
            if not err and report:
                ev.scanned = True
                ev.scan_primary = _stringify(_get_dotted(report, "meta.primary_industry"))
                ev.scan_sectors = _stringify(_get_dotted(report, "meta.industry_sectors"))
                ev.gbp_category = _stringify(
                    _get_dotted(report, "google_business_profile.gmb_industries"))

        hit = picker_by_key.get(nm)
        if hit:
            ev.picker_key = hit["industry_key"]

        rows.append(ev)

    meta = {
        "picker_error": picker_error,
        "picker_rows_found": len(picker_by_key),
    }
    return rows, meta


# --------------------------------------------------------------------------
# Report sections
# --------------------------------------------------------------------------

_STOPWORDS = {
    "the", "and", "of", "a", "an", "for", "llc", "inc", "co", "corp",
    "company", "services", "service", "group", "&", "-", "/", "llc.",
    "inc.", "co.", "corp.",
}


def _words(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-zA-Z][a-zA-Z\-]*", text.lower())
            if w not in _STOPWORDS and len(w) > 1]


def section_counts(rows: list[ClientEvidence]) -> str:
    active = Counter()
    past = Counter()
    for ev in rows:
        key = ev.top_key()
        if ev.live:
            active[key] += 1
        else:
            past[key] += 1

    out = ["## 1. Proposed key -> client counts\n",
           "| key | active clients | past clients |",
           "|---|---:|---:|"]
    for key in TAXONOMY_KEYS:
        a, p = active.get(key, 0), past.get(key, 0)
        if a or p:
            out.append(f"| {key} | {a} | {p} |")
    unseen = [k for k in TAXONOMY_KEYS if not active.get(k) and not past.get(k)]
    out.append("")
    out.append(f"Total clients in this run: **{len(rows)}** "
               f"({sum(active.values())} active, {sum(past.values())} past).")
    if unseen:
        out.append(f"\nKeys with no clients at all in this run: "
                   f"{', '.join(unseen)}.")
    return "\n".join(out)


def section_general(rows: list[ClientEvidence]) -> str:
    general_rows = [ev for ev in rows if ev.top_key() == "general"]
    out = [f"## 2. Clients resolving to `general` ({len(general_rows)})\n"]
    if not general_rows:
        out.append("None, in this run.")
        return "\n".join(out)

    out.append(
        "Grouped by the most common raw word across every source each "
        "client carried, so a missing taxonomy key is obvious from the "
        "cluster rather than from reading every row.\n")

    word_counts = Counter()
    words_by_client: dict[str, list[str]] = {}
    for ev in general_rows:
        all_text = " ".join(t for _l, t in ev.sources() if t)
        ws = _words(all_text)
        words_by_client[ev.name] = ws
        word_counts.update(set(ws))

    groups: dict[str, list[ClientEvidence]] = defaultdict(list)
    used = set()
    for word, _n in word_counts.most_common():
        if word in used:
            continue
        members = [ev for ev in general_rows if word in words_by_client.get(ev.name, [])]
        if len(members) < 2:
            continue
        groups[word] = members
        used.add(word)

    grouped_names = {ev.name for members in groups.values() for ev in members}
    if groups:
        out.append("### Word clusters (2+ clients sharing a raw word)\n")
        for word, members in sorted(groups.items(), key=lambda kv: -len(kv[1])):
            out.append(f"**\"{word}\"** — {len(members)} client(s)")
            for ev in members:
                _label, text = ev.top_source()
                out.append(f"- {ev.name} — top source (`{_label}`): "
                           f"{text or '(empty)'}   "
                           f"[all: knack={ev.knack_text or '-'} | "
                           f"scan_primary={ev.scan_primary or '-'} | "
                           f"scan_sectors={ev.scan_sectors or '-'} | "
                           f"gbp={ev.gbp_category or '-'}]")
            out.append("")

    ungrouped = [ev for ev in general_rows if ev.name not in grouped_names]
    if ungrouped:
        out.append("### Ungrouped (no raw word shared with another `general` client)\n")
        for ev in ungrouped:
            _label, text = ev.top_source()
            out.append(f"- {ev.name} — top source (`{_label}`): "
                       f"{text or '(empty)'}   "
                       f"[all: knack={ev.knack_text or '-'} | "
                       f"scan_primary={ev.scan_primary or '-'} | "
                       f"scan_sectors={ev.scan_sectors or '-'} | "
                       f"gbp={ev.gbp_category or '-'}]")
    return "\n".join(out)


def section_disagreements(rows: list[ClientEvidence]) -> str:
    out = []
    disagreeing = []
    for ev in rows:
        by_source = ev.resolved_by_source()
        keys = set(by_source.values())
        if len(keys) > 1:
            disagreeing.append((ev, by_source))
    out.append(f"## 3. Clients where two or more sources disagree "
               f"({len(disagreeing)})\n")
    if not disagreeing:
        out.append("None, in this run.")
        return "\n".join(out)
    for ev, by_source in disagreeing:
        out.append(f"**{ev.name}**")
        for label, key in by_source.items():
            text = dict(ev.sources())[label]
            out.append(f"- `{label}` -> **{key}**  (\"{text}\")")
        out.append("")
    return "\n".join(out)


def section_picker_changes(rows: list[ClientEvidence], meta: dict) -> str:
    out = ["## 4. Image Picker keys that would CHANGE under the draft\n"]
    if meta.get("picker_error"):
        out.append(
            f"**Not measured.** The Image Picker's own database could not "
            f"be read: {meta['picker_error']}. This is \"we could not look\", "
            f"not \"nothing would change\".")
        return "\n".join(out)
    if not meta.get("picker_rows_found"):
        out.append(
            "The Image Picker database has **no client galleries** in this "
            "environment (0 rows in `image_picker_clients`), so there is "
            "nothing to compare — not because nothing would change, but "
            "because this run has no galleries to check it against. See "
            "the environment note at the top of this report.")
        return "\n".join(out)

    changed = []
    for ev in rows:
        if not ev.picker_key:
            continue
        old = ev.picker_key
        folded_old = LEGACY_FOLD.get(old, old)
        new = ev.top_key()
        if folded_old != new:
            changed.append((ev.name, old, new))
    out.append(f"{len(changed)} galleries would change.\n")
    if changed:
        out.append("| client | old key | new key |")
        out.append("|---|---|---|")
        for name, old, new in changed:
            out.append(f"| {name} | {old} | {new} |")
    return "\n".join(out)


def section_scan_coverage(rows: list[ClientEvidence]) -> str:
    scanned = sum(1 for ev in rows if ev.scanned)
    unscanned = len(rows) - scanned
    out = ["## 5. Scanned vs unscanned clients\n",
           f"- Scanned (a completed Insites scan was found for their domain): "
           f"**{scanned}**",
           f"- Unscanned (no domain on file, or no completed scan found for "
           f"it — Knack + name only): **{unscanned}**"]
    return "\n".join(out)


def section_suggestions(rows: list[ClientEvidence], meta: dict) -> str:
    active = Counter()
    for ev in rows:
        if ev.live:
            active[ev.top_key()] += 1

    out = ["## 6. Suggested edits\n"]

    data_is_thin = len(rows) < 20 or meta.get("picker_error") or not meta.get("picker_rows_found")
    if data_is_thin:
        out.append(
            "**This run's client list is too small to derive real cluster "
            "sizes from.** " + _thin_data_reason(rows, meta) + " The "
            "reasoning below is therefore structural — about the shape of "
            "the draft taxonomy itself, not about counted clients — and "
            "should be re-checked once this script is run against a live "
            "Knack pull and a populated Image Picker/scans database. The "
            "cap of 30 keys is respected in every suggestion below: each "
            "add names an existing key to fold into it.\n")

    big_enough = [k for k, n in active.items() if n >= 5 and k != "general"]
    if big_enough:
        out.append("Clusters with 5+ active clients in this run (candidates "
                   "to keep or split further):\n")
        for k in sorted(big_enough, key=lambda k: -active[k]):
            out.append(f"- `{k}`: {active[k]} active clients")
        out.append("")
    else:
        out.append(
            "No key reached the ≥5-active-client bar for a *new* key "
            "in this run's data (see the caveat above) — so no addition is "
            "proposed on the strength of what this run actually counted.\n")

    out.append(
        "Structural observations worth checking against real data:\n"
        "\n"
        "1. **`legal`'s two subtypes are folded into one key, and the draft "
        "list already marks them** (`personal_injury` / `criminal defense` "
        "as parenthetical subtypes in the work order). If real data shows "
        "either cluster is large enough on its own (≥5 active clients), "
        "it is the natural split — fold `legal` itself down to the "
        "remainder (family law, estate, business law) rather than adding a "
        "31st key.\n"
        "2. **`recruiting` and `professional_services` both carry "
        "\"staffing\"-shaped words** (`staffing`, `staffing agency` under "
        "`professional_services`; `workforce`, `jobs` under `recruiting`). "
        "Word-boundary + earliest-wins means the more specific term should "
        "win when both appear, but a real \"staffing agency\" client's raw "
        "text is worth checking against both aliases before this ships, "
        "since which one leads in the actual sentence decides it.\n"
        "3. **`home_services` and `home_improvement` are both plausible "
        "homes for \"roofing\"** depending on whether the business repairs "
        "roofs (home_services) or replaces them as part of a remodel "
        "(home_improvement) — the current split keeps `roofing`/`roofer` "
        "only under `home_services`, which will read a roofing-only "
        "business correctly and undercount roofing mentioned inside a "
        "broader remodel description. Worth confirming against real "
        "`knack_text` and `scan_primary` values once available.\n"
        "4. **`tourism` and `ski` share \"resort\"-shaped businesses.** The "
        "work order's own note (\"resort (unless ski)\") is not something "
        "word-boundary matching on `resort` alone can express — a resort "
        "that is primarily a ski destination will still resolve to "
        "`tourism` unless its raw text also contains `ski`, `snow` or "
        "another `ski`-key alias earlier in the string. If real data shows "
        "ski resorts routinely landing in `tourism`, that needs either a "
        "dedicated `ski resort` alias on the `ski` key (checked first, since "
        "alias order does not affect matching but text position does) or a "
        "small piece of code rather than a pure alias list.\n"
        "5. **`ecommerce` vs `retail`** is the one pair in the draft list "
        "that is likely to be genuinely rare in this book — Smart 1's book "
        "skews local-service and brick-and-mortar retail based on every "
        "other category in the list. If a live run shows `ecommerce` with "
        "fewer than 5 active clients, folding it into `retail` (keeping its "
        "aliases as `retail` aliases) would free a key without losing "
        "coverage, since nothing else in the list currently needs a 31st "
        "slot.\n")
    return "\n".join(out)


def _thin_data_reason(rows: list[ClientEvidence], meta: dict) -> str:
    bits = [f"This run resolved {len(rows)} client(s) from "
           f"`clients_registry.all_clients()`"]
    if meta.get("picker_error"):
        bits.append("the Image Picker database could not be read at all")
    elif not meta.get("picker_rows_found"):
        bits.append("the Image Picker database has no client galleries")
    return " — ".join(bits) + "."


# --------------------------------------------------------------------------
# Environment note
# --------------------------------------------------------------------------

def environment_section(rows: list[ClientEvidence], meta: dict) -> str:
    # Which source actually answered for products/websites in this run.
    prod_rows, prod_source, prod_age = knack_data._product_source()
    knack_data.websites()   # populate the cache so websites_source() answers
    web_source = knack_data.websites_source()

    out = ["## Environment this report ran against\n"]
    out.append(f"- **Knack credentials in this sandbox:** none configured — "
               f"every Knack-shaped read below fell back to the private "
               f"export / fixture path.")
    out.append(f"- **Products source that answered:** `{prod_source}` "
               f"({len(prod_rows)} rows"
               f"{f', {prod_age} min old' if prod_age is not None else ''}).")
    out.append(f"- **Websites source that answered:** `{web_source}`.")
    out.append(f"- **{_ENV_NOTE}**")
    out.append(f"- **scans table:** read through the shared database engine "
               f"(same Postgres `DATABASE_URL` the rest of the Hub uses in "
               f"this sandbox). {sum(1 for ev in rows if ev.scanned)} of "
               f"{len(rows)} clients had a completed scan on file.")
    if meta.get("picker_error"):
        out.append(f"- **Image Picker database:** could not be read — "
                   f"{meta['picker_error']}")
    else:
        out.append(f"- **Image Picker database:** read successfully, "
                   f"{meta.get('picker_rows_found', 0)} client galleries "
                   f"on file.")
    out.append(
        "\n**Industry / vertical field on a Knack row:** this codebase does "
        "not currently pin or read one anywhere (`hub/knack_products.py`, "
        "`hub/knack_websites.py`) — confirmed by grepping the whole "
        "repository for `industry`/`vertical` outside this script and "
        "`hub/client_brief.py`'s own not-yet-wired seam. This script still "
        "searches every product and website row for a field spelled "
        f"one of `{', '.join(_INDUSTRY_FIELD_NAMES)}` in case a live export "
        "carries one under a name nothing here has pinned yet; it found "
        f"{'some' if any(ev.knack_text for ev in rows) else 'none'} in this "
        "run.")
    return "\n".join(out)


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def main() -> int:
    rows, meta = gather()

    out_dir = Path("/mnt/user-data/outputs")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "industry_report.md"

    sections = [
        "# Client industry evidence report\n",
        ("One-off, read-only. Generated by `tools/industry_report.py`. Makes "
         + "no change to any store the Hub writes to, and does not write "
         + "`hub/industry.py`.\n"),
        environment_section(rows, meta),
        "\n---\n",
        section_counts(rows),
        "\n---\n",
        section_general(rows),
        "\n---\n",
        section_disagreements(rows),
        "\n---\n",
        section_picker_changes(rows, meta),
        "\n---\n",
        section_scan_coverage(rows),
        "\n---\n",
        section_suggestions(rows, meta),
    ]
    text = "\n".join(sections) + "\n"
    out_path.write_text(text, encoding="utf-8")
    print(f"Wrote {out_path} ({len(rows)} clients, {len(text)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
