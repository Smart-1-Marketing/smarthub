## A client's document, published to the agency's own blog

`hub/ghl_blog.py` publishes a client's llms.txt into Smart 1 Suite as a blog
post — a public URL on somebody's blog, which is as client-facing as anything
here gets — and `_location()` fell back to `GHL_COMPANY_ID` /
`SUITE_COMPANY_ID`. That is the mistake `hub/ghl_contacts.py` spends a section
of its own docstring on (*"companyId is not locationId"*) and that
`hub/suite_opportunity.py` was fixed for, and **this was the third module to
make it**: on this deployment those variables hold the same value as the
company id, so a companyId went out as a locationId and **the client's
document was published to the agency's own blog**, under the agency's domain,
titled with the client's name.

Nothing errors, which is the whole difficulty. The agency location is a real
location with a real blog, real authors and real categories, so the post is
created, a URL comes back, and the only sign is that it is the wrong blog. The
location is its own setting now, and a value matching the company id is
refused **by name** — *"set to the same value as the agency company id"* and
*"no location is set"* send somebody to two different places.

**A guard switched off by exactly the failure it was written for.**
`slug_taken()` swallowed every error and answered `False`, and the caller
reads `False` as *there is no post at that address*. This module's own
docstring says a missing scope "produces a 401 from HighLevel that looks like
a bad token" — and `blogs/check-slug.readonly` is the scope whose absence made
that answer `False`, so a token missing it silently created the second post
the guard exists to refuse, the one leaving *"two files claiming to describe
the same client"* in the comment directly above it. It is tri-state now, and
the publish still goes ahead: refusing every first-time publish over a missing
read scope is a check somebody switches off, which is the `QR_CODE_RULES`
lesson. What changed is that the answer carries `slug_checked` and says the
check did not run, rather than implying it passed.

`check_access()` did not test that scope either — three of the four readable
ones, and the missing one was the only one whose failure is silent — so it
reported a token healthy that would go on to publish duplicates.

**And the link was built from the slug we asked for.** HighLevel suffixes a
collision rather than refusing, and `urlSlug` comes back on the response and
was never read: the address handed to somebody as the published one pointed at
a page that is not there. It reads the assigned slug now and says when the two
differ. A `blog_id` naming nothing fell through to `blogs[0]` the same way —
a stale id published a client's document to a different blog on a different
domain, reporting a clean success — and is refused, naming what is actually
there. A `domain` field arriving with its own scheme composed
`https://https://…`, which is a dead link presented as the live one.

`test_ghl_blog.py` asserts all of it, including that no refusal carries the
token: `BlogError`'s own docstring promises that, and a 401 body from
HighLevel has carried token fragments before.
