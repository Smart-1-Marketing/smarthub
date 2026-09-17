"""Credentials are typed into boxes that mask them, and the check still bites.

    python3 test_secret_fields.py

Same shape as the other test files here — no pytest, no new dependencies.

## Why this file exists

Every one of the Hub's own sign-in fields was already `type="password"`, and
`modules/skills360` keys masking off a declared `f.secret`. Four fields were
not, and what they held is the point:

| field | holds |
|---|---|
| `seo_client.html` `su_pass` | the **client's own website login** |
| `seo_client.html` `wpPass` | their WordPress application password |
| `users_admin.html` `umAddPw` | the starting password an admin types for somebody else |
| `users_admin.html` `umPwValue` | the same, on the "Set a password" box |

The passwords people type for **themselves** were protected and the ones they
type for **other people** were on screen. Neither of the last two was found by
reading the page: the third came from the check, and the fourth from widening
its word list after the third showed that matching a placeholder's wording is
not a check at all.

Masking is not encryption and nothing here pretends otherwise: the values are
sealed at rest by `hub/cms_credentials.py`. `type="password"` is about the
shoulder, the screen share, the recorded call, and the browser offering to
remember a credential as an ordinary field.

`check_unmasked_secret_fields()` runs in the gate against a repo where all
four are fixed, so its finding path is never taken there. Every assertion
below that matters is driven against markup written for it, for the reason
`test_claude_docs_index.py` gives: a check whose failure path is never taken
has not been shown to fail.

And the pattern is asserted from both sides. A check that matches too much is
not the safe direction — `tools/claudedocs.py` learned that twice in one
sitting, reporting fifty-seven files as broken because it was stricter than
the repo — so the words it must **not** match are pinned too.
"""
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from hub import integrity                                   # noqa: E402

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


def flags(tag: str) -> bool:
    """Would the check report this one input tag?

    Driven through the **real** scan, against a throwaway templates tree with
    the tag written into it. The first draft of this reimplemented the
    filtering instead — it called `_looks_secret` and then re-checked the type
    itself — and that made every assertion below an assertion about a copy.
    The mutation that proved it: making an untyped input count as safe, which
    is the defect `umAddPw` actually had, left all thirty-one checks green.

    A helper that paraphrases the thing it is testing is the same failure as a
    comment that claims a lock is taken. The `root` argument exists for this.
    """
    base = Path(tempfile.mkdtemp(prefix="secretfields_"))
    try:
        tpl = base / "hub" / "templates"
        tpl.mkdir(parents=True)
        (base / "modules").mkdir()
        (tpl / "probe.html").write_text(f"<form>{tag}</form>\n", encoding="utf-8")
        return bool(integrity.check_unmasked_secret_fields(root=base))
    finally:
        shutil.rmtree(base, ignore_errors=True)


# ---------------------------------------------------------------------------
section("1. The four that were open, in the markup that is shipped")
seo = (ROOT / "hub" / "templates" / "seo_client.html").read_text(encoding="utf-8")
admin = (ROOT / "hub" / "templates" / "users_admin.html").read_text(encoding="utf-8")
check("the client's website login is masked",
      'id="su_pass" type="password"' in seo, True)
check("the WordPress application password is masked",
      'id="wpPass" style="grid-column:2/4" type="password"' in seo, True)
check("...and it is still the field the code reads",
      "$('wpPass').value" in seo, True)
check("the admin's starting password for somebody else is masked",
      'id="umAddPw" type="password"' in admin, True)
# It is optional -- blank means the Hub generates one -- and the sentence
# saying so is a placeholder, which a password field still renders.
check("...and still says what leaving it blank does",
      "Leave blank and the Hub generates a starting password" in admin, True)
check("the admin's 'Set a password' box is masked too",
      'id="umPwValue" type="password"' in admin, True)
check("...and neither offers to remember it as an ordinary field",
      admin.count('autocomplete="new-password"'), 2)

section("2. The whole repo, which is the claim the gate makes")
check("no template takes a credential in a box that shows it",
      integrity.check_unmasked_secret_fields(), [])

section("3. The check bites")
# Every one of these is a tag the repo does not contain, because the repo is
# fixed. Without them the finding path is never taken and this file would be
# asserting that a check which cannot fail is passing.
check("an explicit type=text is reported",
      flags('<input id="su_pass" type="text" placeholder="Password">'), True)
check("so is no type at all, which defaults to text",
      flags('<input id="umAddPw" placeholder="a starting password">'), True)
check("...and type=email, or anything else that renders the value",
      flags('<input name="api_token" type="email">'), True)
check("a name gives it away as well as an id",
      flags('<input name="client_secret">'), True)
check("and so does a placeholder alone",
      flags('<input id="f1" placeholder="Application password">'), True)
check("api key, however it is spelled",
      [flags(f'<input id="{i}">') for i in ("apiKey", "api_key", "api-key")],
      [True, True, True])
# The three a word-boundary regex silently MISSES, because `_` is a word
# character and camelCase has no boundary either. Each one is a real
# identifier shape, and `\bpass\b` passes over all three.
check("an underscore is a separator, not a word character",
      [flags('<input name="api_token">'), flags('<input name="client_secret">')],
      [True, True])
check("...and so is a capital letter mid-word",
      flags('<input id="wpPass">'), True)
check("...which is exactly the field this change had to mask",
      flags('<input id="umPwValue" placeholder="Leave blank and the Hub '
            'generates one">'), True)

section("4. And it does not bite what it should not")
# The direction that is easy to get wrong in the name of safety. A pattern
# matching "passed" or "compass" reports files that are correct, and what that
# produces is a list people scroll past -- the failure docs/claude/58 is about,
# and the one tools/claudedocs.py made twice while it was being written.
check("the fix itself is not a finding",
      flags('<input id="su_pass" type="password">'), False)
check("a checkbox named for a token is not a box anybody types one into",
      flags('<input type="checkbox" name="save_token">'), False)
check("nor is a hidden field",
      flags('<input type="hidden" name="csrf_token">'), False)
for word in ("compass", "passed", "bypass_cache", "passenger", "surpassed"):
    check(f"{word!r} is not a password",
          flags(f'<input id="{word}">'), False)
check("a type the template computes is left alone",
      flags('<input id="f-secret" type="${f.secret?\'password\':\'text\'}">'), False)
check("...which is the shape modules/skills360 actually uses",
      "f.secret?'type=\"password\"'"
      in (ROOT / "modules" / "skills360" / "templates"
          / "skills360.html").read_text(encoding="utf-8"), True)
check("an ordinary field is not a credential",
      flags('<input id="su_login" placeholder="Login">'), False)
check("nor is a site URL beside one",
      flags('<input id="wpSite" placeholder="the website on this record">'), False)

section("5. It is registered, or it runs nowhere")
names = [row[0] for row in integrity.CHECKS]
check("the check is on /api/integrity", "unmasked_secret_fields" in names, True)
severity = {row[0]: row[2] for row in integrity.CHECKS}
# Medium rather than high: an unmasked box is a real exposure and is not the
# production-breaking kind the high band is reserved for, and a check that
# fails the build the day it is switched on is one people learn to route
# around. --strict fails on it.
check("at medium", severity.get("unmasked_secret_fields"), "medium")

print(f"\n{'-' * 60}\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
