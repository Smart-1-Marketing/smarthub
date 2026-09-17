# The upload URL that nothing ever served

`hub/storage.py` is the shared uploader. Fifty call sites across thirty-odd
modules hand it bytes and file what comes back: the SEO pipeline, the image
picker, proposals, prospect records, legal reports, IO builder, Fan Radio,
Radio Promo, Commercial Builder, Magic Resize. It has two branches.

When Cloudinary is configured it uploads and returns the `secure_url`.

When it is not, it wrote the bytes to `<data_dir>/assets/<kind>/` and returned:

```python
url=f"/hub/assets/{kind}/{safe}"
```

**Nothing has ever served that path.** Before this change the string
`hub/assets` appeared exactly once in the whole repository — on the line that
manufactured it. No route matched it, no template linked it, no test covered
it, and a booted `wsgi.application` answered 404 for it.

## Why nobody noticed

Two of the fifty call sites look at `.backend`. `modules/gpt_ads/app.py` even
says why, in a comment written before this one:

> A disk fallback URL is not a link anyone outside this container can open, and
> it is on the disk that is not backed up. Say so rather than presenting it as
> a stored asset.

One module worked it out and defended itself. The shared function kept handing
the same dead link to the other forty-eight, which take `.url` on trust:
`modules/proposal_builder/store.py` returns it as the proposal's URL,
`modules/legal/app.py` files it as `secure_url`, `hub/prospect.py` writes it
into the prospect record that `prospect.html` renders.

So on any Hub without the Cloudinary credential, an upload reported success, a
row appeared, a thumbnail slot appeared — and the link went nowhere. This is
the `/signup` failure CLAUDE.md names, wearing a URL instead of a swallowed
exception.

## The answer was already in the same file

Twenty lines below, `put_remote()` refuses outright:

> Requires Cloudinary: there is no sensible disk fallback for "have someone
> else fetch this", and silently downloading it here would reintroduce the
> behaviour this exists to avoid.

`put()` was doing exactly that, with a string. The rule the file had already
written down for itself is the one it now follows in both branches.

## What it does now

The bytes are still kept. An upload a person waited for, or a render that cost
money, is not thrown away because a credential is missing — that is Fan Radio's
rule and it still holds.

What changed is that there is no invented URL. `url` is `""`, and a new
`note` field says why and names the file on disk, so a caller that shows a
person where their file went has something true to show them. An empty string
is a condition a template and an `if` can both act on; a 404 is not.

`storage.local_assets()` counts what is sitting in the fallback directory, and
`/diagnostics`' Cloudinary row reports it — "340 file(s), 12040 KB, are already
on this instance's disk with no delivery URL" rather than the setting alone. It
carries `measured`, so "nothing has fallen back here" and "the directory could
not be read" stay different answers. `/status`' Cloudinary row said "assets
persist to local disk only", which was the true half; it now says they also get
no delivery URL and sit outside the backup.

## What this is not

It is not a route. Adding `/hub/assets/<kind>/<name>` would have made the URL
true and taken thirty seconds, and it is the wrong direction: it would cement a
per-instance disk in the middle of the work removing it, and it would still be
a coin flip across the two instances of a zero-downtime deploy — the asset is
on whichever one happened to take the upload.

It is not a refusal either. `put()` raising would have matched `put_remote()`
exactly, and would have broken every test run and local boot, which are
precisely the deployments with no Cloudinary credential. Losing the bytes to
make a point about the URL would be a worse trade than the one being fixed.

## The test

`test_storage_fallback.py`. The assertion that catches this is deliberately
the general one rather than `url == ""`:

> whatever `put()` returns, a relative URL must be a path the booted app can
> route.

A test pinned to the empty string would pass over a differently-spelled
invention — `/assets/...`, `/static/uploads/...` — which is the same defect
again. This one boots `wsgi.application` and asks it, and against the original
code it fails naming the path and the 404.

It also guards its own premise. The first test asserts `storage.ready()` is
False, because every other assertion in the file is vacuous if something has
configured Cloudinary — a developer with real credentials exported would
otherwise run a different branch than CI and watch it pass for the wrong
reason. The file unsets all four Cloudinary spellings for the same reason.

## Render environment

Nothing to add for the change itself. What it makes visible is that
`CLOUDINARY_URL` — or the three-part `CLOUDINARY_CLOUD_NAME` +
`CLOUDINARY_API_KEY` + `CLOUDINARY_API_SECRET`, which `hub/config.py` composes
into the URL — has to be arriving at every service that files an asset. Where
it is not, uploads land on that instance's disk and now say so on `/status`
and `/diagnostics` instead of returning a link that does not open.
