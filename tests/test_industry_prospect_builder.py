from modules.landing_ads import prospect_builder as pb


def test_parse_csv_maps_common_vendor_headers_and_dedupes():
    raw = (b"Business Email,First Name,Last Name,Company Name,Job Title,Website,Country\n"
           b"Owner@Example.com,Ada,Lovelace,Example HVAC,Owner,example.com,United States\n"
           b"owner@example.com,Ada,Lovelace,Example HVAC,Owner,example.com,USA\n"
           b"not-an-email,No,Address,Bad Co,Owner,bad.example,United States\n")
    result = pb.parse_csv(raw)
    assert len(result["rows"]) == 1
    assert result["duplicate"] == 1
    assert result["invalid"] == 1
    assert result["rows"][0]["email"] == "owner@example.com"
    assert result["rows"][0]["company"] == "Example HVAC"
    assert result["rows"][0]["website"] == "https://example.com"
    assert result["rows"][0]["country"] == "US"


def test_country_normalization_never_invents_a_code():
    assert pb._normal_country("United States") == "US"
    assert pb._normal_country("usa") == "US"
    assert pb._normal_country("ca") == "CA"
    assert pb._normal_country("Canada") == "CA"
    assert pb._normal_country("France") == ""


def test_tags_are_tracking_first_and_trigger_is_opt_in():
    job = {
        "source": "Apollo",
        "industry": "HVAC Contractors",
        "campaign": "Ohio Fall 2026",
        "landing_url": "https://smart1marketing.com/rv-dealer-marketing-gameplan",
    }
    passive = pb.tags_for(job, ["Owner List"], activate=False)
    assert "s1-prospect" in passive
    assert "source-apollo" in passive
    assert "industry-hvac-contractors" in passive
    assert "campaign-ohio-fall-2026" in passive
    assert "landing-rv-dealer-marketing-gameplan" in passive
    assert "Owner List" in passive
    assert "s1-outbound-ready" not in passive

    active = pb.tags_for(job, activate=True, trigger_tag="start-industry-outreach")
    assert "start-industry-outreach" in active


def test_cost_estimate_matches_current_lc_email_rates():
    estimate = pb.cost_estimate(10000, 3)
    assert estimate["ghl_verification"] == 25.00
    assert estimate["ghl_email"] == 20.25
    assert estimate["ghl_total"] == 45.25


def test_contact_payload_does_not_include_tags():
    payload = pb._contact_payload({
        "email": "owner@example.com",
        "company": "Example HVAC",
        "country": "United States",
    }, "location-123", "Apollo")
    assert payload["email"] == "owner@example.com"
    assert payload["locationId"] == "location-123"
    assert payload["country"] == "US"
    assert payload["createNewIfDuplicateAllowed"] is False
    assert "tags" not in payload
