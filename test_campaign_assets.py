"""Campaigns waiting on an asset, and the four ways that list goes quietly wrong.

`/tools/campaign-assets` reads two fields on object_135 that nothing had ever
read: `field_2742` (Clarification needed), and `field_2347` — the assets still
outstanding — which counts only where the tickbox `field_2346` beside it is
ticked. The list is per campaign, sorted by media partner then internal sales.

Everything asserted here is a way the page could look healthy and be wrong:

* **the tickbox is the answer.** Text in 2347 with the box unticked is not an
  ask. It is also not nothing, so it is counted and named rather than dropped
  in silence;
* **absent is not zero.** A product cache written before these fields existed
  carries neither key on any row, and a missing key reads as "no campaign
  needs anything" — a confident wrong answer about every client at once. The
  report says *not measured* instead, and `knack_products.FIELDS_VERSION`
  makes that state clear itself;
* **a blank media partner sorts last.** An empty string is not an early letter
  of the alphabet, and a campaign nobody has filed must not head the queue;
* **a campaign that has not started yet is exactly the point.**
  `knack_data.is_running` answers a different question — is it delivering
  today — and a campaign starting in three weeks is the one somebody has to
  chase artwork for.

Run directly: ``python3 test_campaign_assets.py``. No pytest, no network — the
Knack reader is replaced with fixture rows and the store points at a temporary
directory.
"""
import datetime as _dt
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

_TMP = tempfile.mkdtemp(prefix="hub-assets-")
os.environ["HUB_DATA_DIR"] = _TMP
os.environ.setdefault("DATABASE_URL", "sqlite:///" + os.path.join(_TMP, "t.db"))
os.environ.setdefault("SECRET_KEY", "test-not-a-secret")

from hub import campaign_assets, knack_products

FAILURES = []
TODAY = _dt.date(2026, 8, 25)
# Kept before served() replaces it, so the cache section below exercises the
# real reader rather than the fixture that stands in for it everywhere else.
REAL_ROWS = knack_products.rows


def check(label, got, want):
    ok_ = got == want
    print(f"  {'ok  ' if ok_ else 'FAIL'}  {label}: {got!r}")
    if not ok_:
        FAILURES.append(f"{label}: expected {want!r}, got {got!r}")


def ok(label, condition, detail=""):
    print(f"  {'ok  ' if condition else 'FAIL'}  {label}"
          f"{(' — ' + detail) if detail and not condition else ''}")
    if not condition:
        FAILURES.append(f"{label}{(': ' + detail) if detail else ''}")


def product(**kw):
    """One flattened object_135 row, with everything _row() carries."""
    row = {
        "id": kw.get("id", ""), "product": kw.get("product", "Display"),
        "product_num": "", "kind": "", "tactics": "",
        "io": kw.get("io", "2100"), "campaign": kw.get("campaign", "Annual"),
        "client": kw.get("client", "Riverside HVAC"),
        "organization": kw.get("organization", ""),
        "partner": kw.get("partner", "STAR 99.1 FM"),
        "sales": kw.get("sales", "Dana Reeve"),
        "status": kw.get("status", "Live"),
        "start": kw.get("start", "2026-08-01"),
        "end": kw.get("end", "2026-12-31"),
        "clarification": kw.get("clarification", ""),
        "assets_flag": kw.get("assets_flag", False),
        "assets_needed": kw.get("assets_needed", ""),
    }
    return row


def served(rows, *, source="knack", fields_version=knack_products.FIELDS_VERSION):
    """Point the report at fixture rows instead of Knack."""
    knack_products.rows = lambda *a, **k: {
        "rows": rows, "source": source, "age_minutes": 0,
        "count": len(rows), "fields_version": fields_version}


FIXTURE = [
    # Two lines on one campaign, each waiting on something different.
    product(id="a1", product="Programmatic Display", clarification="",
            assets_flag=True, assets_needed="Six banner sizes, 300x250 first"),
    product(id="a2", product="Digital Audio :30",
            clarification="Which phone number do they want on the spot?"),
    # Same client and campaign name, a different IO — a different flight.
    product(id="a3", io="2199", campaign="Annual", start="2027-01-01",
            end="2027-12-31", status="Scheduled",
            clarification="Confirm the 2027 budget split"),
    # A second partner, alphabetically first, so ordering is visible.
    product(id="b1", client="Ascend Construction", campaign="Spring Push",
            io="2216", partner="Commonwealth Broadcasting", sales="Alex Poe",
            assets_flag=True, assets_needed="Logo in vector"),
    # Same partner, a rep whose name sorts before the other one's.
    product(id="b2", client="DMR Roofing", campaign="Storm", io="2184",
            partner="Commonwealth Broadcasting", sales="Aaron Vance",
            clarification="Do they still service Clark County?"),
    # No media partner on the record at all.
    product(id="c1", client="Harris Lumber", campaign="Buy", io="2196",
            partner="", sales="Dana Reeve", assets_flag=True,
            assets_needed="Storefront photography"),
    # The trap: text with the box unticked.
    product(id="d1", client="Five Star Bath", campaign="Annual", io="2135",
            partner="STAR 99.1 FM", assets_flag=False,
            assets_needed="Maybe new creative next quarter?"),
    # Finished, and still carrying an ask.
    product(id="e1", client="Hern Marine", campaign="Boat Show", io="2001",
            partner="STAR 99.1 FM", status="Complete",
            start="2025-01-01", end="2025-06-30",
            clarification="Never got the dock photos"),
    # Nothing outstanding: must not appear at all.
    product(id="f1", client="Icon Solar", campaign="Evergreen", io="2222"),
]


def main():
    print("the tickbox is what says an asset is needed")
    served(FIXTURE)
    r = campaign_assets.report(today=TODAY)
    ok("field_2347 counts when field_2346 is ticked",
       campaign_assets.asset_ask(FIXTURE[0]) == "Six banner sizes, 300x250 first")
    check("and does not when it is not",
          campaign_assets.asset_ask(FIXTURE[6]), "")
    check("the unticked text is counted, not dropped", r["unticked"], 1)
    stray = [x for x in r["unticked_rows"] if x["client"] == "Five Star Bath"]
    ok("and named, so it is findable", bool(stray))
    check("with the rep on it, because it is a chase list too",
          stray[0]["sales"] if stray else None, "Dana Reeve")
    ok("and the media partner beside them",
       bool(stray) and stray[0]["partner"] == "STAR 99.1 FM")
    ok("but it is not listed as work",
       not any(c["client"] == "Five Star Bath" for c in r["campaigns"]),
       "an unticked note would read as an outstanding asset")

    print()
    print("sorted by media partner, then internal sales")
    names = [(c["partner"], c["sales"]) for c in r["campaigns"]]
    check("partner first, rep second, blank partner last", names, [
        ("Commonwealth Broadcasting", "Aaron Vance"),
        ("Commonwealth Broadcasting", "Alex Poe"),
        ("STAR 99.1 FM", "Dana Reeve"),
        ("STAR 99.1 FM", "Dana Reeve"),
        ("", "Dana Reeve"),
    ])
    check("grouped one card per partner",
          [g["partner"] for g in r["groups"]],
          ["Commonwealth Broadcasting", "STAR 99.1 FM",
           campaign_assets.NOT_RECORDED])
    ok("and the blank group says it is blank, not that it is a partner",
       r["groups"][-1]["recorded"] is False)

    print()
    print("one row per campaign, per insertion order")
    riverside = [c for c in r["campaigns"] if c["client"] == "Riverside HVAC"]
    check("two IOs of the same campaign name stay apart",
          sorted(c["io"] for c in riverside), ["2100", "2199"])
    this_year = [c for c in riverside if c["io"] == "2100"][0]
    check("both blocked lines sit under the one campaign",
          len(this_year["products"]), 2)
    check("counted as one clarification", this_year["clarifications"], 1)
    check("and one asset request", this_year["asset_asks"], 1)
    check("the flight spans the lines under it",
          (this_year["start"], this_year["end"]),
          ("2026-08-01", "2026-12-31"))
    ok("a campaign with nothing outstanding is absent",
       not any(c["client"] == "Icon Solar" for c in r["campaigns"]),
       "this list is the queue, not the client base")

    print()
    print("a campaign that has not started yet is the whole point")
    ok("next year's IO is on the list",
       any(c["io"] == "2199" for c in r["campaigns"]),
       "is_running() answers a different question — delivering today")
    ok("a finished campaign is not", not any(c["io"] == "2001" for c in r["campaigns"]))
    check("but it is counted rather than vanishing", r["closed_skipped"], 1)
    ok("and one toggle brings it back",
       any(c["io"] == "2001"
           for c in campaign_assets.report(scope="all", today=TODAY)["campaigns"]))

    print()
    print("prose is spent only on what the screen cannot say itself")
    check("a report that measured carries no blurb", r["note"], "")

    print()
    print("absent data reads as not measured, never as zero")
    served([{"id": "x", "client": "Icon Solar", "campaign": "Annual",
             "io": "1", "product": "Display"}], source="static export",
           fields_version=0)
    old = campaign_assets.report(today=TODAY)
    ok("a cache predating the fields is not measured", not old["measured"])
    check("and lists nothing rather than nothing-is-wrong", old["count"], 0)
    ok("the note says why", "nothing could be read from" in old["note"],
       old["note"])
    served([])
    empty = campaign_assets.report(today=TODAY)
    ok("no rows at all is also not measured", not empty["measured"])

    print()
    print("the cache cannot serve rows that predate the field map")
    ok("FIELDS_VERSION is stamped on a write",
       knack_products.FIELDS_VERSION >= 2)
    saw = {}
    knack_products._read_cache = lambda: {
        "fetched": __import__("time").time(), "count": 1,
        "fields_version": 1, "rows": [{"id": "old"}]}
    knack_products.configured = lambda: True
    knack_products.fetch = lambda *a, **k: (saw.setdefault("refetched", True),
                                            [{"id": "new", "assets_flag": False}])[1]
    knack_products._write_cache = lambda rows: None
    fresh = REAL_ROWS()
    ok("a fresh-looking cache with an old field map is refetched",
       saw.get("refetched") is True,
       "otherwise every campaign reads clear for the length of the TTL")
    check("and the new rows are served", [x["id"] for x in fresh["rows"]], ["new"])

    print()
    print("a Knack tickbox is read whichever way it is published")
    for raw, want in ((True, True), ("Yes", True), ("yes", True), (1, True),
                      ("true", True), (False, False), ("No", False),
                      ("", False), (None, False), (0, False)):
        ok(f"{raw!r} reads as {want}", knack_products._bool(raw) is want)

    print()
    print("the pinned ids are checkable, not merely pinned")
    fc = campaign_assets.field_check()
    check("all three are reported", len(fc["fields"]), 3)
    ok("with no Knack credentials they are labeled as ours",
       all(f["label_source"] == "house" for f in fc["fields"])
       if not fc["configured"] else True)
    check("the ids are the ones asked for",
          sorted(f["id"] for f in fc["fields"]),
          sorted([knack_products.F_CLARIFICATION, knack_products.F_ASSETS_FLAG,
                  knack_products.F_ASSETS_NEEDED]))
    check("and they are object_135's", fc["object"], "object_135")

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED")
        for f in FAILURES:
            print("  -", f)
        return 1
    print("the asset queue lists what is asked for, in the order asked for, "
          "and says when it could not read")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    finally:
        shutil.rmtree(_TMP, ignore_errors=True)
