## One industry taxonomy, resolved and written down

`hub/industry.py` is the one canonical industry taxonomy the Hub resolves
and stores against -- 30 keys including `general`, matched with the same
word-boundary/earliest-alias algorithm `modules/image_picker/taxonomy.py`
already had ("Roofing Contractor" must resolve to `home_services`, never
`hvac`). `resolve_industry()` answers automatically the moment any source
does, `write_industry()` records the answer on the client's own record
beside its source, confidence and date -- the one deliberate exception to
"observed is offered, not recorded" in this file, because an industry key
is read by enough modules that leaving it merely offered would mean each
one asking the question its own way again. A manual pick always wins and
is never overwritten by a re-resolve. `LEGACY_MAP` joins every legacy
spelling this repo already had on disk (`auto`, `boat`, `medical`,
`medical_dental`, `professional`, `recruit`, `stadium`, `home_builder`)
onto the canonical key, so nothing already written had to be renamed.

**Industry keys come from hub/industry.py only; LEGACY_MAP joins old keys;
manual wins. Knack is not a source for this deployment pass -- no knack
tier in resolve_industry().** The client universe every trigger and the
nightly `industry_resolve` scheduler job walk is read from what the Hub
already has on disk (each client's own SEO store, and Image Picker
galleries) rather than `clients_registry.all_clients()`, because that
function wraps a live Knack read (`knack_data.websites()` / `.products()`).
The same constraint reaches `hub/client_brief.py`: its `identity` section
resolves industry through `hub.industry` directly rather than through the
seam this file used to carry, and its `products` section reads
`{"measured": False, "source": "not available"}` -- Knack's "currently
selling" list was the only source that section ever had, and there is no
substitute for it in this pass. `test_industry.py` and
`test_industry_consumers.py` are the gates: the taxonomy and its
precedence, and that no `INDUSTRIES`/`INDUSTRY_PACKS` literal anywhere in
`hub/` or `modules/` declares a key neither canonical nor in `LEGACY_MAP`.
