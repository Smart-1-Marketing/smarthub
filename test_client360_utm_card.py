"""Tracked links sits beside Social Media, and every copy control is a button
this app actually styles.

    python3 test_client360_utm_card.py

Same shape as the other test files here — no pytest, no new dependencies.

## Why this file exists

Two failures, neither of which shows up as an error:

  1. **A card in the wrong row.** Tracked links used to render eight cards
     below Social Media, so the profile URLs and the tagged link that sends
     traffic to them were never on screen together. Ordering is a one-line
     edit and nothing complains when it drifts back, so the order is asserted
     by the position of each card's own id in the template.

  2. **A class the stylesheet has never heard of.** `.mini` is not defined in
     hub.css, theme.css or anywhere else — a button carrying it renders as the
     browser's default control in the middle of a card of `.gbtn` pills. It
     looks like a styled button in review and like a mistake in the app, which
     is why it survived. `.gbtn` is the class this page uses (30-odd times);
     nothing may reintroduce `.mini`.

     The same buttons also called navigator.clipboard directly and set their
     own label to 'copied' whether or not the write resolved. copyToClipboard()
     already exists for exactly that and reports what really happened, so the
     copy controls are required to go through it.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent
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


REC = (ROOT / "hub" / "templates" / "client360.html").read_text(encoding="utf-8")
CSS = "\n".join(p.read_text(encoding="utf-8")
                for p in sorted((ROOT / "hub" / "static").glob("*.css")))

# ------------------------------------------------------------------------
section("1. Tracked links renders next to Social Media")

social = REC.find('id="c-social"')
utm = REC.find('id="c-utm"')
notes = REC.find('id="c-notes"')
images = REC.find('id="c-images"')

check("all three cards are still emitted", min(social, utm, notes) > 0)
check("Tracked links follows Social Media", social < utm)
check("nothing else is drawn between them",
      len([m for m in re.finditer(r'id="c-[a-z0-9-]+"', REC[social:utm])
           if m.group(0) != 'id="c-social"']), 0)
check("Client Notes moved out from between them", notes > utm)
check("Client Notes took the row Tracked links left behind", images < notes)
check("the section comment names the pair it now draws",
      "Social Media + Tracked links (one row, two columns)" in REC)

# ------------------------------------------------------------------------
section("2. The copy controls follow the page's own button scheme")

check(".mini is still undefined in every stylesheet",
      bool(re.search(r"(^|[\s,{])\.mini\s*[,{]", CSS, re.M)), False)
check("...so no control on Client 360 asks for it",
      'class="mini"' in REC, False)
check(".gbtn — the class this page does use — is defined",
      bool(re.search(r"\.gbtn\s*\{", CSS)))

# Every button that copies something must hand the text to the shared helper.
raw = re.findall(r"navigator\.clipboard\.writeText\(", REC)
check("no card writes to the clipboard behind copyToClipboard's back",
      len(raw), 1)  # the one inside copyToClipboard itself
check("copyToClipboard still exists to be used",
      "function copyToClipboard(text, btn, fieldId)" in REC)
check("no button still claims a copy it cannot know it made",
      "this.textContent='copied'" in REC or "this.textContent=\\'copied\\'" in REC,
      False)

check("the Tracked links copy button is a gbtn carrying its own url",
      'class="gbtn" data-utm-url=' in REC)
check("...and is wired to the shared helper",
      "copyToClipboard(b.getAttribute('data-utm-url'), b)" in REC)
check("the QuickBooks customer-link copy button is wired the same way",
      'class="gbtn" data-copy-url=' in REC
      and "closest('button[data-copy-url]')" in REC)
check("the client-links copy button names the field it falls back to",
      "copyToClipboard(d.url,this,'c-client-links-url')" in REC)

print(f"\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
