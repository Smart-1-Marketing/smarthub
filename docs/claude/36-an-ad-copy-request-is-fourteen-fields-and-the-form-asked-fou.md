## An ad copy request is fourteen fields, and the form asked four

`hub/ad_copy.py`, `/api/client/ad-copy`, and the button on Client 360 and on
the dashboard. Ad Copy was a **Campaign Change Request with its subject
pre-written** — one shared object, four boxes, and a rep retyping the client,
the campaign, the current order number and the media partner out of the
record on the screen behind them. The campaign team's own form has fourteen
fields, and ten of them arrived blank on every request the Hub raised, so the
first thing anybody did with one was go and ask.

The ids are pinned (`AD_COPY_FIELDS`, one environment override each) for the
reason `hub/knack_api.py` gives at length. What is **not** pinned is the
object: nobody has told us its number, and inventing one writes ad copy
requests into whichever object answers — which reads on every screen as a
form that worked. So it is **discovered from the ids**: whichever object
carries `field_1804` is the Ad Copy Request object. Knack publishes each
object's fields inline on some plans, and where it does that is one call;
where it does not, the two request objects this Hub already knows are tried
before the walk, the walk is bounded, and a discovery that fails **names
`KNACK_AD_COPY_OBJECT`** rather than falling back to something plausible.

Four rules on the prefill, each a way to be confidently wrong on a form
somebody sends without re-reading it:

- **Exactly one candidate, or none.** A client with two campaigns gets a
  dropdown; a client with one gets it filled in, with the order number and
  the media partner that campaign's insertion order carries. Filling in the
  first of two is a plausible answer nobody proof-reads, which is worse than
  a blank — the `client_key` rule, wearing a prefill.
- **Nothing is invented.** The due date, the deadline and the change itself
  are blank: there is no source for any of them here, and a date the Hub made
  up is a date the campaign team works to. Submitted Date is the clock,
  because sending the form *is* submitting, and Status opens on whatever
  **Knack itself publishes** as that field's default — reading a default is
  not the same as choosing one.
- **An empty answer says which kind of empty it is.** "This client has no
  insertion orders", "we could not read the client list" and "this session
  has no account behind it, so there is no email address" are three
  situations and they read identically as a blank box. `current_user()` is
  deliberately *not* what fills Seller Name: it answers **"Shared login"** for
  a `PANEL_PASSWORD` session, which is a true statement about the session and
  a wrong one in a box the campaign team reads as a person. `NOT_A_PERSON`
  holds that refusal for the *write* as well as the prefill — the write has
  its own attribution fallback, and a rule the form keeps while the write
  breaks it is not a rule.
- **A file field is not a text box.** `field_1813` is a Knack file field, and
  a file field is written by uploading the bytes to Knack's own asset endpoint
  and putting the id it hands back on the record. Posting a string into it
  writes nothing — and because Knack refuses the whole record over one bad
  value, it would cost the request rather than the attachment. It is drawn,
  disabled, saying where files actually go, because a request sent believing
  the artwork went with it is worse than one that says it did not.

The write goes through the same `knack_api.coerce_field` that writes tickets
and website records, so a value Knack would refuse is refused **here**, by
name, and the rest of the record still goes. The request is logged under
**`ad_copy`**, not `hub`: `client_brand.work_log()` skips a module its own
table cannot name, and a skipped module reads on the record as a client
nobody has done any work for — the `display_ads` failure, one tool later.

**And the controls are drawn once, not once per object.**
`hub/static/knack-form.js` draws this form, the web ticket and campaign
support — three objects asking one question. What is decided *here* rather
than in the browser is which of this client's own answers each field can
offer: `_decorate()` hangs the campaigns, the order numbers, the partners and
the client's URLs onto the fields as `suggest`, and the drawer renders them as
a datalist, which suggests without restricting. There is deliberately **no
JavaScript mirror of the prefill** — target areas and the creative classifier
each carry a mirror already and each needs a test proving the halves still
agree. The one thing the page decides for itself is that picking a campaign
fills in the order number beside it, and it writes into the box rather than
redrawing the row: a container that re-renders while somebody is typing into
it eats what they typed. `test_ad_copy.py` asserts all of it.
