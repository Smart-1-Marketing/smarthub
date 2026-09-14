import pytest

from hub import master_identity as mi


def _isolated_store(monkeypatch, tmp_path):
    monkeypatch.setenv("HUB_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(mi.jsonstore, "_upsert", lambda *_args, **_kwargs: True)


def test_master_id_survives_name_change(monkeypatch, tmp_path):
    _isolated_store(monkeypatch, tmp_path)
    first = mi.ensure_entity(
        entity_kind="organization",
        canonical_name="Monogram Homes",
        roles=["client"],
        natural_key="client:d:monogramhomes.net",
        domains=["monogramhomes.net"],
    )
    renamed = mi.ensure_entity(
        entity_kind="organization",
        canonical_name="Monogram Homes LLC",
        roles=["client"],
        natural_key="client:d:monogramhomes.net",
        domains=["https://www.monogramhomes.net"],
    )
    assert first["master_id"] == "S1-000001"
    assert renamed["master_id"] == first["master_id"]
    assert mi.status()["entities"] == 1


def test_one_person_can_be_salesperson_and_partner(monkeypatch, tmp_path):
    _isolated_store(monkeypatch, tmp_path)
    salesperson = mi.ensure_salesperson(
        "Alex Example",
        natural_key="staff:42",
        emails=["alex@example.com"],
        external_ids={"knack": "42"},
    )
    partner = mi.ensure_partner(
        "Alex Example",
        entity_kind="person",
        roles=["salesperson"],
        emails=["alex@example.com"],
        external_ids={"crm": "partner-18"},
    )
    assert partner["master_id"] == salesperson["master_id"]
    assert set(partner["roles"]) == {"partner", "salesperson"}
    assert partner["external_ids"]["knack"] == ["42"]
    assert partner["external_ids"]["crm"] == ["partner-18"]


def test_same_name_without_hard_key_does_not_merge(monkeypatch, tmp_path):
    _isolated_store(monkeypatch, tmp_path)
    first = mi.ensure_salesperson("John Smith", natural_key="staff:100")
    second = mi.ensure_salesperson("John Smith", natural_key="staff:200")
    assert first["master_id"] != second["master_id"]
    assert mi.status()["entities"] == 2


def test_external_id_collision_is_blocked(monkeypatch, tmp_path):
    _isolated_store(monkeypatch, tmp_path)
    first = mi.ensure_salesperson(
        "First Person", natural_key="staff:1", external_ids={"crm": "777"}
    )
    second = mi.ensure_salesperson("Second Person", natural_key="staff:2")
    with pytest.raises(mi.IdentityConflict):
        mi.ensure_entity(
            entity_kind="person",
            canonical_name="Second Person",
            roles=["salesperson"],
            natural_key="staff:2",
            external_ids={"crm": "777"},
        )
    assert mi.resolve(source="crm", source_id="777")["master_id"] == first["master_id"]
    assert mi.get(second["master_id"])["canonical_name"] == "Second Person"


def test_client_alias_rows_share_master_id(monkeypatch, tmp_path):
    _isolated_store(monkeypatch, tmp_path)
    rows = [
        {
            "name": "Icon Solar",
            "key": "d:iconsolar.com",
            "url": "https://iconsolar.com",
            "domain": "iconsolar.com",
            "is_alias": False,
        },
        {
            "name": "ICON Solar Inc.",
            "canonical_name": "Icon Solar",
            "canonical_key": "d:iconsolar.com",
            "key": "d:iconsolar.com",
            "url": "https://iconsolar.com",
            "domain": "iconsolar.com",
            "is_alias": True,
        },
    ]
    first = mi.attach_client_master_ids(rows)
    assert first[0]["master_id"] == "S1-000001"
    assert first[1]["master_id"] == first[0]["master_id"]

    again = mi.attach_client_master_ids(rows)
    assert again[0]["master_id"] == "S1-000001"
    assert mi.status()["entities"] == 1
