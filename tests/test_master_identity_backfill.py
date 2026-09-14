from hub import master_identity_backfill as backfill


def test_first_prefers_explicit_column_order():
    cols = {"id", "name", "email"}
    assert backfill._first(cols, "partner_name", "name") == "name"
    assert backfill._first(cols, "partner_email", "email") == "email"


def test_partner_table_requires_partner_name():
    # The production scanner only enters tables whose table name contains
    # partner. This helper test pins the next safety rule: generic source
    # columns are selected explicitly instead of guessed by fuzzy labels.
    cols = {"id", "company_name", "contact_email", "address", "phone"}
    assert backfill._first(cols, "partner_name", "company_name", "name") == "company_name"
    assert backfill._first(cols, "partner_email", "email", "contact_email") == "contact_email"
    assert backfill._first(cols, "domain", "website", "url") == ""
