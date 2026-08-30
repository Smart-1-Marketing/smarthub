"""What a landing page is for, and what it is selling.

    python3 test_landing_spec.py

Same shape as test_landing_maker.py: no pytest, no new dependencies, a
temporary data directory and a throwaway SQLite mirror, and it runs with no
OpenAI key — which is the fallback-copy path, and the one where an invented
offer would be least visible.

## Why this file exists

Three things the maker asked badly, or not at all:

  1. **The goal was a sentence.** "Book a viewing", "Request a quote" and
     "Call today" produced the same page with different words on the button.
     They want different forms and different proof. The goal is now chosen
     from a list, and the tests below assert the choice actually reaches the
     rendered form — not just the label.

  2. **The offer was one string.** "Special offer available" and a bare
     "20% off" both reached the page verbatim: a promise to the visitor with
     nothing behind it. Worse than no offer band, which is what they get now.

  3. **Nothing asked what was being promoted.** The action is "request a
     quote"; the subject is "ducted air conditioning installation". A page
     that knows only the action writes around its own subject.

The rule underneath all three is the Smart 1 Labs rule: a prompt is a
request, and "the model was told not to" is not evidence that it did not. So
the no-offer case is asserted on the *output*, not on the prompt.
"""
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1lspec_test_")
os.makedirs(os.path.join(TMP, "disk"), exist_ok=True)
os.environ["HUB_DATA_DIR"] = os.path.join(TMP, "disk")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "mirror.sqlite3")
os.environ["SECRET_KEY"] = "landing-spec-test"
os.environ["PANEL_PASSWORD"] = "landing-spec-test"
os.environ.pop("OPENAI_API_KEY", None)

_passed, _failed = 0, 0


def check(label, got, want):
    global _passed, _failed
    if got == want:
        _passed += 1
        print(f"  ok    {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}\n          got  {got!r}\n          want {want!r}")


def section(title):
    print(f"\n{title}\n{'-' * len(title)}")


from hub import landing_spec as spec                            # noqa: E402
from hub import landing_maker as lm                             # noqa: E402
from hub.landing_render import render_page                      # noqa: E402

BRIEF = {"client": "Riverside HVAC", "industry": "HVAC", "city": "Columbus",
         "state": "OH", "geo": "Columbus, OH", "phone": "614-555-0100",
         "products": ["Ducted air conditioning installation"],
         "objectives": "Lead Generation"}


def fields_in(html):
    return re.findall(r'<(?:input|textarea) name="([^"]+)"', html)


def page_for(goal, offer="", promoting=""):
    copy = lm.write_copy(BRIEF, goal, offer, promoting)
    return copy, render_page(BRIEF, copy, lm.DIRECTIONS["trust"], {},
                             goal_id=spec.goal(goal)["id"])


section("The goal is a choice, and every choice is renderable")

check("there is more than one goal to pick", len(spec.PAGE_GOALS) > 1, True)
check("ids are unique",
      len({g["id"] for g in spec.PAGE_GOALS}), len(spec.PAGE_GOALS))
# A goal naming a field the renderer cannot draw is a field that silently
# never appears. Caught here rather than by a rep wondering where it went.
check("no goal asks for a field the renderer can't draw",
      [f for g in spec.PAGE_GOALS for f in g["fields"]
       if f not in spec.KNOWN_FIELDS], [])
check("every field has a human label",
      [f for f in spec.KNOWN_FIELDS if f not in spec.FIELD_LABELS], [])
for key in ("cta", "blurb", "proof", "guidance", "kpi"):
    check(f"every goal defines {key}",
          [g["id"] for g in spec.PAGE_GOALS if not str(g.get(key) or "").strip()], [])
# Read while rendering a page for a prospect. A KeyError here is a blank page.
check("an unknown goal falls back rather than raising",
      spec.goal("something-nobody-defined")["id"], spec.DEFAULT_GOAL)
check("and matching is forgiving of the label",
      spec.goal("Request a quote")["id"], "quote")


section("The goal reaches the page, not just the button")

_, quote_html = page_for("quote")
_, call_html = page_for("call")
_, book_html = page_for("book")
_, apply_html = page_for("apply")

check("a quote page asks where they are",
      "postcode" in fields_in(quote_html), True)
check("a call page does not", "postcode" in fields_in(call_html), False)
check("a call page asks the least of anyone",
      len(fields_in(call_html)) < len(fields_in(quote_html)), True)
check("booking asks when they want it",
      "preferred_time" in fields_in(book_html), True)
check("applying asks which role", "role" in fields_in(apply_html), True)
check("two different goals do not produce the same form",
      fields_in(call_html) == fields_in(book_html), False)

# The lead panel reads "detail"; the spec calls the field "details". A page
# whose free-text box never reaches the panel loses the part of the lead a
# rep actually reads.
check("the free-text box is named for what the lead panel reads",
      "detail" in fields_in(quote_html), True)
check("and not the spec's internal name",
      "details" in fields_in(quote_html), False)

check("the call-to-action follows the goal",
      (page_for("call")[0]["cta"], page_for("book")[0]["cta"]),
      (spec.goal("call")["cta"], spec.goal("book")["cta"]))


section("An offer is read, questioned, or absent")

check("nothing given is 'none'", spec.offer_state("")[0], spec.NONE)
check("a real offer is used as written",
      spec.offer_state("Free spring service with every new system")[0], spec.READ)
for vague in ("Special offer available", "Great deals", "Discounts now",
              "offer", "20% off", "$500 off"):
    check(f"{vague!r} is not usable as written",
          spec.offer_state(vague)[0], spec.UNCLEAR)
check("a bare number is told what is wrong with it specifically",
      "nothing attached" in spec.offer_state("20% off")[1], True)

# The output, not the prompt. This is the check that matters: an unclear
# offer used to land in the subhead verbatim.
copy_vague, html_vague = page_for("quote", "Special offer available")
check("an unclear offer never reaches the page copy",
      "Special offer available" in copy_vague["subhead"], False)
check("nor the rendered page",
      "Special offer available" in html_vague, False)
copy_real, html_real = page_for("quote", "Free spring service with every new system")
check("a real offer does reach the page",
      "Free spring service" in html_real, True)
check("the copy records which state the offer was in",
      (copy_vague["offer_state"], copy_real["offer_state"]),
      (spec.UNCLEAR, spec.READ))

# With no offer, the writer is told there is none -- and told not to invent
# one, rather than simply not being asked for one.
guidance = spec.offer_guidance("")
check("no offer means the writer is told so", "NO offer" in guidance, True)
check("and forbidden from implying one",
      all(w in guidance for w in ("discount", "free trial", "limited-time")), True)
check("an unclear offer gets the same prohibition as none",
      spec.offer_guidance("Special offer available"), guidance)


section("What is being promoted")

check("what the rep typed wins",
      spec.promoting_from(BRIEF, "Spring service plans"), "Spring service plans")
check("otherwise the campaign's own products are used",
      spec.promoting_from(BRIEF), "Ducted air conditioning installation")
# "" rather than a guess, so the caller can ask. An industry is not a subject.
check("with neither it is blank, not the industry",
      spec.promoting_from({"industry": "HVAC"}), "")
check("and that becomes a question",
      any("promoting" in q for q in
          spec.open_questions({"industry": "HVAC", "geo": "Columbus"}, "quote", "")), True)


section("Open questions are asked, not written around")

qs = spec.open_questions({"industry": "HVAC"}, spec.DEFAULT_GOAL, "20% off")
check("an unknown subject is asked about",
      any("promoting" in q for q in qs), True)
check("an unusable offer is raised", any("nothing attached" in q for q in qs), True)
check("a missing area is raised", any("area" in q for q in qs), True)
check("a general inquiry is challenged", any("general inquiry" in q for q in qs), True)
check("a call page with no phone number is flagged",
      any("phone number" in q for q in
          spec.open_questions({"geo": "Columbus", "products": ["x"]}, "call", "")), True)
check("a complete brief raises nothing",
      spec.open_questions(BRIEF, "quote", "Free spring service with every system",
                          "Ducted air conditioning"), [])


section("One list, read by everyone")

page = (ROOT / "hub" / "templates" / "landing_maker.html").read_text()
check("the maker fetches the goals rather than hard-coding them",
      "/api/landing/goals" in page, True)
# A second copy of the list is how the button, the prompt and the form end up
# disagreeing about what a goal is, with nothing erroring when they do.
check("no goal label is typed into the template",
      [g["label"] for g in spec.PAGE_GOALS if g["label"] in page], [])
check("the offer is checked while the rep types",
      "/api/landing/offer-check" in page, True)
check("the promoting field is on the form", 'id="lpPromoting"' in page, True)
check("and is sent when the page is built", "promoting:" in page, True)


# ------------------------------------------------------------------- summary
shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{'-' * 60}\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
