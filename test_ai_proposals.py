"""The model proposes, the code decides, and a person presses.

    python3 test_ai_proposals.py

Same shape as the other test files: no pytest, no new dependencies, and it
runs against a temporary data directory and a throwaway SQLite database, so it
never touches /var/data or the real one. No provider is reached — every model
call is stubbed, including one deliberately answering badly.

## Why this file exists

Three places where the Hub already held everything a model needed and asked it
nothing:

  1.  `hub/site_names_ai.py`     — 1,021 Simvoly projects, of which the
                                    hand-written rules match 305 exactly and
                                    offer a candidate for 60 more. Most of the
                                    rest carry a real business name in a shape
                                    no rule anticipated, and each one is a
                                    client whose website cannot be joined to
                                    anything.
  2.  `modules/image_picker/vision.py`
                                  — a client sends forty photographs and
                                    nothing looks at any of them, while
                                    seo_images and video_library both run
                                    vision on the same Cloudinary account.
  3.  `hub/request_triage.py`     — a web ticket and a campaign support request
                                    arrive with a paragraph describing the work
                                    and every dropdown above it untouched.

All three are the same shape, and this file exists to hold them to it. Every
check below is a way one of them becomes confidently wrong:

  a.  the model names something   — the site matcher's whole safety argument is
      it was never shown            that the client book is NOT in the prompt,
                                    so a client name cannot come out of it
  b.  an answer that is not in     — a tidied name, an expanded abbreviation or
      what it was given is used     an invented dropdown option. Knack refuses
                                    the whole record over the last one
  c.  a proposal is written        — none of these three may write anything.
      rather than offered           A press is the write, and a value somebody
                                    typed is never overwritten
  d.  a failure retries for ever   — one unreadable file costs a vision call an
                                    hour until something gives up in writing
  e.  a count reads as zero when   — "nothing has been described" and "we could
      it could not be measured      not read the store" are different answers,
                                    and only the first is a button to press
  f.  a discard is silent          — a prompt that has started inventing is
                                    something to see, not something to absorb
"""
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1aiprop_test_")
os.environ["HUB_DATA_DIR"] = os.path.join(TMP, "data")
os.environ["AUDIT_LOG_PATH"] = os.path.join(TMP, "audit.jsonl")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "db.sqlite3")
os.environ["SECRET_KEY"] = "ai-proposals-test-secret"
os.environ["PANEL_PASSWORD"] = "ai-proposals-test-shared"
os.environ["OPENAI_API_KEY"] = "sk-test-not-a-real-key"

from hub import ai as hub_ai                                    # noqa: E402
from hub import request_triage, site_names, site_names_ai       # noqa: E402

_passed = _failed = 0


def ok(label, condition, detail=""):
    global _passed, _failed
    if condition:
        _passed += 1
        print(f"  ok    {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}{('  — ' + detail) if detail else ''}")


def check(label, got, want):
    ok(label, got == want, f"got {got!r}, want {want!r}")


def section(title):
    print(f"\n{title}\n{'-' * len(title)}")


def _calls_in(src, name):
    """Is `name` actually called in this source, or only talked about?

    Three modules in this repo explain a trap by naming the call they
    deliberately do not make, so a plain substring search reads the
    explanation of the fix as the defect — the reason tools/spellcheck.py
    reads Python through the AST rather than as text. This is the cheap
    version of that: a call has a bracket after it and is not inside a
    comment or a docstring.
    """
    import ast
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return name + "(" in src
    tail = name.split(".")[-1]
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        got = getattr(fn, "attr", None) or getattr(fn, "id", None)
        if got == tail:
            return True
    return False


def stub(fn):
    """Swap the one AI entry point out. Every call in this file is stubbed."""
    hub_ai.chat_json = fn
    hub_ai.ready = lambda: True


# ===========================================================================
section("Simvoly project names: the model is never shown the client list")
# ===========================================================================

TITLES = [
    "FabLocal -  SERVPRO of Southwest San Antonio",
    "copy of Buckeye Lake Marina v3 FINAL",
    "Anna's Website",
    "Hern Marine — Summer '25 (do not delete)",
]

_seen_prompts = []


def _name_reader(messages, **kw):
    _seen_prompts.append("\n".join(m.get("content", "") if isinstance(
        m.get("content"), str) else "" for m in messages))
    out = []
    for line in messages[-1]["content"].split("\n"):
        title = line.split(". ", 1)[-1]
        if "SERVPRO" in title:
            out.append({"source": title, "business": "SERVPRO of Southwest San Antonio",
                        "confidence": "high", "note": "media partner in front"})
        elif "Buckeye" in title:
            out.append({"source": title, "business": "Buckeye Lake Marina",
                        "confidence": "high", "note": "copy and version markers"})
        elif "Hern" in title:
            # Deliberately ungrounded: "Services" is not in the title. This is
            # the answer that must be refused — a tidied name is a different
            # string that matches a different client, or none.
            out.append({"source": title, "business": "Hern Marine Services",
                        "confidence": "high", "note": "expanded"})
    return {"readings": out}


stub(_name_reader)

# A placeholder is never sent: is_placeholder() has already answered for it,
# and paying a model to find a business in "Anna's Website" invites it to.
# Whitespace is collapsed on the way through — the title as Simvoly holds it
# has a double space in it — and the lookup key is normalised the same way, so
# a reading stored under the tidied string is still found from the raw one.
check("a placeholder costs no call", site_names_ai.pending(TITLES),
      [" ".join(t.split()) for t in TITLES if "Anna" not in t])
check("and the raw title still finds its reading later",
      bool(site_names_ai.pending([TITLES[0]])), True)

report = site_names_ai.read_missing(TITLES)
ok("the pass reports success", report["ok"], json.dumps(report))
check("three titles read, one placeholder skipped", report["read"], 3)
# Counted, not swallowed: a prompt that has started inventing names is
# something to look at.
check("the invented name is counted", report["ungrounded"], 1)
ok("and named in the note",
   "not in the original" in report["note"], report["note"])

ok("the client list never reaches the prompt",
   all("Buckeye Lake Marina" not in p or "readings" in p
       for p in _seen_prompts if "allowed" not in p))
_prompt_text = " ".join(_seen_prompts)
for _forbidden in ("client list", "registry", "Icon Solar"):
    ok(f"the prompt does not carry {_forbidden!r}", _forbidden not in _prompt_text)

# A reading is a *candidate*, in the same shape as one a rule derived — so it
# goes through exact_matches() against the real book and cannot be a match on
# its own.
_cands = site_names_ai.candidates_for(TITLES[0])
check("a grounded reading becomes one candidate", len(_cands), 1)
check("with the kind that says where it came from", _cands[0]["kind"], "ai")
ok("and a why a person can read",
   "shown the project title and nothing else" in _cands[0]["why"])
check("the ungrounded one produces none",
      site_names_ai.candidates_for(TITLES[3]), [])
check("and so does the placeholder",
      site_names_ai.candidates_for("Anna's Website"), [])

BOOK = site_names.index_names([
    ("SERVPRO of Southwest San Antonio", "registry"),
    ("Buckeye Lake Marina", "registry"),
    ("Hern Marine", "registry"),
    ("FabLocal", "registry"),
])

# The two the rules could not reach now match — and the media partner, which
# the raw title also contains, still does not. A substring is never a match.
_hits = site_names.exact_matches(TITLES[0], BOOK,
                                 extra=site_names_ai.candidates_for(TITLES[0]))
check("the business behind the partner prefix matches",
      [h["client"] for h in _hits], ["SERVPRO of Southwest San Antonio"])
ok("and the media partner in the same string does not",
   all(h["client"] != "FabLocal" for h in _hits))

_hits2 = site_names.exact_matches(TITLES[1], BOOK,
                                  extra=site_names_ai.candidates_for(TITLES[1]))
check("version markers no longer hide the business",
      [h["client"] for h in _hits2], ["Buckeye Lake Marina"])

# The one that matters most: a client of that name exists, the model gave a
# tidied version of it, and the tidied version is refused rather than matched.
_hits3 = site_names.exact_matches(TITLES[3], BOOK,
                                  extra=site_names_ai.candidates_for(TITLES[3]))
ok("an invented word refuses the match rather than making one",
   "Hern Marine" not in [h["client"] for h in _hits3])

# Nothing is read twice: the whole affordability argument.
check("a second pass costs nothing", site_names_ai.read_missing(TITLES)["read"], 0)

# The whole map, read once and handed down. sites_match.suggest() walks a
# thousand projects and asks for a candidate per one; a fresh read each time
# is a thousand file reads, each asking jsonstore to restore from the database
# mirror on a miss.
_store = site_names_ai.readings()
check("a caller can hand the whole map in",
      site_names_ai.candidates_for(TITLES[0], _store),
      site_names_ai.candidates_for(TITLES[0]))
ok("and sites_match reads it once for the pass",
   "_ai_readings()" in (ROOT / "hub" / "sites_match.py").read_text(encoding="utf-8"))


# ===========================================================================
section("Client photographs: described in a batch, given up on in writing")
# ===========================================================================

from modules.image_picker import vision                          # noqa: E402
from modules.image_picker.models import (                        # noqa: E402
    ImageDescription, PickerClient, SavedImage, init_db, session)

init_db()
with session() as _db:
    _client = PickerClient(name="Icon Solar", slug="icon-solar-test")
    _db.add(_client)
    _db.commit()
    for _i, (_fn, _alt, _rt) in enumerate([
            ("shopfront.jpg", "", "image"),
            ("team.jpg", "Our team outside the office", "image"),
            ("brochure.pdf", "", "raw"),
            ("broken.jpg", "", "image")]):
        _db.add(SavedImage(client_id=_client.id, provider="upload",
                           provider_image_id=f"p{_i}", filename=_fn,
                           alt_text=_alt, resource_type=_rt,
                           cloudinary_url=f"https://example.invalid/{_fn}"))
    _db.commit()
    IDS = {im.filename: im.id for im in _db.query(SavedImage).all()}

_calls = {"n": 0}


def _describer(messages, **kw):
    _calls["n"] += 1
    url = messages[0]["content"][1]["image_url"]["url"]
    if "broken" in url:
        raise RuntimeError("could not fetch")
    return {"description": "A solar company storefront with signage, in daylight.",
            "alt": "Image of a solar company storefront with signage",
            # One term from the vocabulary and one invented: the invented one
            # must be dropped and counted, or the search vocabulary grows in
            # silence and nobody can put chips on it.
            "tags": ["storefront", "signage", "bright", "sunshiney-vibes"]}


stub(_describer)

_before = vision.pending_count()
ok("the count is measured", _before["measured"])
check("four files, none described", (_before["images"], _before["described"]),
      (4, 0))

_pass1 = vision.describe_backlog(limit=10)
check("two images described", _pass1["described"], 2)
# A PDF is a real thing for a client to send and not a thing to describe from
# its pixels. Given up on at once rather than retried twice more.
check("the PDF is given up on without a call", _pass1["gave_up"], 1)
check("the unreadable image is left to retry", _pass1["failed"], 1)
check("the invented tags are counted", _pass1.get("dropped_tags"), 2)

with session() as _db:
    _rows = {r.image_id: r for r in _db.query(ImageDescription).all()}
_pdf = _rows[IDS["brochure.pdf"]]
check("and the PDF's reason is written down", _pdf.state, "given_up")
ok("in words", "not an image" in (_pdf.last_error or ""))

_shop = _rows[IDS["shopfront.jpg"]]
check("only vocabulary terms are stored", _shop.tags, "storefront,signage,bright")
# hub/alt_text's cleaner, not a second copy of its rules.
ok('the "image of" preamble is stripped',
   not _shop.alt_suggestion.lower().startswith("image of"), _shop.alt_suggestion)

# A file that fails comes straight back, so without a ceiling one unreadable
# image costs a vision call an hour for ever.
vision.describe_backlog(limit=10)
_third = vision.describe_backlog(limit=10)
check("the third failure gives up", _third["gave_up"], 1)
with session() as _db:
    _broken = _db.query(ImageDescription).filter_by(
        image_id=IDS["broken.jpg"]).one()
check("in writing, not in memory", _broken.state, "given_up")
check("after three attempts", _broken.attempts, vision.MAX_ATTEMPTS)
check("and it is not asked about again",
      vision.describe_backlog(limit=10)["described"], 0)

# The press. A description is an observation; the alt text is what somebody
# chose, and the sweep never writes into it.
with session() as _db:
    ok("the sweep wrote nothing into alt text",
       not (_db.get(SavedImage, IDS["shopfront.jpg"]).alt_text or ""))

_took = vision.accept(IDS["shopfront.jpg"], actor="todd@smart1marketing.com")
ok("the press writes it", _took.get("ok") and _took.get("written"), json.dumps(_took))
_refused = vision.accept(IDS["team.jpg"], actor="todd@smart1marketing.com")
ok("and refuses a field somebody had already filled", not _refused.get("ok"))
ok("saying why, rather than reporting a clean success",
   "not written over" in _refused.get("error", ""), _refused.get("error", ""))
with session() as _db:
    check("the typed alt text is untouched",
          _db.get(SavedImage, IDS["team.jpg"]).alt_text,
          "Our team outside the office")

check("the PDF never cost a vision call", _calls["n"], 5)


# ===========================================================================
section("Ticket and support requests: into the empty fields only")
# ===========================================================================

FIELDS = [
    {"key": "type", "label": "Type of Ticket", "control": "select",
     "choices": ["New Website", "Revision", "Bug Fix", "Hosting", "SEO"]},
    {"key": "billable", "label": "Revision Requires Billing",
     "control": "boolean", "choices": []},
    {"key": "description", "label": "Describe the changes",
     "control": "textarea", "choices": []},
    {"key": "client", "label": "Client", "control": "connection",
     "choices": [{"id": "1", "label": "Icon Solar"}]},
    {"key": "developer", "label": "Developer", "control": "select",
     "choices": ["Only one option"]},
]
TEXT = ("The contact form on the site stopped sending. Nothing has arrived in "
        "the inbox since Tuesday. Please fix it — this is a fault rather than "
        "a change, so it should not be billed.")

_asked = {"body": ""}


def _triager(messages, **kw):
    _asked["body"] = messages[-1]["content"]
    return {"answers": [
        {"key": "type", "value": "Bug Fix", "why": "the form stopped sending"},
        {"key": "billable", "value": "No", "why": "they call it a fault"},
        # An option the field does not publish. Knack refuses the whole record
        # over one of these, so it must never reach a screen.
        {"key": "type", "value": "Broken Thing", "why": "n/a"},
        # A question that was not asked.
        {"key": "nonsense", "value": "Bug Fix", "why": "n/a"},
        # A field the caller did not say was empty.
        {"key": "client", "value": "Icon Solar", "why": "named in the text"},
    ]}


stub(_triager)

_out = request_triage.suggest(TEXT, FIELDS, ["type", "billable", "client",
                                             "developer"])
ok("the pass succeeds", _out["ok"], json.dumps(_out))
check("only the choice fields are proposed for",
      sorted(_out["suggestions"]), ["billable", "type"])
check("the type is one of the published options",
      _out["suggestions"]["type"]["value"], "Bug Fix")
ok("with the reason that decided it",
   "stopped sending" in _out["suggestions"]["type"]["why"])

# A connection is a record id, never a name — the rule connection_choices()
# exists for. A single-option field is not a question. Neither is offered to
# the model at all.
ok("a connection is never offered to the model", "key: client" not in _asked["body"])
ok("nor is a field with one option", "key: developer" not in _asked["body"])
ok("nor a free-text box", "key: description" not in _asked["body"])

check("the unusable answers are counted", _out["unusable"], 3)
ok("and named in the note",
   "not being one of the options" in _out["note"], _out["note"])

# The gate is here, not only in the browser: a rule the form keeps while the
# endpoint breaks it is not a rule.
_only = request_triage.suggest(TEXT, FIELDS, ["billable"])
check("a field already answered is never asked about",
      sorted(_only["suggestions"]), ["billable"])
ok("and it is not even sent", "key: type" not in _asked["body"])

# Nothing is a default. Below the floor there is nothing to classify, and
# asking anyway spends a call to be told so.
_thin = request_triage.suggest("fix it", FIELDS, ["type"])
check("too little text proposes nothing", _thin["suggestions"], {})
ok("and says what is missing", "Describe the work first" in _thin["note"])

_none_left = request_triage.suggest(TEXT, FIELDS, [])
check("nothing to fill in proposes nothing", _none_left["suggestions"], {})
ok("and says so rather than reading as a failure",
   "already has one" in _none_left["note"], _none_left["note"])

# "we could not ask" and "there was nothing to say" are different answers.
hub_ai.ready = lambda: False
_unset = request_triage.suggest(TEXT, FIELDS, ["type"])
ok("no key is an error, not an empty answer",
   not _unset["ok"] and "OPENAI_API_KEY" in _unset["error"])
hub_ai.ready = lambda: True

# Exact or not at all — never the nearest. "Revision" and "Revision (billable)"
# are different answers and picking one is the guess client_key refuses.
check("a near option is refused",
      request_triage._match("Revision", ["Revision (billable)", "Bug Fix"]), "")
check("punctuation and case alone still match",
      request_triage._match("bug fix.", ["Bug Fix"]), "Bug Fix")
check("an ambiguous normalized match refuses both",
      request_triage._match("bug fix", ["Bug Fix", "Bug-Fix"]), "")


# ===========================================================================
section("None of the three writes anything by arriving")
# ===========================================================================

# Every one of these is a proposal a person presses. Stated as a check rather
# than as a comment, because the whole difference between these three features
# and a silent corruption of the client book is that nothing here is applied
# on its own.
_src = (ROOT / "hub" / "site_names_ai.py").read_text(encoding="utf-8")
ok("reading a project name writes no client anywhere",
   "clients_registry" not in _src and "domain_links" not in _src)
_machinery = (ROOT / "hub" / "name_reading.py").read_text(encoding="utf-8")
ok("and it is stored through jsonstore, not a bare file write",
   "jsonstore.write_json" in _machinery and not _calls_in(_machinery, "os.remove"))
ok("under the data directory, not the working directory",
   "jsonstore.data_dir(" in _machinery)
# The reader takes a prompt and a store; it does not take a client book, and
# that is a property of the machinery rather than of each caller remembering.
ok("the shared reader cannot be handed a client book",
   "book" not in _machinery.split("class NameReader")[1].split("def read_missing")[0])
ok("the caller carries no second copy of the batching",
   "chat_json" not in _src)

_vsrc = (ROOT / "modules" / "image_picker" / "vision.py").read_text(encoding="utf-8")
ok("the sweep only writes alt text from accept()",
   _vsrc.count("image.alt_text =") == 1)
ok("and accept() is the only thing that touches it",
   "def accept" in _vsrc and
   _vsrc.index("def accept") < _vsrc.index("image.alt_text ="))

# The sweep is a scheduler job, and `_run_job` calls every one of them as
# `fn(app)`. A job written without that argument raises TypeError on every
# single run, and the only trace is one line in the activity log nobody is
# reading — so the signature is asserted for all of them rather than for the
# one added here.
import ast as _ast                                              # noqa: E402
_sched = _ast.parse((ROOT / "hub" / "scheduler.py").read_text(encoding="utf-8"))
_jobs = {n.name: [a.arg for a in n.args.args] for n in _ast.walk(_sched)
         if isinstance(n, _ast.FunctionDef) and n.name.startswith("job_")}
ok("the upload sweep is registered as a scheduler job",
   "job_describe_client_uploads" in _jobs)
_wrong = {k: v for k, v in _jobs.items() if v != ["app"]}
ok("and every job takes the argument the runner passes", not _wrong, str(_wrong))

_tsrc = (ROOT / "hub" / "request_triage.py").read_text(encoding="utf-8")
ok("triage never writes to Knack",
   not any(_calls_in(_tsrc, fn) for fn in
           ("create_ticket", "update_ticket", "coerce_field", "requests.post")))

_form = (ROOT / "hub" / "static" / "knack-form.js").read_text(encoding="utf-8")
# `control()` draws a boolean as a pair of radios OR as a data-toggle button,
# and a `multi` as a <select multiple>. On the last two, assigning `.value`
# does nothing at all — the field would be marked as suggested, the reason
# would appear beside it, and `read()` would still return the old value. The
# form would read as filled in and send what was there before, which is the
# worst way a suggestion can fail.
for _shape, _needle in (("a radio group", 'input[name="'),
                        ("a toggle button", "dataset.toggle"),
                        ("a multi-select", "el.multiple"),
                        ("a plain select", "el.tagName === 'SELECT'")):
    ok(f"a suggestion knows how to set {_shape}", _needle in _form)
ok("and refuses rather than marking a control it could not set",
   "if (!set) return;" in _form)
ok("the form draws a suggestion dashed rather than as a value",
   "borderStyle = 'dashed'" in _form)
ok("and offers to put it back", "Dismiss" in _form)
ok("the control is drawn once, for both objects",
   _form.count("function triageButton") == 1)
for _f in ("hub/static/web-ticket.js", "hub/static/campaign-request.js"):
    _s = (ROOT / _f).read_text(encoding="utf-8")
    ok(f"{_f} uses the shared control", "triageButton(" in _s)
    ok(f"{_f} carries no second copy of it", "function triageButton" not in _s)




# ===========================================================================
section("Invoice descriptions: only the lines every rule gave up on")
# ===========================================================================

from hub import domain_renewals, invoice_names                    # noqa: E402

DESCS = [
    "syrons-market.com\tSyrons",                        # the domain rule wins
    "Annual renewal",                                    # a label — never sent
    "renewal for the Buckeye acct (Buckeye Lake Marina) thx",
    "2026 dom ren // BLUE RIDGE DENTAL ARTS pls bill",
]

# A label names nobody, and `_is_label()` has already said so. Paying a model
# to find a business in "Annual renewal" invites it to find one.
ok("a label is never sent",
   "label" in invoice_names.worth_reading("Annual renewal"))
ok("nor is a description too short to name anybody",
   bool(invoice_names.worth_reading("QB")))
ok("a real description is worth reading",
   not invoice_names.worth_reading(DESCS[2]))
check("so only the readable ones are pending",
      len(invoice_names.pending(DESCS)), 3)


def _desc_reader(messages, **kw):
    out = []
    for line in messages[-1]["content"].split("\n"):
        text = line.split(". ", 1)[-1]
        if "Buckeye" in text:
            out.append({"source": text, "business": "Buckeye Lake Marina",
                        "confidence": "high", "note": "named in brackets"})
        elif "BLUE RIDGE" in text:
            # Ungrounded: "Studio" is not in the description.
            out.append({"source": text, "business": "BLUE RIDGE DENTAL Studio",
                        "confidence": "high", "note": "tidied"})
        elif "Syrons" in text:
            out.append({"source": text, "business": "Syrons",
                        "confidence": "high", "note": ""})
    return {"readings": out}


stub(_desc_reader)
_rep = invoice_names.read_missing(DESCS)
check("the invented business is refused", _rep["ungrounded"], 1)
check("and it reads as nothing rather than as a name",
      invoice_names.business_in(DESCS[3]), "")
check("the messy one is read", invoice_names.business_in(DESCS[2]),
      "Buckeye Lake Marina")

ROWS = [{"id": "r1", "domain": "buckeyelakemarina.com",
         "client": "Buckeye Lake Marina", "media_partner": "WXYZ Radio",
         "field_2964": "Yes"},
        {"id": "r2", "domain": "syrons-market.com", "client": "Syrons",
         "media_partner": "", "field_2964": "Yes"}]
LINES = [{"description": d, "invoice_id": f"i{i}", "doc_number": str(i),
          "date": "2026-03-01", "amount": 24.0, "customer": "WXYZ Radio"}
         for i, d in enumerate(DESCS)]
_by_desc = {m["description"]: m for m in
            domain_renewals.match_charges(LINES, ROWS,
                                          readings=invoice_names.readings())}

# The domain is an identifier and stays the strongest rule. A reading must not
# get a second opinion on a line the rules already answered.
check("the domain rule still wins where it fires",
      (_by_desc[DESCS[0]]["matched_on"], _by_desc[DESCS[0]]["confidence"]),
      ("domain", "exact"))
# And this is the whole point: a line whose parsed "name" was the entire
# sentence now resolves to a client.
check("a description no rule could join is read",
      _by_desc[DESCS[2]]["client"], "Buckeye Lake Marina")
check("and it can never be better than a suggestion",
      _by_desc[DESCS[2]]["confidence"], "probable")
ok("the row says the name came from a reading",
   _by_desc[DESCS[2]]["read_name"] == "Buckeye Lake Marina")
ok("and says so in words a person can judge",
   "suggestion" in _by_desc[DESCS[2]]["why"], _by_desc[DESCS[2]]["why"])
# The refused reading leaves the charge exactly where it was.
check("an ungrounded reading joins nothing",
      _by_desc[DESCS[3]]["confidence"], "unmatched")

# `probable` is what makes this safe on a report about money: a suggestion
# counts as having no record here, in BOTH directions, until somebody links
# it. Asserted against the report rather than trusted from the matcher.
_src_dp = (ROOT / "hub" / "domain_purchase.py").read_text(encoding="utf-8")
ok("a probable charge still counts as unrecorded",
   'if c.get("confidence") == "probable":' in _src_dp
   and "unrecorded.append(_orphan_charge(c))" in _src_dp)
ok("and only confirmed or exact charges mark a renewal billed",
   'c.get("confidence") in ("confirmed", "exact")' in _src_dp)


# ===========================================================================
section("Google resource labels: the reading never outranks an identifier")
# ===========================================================================

from hub import google_names_ai                                   # noqa: E402

ok("a label of only platform words is never sent",
   bool(google_names_ai.worth_reading("GA4 Property (new) - test")))
ok("a label carrying a business is worth reading",
   not google_names_ai.worth_reading("FabLocal - SERVPRO Fresno GTM"))
check("labels are deduped for the pass",
      google_names_ai.labels_of([{"name": "A"}, {"name": "A"}, {"name": ""}]),
      ["A"])

_gsrc = (ROOT / "hub" / "google_links.py").read_text(encoding="utf-8")
# Through the same resolve() the raw label already goes through, so the rules
# that decide are unchanged and a reading only changes which string is asked
# about.
ok("a reading is resolved by the same client lookup as a typed name",
   _gsrc.count("client_key.resolve(name=") == 2)
ok("and it is never better than what client_key called it",
   'rhit.get("confidence") == "exact"' in _gsrc)
# _add() keeps the best confidence anything gave a client, so a reading can
# never displace a recorded id or a domain — those are identifiers.
ok("a stronger row still wins the merge",
   "CONFIDENCE_RANK[confidence] > CONFIDENCE_RANK[row[\"confidence\"]]" in _gsrc)


# ===========================================================================
section("The audit a prospect reads: grounded, or absent")
# ===========================================================================

from hub import audit_summary                                     # noqa: E402

AUDIT = {
    "measured": True, "domain": "iconsolar.example", "public_id": "sc_1",
    "spend": {"measured": True, "counted": 1, "total_display": "$2,400",
              "total_note": "Google search only",
              "total_excludes": ["Meta, which publishes the ads and not the spend"],
              "observed": [
                  {"label": "Google Ads, monthly", "value": "$2,400", "measured": True},
                  {"label": "Display spend", "value": "not measured", "measured": False}],
              "earned": [{"label": "Search terms they rank for", "value": "312"}]},
    "opportunities": [
        {"finding": "No retargeting pixel of any kind is on the site.",
         "means": "Every visitor who leaves without acting is gone for good.",
         "sells": "Retargeting"}],
}

_prompt_seen = {}
GOOD = ("You are already putting about $2,400 a month into Google search, and "
        "312 terms bring people to you without paying for them. That total "
        "leaves out Meta, which publishes the ads and not the spend.\n\n"
        "Nothing on the site remembers a visitor who leaves.")


def _summariser(messages, **kw):
    _prompt_seen["body"] = messages[-1]["content"]
    return {"summary": GOOD}


stub(_summariser)
_sum = audit_summary.summary(AUDIT)
ok("a grounded summary is kept", _sum["measured"], _sum["why"])

# Only what fired. A finding that did not match is absent from the prompt
# entirely, so there is nothing to soften into "you may also want to consider".
ok("a measured figure reaches the prompt", "$2,400" in _prompt_seen["body"])
ok("an unmeasured one does not", "Display spend" not in _prompt_seen["body"])
ok("a finding that fired reaches it",
   "retargeting pixel" in _prompt_seen["body"].lower())
ok("and what the total leaves out is required in it",
   "you must say this" in _prompt_seen["body"])

# Their own measured figure carries a dollar sign, and the summary is supposed
# to lead with it — so a flat ban on "$" refuses the correct answer, which is
# how a check comes to be switched off. It is grounded instead.
stub(lambda m, **k: {"summary": "That total leaves out Meta, which publishes "
                                "the ads and not the spend. A campaign like "
                                "this usually runs $1,500 a month.\n\n"
                                "Nothing remembers a visitor."})
_bad = audit_summary.summary(AUDIT)
ok("a figure nothing measured is refused", not _bad["measured"])
ok("and named", "$1,500" in _bad["why"], _bad["why"])

stub(lambda m, **k: {"summary": "You spend $2,400 a month on Google.\n\n"
                                "Nothing remembers a visitor."})
ok("a total quoted without what it leaves out is refused",
   "leaves out" in audit_summary.summary(AUDIT)["why"])

stub(lambda m, **k: {"summary": "That total leaves out Meta, which publishes "
                                "the ads and not the spend.\n\nWe can fix "
                                "this within 30 days, guaranteed."})
_promise = audit_summary.summary(AUDIT)
ok("a promise is discarded rather than patched", not _promise["measured"])
for _what in ("promise", "guarantee", "timeline"):
    ok(f"and {_what} is named in the reason", _what in _promise["why"])

check("nothing measured means no summary, said plainly",
      audit_summary.summary({"measured": False})["measured"], False)

# One call per audit, ever. A prospect refreshing, a rep checking the link and
# the mailed copy opened on a phone are three views of a paragraph that cannot
# have changed.
_calls = {"n": 0}


def _counted(messages, **kw):
    _calls["n"] += 1
    return {"summary": GOOD}


stub(_counted)
_first = audit_summary.for_scan("sc_1", AUDIT)
_again = audit_summary.for_scan("sc_1", AUDIT)
audit_summary.for_scan("sc_1", AUDIT)
check("three views cost one call", _calls["n"], 1)
ok("the first is written", not _first["cached"])
ok("the rest are read", _again["cached"])
# Keyed on the scan, not the domain: a re-scan is a new audit and deserves its
# own paragraph rather than inheriting last month's.
audit_summary.for_scan("sc_2", AUDIT)
check("a re-scan gets its own", _calls["n"], 2)
check("and a screen can read without ever spending",
      audit_summary.for_scan("sc_never", AUDIT, write=False)["cached"], False)
check("without a call", _calls["n"], 2)

_view = (ROOT / "modules" / "scans" / "app.py").read_text(encoding="utf-8")
ok("the client report asks for it by scan id",
   "audit_summary.for_scan(" in _view)
_tpl = (ROOT / "modules" / "scans" / "templates"
        / "widget_audit_report.html").read_text(encoding="utf-8")
# Absent silently where it could not be grounded: a line saying "we could not
# summarize this" is a sentence about our tooling on a document about their
# business.
ok("the report draws it only when there is one",
   "a.summary and a.summary.text" in _tpl)

# Asserted on what the browser receives, not on the template source: the
# comment above that block explains the rule by quoting the sentence it
# refuses to print, and a check that reads the explanation as the defect is
# the trap tools/spellcheck.py had to learn about by reading the AST.
try:
    from jinja2 import DictLoader, Environment                    # noqa: E402
    _env = Environment(loader=DictLoader({"r": _tpl}))
    _ctx = {"a": {"domain": "x.example", "summary": {"text": "", "why":
                  "The summary was discarded."}, "spend": {}, "groups": [],
                  "opportunities": [], "age": {}, "headline": {}},
            "lead": {"company": ""}, "w": {}, "brand": {}}
    _out = _env.get_template("r").render(**_ctx)
except Exception as _exc:                                         # noqa: BLE001
    _out = None
if _out is None:
    ok("the report renders without a summary", False, "template did not render")
else:
    ok("the report renders without a summary", True)
    # The honest empty is the report as it was. A line saying "we could not
    # summarize this" is a sentence about our tooling on a document about
    # their business.
    # Not a bare "summar" search: <summary> is the HTML disclosure element
    # this report uses for its collapsible sections, and a check that reads
    # those as the defect is one somebody switches off.
    for _phrase in ("could not", "discarded", "no summary", "summarize"):
        ok(f"and says nothing about {_phrase!r}", _phrase not in _out.lower())


# ===========================================================================
section("One reader, three configurations")
# ===========================================================================

# Three modules read a business name out of a messy string. The batching, the
# grounding, the store and the give-up live in hub/name_reading.py, because
# writing them three times is the drift hub/storage.py exists to stop.
_shared = (ROOT / "hub" / "name_reading.py").read_text(encoding="utf-8")
for _rel in ("hub/site_names_ai.py", "hub/invoice_names.py",
             "hub/google_names_ai.py"):
    _s = (ROOT / _rel).read_text(encoding="utf-8")
    ok(f"{_rel} configures the shared reader", "NameReader(" in _s)
    ok(f"{_rel} carries no second copy of the batching",
       not _calls_in(_s, "chat_json"))
    ok(f"{_rel} carries no second copy of the grounding check",
       "def is_grounded" not in _s and "def _is_grounded" not in _s)
    ok(f"{_rel} says what is not worth sending", "skip=" in _s)
ok("and the rules are stated once", _shared.count("def is_grounded") == 1)


print()
if _failed:
    print(f"{_failed} FAILED, {_passed} passed")
    shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(1)
print(f"{_passed} checks passed — the model proposes, the code decides, "
      "and nothing is written until somebody presses")
shutil.rmtree(TMP, ignore_errors=True)
