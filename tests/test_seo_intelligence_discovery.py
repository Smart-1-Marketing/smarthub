from modules.seo_intelligence.discovery import preferred_site


def test_domain_property_wins_over_url_prefix_property():
    sites = [
        {"siteUrl": "http://www.example.com/"},
        {"siteUrl": "https://example.com/"},
        {"siteUrl": "sc-domain:example.com"},
    ]
    assert preferred_site(sites, "https://www.example.com") == "sc-domain:example.com"


def test_https_prefix_wins_when_no_domain_property_exists():
    sites = [
        {"siteUrl": "http://example.com/"},
        {"siteUrl": "https://example.com/"},
    ]
    assert preferred_site(sites, "example.com") == "https://example.com/"


def test_property_matching_does_not_cross_domains():
    sites = [
        {"siteUrl": "sc-domain:other-example.com"},
        {"siteUrl": "https://other-example.com/"},
    ]
    assert preferred_site(sites, "example.com") == ""
