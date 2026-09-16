## A dropdown that cannot hold the answer is worse than a text box

Every control on the four request forms is read off the live Knack object,
which is the rule these forms are built on: a dropdown's choices are Knack's,
and a form that guesses one writes a value Knack refuses over the *whole*
record. So `multiple_choice` becomes a select, `boolean` a yes/no, and a field
publishing no choices degrades to a text box, which is honest.

**A connection is the one control that can be a picker and still be wrong.**
`connection_choices()` asked for `rows_per_page=500` and returned whatever came
back, so a connection pointing at the client book or the insertion orders came
back **alphabetically truncated** — 500 of several thousand, in a complete-
looking `<select>`, with nothing saying so. A rep who cannot find their IO in
it concludes it does not exist; and `coerce_field` then refused a typed name
as matching "no record on this connection (of 500)", quoting the fraction as
though it were the book. Both halves read as a fact about the client's record
rather than about our paging.

`connection_records()` reads Knack's own `total_records` beside the page and
reports `truncated`, `connection_note()` turns it into the line the field
carries, and the refusal names the real total and `KNACK_CONNECTION_LIMIT` —
the variable that fixes it, because a warning nobody can act on is furniture.
A full page with no count published is *assumed* truncated: a page that came
back exactly full is the shape of a list with more behind it, and that is the
safe way to be wrong. A picker that is complete gets no note at all, or a
warning on every field is a warning nobody reads.

It also stops conflating the two empties. An object with no records and a read
that failed both came back as `[]`, and the form said "could not be read"
about both — the `connected_accounts_result()` rule from Google Finder, one
form later. `error` is set only for the second.

**And what the Hub knows is offered wherever Knack publishes nothing.** The
client's own campaigns, IO numbers, products and **media partner** ride on the
fields as `suggest`, which the drawer renders as a datalist: it suggests
without restricting, because the IO that needs help is not always one we hold
a row for and a picker that refuses an unknown number is a form somebody gives
up on. The partner was the one value on those rows that the campaign support
form did not offer while the ad copy form offered it from the identical data.
