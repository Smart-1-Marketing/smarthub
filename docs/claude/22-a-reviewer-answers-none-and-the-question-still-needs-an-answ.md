## A reviewer answers "none", and the question still needs an answer

`hub/schema_questions.py` asks 35 questions and refuses to let a schema be
approved while any is unanswered. Its docstring is emphatic about why — *"An
empty field is honest; a plausible guess is not … structured data is consumed
by machines that treat it as fact"* — and ends the paragraph **"The block is
the feature."** The block could not be cleared for the commonest honest
answer there is.

`_blank()` reads `none`, `n/a`, `unknown` and `-` as an unfilled field. That
is right for a value coming off a **record**, where it means nobody typed
anything, and exactly wrong for one a person types into *"does the business
hold any licenses?"* — where it is the true answer for most small businesses,
as it is for awards, trade associations and a slogan. So a reviewer answered
`awards: none`, `save_answers()` stored it, `build()` read it back as blank,
and it came out **NEED ANSWER** again. They typed it, saved it, reloaded, and
approval was still blocked by a question they had answered. For ever.

What a person saved is kept apart from the record now and held to the looser
test: theirs is the best source there is, and the one place "none" means
none. **And the first fix for it did not work**, which is worth recording
because it is the same shape as the bug: `_lookup()` returned the typed
answer correctly and the line immediately after **re-tested it** with the
strict rule, because that call site cannot know the value came from a person.
It is `val is None` now — one reading, asked once.

**The panel contradicted itself on the one question it exists to answer.**
The GET builds with AI and reported `can_approve` on the strength of
inferences; the POST beside it calls `can_approve()`, which re-derives with
`use_ai=False` and turns every inference back into a NEED ANSWER. So the same
screen said *"Every question answered. Ready to approve."* in green on load
and *"N still marked NEED ANSWER, so approval stays blocked."* in red on
save — the `/api/db/structure` versus `/api/integrity` trap, on a schema
builder. `_blocking()` is the one reading, and an **AI answer blocks**: this
module says an inference is "always worth checking" and that a plausible
guess is worse than an empty field, so saving one is the check, and saving is
what unblocks it. The two paths now agree by construction rather than by
coincidence — without AI those rows are `needed`, with it they are `ai`, and
both count.

**And two confidence levels were always zero.** The docstring promised each
question is answered *"from the Hub's own records first, then the client's
website, then a web search"*. Neither of the last two is built — nothing
fetches their pages, and the AI call is told to use only what it is given —
and `by_confidence` reported `site: 0, search: 0`, which reads as *their
website had nothing on it* rather than as *nothing looked*. `NOT_BUILT` names
them with the reason and the two keys are gone from the count, the
`_KIT_UNREAD` rule one module over. `test_schema_questions.py` asserts all of
it.
