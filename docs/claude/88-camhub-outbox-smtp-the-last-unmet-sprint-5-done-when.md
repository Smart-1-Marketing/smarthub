# CamHub outbox: SMTP is the last unmet Sprint 5 done-when

Sprint 5 shipped the monthly PDF, the outbox lifecycle and the sponsor
portal, but the default sender ended in `no_channel` because the Hub's
one ESP (GHL, per-client-contact) is a poor fit for sponsors. The
outbox now sends over plain SMTP when it is configured, and falls back
to staff hand-off when it isn't.

## What ships

- `outbox._send_via_smtp(sponsor, row)` renders the month's PDF from the
  rollup, builds a multipart alternative + PDF attachment message, and
  ships it through stdlib `smtplib`. STARTTLS on port 587 by default;
  `SMTP_SSL` flips to SMTPS for 465. No new Python dependency (`email`
  and `smtplib` are stdlib) and the seam that lets a future test
  channel replace it (`outbox.register_sender`) is untouched.
- `hub.config.Settings` reads `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`,
  `SMTP_PASSWORD`, `SMTP_FROM`, `SMTP_STARTTLS`, `SMTP_SSL`. `smtp_ready`
  is True when a host and a From address are both set. `settings.smtp()`
  normalizes the port and flags so the sender does not re-parse them.
  The `From` header falls back to `SMTP_USER` when `SMTP_FROM` is unset,
  because many providers require the two to match and picking the SASL
  user is a defined value where guessing would not be.
- `settings.status()` gets a row for "CamHub sponsor mail". It reads
  `warn` when SMTP is unset (staff hand-off is still working) and `ok`
  when it is.
- `render.yaml` documents all seven env vars, all `sync: false`.

## Departures worth naming

The sender re-renders the PDF at send time rather than reading
`row["pdf_url"]` off the outbox row. The URL is empty when Cloudinary
is not configured (local-disk backend), and a Cloudinary fetch at send
time would add a second failure mode. The render is rollup-only, no
adapter calls, so it is cheap; and re-rendering here means the emailed
PDF is always the same PDF `/reports/<sid>/<period>.pdf` serves.

The default sender does not retry inside a single `send_row` call.
`send_row` already increments `attempts` and moves to `failed` at
`MAX_ATTEMPTS`; a retry loop inside the sender would double-book that
counter, and the scheduled `run_monthly` on the 1st is the natural
retry cadence.

`smtp_ready` returning True does not remove the runtime failure paths.
A sponsor with no email still gets `no_recipient` (silent hand-off);
`SMTP_FROM` empty (with `SMTP_USER` also empty) still returns
`no_channel` at send time rather than mailing from a made-up address.

## Verification

`test_camhub.py` adds `SmtpSenderTests` -- five tests for the
no-channel path, a full round-trip through a stubbed `smtplib.SMTP`
(subject, headers, PDF attachment, `send` state), the SMTP_SSL path
skipping STARTTLS, the failure path moving the row to `failed` after
MAX_ATTEMPTS, and the empty-From-addr refusal.

Full preflight sweep clean (13/13); test_image_download 76/76;
test_thinking 185/185. `test_camhub.py` is at 96 tests.

## Render environment

Optional. Add all four core keys to have the outbox mail on the 1st:

- `SMTP_HOST` -- the mail server hostname.
- `SMTP_USER` -- the SASL user.
- `SMTP_PASSWORD` -- the SASL password.
- `SMTP_FROM` -- the address the report ships from. Match the SMTP_USER
  when the provider requires it.

Optional overrides:

- `SMTP_PORT` -- defaults to 587.
- `SMTP_STARTTLS` -- "0" to disable STARTTLS on plain SMTP.
- `SMTP_SSL` -- "1" for SMTPS from packet one (typically port 465).

Unset, the outbox still renders and files the PDF; the reports screen
hands off to the operator for forwarding. That was Sprint 5's shipped
default and remains the current unset behaviour.
