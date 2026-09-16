## A web ticket is eight fields, and the form asks for all eight

`hub/knack_api.py` pins object_107's field ids in `TICKET_FIELDS` — they were
pinned because label matching broke silently when a label was renamed, which
is how the Issue column on the Accounting report came to be empty. But pinning
an id is not the same as asking for its value: Client 360's ticket modal sent
a title, a website, a description and a name, and everything else on the
record was left blank on every ticket the Hub raised. `TICKET_CREATE_FIELDS`,
`TICKET_MANAGE_FIELDS` and `update_ticket()` existed with no caller at all.

The write set is title, client organization, media partner, client website
URL, type of ticket, revision requires billing, describe the changes, and
**are you ready to submit** (`field_1696`), which Knack's own workflow reads
and which a ticket arriving blank leaves sitting in nobody's queue. It is a
button rather than a question — sending the form is the act of submitting, so
it opens on yes and one click turns it off for a ticket someone is filing to
finish later. Revision Requires Billing is two radios, because a field with
two answers should not hide one behind a click. Partner Contact was on the
list and came back off it: pinned and read, not asked for.

The website URL opens on the record the ticket was raised from, with the
client's other sites offered beside it and the box still free text — the site
that needs the work is not always one we hold a record for.

The wider set this object carries — web services, the six service checkboxes,
the new website URL, the pause and cancellation fields, status, developer —
stays pinned and is still **read** for the ticket list. It is deliberately not
written: a form asking for a field nobody fills is how twenty questions became
eight answers and twelve blanks. `test_web_tickets.py` asserts that in both
directions — every one of the eight is written and drawn, and none of the
others is in either write set.

The form draws from the live object. It has to: the ids are ours, but a
dropdown's **choices** are Knack's, and Knack refuses the whole record over one
bad choice — so a value it would refuse is refused here, by name, and the
ticket is still created. `/api/client/tickets/fields` returns the control each
field needs; `/web-ticket.js` draws them, and Manage Ticket edits an existing
ticket through `/api/client/tickets/update` (the record id travels in the body
so the URL stays a literal `tools/linkcheck.py` can verify).

Three rules in that path, each of which is a way to lose data quietly:

- **A connection needs a record id, never a name.** Writing the display text
  creates nothing and clears the link, which is why create_ticket used to skip
  those fields entirely. `connection_choices()` offers the real records, and a
  name is resolved only when it matches exactly one of them — "Riverside HVAC"
  against "Riverside HVAC LLC" is refused and listed, not guessed, for the same
  reason `client_key.resolve()` refuses a substring.
- **Nothing is dropped in silence.** Both write paths return `rejected`, and
  both modals show it. A ticket created with half its fields missing must not
  read as a clean success.
- **Title is not editable after creation.** Renaming a ticket breaks the thread
  for whoever raised it, so it is in the create set and not the manage set.

Assigner and the discovered Requested By are written but never asked for —
nobody types them, and a ticket the web team cannot put a name to is one they
have to come asking about.

The audit module at `/tools/tickets` describes the same object, and used to
keep its own copy of the ids — two maps agreeing only for as long as somebody
kept them in step. There is one now: the audit's own field names
(`summary`, `details`, `assignee`, which its reports are written against),
mapped onto `hub.knack_api.field_ids()`. `field_ids()` is the pinned set with
its environment overrides applied and no schema read, so a module that only
wants the ids does not have to reach Knack for them.

That module also used to default its object to `""` and then tell you to go
and map it, on a deployment where the ids were pinned all along. It defaults
to the shared object now, and the fields nobody has pinned — the dates, the
ticket number, priority — are matched against the live object's labels on
first use, the same match the setup page performs when a person clicks
Auto-detect. **A saved map still wins**: someone who has corrected a guess
must not have it re-guessed under them. `test_web_tickets.py` asserts the two
name sets translate, so a pinned id that moves cannot leave a report column
reading a field that no longer means what its heading says.
