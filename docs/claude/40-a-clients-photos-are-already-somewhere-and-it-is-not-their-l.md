## A client's photos are already somewhere, and it is not their laptop

`modules/image_picker/upload_sources.py`. The client-facing picker
(`/tools/image-picker/pick/<token>`) has always been able to take uploads
through Cloudinary's own widget, which speaks Google Drive, Google Photos,
Dropbox, Facebook, Instagram and a web image search out of the box. It offered
three: a file dialog, the camera, and a URL box — because `PICKER_UPLOAD_SOURCES`
defaulted to `local,camera,url` and nobody had ever set it. So a client asked
for "your photos" got a file dialog while the photos sat in their own Instagram
feed and the agency's Dropbox, and what actually happens then is that they do
not send them.

The sources are a catalogue with what each one is for, rather than a comma list
in an environment variable, and four rules follow from that:

- **A source is offered from the catalogue or not at all.** A name the widget
  does not know draws a broken tab or no tab, and both read as our page being
  broken. An unrecognised entry is dropped and **named on the admin page**
  rather than forwarded — the same answer `hub/knack_websites.py` gives a value
  Knack would refuse.
- **A billed add-on is off until somebody turns it on.** Shutterstock, Getty,
  iStock and Unsplash are Cloudinary add-on subscriptions; listed without one,
  the client gets a tab that consents and then fails for a reason that is
  nothing to do with them, which is exactly why Google Ads came off the Google
  Access list. `PICKER_STOCK_SOURCES` names the ones the account actually has.
- **A per-source key is an override, not a gate.** Drive, Dropbox and Instagram
  work on Cloudinary's own registered apps, and the **client signs in to their
  own account either way** — the Hub never sees that password. Our own client id
  only changes the name on the consent screen, so a missing key never hides a
  tab and is not something a staff screen reports; an **empty** key is never
  sent at all, because the widget takes `dropboxAppKey: ""` at its word and
  fails the tab against it.
- **Recording an upload asks whether the source is one of ours, not whether it
  is switched on now.** A source turned off between the widget opening and the
  file landing must not file a real Instagram upload as `local`, which is the
  one thing the gallery's source column exists to say.

The paragraph the client reads is **built from the live list**, because a
sentence naming Dropbox on a deployment where Dropbox is off is a promise the
panel cannot keep. The **staff** page says none of this. It carried a Services
tick-row and a thirteen-row source table, and neither answered a question
anybody was asking: a service that is working is not a finding, and a roster of
green ticks is read once and skipped for ever while pushing the client list —
the reason the page is open — below the fold. What is left is a note when
Cloudinary is unset, a note when no stock provider is, and a note naming a
source string the widget does not recognise, which is the one that draws a
broken tab and needs somebody to correct a variable.

**The staff pick page 500'd on every visit.** `/tools/image-picker/c/<id>`
includes the upload panel and never passed it the panel's variables, and
`{{ sources|tojson }}` over an Undefined raises while Flask is *rendering* — so
it was never a broken widget, it was the whole page, exactly like
`url_for('website_check_limits')` in Sites Admin. `tools/pagecheck.py` covers
the module root now and `test_image_picker.py` covers the page that needs a
gallery id.

**A duplicate was reported and that was the whole of the answer.** Both places
this Hub detects one — `filing.file_asset()`, which eleven tools file through,
and the widget upload route beside it — said *already there* and changed not one
row. That is right when somebody uploaded a file twice by accident and wrong
every other time: the same photograph genuinely does belong to a second
project, and a client who sends it again usually means *use this one here as
well*. There was no way to say so, so the answer was always the one that
changes nothing.

Three things can be meant and they are three different statements about the
file rather than three strengths of one. **Keep** is *it belongs in both
places*: the row that exists is untouched and a second one is recorded against
the new project pointing at the **same** Cloudinary asset — no second copy of
the bytes, and deliberately no second push into the client's Suite media
library, which would be exactly the duplicate it avoids, so the twin carries
the Suite state its original earned rather than sitting at *pending* for ever.
**Duplicate** is an independent copy somebody can edit or delete without
touching the original, and it is the only one of the three that spends storage
— Cloudinary fetches the file from its own delivery URL through
`hub/storage.put_remote()`, under the original's public_id with a **random**
tail, because an explicit public_id with overwrite off hands back the asset
that is already there and the copy would be the original wearing a new row.
**Move** is *it belongs here instead*: the existing row's project and folder
fields are rewritten in place, nothing is created and, in particular, nothing
is deleted — the Cloudinary object is the same object, and a move that
destroyed a row would be a delete wearing a filing decision. `tool` and
`completed_on` are left alone by it, since they record how the file was made,
and `asset_folder` moves only where the caller named one: a move inside a
gallery does not move the bytes, so a recomputed folder would have the row
claim a place they are not.

**The default is none of them**, which is the load-bearing half.
`hub/ad_builder_link.py`, `hub/blog_images.py`, `modules/seo_images` and the IO
builder's `fileToGallery()` are all finishing a piece of work with nobody
watching, so a caller that says nothing gets precisely the answer it has always
had. What the reply gained is `choices` and `filed_under`, because a screen
cannot offer three choices without being told where the file already is — and
`filed_under` is the thing that decides which press is sensible.

**Two rows for one asset need two provider ids.** `SavedImage` carries a unique
constraint on (client, provider, provider_image_id) — one provider photo lands
in one client's gallery once, which is what stops a double-tap duplicating the
Cloudinary asset and the Suite upload — so a kept row is spelled with the
project it was kept for on the end. The base spelling is never re-used, so the
row every other caller's duplicate check finds is still the original, and a
second *keep* into the same project finds its own twin and creates nothing,
which is what makes that press safe to make twice.

**The choice is offered where somebody can act on it and nowhere else.**
`_upload_panel.html` is shared by the staff gallery and the client's own share
link, and *project* is our word rather than the client's: somebody on a share
link is sending photographs in rather than filing them, so they get exactly
what they got before — the file reported as already present, no project box and
no question. `choices` comes back empty for them, which is what tells the panel
to stay as it was rather than a rule the template keeps while the route breaks
it.

**And the panel it is offered on could not upload at all.**
`_client_from_token_or_staff()` asked `g.hub_user` for its staff half, and
**nothing in this Hub has ever set that** — so a member of staff pressing
Upload on `/gallery/<id>` or `/c/<id>` got *"That link is not valid."*, the
widget never opened (the same helper gates the signature), and every widget
upload that did land was recorded `saved_by="client"` whoever made it, on the
one column that says who to ask about a file. It reads `hub_login_ok()` now,
which is what `staff_only` decides with. A duplicate choice offered on a panel
that cannot upload is a feature nobody can reach, so it is named here rather
than left as the reason the rest of this works.

**Deleting a gallery deletes files nobody can get back**, so the name is typed
rather than an OK button pressed: the button sits in a row of four safe ones,
and for anything the client uploaded our copy is very often the only copy. What
Cloudinary removed and what it refused are **counted apart** — `hub/domain_links.py`
says at length why one tick for both is how somebody learns not to trust the
tick — and the Suite copies are named as staying, because a file already in the
client's media library may be in a funnel. `cloudinary_sink.destroy()` takes the
resource type now: Cloudinary keeps images and raw files in separate namespaces,
so a brochure PDF asked for as an `image` comes back "not found", which the old
signature reported as a **clean success** with the row gone and the file still
in the account.

**"General Business" is the busiest entry in the industry dropdown**, because
"none of the above" always is — and it handed out four generic chips: a team, a
counter, a storefront, a handshake. `modules/image_picker/profile.py` asks that
client two questions instead (what kind of business, and what do you sell or
show on your website) and the answers do three things, because **an answer that
was captured must be used** — the Proposal Builder shipped four discovery
questions that were read by nothing and produced an identical document whatever
was typed. They become the client's **own** topic and service chips, they are
blended into every free-text search from then on, and they are kept on the row
so the next visit and the next rep picking on their behalf start from the same
answers.

Three rules in it. The model writes **search terms and nothing else is
trusted**: `clamp()` caps the collections, the queries per collection and the
lengths, and strips everything a stock query is not — these strings reach three
provider APIs with three quoting rules and a page. **"We could not ask the
model" is not "this business has no topics"**: the chips are still built, from
the client's own words folded into the General Business queries, and the row
records `source: "typed"` so a staff screen can tell that apart from copy
written for this client. And **only General Business is overridden** — a staff
member switching the industry selector to a real trade is asking for that
trade's curated chips, not for a client's description to quietly replace them.
`test_image_picker.py` asserts all of it.
