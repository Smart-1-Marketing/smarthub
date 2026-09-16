## A placement is judged by its leads, so the page counts them

The scan widget --- the embeddable AI-visibility check --- is built at
`/scans/widgets`, and it was reachable only from inside Site Scans. It is a
lead-capture page you paste on a client's site, which is nobody's idea of a
site scan, so it now has its own tile on `/tools` under **Landing Pages**
beside the industry pages doing the same job. The implementation stays in
`modules/scans`: the placements, their runs and the pages those placements
serve are all there, and a second home for it would be a second description of
what a placement is.

The list said nothing about whether a placement had ever produced anything.
`leads.placement_stats_result()` answers that, and every rule in it is a way to
be wrong quietly:

- **A check is not a lead.** A public box on somebody's home page is typed into
  by passers-by; the number that matters is the visitor who handed over a name,
  business, email and phone, which is the same moment the row is written to
  `hub/leads.py`. Counting runs would report a placement converting nobody as
  the best one we have. Both numbers are shown, the lead as the headline.
- **`(stats, error)`, never a bare dict**, for the reason
  `connected_accounts_result()` gives in Google Finder: *nobody has used this
  placement* and *we could not count* are different answers, and the first is
  what somebody deletes a placement over. A failed read reads **not measured**
  on the page and **blocks the delete** rather than drawing a column of noughts.
- **A lead captured but not filed is counted apart.** `_capture_lead` writes
  `lead_id=""` when hub.leads answers without an id, so the count uses
  `nullif` --- an empty string is not null, and a plain count would file a real
  person nobody can find in the panel as filed.
- **Runs count only from the placement's own `created_at`.** A slug deleted and
  created again is a *different* placement at the same address --- the embed
  code is the same three lines --- so without that the old one's leads land on
  the new one's total, which is the single number the column exists to state.

Three actions, and the difference between them is said out loud on the page.
**Pause** leaves everything where it is and is undone by pressing it again; the
embed on the client's site says the scan isn't available. **Edit** names the
placement it is editing --- the route used to upsert on whatever the name
slugified to, so a second placement called "Smart 1 home page" silently
replaced the first: same address, new headline, and the only sign was a list
that did not get longer. **The address is not editable at all**, because it is
in the embed code already pasted on somebody's website and renaming it takes
the widget off that page while this screen reports a clean save. **Delete** is
refused once for a placement that has captured leads, with the count in the
refusal, and it deletes the placement only --- the runs stay, because they are
the evidence of where a real person in the Leads panel came from.

The lead count links into `/sales/leads?page=<tag>`, and the panel reads `page`
and `days` off the URL now; without that the link opened on every lead for
thirty days and the count on this page looked wrong rather than unfiltered.
`test_scan_widgets.py` asserts all of it, the tile included --- a tool with no
tile is invisible, and this file counts six that were.
