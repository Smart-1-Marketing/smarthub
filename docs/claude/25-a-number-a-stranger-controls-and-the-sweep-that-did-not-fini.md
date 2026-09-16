## A number a stranger controls, and the sweep that did not finish

`hub/webargs.py` is fifty-one lines reached from twenty files, and its
docstring is a list of three faults it was written to end: `int()` outside a
try (`?limit=abc` is a 500), an upper bound and no lower one (`?limit=-1`
reaches `rows[:-1]`, *"a wrong answer delivered with no indication anything
was wrong"*), and the same clamp written out twice by people who could not
tell whether it was already there.

The helper is right. **The sweep it implies is what did not finish**, and
each of the three call sites left over is reachable from a URL. Smart 1 Ads
searched the client list with `limit=min(int(…) or 12, 50)` over a
`search_clients()` that ends `[:limit]`, so `?limit=-5` returned every client
except the last five as a clean answer. The Suite panel clamped both ends of
its audit-log limit and had no try — on the activity log of the panel that
creates and deletes client sub-accounts, which is the record somebody
reconstructs an incident from. And the Commercial Builder's stock search had
**neither**, on a `per_provider` that goes straight into
`pexels_service.search()` and `pixabay_service.search()` once per expanded
query: an unbounded caller-controlled fan-out to two billed providers.

**And the check that exists for this found none of them.**
`check_unclamped_limits()` matched the read as **text** and then skipped any
window containing `min(`, `max(` or `clamp` — a guard against crying wolf
that made it blind to precisely the two shapes that were live, because an
upper bound with no lower one contains `min(` and both-bounds-no-try contains
both. It also needed `hub/webargs.py` exempted **by name**, because that
file's docstring quotes the bad pattern to explain it — prose is not a call
site, for the fifth time in this file, and it duly reported the new test
file's own fixtures three times over. It reads the AST now and asks two
narrow questions: a bare `int()` over a caller's value outside a try, and a
`min()` over one with no `max()` or `clamp_int()` around it. Both empty the
day it changed.

**The helper's own promise had a hole in exactly the place its comment
names.** *"OverflowError is here because float() accepts 'inf' and int() then
refuses it — the one input that still crashed a helper written to make
crashing impossible."* That guard was on the **inner** branch, which is the
one the *string* `"inf"` takes; a real `float('inf')` is refused by `int()`
on the **outer** branch and propagated. Not hypothetical and not only a query
string: Python's `json.loads` accepts the bare literal `Infinity`, and three
call sites pass a JSON body value straight in — `google_finder`,
`video_backgrounds` and the Hub's own blog planner — so `{"limit": Infinity}`
was a 500 out of the function whose first promise is that it never raises. An
infinity now takes the documented fallback to the **default** rather than the
ceiling, which is what `"inf"` and `NaN` already did: `"1e5"` is capped
because it parses to a real number above the ceiling, and an infinity parses
to no number at all.

`_page_arg()` in the Suite panel was the third fault standing on its own —
the same rule, worked out independently and correctly, in a module that could
have imported it. That one was **not** a defect, and it is worth saying so:
the only observable difference is the shared rule's own, that a float
truncates rather than being thrown away for the default.
`test_suite_panel.py` asserted it by matching the literal `max(lo, min(hi,
int(` in the source — the implementation restated in the test, a third thing
to keep in step, which duly failed on a change that made the code better. It
drives the function now.
