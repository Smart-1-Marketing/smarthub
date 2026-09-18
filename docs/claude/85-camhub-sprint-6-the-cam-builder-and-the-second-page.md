# CamHub Sprint 6: the Cam Builder wizard, re-probe, and a second location type

Sprint 6 of `docs/camhub-spec.md`, on top of #741, #742, #745 and #755:
an address goes in and a working page comes out. The spec's *done when*.
And a second cam page at a different location type, to prove the adapter
boundary is a real boundary and not a Buckeye-shaped one.

## What ships

- **`modules/camhub/templates/builder.html`** is a four-step wizard on one
  screen: address (Census geocoder answers with every match, the operator
  picks), location type (each with the tiles it wants), review (confirmed
  vs decide vs absent, with per-adapter transparency), name and provision.
  Every step is undoable until Provision; on Provision the page is written
  and a first fetch runs. Idempotent by slug -- a second submit for the
  same slug updates in place, so a re-run is safe.
- **`builder.spec_from_answers()`** turns the wizard's answers plus the
  accepted probe rows into the same dict shape `seeds.provision()` takes.
  Cadences and tolerances are per-key defaults; a source that was in
  "decide" is only added when the wizard's picker chose an index, and
  "skip" means it stays out. The config scaffold has every field the
  render layer reads.
- **`seeds.provision_from_spec(spec, fetch=True)`** takes that dict,
  runs the same store path `provision()` takes, seeds a first fetch. The
  spec's *"drop a dict in, it is a second seed"* rule holds -- both a
  wizard-run and a repo-committed seed go through this one function.
- **`/pages/<slug>/reprobe`** re-runs every adapter's probe against the
  page's current lat/lon and location type and reports the delta: what
  is new since the page was provisioned, what changed adapter or config,
  and which existing source no longer answers. Never writes -- turning
  a probe result into sources is the wizard's step 4.
- **`VERMILION_HARBOR`** is the second seed, a `great_lakes` location on
  Lake Erie's Ohio shoreline, wizard-only (its sources are picked by the
  builder on first provision rather than baked into the file). The
  location type differs from Buckeye's `inland_lake`, so provisioning
  it exercises the CO-OPS tides adapter and the NDBC buoy adapter that
  Sprint 1 shipped but no active page had ever driven.

## Departures from the spec

The wizard is one page rather than a multi-URL flow because a wizard on
one URL keeps the "go back and change something" case working without
carrying state on the server or a session cookie. The state lives in
one `<script>` closure; a browser reload restarts the wizard, which is
the right semantics for an operator-run tool that has no partial commits.

`WANTS` (the list of tiles a location type asks for) is a table on
`builder.py` rather than a per-adapter contribution. Making adapters
declare their location-type suitability would move the collapse rule out
of the tile registry and into the adapters, and the collapse rule is
where the product decision lives. This stays a small table.

## Verification

`test_camhub.py` adds `BuilderTests` covering slugify's fallback and
length cap, `spec_from_answers` reading picker choices for both
confirmed and decide, a "skip this source" decision producing an empty
`sources` list, the probe route answering under the wizard's mocked
adapter surface, `/builder/provision` writing a page and its sources
from JSON, the empty-picked refusal, `/pages/<slug>/reprobe` naming a
new source and not falsely flagging an unchanged one, the second seed
being `great_lakes` and wizard-only, the redirect from
`/provision/vermilion-harbor` into the wizard, and the wizard page
rendering with every location type and timezone.

The class carries a `tearDownClass` that deletes every page it wrote,
because `PageTests` and `StoreTests` run after alphabetically and both
expect the seed-only shape of the pages table.

The suite imports `wsgi` in `setUpClass` so the CamHub Flask app's
error reporter is attached before any `.test_client()` call finalizes
the app. `PageTests`'s later `import wsgi` is a no-op (Python's import
cache), which keeps the composed-app test passing.

## Render environment

Nothing. The wizard uses the Census geocoder (free, no key) and the
existing adapters; the second seed reads the same sources the Buckeye
seed reads. `CAMHUB_USER_AGENT` from Sprint 1 remains optional and has
a working default.
