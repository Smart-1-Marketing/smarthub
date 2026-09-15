# Media Collector architecture

## Decision

Media Collector extends `modules/image_picker`; it does not introduce another
asset store. `image_picker_images` remains the canonical asset row and
`image_picker_clients` remains the client-owned library. Existing producers
already file Cloudinary assets through `modules/image_picker/filing.py`, so
this preserves the intended flow: collect once, store once, use everywhere.

New platform data is additive:

- `media_asset_details` — universal metadata, AI-version fields, suitability,
  brand designation, duplicate fingerprints, and rights/approval.
- `media_collections` / `media_collection_assets` — user, AI, brand, campaign,
  and system collections with many-to-many membership.
- `media_asset_links` — one canonical asset linked to any project/campaign
  entity without copying the file.
- `media_asset_usage` — append-only use history carrying campaign, creative,
  placement, tool, and time for future performance joins.

The scheduler backfills and attaches libraries after the existing Knack client
refresh. Media API reads also provision a known registry client lazily, closing
the gap between client creation and the next scheduled sweep. Both paths reuse
`hub.client_key` and refuse ambiguous matches.

## Existing systems inspected and reused

1. **Client/company records:** `hub/clients_registry.py` aggregates Knack
   products, website records, house clients, and IO-only overlays. It remains
   the source of truth; no new client table is introduced.
2. **Client IDs and matching:** `hub/client_key.py` supplies domain-first keys,
   exact normalized-name matching, and explicitly labelled fuzzy candidates.
   `modules/image_picker/provisioning.py` already applies those rules to
   galleries. Media backfill uses the same identity contract.
3. **Authentication/permissions:** Hub accounts and the legacy signed session
   are checked by the Image Picker's existing `staff_only` guard. All new Media
   APIs are staff-only. The existing tokenized upload portal remains the only
   client-public surface.
4. **Cloudinary:** `hub/storage.py` is the shared storage abstraction;
   `modules/image_picker/cloudinary_sink.py` handles picker uploads and derives
   preview URLs. Media records reference those canonical URLs and public IDs.
5. **Image Optimizer:** `modules/image_optimizer` and
   `modules/page_image_optimizer` remain the image processing paths. Media
   Collector stores their completed assets through the existing filing API.
6. **OpenAI/AI services:** `hub/ai.py` is the shared OpenAI abstraction and
   `modules/image_picker/vision.py` already performs bounded, version-aware
   background description. New analysis fields store results rather than
   causing repeated calls.
7. **Creative Builder:** `modules/creative_studio` and `hub/ad_builder_link.py`
   already resolve clients and read/file gallery assets. They can adopt the
   stable Media API and write `media_asset_usage` as integrations advance.
8. **Smart 1 Sites:** `hub/sites_builder_routes.py` and `modules/sites_admin`
   use the Hub client registry. Website and page relationships are represented
   by `media_asset_links` entity types `website` and `website_page`.
9. **Landing pages:** `hub/landing_*` and `modules/landing_ads` already use the
   shared storage and registry. `landing_page` is a first-class media link.
10. **Social:** `modules/social_planner` already files client-submitted images
    into the gallery as `social_request`. `social_post` is a first-class link.
11. **Proposal Builder:** `modules/proposal_builder` and
    `modules/sales_builder` remain authoritative for proposals; Media Collector
    exposes brand and asset endpoints instead of copying proposal state.
12. **Reporting:** `modules/reports` remains the fact/reporting layer. Media
    usage keeps asset, campaign, and creative IDs ready for later joins.
13. **Campaign/IO records:** `hub/campaign_assets.py`, `hub/ad_assets.py`, and
    the IO routes already carry IO/product identifiers into gallery rows.
    `campaign` and `creative` links make those relationships reusable.
14. **Background jobs:** `hub/scheduler.py` supplies single-leader scheduled
    execution. The library backfill is registered there rather than starting a
    second worker system.
15. **File storage:** `hub/storage.py` remains the sole general storage API and
    Cloudinary remains the durable asset backend. No bytes are duplicated by
    collection, link, or usage records.

## Phase 2 intelligence

The hourly `media_intelligence` job performs a bounded local pass before the
existing vision job. It downloads only canonical HTTPS Cloudinary image URLs,
with redirects disabled and a 25 MB ceiling. It then stores a SHA-256 exact
fingerprint, a 64-bit perceptual fingerprint, dimensions, orientation, a local
quality score, and baseline website/social/advertising/hero scores. Duplicate
matches are client-scoped and point later assets at the earliest canonical
asset; no file is deleted, merged, or relabelled automatically.

The existing bounded vision pass now writes one normalized analysis record for
description, subjects, category, closed-vocabulary tags, people count,
indoor/outdoor context, represented service/product, suggested ALT text,
suggested SEO filename, suitability scores, and composition/open-space notes.
Its version combines the analysis methodology and configured vision model.
The asset fingerprint is the analysis input version, so unchanged bytes are not
sent again. A changed methodology or fingerprint receives a fresh allowance of
three attempts; a terminal failure remains stored and quiet.

`media_search_documents` materializes provider-neutral search text from source
metadata, AI observations, technical properties, rights, and brand metadata.
The Media API ranks that index and composes query, orientation,
indoor/outdoor, people-count, minimum-quality, approval, collection, and unused
filters. The table also isolates embedding model/vector fields and exposes
index readiness in API responses. Metadata ranking remains authoritative until
an embedding producer and vector ranker are deliberately connected; the API
does not pretend a request was semantically ranked merely because a vector is
present.

## API contract

- `GET /api/clients/:clientRef/media`
- `GET /api/clients/:clientRef/media/search`
- `GET /api/clients/:clientRef/media/recommendations`
- `GET /api/clients/:clientRef/media/brand`
- `GET|POST /api/clients/:clientRef/media/collections`
- `GET|PATCH /api/media/:assetId`
- `POST /api/media/:assetId/collections`
- `POST /api/media/:assetId/links`
- `POST /api/media/:assetId/usage`
- `POST /api/media/admin/backfill`

`clientRef` accepts an existing gallery ID, Hub client key/ID, slug, or exact
registry name. A name not known to the registry is never silently promoted to a
client. This keeps prospect galleries useful without turning them into a
second identity system.

## Phase boundary

Phases 1 and 2 are established. Semantic embedding generation/vector ranking,
website and social collection crawlers, SmartHub consumer integrations, Media
Health recommendations, and performance attribution remain later phases. The
durable asset IDs, intelligence versions, search documents, links, and usage
events let those phases arrive without moving or duplicating files.
