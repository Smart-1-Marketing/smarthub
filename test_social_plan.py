"""hub/social_plan.py and the Social Content Planner — test harness.

    python3 test_social_plan.py

Same shape as test_target_areas.py and test_proposal_spec.py: no pytest, no new
dependencies, a throwaway SQLite database and a temporary data directory, so it
never touches /var/data or the real one.

## What is actually worth asserting here

Most of this module is a form. Three parts are not, and all three fail quietly
rather than loudly:

  * **The copy checks.** They are the reason this tool can be trusted with bulk
    work. A model that invents "$50 off through Friday" produces copy that
    reads perfectly, passes every syntax check, renders correctly and gets the
    client a phone call from someone holding us to an offer they never made.
    So the fixtures here are deliberately plausible — the failure mode is not
    gibberish, it is confident and wrong.

  * **The apportionment.** A 20-post month has to contain exactly 20 posts.
    Rounding each share independently gives 19 or 21 depending on the weights,
    and the person who asked for 20 has to work out which one to fix.

  * **Determinism.** Re-opening a plan must show the calendar the strategist
    left. A grid that reshuffles on reload is one nobody edits, because the
    edits appear to move.

Plus the ordinary regressions: the CSV must not export empty slots as empty
posts, deleting a plan must not leave a database copy to restore itself, and a
slot with no copy must read as `empty` rather than sprouting filler.
"""
import csv
import io
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

_TMP = tempfile.mkdtemp(prefix="s1-social-")
os.environ.setdefault("DATABASE_URL", "sqlite:///" + os.path.join(_TMP, "t.db"))
os.environ.setdefault("SECRET_KEY", "social-plan-test")
os.environ.setdefault("PANEL_PASSWORD", "test")
os.environ.setdefault("HUB_DATA_DIR", _TMP)

PASS = FAIL = 0


def check(label, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print("  ok   " + label)
    else:
        FAIL += 1
        print("  FAIL " + label + (("  — " + str(detail)) if detail else ""))


def section(title):
    print("\n" + title)
    print("-" * 60)


from hub import social_plan as sp                                  # noqa: E402

# ---------------------------------------------------------------------------
section("The calendar")

# September 2026 begins on a Tuesday and has 30 days.
slots = sp.build_grid("2026-09", channels=["facebook", "instagram"], per_week=3)
check("three posts a week lands on Mon/Wed/Fri only",
      all(__import__("datetime").date.fromisoformat(s["date"]).weekday() in (0, 2, 4)
          for s in slots))
check("and fills the month", len(slots) == 13, len(slots))
check("slot ids are unique", len({s["id"] for s in slots}) == len(slots))
check("every slot starts empty",
      all(s["status"] == "empty" and s["copy"] == "" for s in slots))
check("no slot invents an image",
      all(s["image_url"] == "" for s in slots))
check("both channels ride on one slot rather than doubling the grid",
      all(s["channels"] == ["facebook", "instagram"] for s in slots))

again = sp.build_grid("2026-09", channels=["facebook", "instagram"], per_week=3)
check("building the same plan twice gives the same calendar", again == slots)

blacked = sp.build_grid("2026-09", channels=["facebook"], per_week=3,
                        blackout=["2026-09-07"])
check("a blackout date is removed, not shifted",
      len(blacked) == len(slots) - 1 and
      all(s["date"] != "2026-09-07" for s in blacked))

check("one post a week is a quarter of five",
      len(sp.build_grid("2026-09", channels=["facebook"], per_week=1)) == 5)
check("posts are spread through the day, not all at 09:15",
      len({s["time"] for s in slots}) > 1)

for bad in ("", "2026", "2026-13", "sept"):
    try:
        sp.build_grid(bad, channels=["facebook"])
        check(f"a bad month ({bad!r}) is refused", False)
    except ValueError:
        check(f"a bad month ({bad!r}) is refused", True)

# An unselectable channel must not silently produce an empty plan.
check("an unknown channel falls back to the defaults rather than nothing",
      sp.build_grid("2026-09", channels=["myspace"])[0]["channels"]
      == list(sp.DEFAULT_CHANNELS))

# ---------------------------------------------------------------------------
section("The mix")

for total in (0, 1, 4, 7, 13, 20, 31):
    counts = sp.mix_counts(total)
    check(f"{total} posts apportion to exactly {total}",
          sum(counts.values()) == total, counts)

check("a type with no weight never appears",
      "hiring" not in sp.mix_counts(30))
check("zeroing a type out redistributes rather than shrinking the month",
      sum(sp.mix_counts(20, {"promo": 0, "educational": 5, "faq": 5}).values()) == 20)
check("a single type takes the whole month",
      sp.mix_counts(12, {"promo": 1}) == {"promo": 12})

seq = sp.type_sequence(20)
check("the sequence is as long as the month", len(seq) == 20)
check("its counts match the apportionment",
      {k: seq.count(k) for k in set(seq)} == sp.mix_counts(20))
adjacent = sum(1 for i in range(1, len(seq)) if seq[i] == seq[i - 1])
check("no two posts of the same type run back to back", adjacent == 0, seq)
check("the sequence is deterministic", sp.type_sequence(20) == seq)
# With only one type there is nothing to interleave — it must not loop forever
# or drop posts trying.
check("an unavoidable repeat still produces a full month",
      sp.type_sequence(5, {"promo": 1}) == ["promo"] * 5)

# ---------------------------------------------------------------------------
section("The copy checks")

FACTS = {"offers": "$89 seasonal tune-up through September",
         "phone": "(555) 555-0100", "url": "https://example.com"}


def codes(text, channels=("facebook",), facts=FACTS):
    return {f["code"] for f in sp.validate_copy(text, channels=channels, facts=facts)}


def level_of(text, code, channels=("facebook",), facts=FACTS):
    for f in sp.validate_copy(text, channels=channels, facts=facts):
        if f["code"] == code:
            return f["level"]
    return ""


check("empty copy raises nothing at all", sp.validate_copy("", facts=FACTS) == [])
check("a clean post raises nothing",
      codes("Cooler mornings are here. A quick furnace check now saves a "
            "cold week later. Call us to book a visit.") == set())

check("an offer we were given is allowed",
      "price" not in codes("Our $89 seasonal tune-up runs through September."))
check("an offer nobody authorized is blocked",
      level_of("Save with our $50 off any repair this month.", "price") == "block")
check("a discount nobody supplied is blocked whichever way it is written",
      level_of("Take 20% off your next visit.", "price") == "block")
check("and a bare percentage that is not an offer is still flagged to check",
      level_of("Our units run 20% more efficient than the old ones.",
               "percent") == "warn")
check("the phone on file is allowed even spelled differently",
      "phone" not in codes("Call 555-555-0100 to book."))
check("a different phone number is blocked",
      level_of("Call 555-867-5309 to book.", "phone") == "block")
check("an invented deadline is blocked",
      level_of("Book by Friday — this offer ends soon.", "deadline") == "block")
check("an unprovable superlative is a warning, not a block",
      level_of("We are the best in town for furnace work.", "superlative") == "warn")
check("an unfilled placeholder is blocked",
      level_of("Welcome to [INSERT CLIENT NAME], your local experts.",
               "placeholder") == "block")
check("the model breaking character is caught",
      "placeholder" in codes("As an AI language model I would suggest..."))
check("Smart 1 Labs never reaches a client",
      level_of("Built with Smart 1 Labs technology.", "banned") == "block")

long_post = "Cooler mornings are here. " * 20
check("a post over X's hard limit is blocked",
      level_of(long_post, "length", channels=("x",)) == "block")
check("the same post is merely long on Facebook",
      level_of(long_post, "long", channels=("facebook",)) == "warn")
check("hashtag sprawl is flagged per channel",
      "hashtags" in codes("Great day on site. " + " ".join("#tag%d" % i for i in range(9)),
                          channels=("x",)))
check("the same hashtags are fine on Instagram",
      "hashtags" not in codes("Great day on site. " +
                              " ".join("#tag%d" % i for i in range(9)),
                              channels=("instagram",)))

# With nothing authorised at all, every commercial claim has to be blocked —
# this is the common case, because most months have no offer.
check("with no offers supplied, any price is blocked",
      level_of("Just $99 today.", "price", facts={"offers": ""}) == "block")

# ---------------------------------------------------------------------------
section("Slots, images and batch counts")

slot = {"id": "s01", "date": "2026-09-02", "time": "09:15",
        "channels": ["instagram"], "type": "promo",
        "copy": "A quiet morning on site.", "hashtags": [], "link": "",
        "image_url": "", "status": "drafted", "flags": []}
check("Instagram with no image is blocked",
      any(f["code"] == "asset" for f in sp.validate_slot(slot, FACTS)))
slot["image_url"] = "https://res.cloudinary.com/demo/x.jpg"
check("and passes once an image is attached",
      not any(f["code"] == "asset" for f in sp.validate_slot(slot, FACTS)))
check("an empty slot is not nagged about a missing image",
      sp.validate_slot(dict(slot, copy="", image_url=""), FACTS) == [])

batch = {"brief": FACTS, "slots": [
    dict(slot, id="s01"),
    dict(slot, id="s02", copy="Save $50 today.", image_url=""),
    dict(slot, id="s03", copy="", status="empty"),
]}
counts = sp.validate_batch(batch)
check("the batch counts what is written", counts["drafted"] == 2, counts)
check("and counts the blocking problems", counts["block"] >= 2, counts)
check("re-validating is idempotent", sp.validate_batch(batch) == counts)

# ---------------------------------------------------------------------------
section("Export")

export_batch = {"month": "2026-09", "brief": FACTS, "slots": [
    {"id": "s01", "date": "2026-09-02", "time": "09:15",
     "channels": ["facebook"], "type": "educational",
     "copy": "A quick furnace check now saves a cold week later.",
     "hashtags": ["hvac", "#localbusiness"], "link": "https://example.com/book",
     "image_url": "https://res.cloudinary.com/demo/a.jpg", "status": "approved",
     "flags": []},
    {"id": "s02", "date": "2026-09-04", "time": "12:30",
     "channels": ["facebook"], "type": "promo", "copy": "", "hashtags": [],
     "link": "", "image_url": "", "status": "empty", "flags": []},
]}

planner = sp.planner_csv(export_batch)
# Read as CSV, not as lines: a post's copy and its hashtags are one field with a
# blank line between them, so splitlines() turns one record into three and an
# export that is correct looks broken.
rows = list(csv.reader(io.StringIO(planner)))
check("the planner CSV header is the documented column set",
      rows[0] == list(sp.PLANNER_COLUMNS), rows[0])
check("an empty slot is not exported as an empty post", len(rows) == 2, rows)
check("the date is written the way Social Planner reads it",
      rows[1][0] == "09/02/2026 09:15", rows[1][0])
check("the copy and its hashtags travel as one field",
      "furnace check" in rows[1][1] and "#hvac" in rows[1][1], rows[1][1])
check("the link travels in the OG meta column",
      rows[1][2] == "https://example.com/book", rows[1][2])
check("the image travels in the media column",
      rows[1][3] == "https://res.cloudinary.com/demo/a.jpg", rows[1][3])

text = sp.post_text(export_batch["slots"][0])
check("hashtags are appended to the copy, not lost", "#hvac" in text)
check("a hashtag typed without its # gets one", text.count("#hvac") == 1, text)
check("one already carrying a # is not doubled", "##" not in text, text)
check("a post with no hashtags is just the copy",
      sp.post_text({"copy": "Hello", "hashtags": []}) == "Hello")

review_rows = list(csv.reader(io.StringIO(sp.review_csv(export_batch))))
check("the review sheet keeps the empty slot, because that is its job",
      len(review_rows) == 3, review_rows)
check("the review sheet carries the flag column",
      review_rows[0] == list(sp.REVIEW_COLUMNS), review_rows[0])

# ---------------------------------------------------------------------------
section("The module: storage, routes and the CSV endpoint")

from modules.social_planner import app as mod                      # noqa: E402

mod._client_context = lambda client, url="": {                     # noqa: E731
    "client": client, "url": url, "domain": "example.com", "industry": "HVAC",
    "description": "Residential heating and cooling.", "products": ["SEO"],
    "colors": ["#1A2E58"], "logo": "", "gallery": [], "gallery_note": "none",
    "brand_note": "",
}

client = mod.app.test_client()

r = client.get("/health")
check("the module answers /health", r.status_code == 200 and r.get_json()["ok"])

r = client.get("/")
check("the page renders", r.status_code == 200 and b"Social Content Planner" in r.data)
check("and hands the browser its vocabulary as JSON, not as inlined script",
      b'type="application/json" id="boot"' in r.data)

r = client.post("/api/batches", json={
    "client": "Riverstone Heating", "month": "2026-09",
    "channels": ["facebook", "google_business"], "per_week": 2,
    "brief": {"offers": "$89 tune-up", "notes": "Busy season starting."}})
made = r.get_json()
check("a plan can be created", r.status_code == 200 and made["ok"], made)
batch_id = made["batch"]["id"] if made.get("ok") else ""
check("it is filed against the client",
      made["batch"]["client"] == "Riverstone Heating")
check("the authorized offer is stored on the plan, not just in the prompt",
      made["batch"]["brief"]["offers"] == "$89 tune-up")

r = client.post("/api/batches", json={"client": "X", "month": "2026-09",
                                      "channels": []})
check("a plan with no channel is refused", r.status_code == 400)
r = client.post("/api/batches", json={"client": "", "month": "2026-09",
                                      "channels": ["facebook"]})
check("a plan with no client is refused", r.status_code == 400)

r = client.get("/api/batches/" + batch_id)
check("it reads back", r.status_code == 200 and r.get_json()["batch"]["id"] == batch_id)
check("a made-up id is a 404, not a traceback",
      client.get("/api/batches/deadbeef99").status_code == 404)
check("and a malformed one cannot walk out of the data directory",
      client.get("/api/batches/..%2F..%2Fetc").status_code in (400, 404))

first = made["batch"]["slots"][0]["id"]
r = client.put("/api/batches/" + batch_id, json={"slots": [
    {"id": first, "copy": "Save $500 on anything, this Friday only.",
     "hashtags": ["#hvac"]}]})
saved = r.get_json()
edited = [s for s in saved["batch"]["slots"] if s["id"] == first][0]
check("an edit saves", edited["copy"].startswith("Save $500"))
check("editing marks the slot edited, not drafted", edited["status"] == "edited")
check("and the flags are recomputed on save, not only on draft",
      any(f["level"] == "block" for f in edited["flags"]), edited["flags"])

r = client.post("/api/batches/" + batch_id + "/status", json={"status": "approved"})
check("a plan with a blocking flag cannot be approved", r.status_code == 400)
check("the refusal says how many are blocking",
      "blocking flag" in (r.get_json().get("error") or ""))

client.put("/api/batches/" + batch_id, json={"slots": [
    {"id": first, "copy": "Cooler mornings are here — book a furnace check."}]})
r = client.post("/api/batches/" + batch_id + "/status", json={"status": "approved"})
check("and can be approved once it is clean", r.status_code == 200,
      r.get_json())

r = client.get("/api/batches/" + batch_id + "/export.csv")
check("the CSV endpoint serves a CSV",
      r.status_code == 200 and r.mimetype == "text/csv")
check("named for the client and the month",
      "riverstone-heating-2026-09" in r.headers.get("Content-Disposition", ""),
      r.headers.get("Content-Disposition"))
check("carrying the one post that has copy",
      b"Cooler mornings" in r.data)

r = client.get("/api/batches/" + batch_id + "/export.csv?format=review")
check("and a review sheet on request", b"Needs attention" in r.data)

index_rows = client.get("/api/batches").get_json()["batches"]
check("the plan appears in the index", any(b["id"] == batch_id for b in index_rows))
check("the index row counts rather than trusting a stored total",
      [b for b in index_rows if b["id"] == batch_id][0]["drafted"] == 1)

check("deleting removes it", client.delete("/api/batches/" + batch_id).status_code == 200)
check("and it does not restore itself from the database mirror",
      mod.load_batch(batch_id) is None)
check("nor linger in the index",
      not any(b["id"] == batch_id for b in
              client.get("/api/batches").get_json()["batches"]))
check("deleting it twice is a 404, not a 500",
      client.delete("/api/batches/" + batch_id).status_code == 404)

# ---------------------------------------------------------------------------
print("\nTone is a set of options, and each one instructs")
# ---------------------------------------------------------------------------
# A free-text tone box got one of three answers — nothing, "professional", or a
# sentence pasted since 2023 — so the whole month came out in one register. The
# options exist to carry the *guidance*, not the label.
check("every tone option carries an instruction, not just a name",
      all(t.get("guidance") and len(t["guidance"]) > 20
          for t in sp.TONES.values()))
guide = sp.tone_guidance(["friendly", "straight"], "no exclamation marks")
check("picked tones are combined rather than ranked",
      "neighborly" in guide and "Plain and direct" in guide, guide)
check("and the strategist's own words survive alongside them",
      "no exclamation marks" in guide, guide)
check("an unknown tone key is ignored rather than passed through",
      sp.tone_guidance(["not-a-tone"]) == "")
check("the browser is handed the tones with their guidance",
      all(t.get("guidance") for t in sp.spec_payload()["tones"]))


# ---------------------------------------------------------------------------
print("\nSocial media holidays: dated, filtered, and ours")
# ---------------------------------------------------------------------------
# The moving ones are computed. A hard-coded Thanksgiving is correct for one
# year and quietly wrong every year after it, which is the worst kind of wrong
# — the calendar still renders.
nov = {h["name"]: h["date"] for h in sp.holidays_for("2026-11")}
check("Thanksgiving 2026 is the fourth Thursday",
      nov.get("Thanksgiving") == "2026-11-26", nov.get("Thanksgiving"))
check("Black Friday is the day after it",
      nov.get("Black Friday") == "2026-11-27", nov.get("Black Friday"))
check("Veterans Day is fixed",
      nov.get("Veterans Day") == "2026-11-11", nov.get("Veterans Day"))
may = {h["name"]: h["date"] for h in sp.holidays_for("2026-05")}
check("Mother's Day 2026 is the second Sunday",
      may.get("Mother's Day") == "2026-05-10", may.get("Mother's Day"))
check("Memorial Day is the last Monday",
      may.get("Memorial Day") == "2026-05-25", may.get("Memorial Day"))
check("and 2027's Thanksgiving is not 2026's",
      {h["name"]: h["date"] for h in sp.holidays_for("2027-11")}
      ["Thanksgiving"] == "2027-11-25")

names = [h["name"] for h in sp.holidays_for("2026-02", ["hvac"])]
check("a day tagged for retail is not offered to a trade client",
      "Valentine's Day" not in names, names)
check("...but an untagged day is offered to everyone",
      "Groundhog Day" in names, names)
check("every day says the list is ours rather than an authority",
      all(h["source"] == "house" for h in sp.holidays_for("2026-12")))
check("the source note reaches the browser",
      "no authority" in sp.spec_payload()["holiday_source"])

grid = sp.build_grid("2026-11", channels=["facebook"], per_week=3,
                              holidays=sp.holidays_for("2026-11"))
marked = [s for s in grid if s.get("holiday")]
check("every holiday in the month lands on a slot", len(marked) == 4, len(marked))
check("a holiday slot is filed as seasonal, which is what it is",
      all(s["type"] == "seasonal" for s in marked))
check("ids stay sequential in date order once days are inserted",
      [s["id"] for s in grid] == [f"s{i+1:02d}" for i in range(len(grid))])
check("the dates stay sorted", [s["date"] for s in grid] == sorted(s["date"] for s in grid))
blacked = sp.build_grid("2026-11", channels=["facebook"], per_week=3,
                                 blackout=["2026-11-26"],
                                 holidays=sp.holidays_for("2026-11"))
check("a blacked-out date does not come back as a holiday",
      not any(s["date"] == "2026-11-26" for s in blacked))
plain = sp.build_grid("2026-11", channels=["facebook"], per_week=3)
check("and a plan that did not ask for holidays gets none",
      not any(s.get("holiday") for s in plain))


# ---------------------------------------------------------------------------
print("\nWhat to promote this month reaches the writer")
# ---------------------------------------------------------------------------
batch = {"brief": {"tones": ["friendly"], "promote": ["Furnace tune-ups", "Duct cleaning"],
                   "offers": ""},
         "slots": []}
slot = {"id": "s01", "date": "2026-11-26", "type": "seasonal",
        "channels": ["facebook"], "holiday": "Thanksgiving"}
prompt = sp.draft_messages(batch, slot, {"client": "Riverstone Heating"})[-1]["content"]
check("the focus list is in the prompt", "Furnace tune-ups" in prompt, prompt[:200])
check("it is a focus, not a mandate on every post",
      "do not force them" in prompt)
check("the day being marked is named", "Thanksgiving" in prompt)
check("and the model is told not to invent an offer for it",
      "Claim no offer or event" in prompt)
check("the tone instruction travels with it", "neighborly" in prompt)

flags = sp.validate_copy(
    "Book a furnace tune-up before the cold sets in.",
    channels=["facebook"], facts=batch["brief"])
check("a service named in the promote list is not flagged as invented",
      not [f for f in flags if f["level"] == "block"], flags)


# ---------------------------------------------------------------------------
print("\n" + "-" * 60)
print(f"{PASS} passed, {FAIL} failed")
shutil.rmtree(_TMP, ignore_errors=True)
sys.exit(1 if FAIL else 0)
