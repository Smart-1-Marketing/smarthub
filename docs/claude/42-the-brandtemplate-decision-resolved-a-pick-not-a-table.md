## The BrandTemplate decision, resolved: a pick, not a table

`brand_profile_ref` sat on a Magic Resize project as a placeholder FK, "pending
the BrandTemplate decision" — deliberately unread, so it could not quietly
become a working integration nobody signed off. The decision is that there is
nothing to point at that the project's own `client` field does not already
say, and no new table of curated brand fields is needed either. What was
actually missing sat one layer down, in `hub/client_brand.brand_kit()` itself:
it has merged several logos and several colours into one card since v1.6 and
never answered which one is *the* brand. Brandfetch says so about itself — the
Display Ad Builder's own `/site-brand` route notes that it "frequently does
not say which entry is the brand colour" — so `brand_guide_payload()` has
always guessed `kit["colors"][0]`, whatever position the API happened to
answer first, and `hub/client_context.py` has always taken `kit["logos"][0]`
the same way. Two guesses, in two files, standing in for a decision nobody had
actually made.

`hub/brand_template.py` is the pick that replaces the guess, and it is
deliberately thin: one logo URL and up to three colour roles (primary,
secondary, accent) per client, stored through `jsonstore` and keyed on
`hub.client_key.name_slug()` — the same normalisation 32 other files already
join a client on, so "Riverside HVAC" and "Riverside HVAC, LLC" cannot end up
with two brand picks. **Nothing is invented**: a pick is checked against a
fresh `brand_kit()` call at save time, so the exact logo URL or the exact hex
has to be one the merge is offering *right now*, and a stale pick — confirmed
against a Brandfetch answer that has since changed — is simply not found on
the next read rather than raising or being silently kept.

**Read by nothing until it is used, which is how a placeholder stays a
placeholder.** `brand_kit()` reads the pick on every call and promotes it to
position zero in `logos` and `colors` — so `brand_guide_payload()` and
`client_context.py`, which have always trusted position zero, get the
confirmed answer with **no caller-side change at all**. `logo_tiles` and
`palette` — the sets Client 360's card actually draws — are tagged
`confirmed` (and, for a colour, which `role`) so the card can show the pick
without a second round trip. A rep confirms a tile or a swatch right there,
through `POST /api/client/brand-template`; clearing always succeeds, because
taking a pick back can never be "not offered".

Magic Resize resolves *which brand this client's is* the way
`hub/suite_accounts.location_for()` resolves a client's Suite sub-account:
derived from the project's `client` name on every read (`store.brand_for()`),
never stored. Storing a second, derived key beside a name already on the
record is the exact mistake `hub/client_key.py` spends a section refusing —
a client renamed later would leave a stored reference pointing at nobody,
where deriving it re-joins on the next request. The project page and the
new-project form both show the client's confirmed brand live, including a
pick made *after* the project was created.

**`brand_profile_ref` is a different, smaller fact, and it survives.** It is
not the FK the build plan asked for and it is not a pointer to a brand at
all — it is the domain a design's own Logo- and Background-tagged objects
were last pulled a logo and a colour *from*, set by `store.apply_brand()`
once a rep has actually pressed "Apply to design" on the project's own
"Client brand" card. That card runs a fresh `brand_kit(client, domain)` and
writes the resolved logo onto every `Logo`-role object and a chosen colour
onto every `Background`-role one — refusing rather than guessing where the
client has no brand on file, no domain was given, or the design carries
neither role — and nothing else on the design is touched, because a headline
or a disclaimer recoloured out from under whoever wrote it would be an
unannounced edit, not a brand pull-in. It rarely has to guess which colour or
logo counts: `apply_brand()` reads position zero of the same `brand_kit()`
call `brand_for()` reads the confirmed pick out of, so once a rep has
confirmed one on Client 360, applying "the client's brand" onto a design and
seeing "the client's confirmed brand" in the project header are answering
from the same promoted row.

**What this deliberately does not do.** It does not reach a provider merely to
show the confirmed pick — that costs nothing at Brandfetch, the same
distinction `hub/brand_lookup.py` draws between a page load and a button; only
pressing "Look up" or "Apply to design" on the card spends one. And it does
not yet let a pick built from an *observed*-only tile (no Brandfetch record,
only what the last scan saw) reach `brand_guide_payload()`'s Suite push, which
still gates on `kit["found"]` specifically — the pick shows on Client 360 and
on a Magic Resize project either way, but widening what "there is brand data
to push" means is a real next step and a separate change, since it is also the
Suite button's own error message. `test_brand_template.py` asserts the
refusal, the promotion, the stale-pick case, and that Magic Resize's
*confirmed-brand* reference resolves live rather than from a stored key;
`test_magic_resize.py` asserts `apply_brand()` and the domain it leaves behind
in `brand_profile_ref`.
