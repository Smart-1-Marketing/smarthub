"""linkcheck's blind spots: a module's own request helper, and sendBeacon.

    python3 test_linkcheck_helpers.py

Same shape as the other test files here — no pytest, no new dependencies, and
it touches no data directory at all: everything below is read out of the repo.

## Why this file exists

`tools/linkcheck.py` sees a URL literal only where it sits directly inside
`fetch("…")`. Two shapes went past it entirely:

  1. **A module's own request helper.** `post('/api/seo/checks', body)` is a
     literal in a call the checker does not know, so it verified none of the
     twenty-seven paths on the SEO client record — most of what that page
     does. Four files declare a pass-through helper and their URLs are
     resolved now.

  2. **`sendBeacon`.** CLAUDE.md spends a paragraph on how invisible it is: it
     returns a boolean nobody reads and fires on `pagehide`, so a wrong path
     fails in total silence, which is how six landing modules lost their
     abandoned-form leads.

Three things this had to get right, each of which caught a draft:

  **A helper name alone is not enough.** `post` hands the URL straight to
  `fetch` in `seo_client.html` and does `fetch(BASE + path)` in
  `ads_estimate.html`; `api` splits the same way between the Suite panel and
  the Commercial Builder. Resolving a prefixed helper's fragment as a
  root-absolute path reports a break that is not there — the crying wolf
  `UNCHECKED` exists to avoid — so the table is keyed on the file and the
  prefixed ones are counted as unverified instead.

  **A bare name matches Python.** `\\bpost\\s*\\(` matches `app.post(` and
  `client.post(`, and the first run of it reported **292** breaks that were
  route decorators and test clients. Hence the file key and the `(?<![.\\w$])`
  guard.

  **Prose is not a call site.** The first run of the beacon pattern reported
  `test_landing_embeds.py:263` — a comment reading *"The bug this section
  exists for: sendBeacon('/api/partial-lead')"*, the note describing the trap,
  flagged as the trap. `sendBeacon` is a browser call, so a match in a `.py`
  file is prose by definition; both new browser-only patterns are scoped to
  front-end files.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT / "tools"))

_passed, _failed = 0, 0


def check(label, got, want=True):
    global _passed, _failed
    if got == want:
        _passed += 1
        print(f"  ok    {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}\n          got  {got!r}\n          want {want!r}")


def section(title):
    print(f"\n{title}\n{'-' * len(title)}")


import linkcheck                                              # noqa: E402

# ------------------------------------------------------------------------
section("1. Every declared helper is real, and classified correctly")

# An entry that outlives its helper goes on claiming to cover a file it no
# longer reads — the rule check_stale_json_exemptions() works to.
for table, kind in ((linkcheck.HELPERS_PASS_THROUGH, "pass-through"),
                    (linkcheck.HELPERS_PREFIXED, "prefixed")):
    for rel, names in table.items():
        path = ROOT / rel
        check(f"{rel} exists ({kind})", path.exists())
        if not path.exists():
            continue
        src = path.read_text(encoding="utf-8", errors="ignore")
        for name in names:
            # The helper must be defined in the file that declares it.
            defined = re.search(
                r"(?:function\s+%s\s*\(|(?:const|let|var)\s+%s\s*=)" % (name, name),
                src)
            check(f"  {rel} really defines {name}()", bool(defined))
            if not defined:
                continue
            body = src[defined.start():defined.start() + 600]
            # Pass-through hands the URL to fetch unchanged; prefixed
            # concatenates a base onto it. Reading the body is what stops the
            # two tables drifting into each other.
            prefixed = re.search(r"fetch\s*\(\s*[A-Za-z_$][\w.$]*\s*\+", body) \
                or re.search(r"=>\s*\(?[A-Za-z_$][\w.$]*", body)
            if kind == "pass-through":
                check(f"  {name}() in {rel} passes the URL through",
                      not prefixed)
            else:
                check(f"  {name}() in {rel} prefixes a base", bool(prefixed))

check("no file is in both tables",
      set(linkcheck.HELPERS_PASS_THROUGH) & set(linkcheck.HELPERS_PREFIXED),
      set())

# ------------------------------------------------------------------------
section("2. The patterns match what they are for, and nothing else")

beacon = dict(linkcheck.PATTERNS)["beacon"]
check("sendBeacon with a root-absolute literal is seen",
      bool(beacon.search('navigator.sendBeacon("/api/help/tour-event", b)')))
check("and single quotes too",
      bool(beacon.search("navigator.sendBeacon('/api/demos/event')")))

helper = linkcheck._helper_rx("post")
check("a bare helper call is seen",
      bool(helper.search("post('/api/seo/checks', body)")))
check("but app.post( is not — that is a route decorator",
      helper.search('app.post("/api/submit-io")'), None)
check("nor client.post( — that is a test client",
      helper.search('client.post("/login")'), None)
check("nor a name that merely ends in the helper's",
      helper.search('compost("/x")'), None)

rel_rx = dict(linkcheck.UNCHECKED)["concat-relative"]
check("a concat with no leading slash is named as unverified",
      bool(rel_rx.search("fetch(base+'api/gallery?company=x')")))
check("and one WITH a slash is left to concat-fetch",
      rel_rx.search("fetch(base+'/api/thing')"), None)

# ------------------------------------------------------------------------
section("3. Browser-only patterns do not read Python prose")

check("beacon and concat-relative are declared browser-only",
      sorted(linkcheck.BROWSER_ONLY), ["beacon", "concat-relative"])
check("and every one of them is a real pattern name",
      all(k in dict(linkcheck.PATTERNS) or k in dict(linkcheck.UNCHECKED)
          for k in linkcheck.BROWSER_ONLY))

# The comment that caught the first draft is still there, and must stay
# unflagged: it is the note describing the trap, not an instance of it.
_embeds = (ROOT / "test_landing_embeds.py").read_text(encoding="utf-8")
check("the note explaining the sendBeacon trap is still in the repo",
      "sendBeacon('/api/partial-lead')" in _embeds)
found = linkcheck.literals()
check("and linkcheck does not report it as a call site",
      [w for w in found.get("/api/partial-lead", [])
       if w[0] == "test_landing_embeds.py"], [])

# ------------------------------------------------------------------------
section("4. The helpers' URLs are actually being read")

_seo = [u for u, w in found.items()
        if any(f == "hub/templates/seo_client.html" and k == "helper"
               for f, _n, k in w)]
check("the SEO client record's POSTs are seen now", len(_seo) >= 20, True)
check("including the one the record's checkboxes use",
      "/api/seo/checks" in _seo)

print(f"\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
