# The client website password that was in every backup

`hub/cms_credentials.py` opens by explaining why it holds a WordPress
application password rather than a site login, and names this in passing:

> the SEO store's own `setup.password` is written to `data/seo/<client>.json`
> in plain text and, because that goes through `hub/jsonstore.py`, mirrored
> verbatim into Postgres and into every database backup.

That was written as deferred work, and stayed deferred. This is it.

## The disk is not the exposure

A rep saves a client's CMS access on the SEO record — method, URL, login,
password — and `/api/seo/setup` puts each field into `store["setup"]`. That
store is written with `jsonstore.write_json()`, which **mirrors every write
into Postgres on purpose**. So the credential that opens a client's live
website was a plain string in the database, and therefore in every backup
taken since it was saved.

Sealing it now keeps it out of the **next** backup. It does nothing about the
ones already taken, and nothing in this change should be read as though it
does.

## Nothing reads it back, and that is not why it goes

The first instinct after `docs/claude/58`'s check images is the same answer:
write-only, so stop keeping it. It really is write-only — driven rather than
assumed, with a sentinel value and a sweep of **261 responses** across every
SEO, client, backup and database route. The password is in none of them.
`client_detail()` strips it and the record page only ever learns whether one
exists.

**It still goes in, sealed, rather than away.** The check images were consumed
at upload time by OCR, so dropping the bytes lost nothing anybody could ever
have wanted. This field is different: the page says *"(saved — leave blank to
keep)"*, so a rep is deliberately saving a credential they expect to persist,
and that no route returns it reads as an omission rather than a decision
somebody made. Destroying it would be this change deciding something it was
not asked to decide. Sealing loses nothing and closes the exposure; the
missing read path is reported instead, and `setup_password()` exists so the
next person needing one does not reach into the raw dict and get a blob.

## One copy of the sealing

Three files had already written Fernet-over-`TOKEN_ENCRYPTION_KEY`:
`hub/cms_credentials.py`, `hub/ghl_oauth.py` and
`modules/check_reconciliation/app.py`. A fourth caller is where it moves rather
than being copied — the rule `hub/jsonstore.py`, `hub/storage.py` and
`hub/images.py` exist to enforce.

`hub/sealing.py` is the shared one. `hub/cms_credentials.py` is a thin binding
over it and `test_wordpress_publish.py`'s 102 checks pass unaltered. The other
two are untouched: they were not being edited, and the opportunistic-migration
rule is about the module you are already in.

## It seals in `save_store()`, not in the route

More than twenty callers load this store, change one key and write the whole
thing back. A route that sealed for itself would leave every one of them able
to put a plain password beside a sealed one with nothing saying which it was.
`save_store()` is the one door onto the file, so the seal is there — the same
reason `docs/claude/58` records `_write_state()` being deleted rather than
left as a second door.

Which makes idempotency load-bearing rather than tidy: the ordinary path is
read-modify-write, and a seal that fires twice produces a value unreadable
with the very key it was saved under. Asserted directly, and the mutation that
double-seals turns two checks red.

## The bug the test found in the check

`plaintext_password_clients()` was written to look for a bare string, because
that is the legacy shape. It is not the shape that matters.

With no key configured, `seal()` returns `{"enc": False, "data": "…"}` — the
stored shape **without** the protection. That is what a live deployment
actually produces when `TOKEN_ENCRYPTION_KEY` is missing, so a scan looking
only for a bare string would have reported a clean bill on precisely the
deployment with the problem. `sealing.is_plain()` knows both shapes, and the
sweep counts them as one thing because they are one thing.

Found by writing the assertion, not by reading the code.

## No marker, and the reason is the backups

`modules/check_reconciliation`'s upload sweep runs every boot with no marker
because its directory is *local to an instance*, so a shared "already done"
would leave a second instance's disk untouched for ever. This store is
mirrored, so that argument does not apply — and a marker would **still** be
wrong, for a different reason: the plaintext is in the database backups, and
restoring one puts it back. A sweep that had marked itself done would then
leave it there.

With no marker it self-heals. Asserted by writing a plaintext store back over
a sealed one and running the sweep again.

## Three states, and the one that is always collapsed

`hub/sealing.py` keeps sealed, stored-in-the-clear and cannot-be-decrypted
apart. The third is the one worth the words: a rotated key must read as an
error and never as "there is no password". `connected_accounts_result()` in
Google Finder is the precedent — a rotated key reading as an empty book cost
that module months of silent failure — and here it would send a rep to
re-enter a credential that is already there, while the record page quietly
dropped its "saved" placeholder. `has_secret()` is separate from `unseal()`
for exactly that: the page still says a password is on file.

## On the panel, because an unsealed value looks like nothing

`/diagnostics` carries a row. It counts **what is on the disk**, not what the
key can do — a green row means no store still holds a plain password, which is
a stronger claim than "sealing is configured" — and it names the clients. It
says, even when green, that backups taken before the seal still contain them.

## What is reported and not fixed

- **`/api/seo/setup` has no server-side guard on a blank password.** The page
  keeps its "leave blank to keep" promise in JavaScript (`seo_client.html`
  omits the key when the field is empty); the route does not, so any other
  caller posting `""` clears it. Left alone because adding a guard removes the
  only way to clear one, and no screen offers another.
- **The field is `<input type="text">`**, so the password renders visible as
  it is typed.

## Render environment

**`TOKEN_ENCRYPTION_KEY`** — check it in the linked env group first rather
than adding it to the service; a service-level value overrides the group's
(`docs/claude/03`), and the two then disagree with nothing saying so.

It is already declared in `render.yaml` with `sync: false`, and Google Finder's
refresh tokens have depended on it since before this change, so on a
deployment where Google accounts work it is set. If it is **not** set, nothing
breaks and nothing looks broken: passwords are saved in the clear, the sweep
refuses and says why, and the `/diagnostics` row warns. That is the cost of
leaving it unset — silence, in the one place it matters.
