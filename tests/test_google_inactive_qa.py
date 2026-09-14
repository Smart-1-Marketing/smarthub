"""Small, network-free checks for the inactive Google account QA tool."""

from modules.google_access import qa_inactive as qa


def test_measurement_ids_found_in_nested_gtm_payload():
    payload = {
        "name": "GA4 config",
        "parameter": [
            {"key": "measurementId", "value": "G-ABC12345"},
            {"key": "other", "list": [{"value": "prefix G-XYZ98765 suffix"}]},
        ],
    }
    assert qa._measurement_ids(payload) == {"G-ABC12345", "G-XYZ98765"}


def test_dedupe_same_google_resource_visible_through_two_logins():
    rows = [
        {"kind": "GA4", "account_id": "1", "account": "A", "resource": "123",
         "name": "Client", "login": "one@example.com"},
        {"kind": "GA4", "account_id": "1", "account": "A", "resource": "123",
         "name": "Client", "login": "two@example.com"},
    ]
    out = qa._dedupe(rows)
    assert len(out) == 1
    assert out[0]["resource"] == "123"


def test_skip_key_separates_ga4_and_gtm():
    assert qa._skip_key("GA4", "A@EXAMPLE.COM", "123") != qa._skip_key(
        "GTM", "a@example.com", "123")
    assert qa._skip_key("GA4", "A@EXAMPLE.COM", "123") == "GA4:a@example.com:123"
