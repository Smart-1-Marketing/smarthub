# The client passwords in the backup

Step 1 of the Client Setup modal on the SEO page asks four questions: how do
you get into this site, at what URL, with what login, and with what password.
The rep types the client's real password for the client's real website, and
until September 2026 the answer went straight into
`data/seo/<client>.json` as:

```json
"setup": {
  "access_method": "WordPress admin",
  "login": "admin@theirsite.com",
  "password": "the actual password"
}
```

## Why that was worse than one file on a disk

That store goes through `hub/jsonstore.py`, which is the whole point of it —
the Render disk those files sit on is outside the database backup and does not
survive being recreated, so every write is mirrored into Postgres. Which means
every client site password ever typed into that modal was also written verbatim
into Postgres, and from there into **every database backup taken since**. Not
one copy in one place: the whole book of them, in plain text, in every backup
anybody has ever downloaded.

`TOKEN_ENCRYPTION_KEY` and Fernet have been in this Hub since Google Finder.
`hub/cms_credentials.py` was written *about* this value — its docstring names
`setup.password` as the reason it exists, describes the sealing, and then did
not hold it.

## Three things were true at once, and only one was a defect

1. **Nothing ever read it back.** It is written by `/api/seo/setup` and read in
   exactly one place, `client_detail()` in `hub/seo.py`, which strips it and
   turns it into a boolean. No screen, no route, no export, no prompt has ever
   returned it. So there was no feature to preserve.
2. **It was in every backup.** Which is the defect, and is not made smaller by
   (1) — a value nobody can use is still a value anybody with a backup can read.
3. **It is still collected.** See the open question at the bottom.

## Where it lives now

`hub/cms_credentials.py`, as a second kind beside `WORDPRESS`:

- `SITE_LOGIN` — the human's password, sealed, and **nothing authenticates with
  it**. It is not interchangeable with the application password the WordPress
  API calls are made with, and keeping them in one file with two kinds is what
  stops the second store drifting from the first.
- `save_site_login()` — a blank password **keeps** the stored one. The setup
  form sends the field only when somebody typed in it, and eleven other answers
  on that form are saved by people who never touch it.
- `site_login_state()` — a subset, built rather than filtered, and it never
  carries the password.

## The rule that is easy to get wrong

**A deployment with no key must not "migrate".**

`_seal()` falls back to storing the raw value when `TOKEN_ENCRYPTION_KEY` is
unset — deliberately, so that a save can still happen and the panel can say out
loud that it was stored in the clear. Which means a migration that ran anyway
would move a plaintext password out of one `jsonstore` file and into another
`jsonstore` file, mirrored into the same Postgres and the same backups, and
report a number as though something had improved.

So `adopt_plaintext_site_login()` refuses there and the SEO record keeps its
password exactly where it is. `/status` already says why. This is the assertion
in `test_site_login.py` that is worth the most, and the mutation that removes
it takes six checks red.

## It moves exactly once, through three doors

The migration shape `docs/claude/` already records paying for — in
`hub/ad_assets.py` — is the one that rewrites its source on every read. This
one drops the plaintext key in the same save, so the second pass finds nothing:

- **opening the client's SEO page** — `client_detail()`, before anything reads
  `setup`, so the dict it builds describes the disk afterwards;
- **saving the setup form** — first thing in `/api/seo/setup`, ahead of the
  edits, because that route writes the store itself;
- **the scheduler**, twice a day — `job_seal_site_logins`, which is the only
  one of the three that reaches a client nobody opens a page for, and those are
  most of them. Local disk only: no network, no model, no provider.

The sweep keys on the **name inside each store**, never on the filename. The
file is named for `slugify(client)` and `hub/cms_credentials.py` keys on the
name, so a store with no name recorded is counted and skipped rather than
filed under a slug nothing would ever find again.

## What the panel says now

The setup modal gained one row under the password field, and it distinguishes
the three states `hub/cms_credentials.py` keeps apart rather than showing a
tick:

- **on file and encrypted**, with who saved it and when, and that it is never
  shown;
- **on file, stored in the clear**, quoting `encryption_state()`'s own note —
  said out loud rather than passing as encrypted;
- **on file and cannot be read**, because the key was rotated. Never "no login
  on file". Reporting the third state as absent is what sends somebody to ask a
  client for their password again when the one stored was fine, and it is what
  `connected_accounts_result()` cost Google Finder months over.

And a credential store that cannot be written no longer reads as "Saved" — the
setup answers still store, and the answer says which half did not.

## And a check, so it cannot come back quietly

`check_plaintext_credentials` on `/api/integrity`, at high severity. It is the
mirror image of `check_unbacked_json` sitting beside it: that one asks whether
a store is copied into the database, and this store **was** — being mirrored is
what carried every password into every backup. A store can pass every other
check on that page and still be the worst file on the disk, and until this
nothing asked.

Two decisions inside it are worth keeping:

- **It reads the files, not the source.** A dataflow rule would have missed the
  defect it was written for: the password was not assigned under a literal key
  but copied in a loop over a tuple of field names, which no reasonable AST
  rule catches without reporting half the login routes too. The consequence is
  that it finds nothing in CI, where the data root is empty, and answers for
  real on `/api/integrity`. `tools/integritycheck.py` prints how many stores it
  actually read, so an "ok" on a machine with no data cannot be mistaken for a
  clean bill of health.
- **A sealed credential is a dict.** `{"enc": true, "data": "..."}` is skipped
  by the *shape* of the value rather than by being named in an exemption list
  somebody has to maintain. Only a credential-shaped key holding a bare string
  is reported.

`CREDENTIAL_KEYS` is deliberately short. `token` and `secret` on their own are
not in it: a share token in `hub/radio_share.py` is stored precisely so a
customer's link keeps working, and a check that reports it is a check people
learn to switch off.

## And something has to run the check

A high-severity check nothing runs is not a check. `integrity.run()` had
exactly one caller — the `/api/integrity` JSON route, fetched by
`/diagnostics`. That is a page somebody has to remember to open, which is the
phrase `tools/integritycheck.py` uses about the state it was written to fix.

The command line fixed it **for the checks that read the source**, because CI
runs those on every pull request. It could not fix it for
`check_plaintext_credentials`, which reads the JSON stores on the data disk:
in CI that disk is empty by construction, so the one check whose answer exists
only in production was the one nothing in production ever ran.

`job_integrity_audit` runs the sweep under the leader lock, twelve-hourly.
Three rules make it worth having:

- **Transitions, not a heartbeat.** A finding is written when it *appears*,
  and a `cleared` row when one that was there is gone. A steady state writes
  nothing, so any row in that module is worth looking at. Logging every open
  finding twice a day is how the activity log fills until nobody reads it —
  the failure `rotate_audit_log` beside it exists because of.
- **The fingerprint is the check plus where it points, never the prose.** A
  detail string reworded in a later release would otherwise read as the old
  finding clearing and a new one appearing.
- **No row carries the credential.** The activity log is itself mirrored into
  Postgres, so a credential detector that logged the credential would put the
  password into the backup it was written to keep it out of. `finding` rows
  carry the check, the label and the file — never the value. This is asserted
  directly, because it is the one mistake that would undo the whole chain.

## The open question, for Todd

**Nothing in the Hub can retrieve this password.** That was true before this
change and it is still true; sealing it was not the moment to start handing it
back. So the modal collects, on every client, a credential that has never once
been readable by the person who typed it.

There are two honest answers and this change deliberately picks neither:

- **make it retrievable** — a reveal button on the setup panel, behind the
  admin role, with `audit.log()` on every read, which is what a password
  the rep is meant to use again looks like; or
- **stop collecting it** — drop the field, and let the site login live in
  whatever password manager the team already uses, which is what a credential
  nobody reads looks like.

Collecting it and never showing it is the one answer that has no case for it.
