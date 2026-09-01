# Client data

This directory must not contain real client, campaign, website, analytics, or
billing exports. The application reads those records from private Knack APIs.

For outage fallback, place `products.json` and `websites.json` on a private
mounted volume outside the source checkout and set `CLIENTS_DATA_DIR` to that
directory. Use `tests/fixtures/clients/` for local or CI examples.
