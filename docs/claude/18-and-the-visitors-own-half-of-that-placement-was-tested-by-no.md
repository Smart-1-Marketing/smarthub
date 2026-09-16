## And the visitor's own half of that placement was tested by nobody

The placement admin is the tool a rep opens. The three pages a **stranger**
meets on a client's own website — the widget, the audit form, the waiting
page — are the rest of the second-largest module in this Hub, and nothing
exercised them. Booting the visitor's path found four, and every one answered
a prospect confidently.

**The callback token reached Insites unencoded.** `_callback_url()`
interpolated `SCANS_CALLBACK_TOKEN` straight into a query string, and that
token is a secret somebody typed into Render rather than a string this code
chose: a `+` comes back as a space, an `&` or a `#` truncates it at the
receiving end, and `secrets.compare_digest` then fails on a token that is
perfectly correct. Every callback 403s — and `api_callback` **deliberately**
leaves a refused row `running` rather than errored, which is right for a
malformed POST and is what makes this silent: the audit we paid for never
attaches, the visitor's page polls until it gives up, and nothing anywhere
says why. This is where the `SCANS_CALLBACK_TOKEN="abc"` quoting trap this
file already names actually lands; the quotes survive the round trip and the
characters around them are what do not.

**And it was two lines the shared reader already held.** `_callback_url()`
exists and is called by both staff paths; `_start_widget_scan` built the same
string itself, so the encoding fix would have landed in two of the three
places a scan is started — the drift `hub/storage.py` exists to stop, wearing
a query string. The check counts the *compositions* rather than asserting the
call, so a fourth one cannot be added quietly.

**Both poll loops read `ready` and never `status`.** A run at `error` or
`unconfigured` will never become `complete`: the first is a provider that
refused, the second is a deployment with no Insites key, where the lead is
still captured — correctly, since a lead is a lead whether or not Insites ever
answers — and no audit is ever bought. Those were polled to the ceiling and
then told *"Your deep scan is still running"* and *"Still working … open the
link below in a few minutes and it will be there"*, which is the one thing
neither is. The status was on that response the whole time and nothing read
it: the failure `campaign_assets.report()` already has, where a warning is
computed and dropped on the way to the reader. `STOPPED_SCAN_STATUSES` is the
list, and it is **the server's** — `api_widget_status` answers `stopped` with
the sentence to show, because a second list of which statuses are over, in two
templates, is two more answers to one question. The server-rendered waiting
page had the same gap in Jinja: it tested `status == "error"` and not
`unconfigured`, so a Hub with no Insites key drew a spinner and a 30-second
meta refresh for ever, on a run where nothing was ever started. It stops
refreshing when there is nothing left to wait for.

**And two of those pages promised an email.** *"We'll email your report the
moment it lands"* and *"close it and open the link in your email when it
lands"* — to a stranger, on somebody else's website. **There is no mail sender
in this Hub**, which this file says five times over, so it was a promise
nothing here could keep; and the first one fired on every run that crossed ten
minutes rather than only on the failures. Both say where the report will be
instead, which is what the audit placement's own copy had said correctly all
along.

**A second unlock rewrote the contact on somebody else's run.** The lead is
filed once, properly guarded by `first_unlock` — and the four contact fields
were written unconditionally, directly beside an `unlocked_at` that
deliberately was not (`row.unlocked_at or _now()`). So a second post of the
same token left the run row naming one person while carrying the **lead id of
another**, and the run row is the evidence of where that lead came from: what
the placement list counts and what the report page prints. Anybody holding the
token can post that route, so the second name is not necessarily a typo being
corrected — and correcting the row without correcting a lead that has already
gone to Suite and is never re-delivered makes the two disagree rather than
agree. The contact follows the timestamp now.

**The email sweep is a sweep, and it caught two things a reading would not.**
A list of the two pages we fixed proves nothing about the third, so
`test_scan_run.py` reads every template this module serves without a login.
Two rules it needed. **Prose is not a call site**, for the sixth time in this
file: the first run reported the comment *explaining* the fix as the promise
it describes, so block comments and whole-line `//` ones are stripped — and a
mid-line `//` is left alone, because that is a URL. And **the copy was written
with a backslash in it**: the line that was live is a JS literal reading
`We\'ll email`, which a character class that did not allow one reads straight
past — a sweep that misses the sentence it was written to find is a sweep
reporting a clean page. Both are asserted against the exact text that was
there.
