## A review nobody wrote down is a review nobody can point at

`hub/qa_tasks.py`, `hub/qa_tasks_routes.py` and `/qa-tasks`. There are
twenty-odd tools here and nobody can test all of them, so the way a check
actually happens is that somebody is asked to do it — *open the Proposal
Builder, run a quote end to end, tell me what breaks.* That ask lived in a
chat message, and the answer came back the same way: no list of what had been
asked, no record of what came back, and no way to tell an open question from
one somebody answered three weeks ago. The QA Reports page says what is
**wrong**; this is the other half of the same question, which is how somebody
is asked to go and look.

**Anyone can assign, and the assigner owns the answer.** Not admins only — the
person who notices a page is wrong is very often not an admin, and a review
queue only an admin can fill is one nobody fills. What that costs is a rule: a
response goes back to *whoever raised the task*, keyed on `created_by_email`,
never to a shared inbox. It is in the nav for a General account for the same
reason: a queue behind a door the people answering it cannot open is not a
queue.

**Four states, and the two middle ones are the point.** `open` (nobody has
answered), `answered` (the assignee replied and it is now the assigner's
move), `needs_more` (the assigner read it and asked for something else) and
`complete`. A single `done` flag collapses the two *somebody is waiting*
states into one, which is precisely the thing anybody looking at this list
needs to tell apart — the distinction `hub/social_content.py` draws about a
round still out with the client.

**Which kind of post it is comes from who is posting**, never from a flag the
route passes: the assignee answering and the assigner asking for more are the
same action from the table's point of view, and letting a route decide would
be a second place the state machine lives. The reviewer **cannot close their
own review** — a review the reviewer signs off is a review nobody read — and
nothing is ever deleted, only completed: *what did we ask people to check
before the last release* has to be answerable a year from now.

**A need-by date is optional, and that is Todd's own rule.** Required, it gets
filled in with a guess. `overdue` is derived from it on read rather than
stored, so it cannot go stale the way a written flag would — `hub/creative_evergreen.py`'s
rule, and it matters more here because there are two gunicorn workers.

**The dropdown is read from the Hub, not restated.** `targets()` reads the nav
in `hub/sidebar.py`, the tiles on `/tools` and `/creative`, and the reports in
`hub/qa.py` — the same tiles `test_menu_layout.py` and `hub/help_coverage.py`
read, for the same reason those two do: a hand-typed list of tools is a list
that goes stale the week a tool is added. It comes to 111 entries today and it
still ends with **"Something else"** and a free-text box, because a dropdown
that cannot hold the answer is worse than a text box — a note this repo has
already had to write down once, about the ad copy request form.

**An attachment lives in the database.** Not the disk, which Render recreates
and `hub/jsonstore.py` exists because of; not Cloudinary, which is configured
per environment, and a review that cannot be filed because a key is missing is
a review that does not happen. It is a `LargeBinary` column inside the database
backup with `MAX_ATTACHMENT_BYTES` in front of it — enforced here rather than
left to the app-wide `MAX_CONTENT_LENGTH`, which is 512 MB because one module
renders video. It is served `Content-Disposition: attachment` with
`X-Content-Type-Options: nosniff`: rendering an arbitrary upload inline on the
staff origin is how a stored file becomes a script running on it.

**"Notify me of any new updates" is answered without a mailer, because there
is no mailer in this Hub.** That sentence appears in half a dozen modules here
and the answer every one of them arrives at is the same: put the number where
people already look. So there are two surfaces and they are deliberately
different. The **dashboard card** sits above Recent activity, reads the same
`for_person()` run the page draws — two screens counting the same thing
separately is how they come to disagree in front of one person — and keeps the
two queues apart, because *you have been asked to check this* and *somebody
answered and is waiting on your reply* are two different jobs. And
`hub/static/hub-qa-nudge.js` is the reminder for somebody who signs in and goes
straight to a tool, which is most people most mornings: once per person per
day, marked when it is **shown** rather than when it is dismissed, silent
inside an iframe (a staff to-do list in a client-facing panel is an internal
note in front of a client), silent when nothing is waiting, and a corner card
rather than a modal — the change `hub-help.js` was made for. It is loaded from
`base.html` alone and not from the chrome `HubBar` injects, the rule
`hub-cheers.js` gives: one place that can raise an interruption is how you
stay sure it is raised once.

**What is new to you is what has moved since you last looked.**
`assignee_seen_at` / `owner_seen_at` are one comparison against
`last_activity_at`, rather than a per-message read flag that would have to be
written for both people on every post — and they are stamped by **opening one
task**, never by loading the list. A badge that clears itself because somebody
glanced at a dashboard is a badge that stops meaning anything.

**A shared-password session is told so rather than shown somebody's book.**
`PANEL_PASSWORD` grants a session with no account behind it, so there is no
email to key on — the `hub/ad_copy.py` refusal, where "Shared login" is a true
statement about the session and a useless one where the whole value is whose
it is.

**Every reader answers with `measured` and a sentence, never an exception.**
A SQLAlchemy `OperationalError` carries the database host and the SQL, and
both screens interpolate this straight into a page; the cause goes to the log.
An empty QA list is read as permission to stop looking, so *we could not read
the table* must never render as *you have nothing to do* — which is why the
dashboard card and the reminder each branch on it.

The blueprint carries `hub/blueprint_guard.install()`. Nothing on it is
public: every route names a member of staff, what they were asked to check and
what they said about it. `test_qa_tasks.py` asserts all of it.
