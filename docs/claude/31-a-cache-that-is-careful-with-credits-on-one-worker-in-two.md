## A cache that is careful with credits, on one worker in two

`modules/bg_remover`. The module docstring opens by saying it is deliberately
careful with credits and lists how: the balance is read before you spend
anything, files are validated so a credit is never spent on something that was
going to fail, and **results are cached by content hash, so re-running the
same image — a double-click, a retry after a resize tweak — is free**. The
last of those was a module-level dict, and gunicorn runs two workers.

So it was free about half the time. The second request lands on whichever
worker the balancer picks, that worker's dict is empty, remove.bg is asked
again and charges again. Every screen reports a clean success either way, and
the only evidence anywhere is a credit balance falling faster than the number
of cut-outs anybody made — which nobody reconciles, because the tool has an
account panel that reports the balance and no reason to doubt it. That is the
`_state`-is-per-process trap this file already names for the scheduler panel,
`clients_registry`'s two-minute cache and `suite_panel`'s double-submit claim,
on the one module whose stated design goal is not spending money twice. The
claim is a file on the shared data disk now, exactly as suite_panel's is.

**It is a cache and it is written as one.** Deliberately not through
`hub/jsonstore.py`: there is nothing to restore, a wiped disk costs one credit
on the next retry and no data at all, and mirroring a few megabytes of PNG per
cut-out into the database to protect against that would be the backup rule
applied where it does not earn anything. It is bounded on **total size** as
well as on age — an unbounded cache on the 5 GB disk takes every other module
with it — and nothing in it may raise, because a cache that can break the tool
it accelerates is worse than no cache: every failure in it costs a credit on a
retry and nothing else. A read that hits the disk warms the worker that
missed, so the next request on that worker is local.

**A cut-out was filed with dimensions the tool had already measured.**
`api_save` passed `width=res.get("width")` into the client's gallery, and
`res` is built from a `StoredAsset` — which has no `width` field and never
has. So every cut-out this tool has ever filed carried `None` for both, in a
row whose whole purpose is to describe the image, while `api_remove` opened
the identical bytes two functions earlier to draw the result panel and threw
the answer away. `_dimensions()` is the one reading now, called from both, and
it answers `(None, None)` for something it cannot read rather than zero. It
measures **what is being stored**, not what the browser says: a dimension the
page supplies is one the page can be wrong about.

**And two caps contradicted each other, with only one of them on screen.**
The page offers ten images at twelve megabytes each; `MAX_CONTENT_LENGTH` was
set to forty. A batch inside every rule the page states was therefore refused
by the framework *before the view ran* — and Werkzeug answers that with an
**HTML** 413, which `.then(r => r.json())` cannot parse, so the page reported
`Failed: SyntaxError` and said nothing whatever about sending fewer at a time.
Every one of this module's own carefully worded per-file refusals — over the
size, not an image, empty file, more than ten — sits behind that gate and was
unreachable for the batch that most needed one. The cap is **derived** from
the two numbers the tool actually offers rather than typed a third time, so
the framework cannot go back to refusing what the screen invites, and a 413
reached from any other direction now arrives as a sentence in the same JSON
shape as every other refusal here. `/health` reports all three numbers, so a
screen need not restate them either. `test_utm_bg_tools.py` asserts all of it.
