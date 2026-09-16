## A campaign support request is twenty-three fields, and we sent four

`object_121` has carried the whole of a support request for years — the
insertion order, the due date, what kind of support is being asked for, the
pixel URL, the timeline, whether it is a rush and why, who to notify at the
client and at the partner, the notes, the IO number, the campaign and the
product. The Hub sent a subject, a description, a client name and an IO, and
every other field arrived blank on every request Client 360 and the dashboard
raised. Nothing errored: the campaign team filled the rest in by going back
and asking, which is the same state the web ticket was in before
`TICKET_FIELDS` was pinned.

The ids are pinned now, in `hub/knack_api.SUPPORT_FIELDS`, each overridable by
`KNACK_SUPPORT_<KEY>`. **Pinned, not matched by label**, for the reason that
file gives at length — the old map found its six roles by looking for the word
"request" or "name" in a label, so a subject landed on whichever field matched
first and a rename would have moved it again in silence.

**Every option on the form comes off the live object, and none is invented.**
The ids are ours; a dropdown's choices, the records a connection may point at
and whether a field is a date or a paragraph are Knack's. So Campaign Support
offers its own multi-select, Timeline and IOP Status their own dropdowns,
Insertion Order and Media Partner and Client real record pickers, and Notify
Client? a yes and a no. A field Knack publishes nothing for degrades to a text
box rather than an empty picker — a form that guesses a choice writes a value
Knack **refuses the whole record over**, which costs the request rather than
the field. `coerce_field` catches one before it is sent and names it.

Four more rules in it.

**A field that cannot be written is drawn and says so.** Uploaded Files is a
Knack file field, written by its own upload call and not by a value on the
record, so a box for it would take a filename and drop it. It is on the form
as a line saying where files actually go — a deliverable left off entirely is
one nobody knows to supply.

**The subject leads the issue, because this object has no subject field.**
Folding it into `field_1819` is checkable; writing it onto whatever matched
"name" was not.

**What the Hub already knows is a suggestion, never a restriction.** Picking a
campaign from the client's real insertion orders fills the IO number, the
campaign and the product, and those stay editable behind a `datalist`: the IO
that needs help is not always one we hold a row for, and a picker that refuses
an unknown number is a form somebody gives up on. The Knack `client` field is
resolved to **exactly one** connection record or left for the rep — a near
match is not a match, the `hub/client_key.py` rule.

**Nothing is dropped in silence.** Every write returns `written` and
`rejected`, and both modals show the second: a request created with half its
fields missing must not read as a clean success.

The controls themselves are `hub/static/knack-form.js`, shared with the web
ticket form — the two ask the same question of two objects, and a second copy
of that renderer is the failure this file names twice already. `wsgi.py` does
not serve it; `hub/help_routes.py` does, root-level like the scripts beside
it, and the three templates that load a form load it first.
`test_campaign_support.py` asserts all of it, including that every field the
campaign team named is both written and drawn — a field can be pinned, be
writable, and still be on no screen, which is exactly the state this object
was in.
