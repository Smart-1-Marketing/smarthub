"""hub/social_content.py and the intake/ideas/push half of the planner.

    python3 test_social_content.py

Same shape as test_social_plan.py: no pytest, no new dependencies, a
throwaway SQLite database and a temporary data directory, so it never touches
/var/data or the real one.

## What is worth asserting here, and what is not

Most of this is a form and a queue. Six things are not, and every one of them
fails *quietly* — the page renders, the link resolves, the button reports
success, and the answer is wrong:

  * **A client's own words are authorization.** The copy checks block a price
    or a deadline nobody supplied. A location manager typing "$50 off through
    Friday" into the request form *is* the supply, and a tool that blocks the
    client's own offer on the client's own request reads to a strategist as
    broken rather than as careful. Nothing errors either way.

  * **A duplicate flag must never cross a client.** Two businesses wanting the
    same Friday is a Friday. Flagged across clients, the queue fills with
    pairings nobody can act on and people stop reading the colour.

  * **A failed push must leave the post approved.** A client-approved post
    that quietly reads as scheduled is gone, and the queue says it is handled.
    That is the single worst outcome available to this module and it is
    invisible from both ends.

  * **A missing Suite connection degrades, never 500s.** Nothing here has a
    Suite token on a fresh checkout, which is exactly the state a new
    deployment is in.

  * **The client's four pages are reachable with no login, and the staff ones
    are not.** Both halves: a client-facing page behind AuthGuard is a login
    form in front of somebody who has no account, and a staff queue outside it
    is every client's name and every request answering 200 to anyone with the
    URL — which is the hole `modules/commercial_builder` shipped with.

  * **A promotion carries the ask across.** Without `source_request_id` the
    request is a dead form entry and somebody re-reads the whole queue to
    work out which ones were done.
"""
import os
import shutil
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

_TMP = tempfile.mkdtemp(prefix="s1-social-content-")
os.environ.setdefault("DATABASE_URL", "sqlite:///" + os.path.join(_TMP, "t.db"))
os.environ.setdefault("SECRET_KEY", "social-content-test")
os.environ.setdefault("PANEL_PASSWORD", "test")
os.environ.setdefault("HUB_DATA_DIR", _TMP)

PASS = FAIL = 0


def check(label, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print("  ok    " + label)
    else:
        FAIL += 1
        print("  FAIL  " + label + (("  — " + str(detail)) if detail else ""))


from hub import social_content as sc                              # noqa: E402
from hub import social_plan as sp                                 # noqa: E402
from modules.social_planner import (agent, app as mod, ideas,     # noqa: E402
                                    intake, links, suite_client)

CLIENT = "Riverstone Heating"
CLIENT_URL = "riverstoneheating.com"
OTHER = "Buckeye Marina"
OTHER_URL = "buckeyemarina.com"


def days(n):
    return (date.today() + timedelta(days=n)).isoformat()


# ---------------------------------------------------------------------------
print("\nThe spec describes itself, and says so when it stops")
# ---------------------------------------------------------------------------
check("check_spec() is green", sc.check_spec() == [], sc.check_spec())
check("every flow step is a real status",
      all(s in sc.REQUEST_STATUSES for s in sc.REQUEST_FLOW))
check("every idea tag carries a prompt for the model",
      all(v.get("prompt") for v in sc.IDEA_TAGS.values()))
check("the copy checks are the planner's, not a second copy",
      sc.MONEY_RE is sp.MONEY_RE and sc.PHONE_RE is sp.PHONE_RE)
check("a status that must never follow a failed push is a real status",
      all(s in sc.POST_STATUSES for s in sc.NEVER_ON_FAILURE))
check("the guardrails are on the page as data, not prose in a template",
      len(sc.GUARDRAILS) >= 5)


# ---------------------------------------------------------------------------
print("\nA client's own words are authorization")
# ---------------------------------------------------------------------------
request_row = {"copy_suggestion": "$50 off any tune-up through Friday.",
               "notes": "Call the shop on (317) 555-0142."}
allowed = sc.authorized_text(request_row)
check("what they typed reaches the allowed set", "$50 off" in allowed)
check("so does what they put in the notes", "555-0142" in allowed)

facts = sc.request_facts(request_row, {"notes": "Never mention the old brand."})
blocked = [f for f in sp.validate_copy("$50 off any tune-up through Friday.",
                                       channels=["facebook"], facts=facts)
           if f["level"] == "block"]
check("their own offer is not blocked as invented", not blocked, blocked)
check("the standing brief survives beside it",
      "old brand" in facts.get("notes", ""))

invented = [f for f in sp.validate_copy("$300 off, this week only!",
                                        channels=["facebook"], facts=facts)
            if f["level"] == "block"]
check("a price nobody supplied is still blocked", invented)

slot = {"channels": ["facebook"], "image_url": "x",
        "copy": "$50 off any tune-up through Friday.",
        "supplied": allowed}
check("validate_slot reads the slot's own supplied text",
      not [f for f in sp.validate_slot(slot, {}) if f["level"] == "block"],
      sp.validate_slot(slot, {}))
check("and a slot with no supplied text is unaffected",
      [f for f in sp.validate_slot(dict(slot, supplied=""), {})
       if f["level"] == "block"])


# ---------------------------------------------------------------------------
print("\nA location that is not set up never blocks a submission")
# ---------------------------------------------------------------------------
free = intake.submit(CLIENT, CLIENT_URL, payload={
    "request_type": "post", "location_label": "The one on Route 32",
    "copy_suggestion": "New van, new livery.", "requested_date_mode": "asap"})
check("a request with no location id is still taken", free["id"])
check("what they typed is kept", free["location_label"] == "The one on Route 32")
check("and it carries no location id to pretend otherwise", free["location_id"] == "")

west = intake.add_location(CLIENT, CLIENT_URL, name="Westside",
                           contact_email="west@example.com", actor="tester")
check("a location can be added", west["id"].startswith("loc-"))
check("adding the same name twice returns the first rather than a second row",
      intake.add_location(CLIENT, CLIENT_URL, name="westside")["id"] == west["id"])

placed = intake.submit(CLIENT, CLIENT_URL, payload={
    "request_type": "promo", "location_id": west["id"],
    "copy_suggestion": "$50 off tune-ups through Friday.",
    "requested_date_mode": "specific_date",
    "requested_date_start": days(5), "asset_refs": ["https://cdn/x.jpg"]})
check("a request against a real location carries its id",
      placed["location_id"] == west["id"])
check("and takes the location's name rather than whatever was typed",
      placed["location_label"] == "Westside")

stolen = intake.submit(OTHER, OTHER_URL, payload={
    "request_type": "post", "location_id": west["id"]})
check("a location id from another client's link is refused, not re-filed",
      stolen["location_id"] == "")

rows = intake.for_client(CLIENT, CLIENT_URL)
check("both of this client's requests are in their queue", len(rows) == 2, rows)
check("the other client's is not",
      all(r["client"] == CLIENT for r in rows))
check("a location-filtered view does not crash on the free-text row",
      len([r for r in rows if r["location_label"] == "Westside"]) == 1)
check("the summary counts the typed location under its own label",
      any(r["location"] == "The one on Route 32"
          for r in intake.summary(CLIENT, CLIENT_URL)["by_location"]))


# ---------------------------------------------------------------------------
print("\nOverdue and duplicate are advisory, computed on read, and never cross a client")
# ---------------------------------------------------------------------------
check("ASAP is never overdue — it named no day",
      not sc.is_overdue({"status": "new", "requested_date_mode": "asap"}))
past = {"status": "new", "requested_date_mode": "specific_date",
        "requested_date_start": days(-3), "requested_date_end": days(-3)}
check("a date that has passed is overdue", sc.is_overdue(past))
check("a declined request is not overdue — it has been answered",
      not sc.is_overdue(dict(past, status="declined")))
check("nor is one already scheduled",
      not sc.is_overdue(dict(past, status="scheduled")))

a = {"id": "a", "client": CLIENT, "status": "new",
     "requested_date_mode": "specific_date", "requested_date_start": days(10),
     "requested_date_end": days(10)}
b = {"id": "b", "client": CLIENT, "status": "new",
     "requested_date_mode": "specific_date", "requested_date_start": days(11),
     "requested_date_end": days(11)}
c = {"id": "c", "client": OTHER, "status": "new",
     "requested_date_mode": "specific_date", "requested_date_start": days(10),
     "requested_date_end": days(10)}
flags = sc.duplicate_flags([a, b, c])
check("two requests from one client on adjacent days are flagged both ways",
      flags.get("a") == ["b"] and flags.get("b") == ["a"], flags)
check("another client's identical date is never paired with them",
      "c" not in flags, flags)
far = dict(b, id="d", requested_date_start=days(40), requested_date_end=days(40))
check("a date well outside the window raises nothing",
      "d" not in sc.duplicate_flags([a, far]))
check("a scheduled request is not a duplicate of anything",
      sc.duplicate_flags([a, dict(b, status="scheduled")]) == {})
check("the window is a setting, not a number in the code",
      sc.duplicate_window_days() == 2)


# ---------------------------------------------------------------------------
print("\nA turnaround time is measured or it is not promised")
# ---------------------------------------------------------------------------
note = sc.turnaround_note([])
check("with no history it is not measured", note["measured"] is False)
check("and it says so rather than quoting a plausible number",
      "don't quote" in note["line"] or "not" in note["line"].lower())
made = datetime.now(timezone.utc) - timedelta(hours=30)
history = [{"created_at": made.isoformat(),
            "triaged_at": (made + timedelta(hours=5)).isoformat()}
           for _ in range(4)]
note = sc.turnaround_note(history)
check("with history it is measured", note["measured"] is True, note)
check("and it says the figure is what happened, not a promise",
      "not a promise" in note["line"], note["line"])


# ---------------------------------------------------------------------------
print("\nTriage, decline and the audit trail back to the ask")
# ---------------------------------------------------------------------------
try:
    intake.decline(placed["id"], "", "tester")
    refused = False
except ValueError:
    refused = True
check("declining with no reason is refused", refused)

third = intake.submit(CLIENT, CLIENT_URL, payload={"request_type": "other"})
intake.decline(third["id"], "We ran this in March.", "tester")
check("a declined request keeps the reason",
      intake.get(third["id"])["declined_reason"] == "We ran this in March.")

intake.mark_triaged(placed["id"], "tester")
first_stamp = intake.get(placed["id"])["triaged_at"]
intake.mark_triaged(placed["id"], "somebody else")
check("triage is stamped once, so the turnaround figure cannot be fiddled",
      intake.get(placed["id"])["triaged_at"] == first_stamp)

intake.link_post(placed["id"], "batch1", "r01", "tester")
linked = intake.get(placed["id"])
check("the request records the post it became",
      linked["linked_batch_id"] == "batch1" and linked["linked_slot_id"] == "r01")

intake.sync_from_post(placed["id"], "approved")
check("and moves with it", intake.get(placed["id"])["status"] == "scheduled")
intake.sync_from_post(placed["id"], "drafted")
check("but never backwards when a strategist un-approves a slot",
      intake.get(placed["id"])["status"] == "scheduled")
intake.sync_from_post(placed["id"], "published")
check("forward to posted when it goes out",
      intake.get(placed["id"])["status"] == "posted")
intake.sync_from_post(third["id"], "approved")
check("a declined request is not walked forward by a post it never became",
      intake.get(third["id"])["status"] == "declined")

overdue_row = intake.submit(CLIENT, CLIENT_URL, payload={
    "request_type": "post", "location_label": "Eastside",
    "requested_date_mode": "specific_date",
    "requested_date_start": days(-4), "requested_date_end": days(-4)})
queue = intake.open_requests(CLIENT, CLIENT_URL)
check("the overdue request heads the queue, whatever order it arrived in",
      queue[0]["id"] == overdue_row["id"], [r["id"] for r in queue])
check("and an ASAP request with no day at all sorts last",
      queue[-1]["id"] == free["id"], [r["id"] for r in queue])
check("a declined request is off the open queue entirely",
      third["id"] not in [r["id"] for r in queue])


# ---------------------------------------------------------------------------
print("\nTag weights are one line of arithmetic, and the mix never comes back empty")
# ---------------------------------------------------------------------------
check("no answers is not a certainty", sc.tag_weight(0, 0) == 0.0)
check("one like is not a certainty either", sc.tag_weight(1, 0) == 0.5)
check("and it is reproducible from the two counts",
      sc.tag_weight(3, 1) == round(3 / 5, 4))
weights = sc.apply_response({}, "promo", "liked")
check("a like is folded into the counts", weights["promo"]["liked_count"] == 1)
check("a tag nothing knows about changes nothing",
      sc.apply_response({}, "not-a-tag", "liked") == {})

mix = sc.idea_mix(None, size=6)
check("a brand-new client still gets a full batch", len(mix) == 6, mix)
check("every tag in it is real", all(t in sc.IDEA_TAGS for t in mix))
mix = sc.idea_mix({"promo": {"liked_count": 5, "passed_count": 0, "weight": 0.83}},
                  size=4, wanted=["team_spotlight"])
check("a tag the client asked for outranks the weighting",
      mix[0] == "team_spotlight", mix)
check("and the weighted one is still in the batch", "promo" in mix, mix)
check("exploration is a share, not a number in the code",
      0.0 <= sc.explore_ratio() <= 0.6)
heavy = {"promo": {"liked_count": 9, "passed_count": 0, "weight": 0.9}}
explored = sc.idea_mix(heavy, size=3, explore=0.34)
check("with exploration on, a tag nobody has answered on still gets in",
      any(t != "promo" for t in explored), explored)
check("and with it off, the client's own weighting leads",
      sc.idea_mix(heavy, size=3, explore=0.0)[0] == "promo",
      sc.idea_mix(heavy, size=3, explore=0.0))


# ---------------------------------------------------------------------------
print("\nIdeas: a swipe steers the mix, and answering twice is answering once")
# ---------------------------------------------------------------------------
idea = ideas.add(CLIENT, CLIENT_URL, title="Meet the crew who fit your furnace",
                 idea_tag="team_spotlight", origin="staff")
check("an idea starts unanswered", idea["client_response"] == "pending")
ideas.respond(idea["id"], "liked")
check("a like is recorded",
      ideas.get(idea["id"])["client_response"] == "liked")
check("and reaches the client's weights",
      ideas.weights(CLIENT, CLIENT_URL)["team_spotlight"]["liked_count"] == 1)
ideas.respond(idea["id"], "liked")
check("a second tap does not count twice",
      ideas.weights(CLIENT, CLIENT_URL)["team_spotlight"]["liked_count"] == 1)
check("the weight table names every tag, answered or not",
      len(ideas.weight_table(CLIENT, CLIENT_URL)) == len(sc.IDEA_TAGS))

prefs = ideas.save_preferences(CLIENT, CLIENT_URL,
                               topics_wanted=["promo", "not-a-tag"],
                               topics_avoid="asbestos, our old brand",
                               standing_notes="New depot opens in March.")
check("only real tags are kept", prefs["topics_wanted"] == ["promo"])
check("the do-not-mention list is kept as typed",
      "asbestos" in prefs["topics_avoid"])
check("saving preferences never touches the swipe record",
      ideas.weights(CLIENT, CLIENT_URL)["team_spotlight"]["liked_count"] == 1)

batch = ideas.generate(CLIENT, CLIENT_URL, context={"client": CLIENT}, size=4)
check("a batch is produced even with no model reachable", batch["ideas"])
check("and it says which it got rather than looking like a failure",
      batch["source"] in ("model", "house") and
      (batch["source"] == "model" or batch["note"]), batch["note"])
check("nothing in it names something on the do-not-mention list",
      not any("asbestos" in i["title"].lower() for i in batch["ideas"]))


# ---------------------------------------------------------------------------
print("\nThe Suite push: asked before it is tried, and a failure never overstates")
# ---------------------------------------------------------------------------
state = suite_client.preflight(CLIENT, CLIENT_URL)
check("with nothing configured, pushing is not offered", state["ready"] is False)
check("and the reason is a sentence rather than a blank", state["detail"])
check("the CSV is named as the route that does work",
      "CSV" in state["fallback"])
check("no token value is anywhere in what a page is handed",
      "token" not in state)

from hub import suite_accounts                                    # noqa: E402
found = suite_accounts.location_for("Nobody At All")
check("a client with no mapping is one of three answers, never a bool",
      found["state"] in ("not_connected", "not_measured"), found)
check("and it never claims connected", found["connected"] is False)
pub = suite_accounts.publishing()
check("publishing is tri-state", set(pub) >= {"ready", "known", "detail"})
check("it is not ready on a deployment with no Suite consent",
      pub["ready"] is False)

pushed = {"id": "s01", "status": "approved", "delivery": "approved"}
suite_client.apply_push_result(pushed, {"ok": False, "error": "Suite refused."})
check("a failed push leaves the post approved",
      pushed["delivery"] == "approved")
check("never scheduled or published",
      pushed["delivery"] not in sc.NEVER_ON_FAILURE)
check("and the error is kept on it", pushed["delivery_error"] == "Suite refused.")
suite_client.apply_push_result(pushed, {"ok": True, "ghl_post_id": "p-9"})
check("a real push records the id", pushed["ghl_post_id"] == "p-9")
check("and clears the error", pushed["delivery_error"] == "")

mapped, unmapped = suite_client.platforms_for(["facebook", "google_business",
                                               "carrier_pigeon"])
check("channels map to Suite's own platform names",
      "facebook" in mapped and "google" in mapped, mapped)
check("a channel with no mapping is named rather than silently dropped",
      unmapped == ["carrier_pigeon"], unmapped)


# ---------------------------------------------------------------------------
print("\nOne link per client, derived rather than stored")
# ---------------------------------------------------------------------------
token = links.token_for(CLIENT, CLIENT_URL)
check("a token is produced", bool(token))
check("it is the same string every time", links.token_for(CLIENT, CLIENT_URL) == token)
check("it round-trips to the client", links.client_for(token) == (CLIENT, CLIENT_URL))
check("a made-up token resolves to nobody", links.client_for("nonsense") is None)
check("a tampered token resolves to nobody",
      links.client_for(token[:-3] + "aaa") is None)
check("every client-facing page has an address",
      len(links.all_links(CLIENT, CLIENT_URL, "https://smart1.agency")) == len(links.PAGES))
check("and the mount is not doubled onto it",
      "/tools/social/tools/social" not in
      links.link(CLIENT, CLIENT_URL, "request", "https://smart1.agency/tools/social/"))
links.revoke(CLIENT, "tester")
check("a revoked link stops resolving", links.client_for(token) is None)
links.restore(CLIENT)
check("and can be turned back on", links.client_for(token) == (CLIENT, CLIENT_URL))


# ---------------------------------------------------------------------------
print("\nThe client's half is reachable without a login; the staff half is not")
# ---------------------------------------------------------------------------
import wsgi                                                       # noqa: E402
client = None
try:
    from werkzeug.test import Client as WzClient
    client = WzClient(wsgi.application)
except Exception as exc:                                          # noqa: BLE001
    check("the composed app could be built", False, exc)

if client is not None:
    r = client.get(f"/tools/social/c/{token}/request")
    check("the request form opens with no login", r.status_code == 200,
          r.status_code)
    body = r.get_data(as_text=True)
    check("and it names the client it belongs to", CLIENT in body)
    check("no staff sidebar is injected into it",
          "s1hub-sidebar" not in body and "hub-help" not in body)
    check("crawlers are told to stay out", "noindex" in body)

    for page in ("ideas", "approve", "preferences"):
        code = client.get(f"/tools/social/c/{token}/{page}").status_code
        check(f"the {page} page opens with no login", code == 200, code)

    gone = client.get("/tools/social/c/not-a-real-token/request")
    check("a bad token gets the same page a revoked one would",
          gone.status_code == 404, gone.status_code)
    check("and it does not say which kind of nothing it was",
          "expired" not in gone.get_data(as_text=True).lower())

    staff = client.get("/tools/social/requests")
    check("the staff queue is behind the login",
          staff.status_code in (301, 302, 303, 307, 308, 401, 403),
          staff.status_code)
    api = client.get(f"/tools/social/api/requests?client={CLIENT}")
    check("and so is the API underneath it",
          api.status_code in (301, 302, 303, 307, 308, 401, 403),
          api.status_code)


# ---------------------------------------------------------------------------
print("\nWhat a client is shown, and what they are never shown")
# ---------------------------------------------------------------------------
grid = sp.build_grid(date.today().strftime("%Y-%m"), channels=["facebook"],
                     per_week=1)
plan = {"id": "abc123", "client": CLIENT, "url": CLIENT_URL,
        "month": date.today().strftime("%Y-%m"), "channels": ["facebook"],
        "status": "draft", "brief": {}, "slots": grid,
        "created_at": "", "created_by": "tester"}
plan["slots"][0].update({"copy": "Furnace tune-ups before the cold sets in.",
                         "status": "approved", "image_url": "https://cdn/a.jpg",
                         "client_state": "pending_client_approval"})
plan["slots"][1].update({"copy": "$900 off everything, today only.",
                         "status": "approved"})
mod.save_batch(plan)

shown = mod._posts_for_client(CLIENT)
check("only what has been put to the client is shown", len(shown) == 1, shown)
first = shown[0]
check("and it carries no flags, no status and no strategist",
      not any(k in first for k in ("flags", "status", "created_by")), first)
check("it does carry what the client needs to answer",
      first["copy"] and first["date"] and first["channels"])

counts = sp.validate_batch(plan)
check("a plan re-validated flags the invented price", counts["block"] >= 1, counts)
check("and the flag is on the slot that carries it",
      any(f["level"] == "block" for f in plan["slots"][1]["flags"]),
      plan["slots"][1]["flags"])
check("the post the client is looking at is not one of them",
      not [f for f in plan["slots"][0]["flags"] if f["level"] == "block"],
      plan["slots"][0]["flags"])
sent = [s for s in plan["slots"] if s.get("client_state")]
check("a blocking flag is why the other one was never sent",
      len(sent) == 1)


# ---------------------------------------------------------------------------
print("\nThree answers on a post, and a change request needs the words")
# ---------------------------------------------------------------------------
page = mod.app.test_client()
base = f"/c/{token}/approve/{plan['id']}/"

r = page.post(base + "s01", json={"decision": "changes_requested", "note": ""})
check("a change request with nothing in it is refused",
      r.status_code == 400, r.status_code)
check("and it says why rather than just failing",
      "guessing" in (r.get_json() or {}).get("error", ""))

r = page.post(base + "s01", json={"decision": "changes_requested",
                                  "note": "Say furnaces, not HVAC."})
check("a change request with the words lands", r.status_code == 200, r.status_code)
after = mod._slot_of(mod.load_batch(plan["id"]), "s01")
check("the note is kept", after["client_note"] == "Say furnaces, not HVAC.")
check("and the approval is cleared, so it cannot be pushed under the request",
      after["status"] != "approved", after["status"])

r = page.post(base + "s01", json={"decision": "approved"})
check("the client can then approve it", r.status_code == 200)
check("which clears the note",
      mod._slot_of(mod.load_batch(plan["id"]), "s01")["client_note"] == "")

r = page.post(base + "s02", json={"decision": "approved"})
check("a post never sent to them cannot be approved through the link",
      r.status_code == 404, r.status_code)
r = page.post(base + "s01", json={"decision": "publish it now"})
check("an answer that is not one of the three is refused", r.status_code == 400)

other_token = links.token_for(OTHER, OTHER_URL)
r = mod.app.test_client().post(
    f"/c/{other_token}/approve/{plan['id']}/s01", json={"decision": "approved"})
check("another client's token cannot reach this client's post",
      r.status_code == 404, r.status_code)

r = page.post(f"/c/{token}/request", json={"request_type": "post",
                                           "location_label": "Westside",
                                           "notes": "Photo attached."})
check("the client's own form writes a request", r.status_code == 200)
check("and hands back the turnaround wording rather than a bare ok",
      (r.get_json() or {}).get("turnaround", {}).get("line"))
r = page.post(f"/c/{token}/request", json={})
check("with nothing said about what it is, it is refused", r.status_code == 400)


# ---------------------------------------------------------------------------
print("\nThe agent reports what it measured, and names what it could not")
# ---------------------------------------------------------------------------
sig = agent.signals(CLIENT, CLIENT_URL)
check("every input answers with a state",
      all("state" in v for v in sig.values()), sig)
check("performance with no Suite connection is not measured, never zero",
      sig["performance"]["state"] == "not_measured")
check("and it says why", sig["performance"]["detail"])
notes = agent.notes(CLIENT, CLIENT_URL, sig)
check("the missing numbers are raised as a note rather than left blank",
      any(n["key"] == "performance_not_measured" for n in notes))
check("nothing the agent raises is drawn red",
      all(n["level"] in ("warn", "note") for n in notes))
check("every note says where it is acted on", all(n.get("where") for n in notes))
worked = agent.what_worked(CLIENT, CLIENT_URL)
check("what the client said and what was measured stay apart",
      "preference" in worked and "performance" in worked)
check("a preference is never reported as a result",
      worked["performance"]["measured"] is False)
check("with an overdue backlog the agent suggests nothing new",
      agent.idea_tags_for(CLIENT, CLIENT_URL) == [],
      agent.idea_tags_for(CLIENT, CLIENT_URL))
check("and a client with a clear queue still gets a mix",
      agent.idea_tags_for(OTHER, OTHER_URL) != [])


# ---------------------------------------------------------------------------
print("\nStorage goes through the mirror")
# ---------------------------------------------------------------------------
from hub import jsonstore                                         # noqa: E402
writers = [w for w in jsonstore.unmirrored_json_writers()
           if "social" in w.get("file", "")]
check("nothing in this feature writes JSON outside hub/jsonstore.py",
      not writers, writers)
check("requests are stored under the social data directory",
      os.path.isfile(os.path.join(jsonstore.data_dir("social"), "requests.json")))


# ---------------------------------------------------------------------------
print("\n" + "-" * 60)
print(f"{PASS} passed, {FAIL} failed")
shutil.rmtree(_TMP, ignore_errors=True)
sys.exit(1 if FAIL else 0)
