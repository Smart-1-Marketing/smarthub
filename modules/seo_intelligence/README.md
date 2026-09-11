# SmartHub SEO Intelligence

Shared Google Search Console intelligence for SEO clients and every AI-assisted tool in SmartHub.

## What it does

- Stores weekly Search Console snapshots and 28-day comparisons.
- Generates evidence-first opportunities for page optimization, titles/meta, FAQs, schema review, content/blog ideas and cannibalization.
- Maintains a compact `SEOMemory` record plus a mirrored per-client JSON file under the Hub data root at `seo-intelligence/<client-id>.json`.
- Exposes a client SEO Overview and agency-wide Action Queue at `/seo/intelligence/`.
- Exposes controlled Search Console actions for sitemap submit/delete and URL inspection.
- Logs completed SEO actions so later snapshots can measure outcomes.
- Reuses Google Finder's encrypted agency credential store; this module never stores OAuth refresh/access tokens.

## AI tool integration

AI features should not make their own Search Console calls. Import the shared provider:

```python
from modules.seo_intelligence import get_seo_context, as_prompt_block

context = get_seo_context(
    client_id,
    capability="blog",       # blog | faq | schema | title_meta | page | internal_link
    topic="furnace repair",
)

prompt += as_prompt_block(
    client_id,
    capability="blog",
    topic="furnace repair",
)
```

For a page-level generator:

```python
prompt += as_prompt_block(
    client_id,
    capability="title_meta",
    page_url="https://example.com/service-page",
)
```

The provider returns only a compact, relevant slice of weekly Google evidence. It includes guardrails telling AI not to invent rankings/search volume and not to create competing pages without checking cannibalization.

The same context is also available over HTTP:

`GET /seo/intelligence/api/clients/<client_id>/context?capability=blog&topic=furnace%20repair`

## Register a Search Console property

`POST /seo/intelligence/api/properties`

```json
{
  "client_id": "CLIENT-123",
  "site_url": "sc-domain:example.com",
  "display_name": "Example Client"
}
```

A connected Google Finder account must have access to the property. Existing Google logins may need to be reconnected once to grant the `webmasters` scope added by this module.

## Refresh behavior

The Hub scheduler checks once per day. A property with a successful snapshot less than six days old is skipped, producing approximately weekly intelligence while remaining resilient to deployments and worker restarts.

Each successful refresh writes:

- the relational weekly snapshot,
- current open recommendations,
- compact client SEO memory,
- `seo-intelligence/<client-id>.json` on the Hub data volume.

## Search Console actions

- `POST /seo/intelligence/api/properties/<id>/sitemaps` — submit/resubmit a sitemap.
- `DELETE /seo/intelligence/api/properties/<id>/sitemaps` — remove a submitted sitemap.
- `POST /seo/intelligence/api/properties/<id>/inspect` — inspect a URL.

Content/title/meta/schema changes are deliberately not written through Search Console. Those should be routed through approved CMS integrations (WordPress, Smart 1 Sites, Shopify, etc.) and then recorded as `SEOAction` records for before/after measurement.

## Design rule

Google data is evidence. Rules decide whether an opportunity is worth surfacing. AI drafts or explains the work only after the opportunity has supporting evidence. This keeps the system from manufacturing SEO tasks merely because a generated idea sounds plausible.
