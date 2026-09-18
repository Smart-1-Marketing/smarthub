"""File a campaign under a client from its name, when the name says whose it is.

The rename shape ``store.RENAME_SHAPE`` -- ``S1M | <ClientKey> | <Product> |
<anything>`` -- is shown beside every row in the unmapped queue so whoever
can rename the campaign in the platform writes it in a form this module can
read. ``parse_name()`` reads it; ``run()`` files every unmapped campaign
whose name parses and whose client resolves.

Rules, each a way to file spend under the wrong client:

* **The first segment must be the literal ``S1M``**, case-insensitive and
  whitespace-tolerant, and there must be at least two segments -- the mark
  and the client. ``SIM`` is not it -- a near-miss is not read as the shape,
  because a parser that forgives a typo in the one token that marks a name
  as ours will eventually read a campaign that was never meant for it. (It
  may still be filed by likeness, below, on the client's name alone.) A name with no product segment (or an empty one)
  files under the channel type the platform reports on the campaign where
  ``products.GOOGLE_CHANNEL_PRODUCTS`` maps it (a Google Ads VIDEO campaign
  is Online Video, not the platform's Paid Search default), else under
  ``products.DEFAULT_PRODUCT_FOR_PLATFORM`` for its platform -- and the row
  says which (``auto_rule="name_v1+channel_product"`` or
  ``"name_v1+default_product"``).
* **The client resolves exactly or not at all.** The second segment is
  tried as a Hub key (``d:example.com`` / ``n:slug``, the same key
  ``/reports/api/clients`` hands the picker) and then as a client name,
  case-insensitively, against the registry. No substring and no fuzzy pass:
  the ``hub/client_key.py`` rule, because attributing one company's spend to
  another is the worst outcome available here.
* **A human mapping is never overwritten.** Only a campaign with NO
  ``CampaignMap`` row is considered; a row somebody made, and a row this
  module made on an earlier run, both stand. Re-mapping is a person's press
  on the unmapped queue.
* **A refusal is remembered.** Not theirs on the queue deletes the row, so
  without ``store.MapRefusal`` the next run would file the campaign under
  the same client again from the same name and the button would undo
  itself within the hour. A campaign is skipped while its name is the one
  it was refused under; renamed, it is read again.

**An ad account that is one client's files the next campaign on it.**
Most platforms seat one client per account -- a Google Ads customer, a
Meta ad account, a StackAdapt advertiser -- so once a person has confirmed
a campaign on an account as a client's, a new campaign on the same account
is theirs until somebody says otherwise. ``store.account_evidence()`` is
the book's reading per account and ``account_suggestion()`` files on it
only when exactly one client has confirmed campaigns there and no filing
under that client was refused on the account. Pending proposals are not
evidence, so one wrong filing cannot become an account's worth; a mixed
account (an agency seat) says nothing. The rule is ``account_v1``, and
where the pull carries the platform's own name for the account (its
advertiser or account name) that name is read for a likeness as well,
under ``account_name_v1``.

**What the campaigns call a client is learned from the people who file
them.** A registry name is not always the name in the platform: "BLW" for
Buckeye Lake Winery matches nothing above. So every mapping, confirmation
and move a person makes teaches ``alias_phrase()`` -- the campaign name's
words with the client's own words and the ad-ops noise out -- as a name for
that client (``store.CampaignAlias``). Taught once it is a suggestion the
picker opens on; taught twice it files, under ``alias_v1``; taught for two
clients it is a lead for each and a filing for neither. Refusing a filing
forgets what its name taught for that client, and the queue lists every
alias with a Forget button.

**A name that does not carry the mark is read for a likeness.** Most
campaigns were named before the shape existed, and the unmapped queue held
them with the client's name plainly in the campaign name and nobody to type
it. ``suggest_clients()`` scores every registry client
against the name -- the client's name contained whole, the client's domain
label, or a near spelling (``difflib``) of the name -- and ``decide()``
names the one to file only when the best likeness is at or above
``FUZZY_FILE_SCORE`` **and** the runner-up is ``FUZZY_MARGIN`` behind it:
``Acme Plumbing`` and ``Acme Roofing`` scoring alike on ``Acme | Search``
files nobody, and the queue shows both for a person to pick. Filed, it is
``auto_rule="fuzzy_v1"``, a proposal like every other auto filing, and it
reaches no figure until confirmed; the queue shows the likeness beside
every campaign it could not file so the picker opens on the likeliest
client rather than empty, and any proposal can be moved to another client
from the queue or the client's page without refusing it first.

Every mapping it writes is ``mapped_by="auto"`` with ``auto_rule="name_v1"``
(or ``"fuzzy_v1"``) on the row, so a report can tell a filed-by-name campaign from one a person
chose, and an activity row under the client's name says it happened. **And
it is a proposal**: the row carries no confirmation, ``store.facts_for()``
does not read it, and the campaign reaches no figure on the client's page
until a person presses Confirm on ``/reports/unmapped`` or the client's own
staff page. A name is somebody's typing in somebody else's platform; a
typo there would otherwise file one client's spend under another with every
screen reading as working.
"""
from __future__ import annotations

import difflib
import logging
import re

from . import store

log = logging.getLogger(__name__)

RULE = "name_v1"
FUZZY_RULE = "fuzzy_v1"
ACCOUNT_RULE = "account_v1"
ACCOUNT_NAME_RULE = "account_name_v1"
ALIAS_RULE = "alias_v1"
MAPPED_BY = store.AUTO_MAPPED_BY
# The rules that file on evidence rather than the mark: what the queue
# calls "by likeness" and shows the reason for.
EVIDENCE_RULES = (FUZZY_RULE, ACCOUNT_RULE, ACCOUNT_NAME_RULE, ALIAS_RULE)

# A learned alias files only once two campaigns have taught it for the
# same client; taught once it is a suggestion the picker opens on. Taught
# for two clients it is a lead for each and a filing for neither.
ALIAS_FILE_COUNT = 2
ALIAS_ONCE_SCORE = 0.85
# An alias is at most this many words: a phrase longer than that is a
# campaign's description, not a name for the client.
ALIAS_MAX_WORDS = 3

# Words a campaign name carries that name no client: the products and
# vendors, the ad-ops vocabulary, calendar words and bare numbers. What is
# left of a name once these and the client's own words are out is what
# the campaigns call the client -- the alias worth learning.
_NOISE_WORDS = frozenset("""
search display video audio social native retargeting remarketing rt pmax performance max
brand branded nonbrand non generic competitor competitors conversion conversions leads lead
traffic awareness reach engagement clicks sales shopping dsa dynamic prospecting lookalike lal
lookalikes geo geofence geofencing fence ip target targeting targeted ctv ott streaming tv olv
youtube yt preroll pre roll bumper skippable instream in stream outstream discovery demand gen
campaign campaigns ad ads adgroup test testing new old copy draft paused active promo promotion
sale event holiday seasonal spring summer fall autumn winter q1 q2 q3 q4 h1 h2 fy january february
march april may june july august september october november december jan feb mar apr jun jul aug
sep sept oct nov dec local national regional statewide mobile desktop app apps web website site
email sms radio print digital online offline usa us ca
""".split())

# How many of an account's campaigns have to be confirmed as one client's,
# with none confirmed as anybody else's and no refusal there, before a new
# campaign on the account is filed as theirs. One: a person's own mapping
# on the account is a statement about the account.
ACCOUNT_MIN_CONFIRMED = 1

# The likeness a campaign name has to bear to a client before the fuzzy
# pass files a proposal on it, and how far behind the runner-up has to be.
# Both house numbers, written here rather than tuned in a settings screen:
# below FUZZY_SHOW_SCORE a likeness is not even shown, between the two it
# is a suggestion the queue's picker opens on, at FUZZY_FILE_SCORE with the
# margin it is filed and waits for confirmation like a name_v1 filing.
FUZZY_FILE_SCORE = 0.90
FUZZY_SHOW_SCORE = 0.70
FUZZY_MARGIN = 0.08
FUZZY_LIMIT = 3

# Score by kind of evidence. A whole name is a name; a domain label is the
# next best thing; a near spelling is difflib's ratio; a distinctive word
# of the client's name on its own is a lead and never a filing.
_SCORE_NAME = 1.0
_SCORE_DOMAIN = 0.95
_SCORE_PARTIAL = 0.75
_MIN_PARTIAL_CHARS = 4
# A near spelling has to start the way the client's name does, this many
# letters of one word: the gate in front of difflib.
_PREFIX = 3
# A word of a client's name is a lead on its own only while few clients
# carry it: "riverside" leads, "home" in a book of twelve Home-somethings
# does not. Past this many clients a word is common and leads nobody.
COMMON_WORD_CLIENTS = 5

# Separators campaign names are built from, made spaces before the words
# are read: "Acme_Plumbing-Search|2026" is four words, not one.
_SEP = re.compile(r"[|/\\_\-–—:;,.()\[\]{}+&#*\"'`~]+")

_MARK = re.compile(r"^\s*s1m\s*$", re.IGNORECASE)


def parse_name(name: str) -> dict | None:
    """``S1M | <ClientKey> | <Product> | <anything>`` -> the parts, or None.

    Pipe-separated, first segment the S1M literal (any case, any spacing),
    at least two segments, client non-empty. ``product`` is "" when the
    name carries none; the caller fills in the platform's default.
    """
    if not name or "|" not in str(name):
        return None
    parts = [p.strip() for p in str(name).split("|")]
    if len(parts) < 2 or not _MARK.match(parts[0]):
        return None
    client = parts[1]
    product = parts[2] if len(parts) > 2 else ""
    if not client:
        return None
    return {"client": client, "product": product,
            "rest": " | ".join(parts[3:]).strip()}


class RegistryUnavailable(RuntimeError):
    """The client registry could not be read, which is a different answer
    from a token naming nobody -- and only the second means rename it."""


def resolve_client(token: str) -> tuple[str, str] | None:
    """(Hub key, display name) for the second segment, or None.

    Exact key first -- the registry row already carries the key the picker
    hands over, derived the way /reports/api/clients derives it -- then an
    exact, case-insensitive name. Never a substring.
    """
    token = (token or "").strip()
    if not token:
        return None
    try:
        from hub import client_key as ck
        from hub import clients_registry
        rows = clients_registry.all_clients()
    except Exception as exc:               # noqa: BLE001 - registry unavailable
        log.warning("reports automap: client registry unreadable: %s", exc)
        # Not "no such client": nothing can be resolved this run, and
        # naming every campaign unresolved would send somebody to rename
        # campaigns that are fine. run() stops on it and says so.
        raise RegistryUnavailable(f"{type(exc).__name__}: {exc}"[:200]) from exc
    low = token.lower()
    for r in rows:
        name = r.get("name") or ""
        key = r.get("key") or ck.client_key(name, r.get("url") or r.get("domain") or "")
        if key and key.lower() == low:
            return key, name
    for r in rows:
        name = r.get("name") or ""
        if name.lower() == low:
            key = r.get("key") or ck.client_key(name, r.get("url") or r.get("domain") or "")
            return key, name
    return None


# ------------------------------------------------------------- likeness
def _registry_rows() -> list[dict]:
    """The client registry, or RegistryUnavailable -- the same distinction
    resolve_client() draws, for the same reason."""
    try:
        from hub import clients_registry
        return list(clients_registry.all_clients())
    except Exception as exc:               # noqa: BLE001 - registry unavailable
        log.warning("reports automap: client registry unreadable: %s", exc)
        raise RegistryUnavailable(f"{type(exc).__name__}: {exc}"[:200]) from exc


def _words(text: str) -> list[str]:
    """A campaign name or a client name as the words that identify a
    business: separators to spaces, then hub/client_key.normalise_name's
    reading (lowercase, punctuation out, legal suffixes and filler dropped)."""
    from hub.client_key import normalise_name
    return normalise_name(_SEP.sub(" ", str(text or ""))).split()


def _noise(word: str) -> bool:
    """A word that names no client: a product or vendor word, an ad-ops
    word, a calendar word, or anything with a digit in it."""
    if not word or any(ch.isdigit() for ch in word):
        return True
    if word in _NOISE_WORDS:
        return True
    return word in _catalog_words()


_CATALOG_WORDS: set[str] | None = None


def _catalog_words() -> set[str]:
    global _CATALOG_WORDS
    if _CATALOG_WORDS is None:
        words: set[str] = set()
        try:
            from . import products as _products
            for name in tuple(_products.PRODUCTS) + tuple(_products.VENDOR_WORDS):
                words.update(_words(name))
        except Exception:                  # noqa: BLE001 - the catalog is not the alias
            pass
        _CATALOG_WORDS = words
    return _CATALOG_WORDS


def alias_phrase(campaign_name: str, client_name: str) -> str:
    """What the campaign calls the client, or "": the campaign name's
    words with the client's own words, the S1M mark and the noise out, as
    one phrase -- "blw" from "BLW - Search - 2026" filed under Buckeye Lake
    Winery. Empty when nothing distinctive is left (the name already
    carried the client's name, or only noise), or when what is left is
    longer than ALIAS_MAX_WORDS, which is a description and not a name."""
    client_words = set(_words(client_name))
    left = [w for w in _words(campaign_name)
            if w not in client_words and w != "s1m" and not _noise(w) and len(w) >= 2]
    if not left or len(left) > ALIAS_MAX_WORDS:
        return ""
    return " ".join(left)


def _domain_label(row: dict) -> str:
    """``acme`` from acme.com -- the part of a domain people put in a
    campaign name -- or "" when the row has no domain or it is too short
    to mean anything."""
    from hub.client_context import canonical_domain
    dom = canonical_domain(row.get("url") or row.get("domain") or "")
    label = dom.split(".")[0] if dom else ""
    return label if len(label) >= _MIN_PARTIAL_CHARS else ""


def build_index(rows: list[dict], aliases: list[dict] | None = None) -> list[dict]:
    """The registry read once for many names: one entry per row the fuzzy
    pass can score -- key, the name to file under (the canonical row's, not
    an alias's), its words, and its domain label. Rows with no readable
    name are skipped. ``run()`` and ``annotate()`` build it once and hand
    it to ``suggest_clients()`` for every campaign; building it per name
    was most of an hourly run.

    ``aliases`` (``store.campaign_aliases()``) ride on the same list as
    entries of their own kind: ``{"alias_words", "clients": {key: {...}}}``,
    one per learned phrase with every client it was taught for."""
    from hub import client_key as ck
    label_by_key: dict[str, str] = {}
    for r in rows:
        name = (r.get("name") or "").strip()
        key = r.get("key") or ck.client_key(name, r.get("url") or r.get("domain") or "")
        if name and key and not r.get("is_alias") and key not in label_by_key:
            label_by_key[key] = name
    out = []
    for r in rows:
        name = (r.get("name") or "").strip()
        key = r.get("key") or ck.client_key(name, r.get("url") or r.get("domain") or "")
        words = _words(name)
        if not (name and key and words):
            continue
        out.append({"key": key, "name": label_by_key.get(key, name), "alias": name,
                    "words": words, "joined": "".join(words),
                    "prefixes": {w[:_PREFIX] for w in words if len(w) >= _PREFIX},
                    "domain_label": _domain_label(r)})
    # How many clients carry each word of a name: a word on more than
    # COMMON_WORD_CLIENTS of them is common, and common words lead nobody
    # on their own.
    carriers: dict[str, set] = {}
    for cand in out:
        for w in set(cand["words"]):
            carriers.setdefault(w, set()).add(cand["key"])
    common = {w for w, keys in carriers.items() if len(keys) > COMMON_WORD_CLIENTS}
    for cand in out:
        cand["partial_words"] = [w for w in cand["words"] if len(w) >= _MIN_PARTIAL_CHARS and w not in common]
    learned: dict[str, dict] = {}
    for a in aliases or ():
        words = _words(a.get("alias") or "")
        if not words:
            continue
        entry = learned.setdefault(" ".join(words), {"alias_words": words, "alias": a.get("alias"),
                                                     "clients": {}, "generic": False})
        entry["clients"][a["client"]] = {"name": label_by_key.get(a["client"]) or a.get("client_name")
                                         or a["client"], "count": int(a.get("count") or 0)}
    # An alias whose every word is in some OTHER client's registry name is
    # a description ("heating cooling"), not a nickname ("blw"): it is
    # never a filing, however often it was taught, because the day the
    # other client's campaign lands it would file under the wrong one.
    for entry in learned.values():
        alias_words = set(entry["alias_words"])
        for cand in out:
            if cand["key"] in entry["clients"]:
                continue
            if alias_words <= set(cand["words"]):
                entry["generic"] = cand["name"]
                break
    out.extend(learned.values())
    return out


def _contains(haystack: list[str], needle: list[str]) -> bool:
    n = len(needle)
    return n > 0 and any(haystack[i:i + n] == needle for i in range(len(haystack) - n + 1))


def _score(cand: dict, words: list[str], tokens: set[str], prefixes: set[str]) -> tuple[float, str]:
    """How much ``words`` (a campaign name) looks like one client: the
    score and the reason a person reads beside it. 0 when it does not."""
    cw = cand["words"]
    if len(cand["joined"]) < _MIN_PARTIAL_CHARS:
        # "AB" as a client: two letters in a campaign name are a lead at
        # most, whatever else the name says.
        if _contains(words, cw):
            return _SCORE_PARTIAL, "the campaign name carries the client's (short) name"
        return 0.0, ""
    if _contains(words, cw):
        return _SCORE_NAME, "the campaign name contains the client's name"
    if cand["joined"] in tokens:
        return _SCORE_NAME, "the campaign name carries the client's name run together"
    # The domain label, when it is more than one word of the name: "acme"
    # off acme.com is one word of Acme Plumbing and of Acme Roofing both,
    # and reads as the partial it is, below.
    if cand["domain_label"] and cand["domain_label"] in tokens and cand["domain_label"] not in cw:
        return _SCORE_DOMAIN, f"the campaign name carries the client's domain ({cand['domain_label']})"
    target = " ".join(cw)
    # A near spelling: the client's name against the run of the same
    # number of words (give or take one) starting at each campaign word
    # that begins the way one of the client's does. A spelling that differs
    # in the first letters of every word is not near, and difflib over every
    # window of every client for every name was most of an hourly run.
    best = 0.0
    n = len(cw)
    starts = [i for i, w in enumerate(words) if w[:_PREFIX] in cand["prefixes"]]
    if not starts:
        return 0.0, ""
    for size in {max(1, n - 1), n, n + 1}:
        for i in starts:
            window = " ".join(words[i:i + size])
            if not window:
                continue
            sm = difflib.SequenceMatcher(None, window, target)
            if sm.real_quick_ratio() < best or sm.quick_ratio() < best:
                continue
            ratio = sm.ratio()
            if ratio > best:
                best = ratio
    if best >= FUZZY_SHOW_SCORE and best < _SCORE_NAME:
        return round(best, 3), "the campaign name is a near spelling of the client's name"
    # A distinctive word of the client's name, on its own: a lead. Never a
    # filing -- "Acme" is Acme Plumbing and Acme Roofing both -- and never
    # on a word most of the book carries.
    for w in cand.get("partial_words", [w for w in cw if len(w) >= _MIN_PARTIAL_CHARS]):
        if w in tokens:
            return _SCORE_PARTIAL, f"the campaign name carries part of the client's name ({w})"
    return 0.0, ""


def _score_alias(cand: dict, words: list[str]) -> dict[str, dict]:
    """A learned alias against a campaign name: {client key: hit}. The
    phrase has to be in the name whole. One client taught twice or more is
    a filing; taught once, a suggestion; two clients, a lead for each."""
    if not _contains(words, cand["alias_words"]):
        return {}
    clients = cand["clients"]
    shown = cand.get("alias") or " ".join(cand["alias_words"])
    out = {}
    if len(clients) == 1:
        key, c = next(iter(clients.items()))
        n = c["count"]
        score = _SCORE_NAME if n >= ALIAS_FILE_COUNT else ALIAS_ONCE_SCORE
        why = (f"{shown!r} was mapped to them {n} time{'' if n == 1 else 's'} before"
               + ("" if n >= ALIAS_FILE_COUNT else " (once: a suggestion until it is taught again)"))
        if cand.get("generic"):
            score = min(score, ALIAS_ONCE_SCORE)
            why += f"; it is also part of {cand['generic']}'s name, so it suggests and never files"
        out[key] = {"key": key, "name": c["name"], "score": score, "pct": int(round(score * 100)),
                    "why": why, "rule": ALIAS_RULE}
        return out
    others = {k: c["name"] for k, c in clients.items()}
    for key, c in clients.items():
        rest = ", ".join(n for k, n in others.items() if k != key)
        out[key] = {"key": key, "name": c["name"], "score": _SCORE_PARTIAL, "pct": int(round(_SCORE_PARTIAL * 100)),
                    "why": f"{shown!r} was mapped to them before, and also to {rest}", "rule": ALIAS_RULE}
    return out


def _excluded(exclude) -> set:
    """``exclude`` as a set of keys: one key, several, or none."""
    if not exclude:
        return set()
    if isinstance(exclude, str):
        return {exclude}
    return {k for k in exclude if k}


def suggest_clients(name: str, *, rows: list[dict] | None = None,
                    index: list[dict] | None = None,
                    limit: int = FUZZY_LIMIT, exclude="") -> list[dict]:
    """The clients a campaign name looks like, best first, each
    ``{"key", "name", "score", "pct", "why"}``; ``[]`` when nothing scores
    ``FUZZY_SHOW_SCORE``. ``index`` is ``build_index()`` of the registry;
    without it ``rows`` is indexed here, and without those the registry is
    read (RegistryUnavailable when it cannot be). ``exclude`` drops one
    key, the client a person already refused this campaign under. Never a
    filing by itself: ``decide()`` says whether the best one is worth one."""
    words = _words(name)
    if not words:
        return []
    tokens = set(words)
    prefixes = {w[:_PREFIX] for w in words if len(w) >= _PREFIX}
    cands = index if index is not None else build_index(rows if rows is not None else _registry_rows())
    skip = _excluded(exclude)
    best_by_key: dict[str, dict] = {}
    for cand in cands:
        if "alias_words" in cand:
            for key, hit in _score_alias(cand, words).items():
                if key in skip:
                    continue
                cur = best_by_key.get(key)
                if cur is None or hit["score"] > cur["score"]:
                    best_by_key[key] = hit
            continue
        if cand["key"] in skip:
            continue
        score, why = _score(cand, words, tokens, prefixes)
        if score < FUZZY_SHOW_SCORE:
            continue
        cur = best_by_key.get(cand["key"])
        if cur is None or score > cur["score"]:
            best_by_key[cand["key"]] = {"key": cand["key"], "name": cand["name"],
                                        "score": score, "pct": int(round(score * 100)),
                                        "why": why, "rule": FUZZY_RULE}
    out = sorted(best_by_key.values(), key=lambda c: (-c["score"], c["name"].lower()))
    return out[:max(1, int(limit))]


def account_suggestion(row: dict, evidence: dict) -> dict | None:
    """The client the campaign's ad account already belongs to, or None.

    Exactly one client has confirmed campaigns on the account (at least
    ACCOUNT_MIN_CONFIRMED of them), nobody else does, and no filing under
    that client was refused on the account. Pending proposals are not
    evidence. A mixed account -- an agency seat, a reseller filing several
    clients through one login -- says nothing, and a refusal on the account
    is a person saying it is not solely theirs."""
    a = evidence.get((row.get("platform"), row.get("account_id")))
    if not a:
        return None
    confirmed = {k: n for k, n in a["confirmed"].items() if n >= ACCOUNT_MIN_CONFIRMED}
    if len(confirmed) != 1 or len(a["confirmed"]) != 1:
        return None
    key, n = next(iter(confirmed.items()))
    if key in a["refused"]:
        return None
    return {"key": key, "name": a["names"].get(key, key), "score": 1.0, "pct": 100,
            "why": (f"{n} other campaign{'s' if n != 1 else ''} on this ad account "
                    f"{'are' if n != 1 else 'is'} confirmed as theirs"),
            "rule": ACCOUNT_RULE}


def _merge(*lists: list[dict], limit: int = FUZZY_LIMIT) -> list[dict]:
    """One list, best score per client, best first."""
    best: dict[str, dict] = {}
    for hits in lists:
        for h in hits or ():
            cur = best.get(h["key"])
            if cur is None or h["score"] > cur["score"]:
                best[h["key"]] = h
    return sorted(best.values(), key=lambda c: (-c["score"], c["name"].lower()))[:max(1, limit)]


def suggest_for_row(row: dict, *, index: list[dict], evidence: dict,
                    limit: int = FUZZY_LIMIT, exclude="",
                    name_cache: dict | None = None) -> list[dict]:
    """Everything the book and the names say about one unmapped campaign,
    merged: the account's confirmed campaigns, the campaign name's likeness
    and the ad account's own name's likeness (the platform's advertiser or
    account name, when the pull carries one). Each entry says which, in
    ``rule`` and ``why``; ``decide()`` reads the merged list, so an account
    that says one client and a name that says another are two clients at
    the top and file neither."""
    skip = _excluded(exclude)
    hits = []
    acct = account_suggestion(row, evidence)
    if acct and acct["key"] not in skip:
        hits.append([acct])
    # One reading per distinct name across a run: the same campaign name
    # on ten accounts is one likeness, not ten.
    cname = row.get("campaign_name") or ""
    ckey = (cname, tuple(sorted(skip)))
    if name_cache is not None and ckey in name_cache:
        hits.append(name_cache[ckey])
    else:
        by_campaign = suggest_clients(cname, index=index, limit=limit, exclude=skip)
        if name_cache is not None:
            name_cache[ckey] = by_campaign
        hits.append(by_campaign)
    account_name = (row.get("account_name") or "").strip()
    if account_name:
        by_name = suggest_clients(account_name, index=index, limit=limit, exclude=skip)
        for h in by_name:
            h["rule"] = ACCOUNT_NAME_RULE
            h["why"] = f"the ad account is named {account_name!r}: " + h["why"].replace("the campaign name", "it")
        hits.append(by_name)
    return _merge(*hits, limit=limit)


def decide(suggestions: list[dict]) -> dict | None:
    """The suggestion the fuzzy pass files, or None: the best one at or
    above FUZZY_FILE_SCORE with the runner-up FUZZY_MARGIN behind it. Two
    clients alike on a name is not a match; it is a question for the queue."""
    if not suggestions:
        return None
    best = suggestions[0]
    if best["score"] < FUZZY_FILE_SCORE:
        return None
    if len(suggestions) > 1 and best["score"] - suggestions[1]["score"] < FUZZY_MARGIN:
        return None
    return best


def product_from_name(name: str) -> str:
    """A catalog product named whole in the campaign name, else "". Only
    the catalog's own names: "search" in a Meta campaign is not Paid
    Search, and a guessed product draws a bar no budget line can pace."""
    from . import products as _products
    words = _words(name)
    for known in _products.PRODUCTS:
        kw = _words(known)
        if kw and _contains(words, kw):
            return known
    return ""


# The pacing board asks, per line, how many unmapped campaigns look like
# the client's -- once per page, not once per line, and not afresh on every
# load: the reading costs a registry index and a likeness per queue row.
# Held per process for LIKELY_TTL_SECONDS. Read-only, so the two workers
# holding two copies is fine.
LIKELY_TTL_SECONDS = 300
LIKELY_QUEUE_LIMIT = 500
_LIKELY_CACHE: dict = {"at": 0.0, "value": None}


def likely_by_client(refresh: bool = False) -> dict[str, int]:
    """{client key: how many unmapped campaigns look like theirs}, from the
    queue's likeness (``annotate``) over the newest LIKELY_QUEUE_LIMIT rows.
    Raises when the store or registry will not answer: the caller draws
    "not measured" rather than a nought."""
    import time as _time
    cached = _LIKELY_CACHE["value"]
    if cached is not None and not refresh and _time.time() - _LIKELY_CACHE["at"] < LIKELY_TTL_SECONDS:
        return cached
    rows = store.unmapped_campaigns(days=30, limit=LIKELY_QUEUE_LIMIT)
    outcome = annotate(rows)
    if outcome.get("error"):
        raise RegistryUnavailable(outcome["error"])
    out: dict[str, int] = {}
    for r in rows:
        for s in r.get("suggestions") or ():
            out[s["key"]] = out.get(s["key"], 0) + 1
    _LIKELY_CACHE["value"], _LIKELY_CACHE["at"] = out, _time.time()
    return out


def forget_likely() -> None:
    """Drop the held reading: a mapping just changed what the queue holds."""
    _LIKELY_CACHE["value"] = None


def learn(campaign_name: str, *, client: str, client_name: str, by: str) -> dict | None:
    """A person filed this campaign under this client: teach what the
    campaign calls them, if the name says anything distinctive. Never
    raises -- a lesson not learned is not a mapping not made."""
    try:
        phrase = alias_phrase(campaign_name, client_name)
        if not phrase:
            return None
        return store.learn_alias(phrase, client=client, client_name=client_name,
                                 learned_from=campaign_name, by=by)
    except Exception as exc:               # noqa: BLE001
        log.warning("reports automap: alias not learned from %r: %s", campaign_name, exc)
        return None


def forget(campaign_name: str, *, client: str, client_name: str = "") -> bool:
    """A person refused this campaign under this client: whatever the
    name taught for them is forgotten. Never raises."""
    try:
        phrase = alias_phrase(campaign_name, client_name)
        return bool(phrase) and store.forget_alias(phrase, client)
    except Exception as exc:               # noqa: BLE001
        log.warning("reports automap: alias not forgotten from %r: %s", campaign_name, exc)
        return False


def annotate(rows: list[dict], pending: list[dict] | None = None,
             *, limit: int = FUZZY_LIMIT) -> dict:
    """Lay the likeness over the unmapped queue: ``row["suggestions"]`` on
    every unmapped row (the refused client left out) and ``m["match"]`` on
    every pending row the fuzzy pass filed, so the page can say why. Never
    raises: a registry that cannot be read leaves every list empty and is
    named in the returned ``{"error": ...}`` for the page to say so."""
    for r in rows:
        r["suggestions"] = []
    for m in pending or ():
        m["match"] = None
        m["by_evidence"] = any(rule in (m.get("auto_rule") or "") for rule in EVIDENCE_RULES)
    try:
        index = build_index(_registry_rows(), store.campaign_aliases())
    except RegistryUnavailable as exc:
        return {"error": str(exc)}
    evidence = store.account_evidence()

    def suggestions_for(row: dict, exclude: str = "") -> list[dict]:
        try:
            return suggest_for_row(row, index=index, evidence=evidence, limit=limit, exclude=exclude)
        except Exception as exc:           # noqa: BLE001 - one bad row is not the page
            log.warning("reports automap: suggest failed for %r: %s", row.get("campaign_name"), exc)
            return []

    for r in rows:
        refused = set(r.get("refused_clients") or ())
        if (r.get("refused") or {}).get("client"):
            refused.add(r["refused"]["client"])
        r["suggestions"] = suggestions_for(r, refused)
    for m in pending or ():
        if m["by_evidence"]:
            # The proposal's own row is confirmed by nobody, so the account
            # evidence the page recomputes here is the same the run saw.
            hits = suggestions_for(m)
            m["match"] = next((h for h in hits if h["key"] == m.get("client")), None)
    return {"error": ""}


def run(actor: str = "scheduler", limit: int = 5000) -> dict:
    """File every unmapped campaign whose name parses and resolves.

    Returns ``{"mapped": n, "unparsed": n, "unresolved": [...], "clients":
    {key: name}, "refused": n, "suggested": n, "ambiguous": n}``.
    ``unresolved`` names the client tokens that parsed and matched nobody,
    because those are the ones a rename typo produces and the unmapped
    queue is where somebody meets them; ``refused`` counts the campaigns
    left alone because a person refused this filing under this name. A
    name that does not parse is read for the evidence (``suggest_for_row``:
    the ad account's confirmed campaigns, the campaign name's likeness, the
    account's own name's likeness): ``suggested`` counts the ones filed that
    way (they are in ``mapped`` too, and ``by_rule`` says under which rule),
    ``ambiguous`` the ones that looked like a client but not clearly enough,
    or like two, and ``conflicted`` the subset where the account said one
    client and a name said another. Every mapping counted in ``mapped`` is pending
    confirmation.
    """
    out = {"mapped": 0, "unparsed": 0, "unresolved": [], "clients": {}, "refused": 0,
           "suggested": 0, "ambiguous": 0, "conflicted": 0, "by_rule": {}}
    try:
        from hub import audit as hub_audit
    except Exception:                      # noqa: BLE001 - standalone
        hub_audit = None
    cache: dict[str, tuple[str, str] | None] = {}
    index: list[dict] | None = None
    evidence: dict | None = None
    name_cache: dict = {}
    for row in store.unmapped_campaigns(days=3650, limit=limit):
        if row.get("refused"):
            out["refused"] += 1
            continue
        parsed = parse_name(row.get("campaign_name") or "")
        if not parsed:
            out["unparsed"] += 1
            # No mark on the name: read it for a likeness to a client. Filed
            # only on a clear best (decide()); alike on two clients, or not
            # alike enough, it stays for the queue, where the likeness is
            # shown beside it.
            try:
                if index is None:
                    index = build_index(_registry_rows(), store.campaign_aliases())
                if evidence is None:
                    evidence = store.account_evidence()
                hits = suggest_for_row(row, index=index, evidence=evidence, name_cache=name_cache)
            except RegistryUnavailable as exc:
                out["registry_error"] = str(exc)
                break
            best = decide(hits)
            if best is None:
                if hits:
                    out["ambiguous"] += 1
                    if any(h["rule"] == ACCOUNT_RULE for h in hits):
                        # The account said one client and a name said
                        # another, or the same client under two readings
                        # that did not agree. A person's question.
                        out["conflicted"] += 1
                continue
            parsed = {"client": best["name"], "product": product_from_name(row.get("campaign_name") or ""),
                      "rest": "", "fuzzy": best}
            hit = (best["key"], best["name"])
        else:
            token = parsed["client"]
            if token.lower() not in cache:
                try:
                    cache[token.lower()] = resolve_client(token)
                except RegistryUnavailable as exc:
                    out["registry_error"] = str(exc)
                    break
            hit = cache[token.lower()]
            if not hit:
                if token not in out["unresolved"]:
                    out["unresolved"].append(token)
                continue
        key, name = hit
        from . import products as _products
        fuzzy = parsed.get("fuzzy")
        product, rule = parsed["product"], (fuzzy["rule"] if fuzzy else RULE)
        base_rule = rule
        typed = product
        if product and _products.normalize(product) not in _products.PRODUCTS:
            # A segment naming no product in the catalog ("Strming TV") must
            # not become a product: it draws a bar on the client's page that
            # no budget line can ever pace. The platform default, and the
            # rule says the segment was not understood.
            product, rule = "", base_rule + "+unknown_product"
        if product and fuzzy:
            rule = base_rule + "+name_product"
        if not product:
            # The channel type the platform reports on the campaign decides
            # before the platform default does: a Google Ads VIDEO campaign
            # is Online Video, and filed under the platform default it reads
            # as search on the client's page. The rule says which answered.
            channel = row.get("channel_type") or ""
            product = _products.default_for(row["platform"], channel)
            if rule == base_rule:
                rule = base_rule + ("+channel_product"
                               if _products.channel_decided(row["platform"], channel)
                               else "+default_product")
        try:
            store.map_campaign(row["platform"], row["account_id"], row["campaign_id"],
                               client=key, client_name=name, product=product,
                               mapped_by=MAPPED_BY, auto_rule=rule,
                               campaign_name=row.get("campaign_name") or "")
        except ValueError as exc:
            log.warning("reports automap: %s", exc)
            continue
        out["mapped"] += 1
        if fuzzy:
            out["suggested"] += 1
            out["by_rule"][fuzzy["rule"]] = out["by_rule"].get(fuzzy["rule"], 0) + 1
        out["clients"][key] = name
        if hub_audit is not None:
            try:
                how = (f"by {'its ad account' if fuzzy['rule'] == ACCOUNT_RULE else 'a learned name' if fuzzy['rule'] == ALIAS_RULE else 'likeness'} "
                       f"({fuzzy['pct']}%: {fuzzy['why']})" if fuzzy else "from its name")
                hub_audit.log("reports", "campaign_automapped", actor=actor,
                              client=name, client_key=key, action="campaign_automapped",
                              platform=row["platform"], campaign_id=row["campaign_id"],
                              product=product, rule=rule,
                              detail=f"{store.platform_label(row['platform'])} campaign "
                                     f"{row.get('campaign_name') or row['campaign_id']} "
                                     f"filed under {name} {how}, waiting for "
                                     f"confirmation on /reports/unmapped"
                                     + (f" (product segment {typed!r} is not in the catalog; "
                                        f"filed under {product or 'no product'})"
                                        if "unknown_product" in rule else ""))
            except Exception:              # noqa: BLE001 - a log line is not the mapping
                pass
    # The board's "look like theirs" count is of the queue as it was.
    forget_likely()
    return out
