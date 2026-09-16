## Deleting a client destroyed four tables and recorded none of it

`modules/commercial_builder` was the module the walk had been quietest about,
and triaging it found the same shape both earlier triages found, twice over:
it recorded **four** of its forty-three write routes, and the creating *and*
destroying halves of both its create/destroy pairs were among the thirty-nine.
A brand profile a rep spent an afternoon on appeared from nowhere and left the
same way.

**What the destroying half destroyed was not one row, and half of it did not
go.** `Client.projects` cascades and `CommercialProject` cascades to its scenes
and render jobs, so one unconfirmed DELETE took all of that. What it did not
take are the three tables keyed on a **project** that sit outside every one of
those relationships — the render approvals, the review shares with their
decisions and comments, and the compliance acknowledgments. Those stayed
behind pointing at ids that no longer resolve, which is not a record: a
compliance sign-off naming a project nobody can look up says nothing, and
those three are precisely the rows that exist for the day a client says **"we
never signed off on that"**. The route answered `{"ok": true}`, carried no
count of what had gone, and wrote nothing to the activity log — so the only
account of the deletion was the absence it left. Verified by running it rather
than read off the models.

`teardown.py` is the one reading of what a delete takes with it, because
`delete_project` had the identical failure one level down and would otherwise
have grown an identical fix — two readings of one question drift the day
either is edited. Four rules in it. **Nothing in it may raise**: a count that
cannot be taken must not cost the refusal it informs, and a sweep that fails
must not strand the delete somebody asked for. **The name is read before the
delete and it is the row's own**, never one the caller passed — the record
`modules/suite_panel` had to undo on the route that deletes a sub-account. A
client with work behind them is **refused with the counts named**, and a
`confirm` carrying their exact name is the way through, the rule
`modules/image_picker` applies to deleting a gallery: refusing outright would
be a check somebody switches off, and switching this off costs the recording
too. And **a spot does not weigh itself** — counted, every delete of an
untouched draft would come back asking the rep to type its title, which is the
friction that gets a confirmation clicked through without being read, and then
it is not a confirmation.

**The line the module records on is written down rather than left to
judgment**, because it is what decides all twenty-five declarations: a route
records when a file reaches the **client's own Cloudinary tree** or changes
their **own record**, and does not when it moves a draft forward. So the kept
voiceover records and the audition beside it does not; the upload records and
pointing a scene at an asset already in the library does not; and
`save_pronunciation` records, because it writes the same brand-profile field
`update_client` writes and two routes changing one field with only one of them
recorded is exactly what somebody would go looking for later. `client_comment`
was the subtler one: `review_spec.inbox()` already counts a client who left
four timecoded notes and pressed no button as having **answered**, so leaving
it silent while `client_decide` records means one reply reads two ways
depending on which control the client used.

Eighteen routes record and twenty-five are declared with their reason; nothing
is undeclared. `test_write_attribution.py` sweeps it like the other two — its
`path` takes a **list** now, because this module is a blueprint package and
`HOUSEKEEPING_ROUTES` belongs per file where the reason is, while the question
*is every write here attributable* is about the module.
