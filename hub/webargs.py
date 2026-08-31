"""Reading numbers a caller controls.

Twenty-odd endpoints take a `?limit=`, and every one of them wrote its own
parse. Three faults came out of that, each present in several places:

* `int(request.args.get("limit"))` outside a try. `?limit=abc` is a 500, and
  the traceback names a line about pagination rather than the missing guard.
* An upper bound but no lower one. `?limit=-1` reaches `rows[:-1]`, which
  silently returns everything except the last row — a wrong answer delivered
  with no indication anything was wrong, which is the failure mode this
  codebase treats as worse than an error.
* `max(1, max(1, min(500, ...)))` in two files: the same fix applied twice by
  people who could not tell at a glance whether it was already there.

`clamp_int` is the one implementation. It never raises, so a caller cannot
turn a malformed query string into a 500, and it clamps both ends, so a
caller cannot turn one into a wrong answer either.
"""

from __future__ import annotations


def clamp_int(raw, default: int, low: int = 1, high: int = 200) -> int:
    """A caller-supplied integer, forced into [low, high].

    Anything unparseable — None, "", "abc", "1e5", a list, a dict — becomes
    `default`, which is itself clamped, so a wrong default cannot widen the
    range the caller is allowed to ask for.

    Floats truncate rather than round: "10.9" is a request for 10 rows, and a
    caller who asked for 10.9 has no business getting 11.
    """
    if low > high:
        low, high = high, low
    # bool is an int subclass, so int(True) is 1 and float(True) is 1.0 —
    # both fallbacks would accept it. ?limit=true has no sensible reading.
    if isinstance(raw, bool):
        raw = None
    try:
        # OverflowError is on this branch too, and that is the whole of the
        # fix. It was on the inner one only — written for the string "inf",
        # which reaches it — while a real float("inf") is refused by int()
        # *here*, on the outer branch, and propagated: a 500 out of the helper
        # whose first promise is that it never raises. Not hypothetical, and
        # not only a query string: three call sites pass a **JSON body** value
        # straight in (`google_finder`, `video_backgrounds` and the Hub's own
        # blog planner), and Python's json.loads accepts the bare literal
        # `Infinity`, so {"limit": Infinity} is a float infinity by the time
        # it gets here.
        value = int(raw)
    except (TypeError, ValueError, OverflowError):
        try:
            # "10.9" and 10.9 both mean 10. Rejecting the string while
            # accepting the float would be an arbitrary distinction.
            # float() accepts "inf" and int() then refuses it.
            value = int(float(raw))
        except (TypeError, ValueError, OverflowError):
            value = int(default)
    return max(low, min(high, value))
