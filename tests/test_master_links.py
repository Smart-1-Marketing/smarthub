import pytest

from hub import master_identity as identity
from hub import master_links


def _isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("HUB_DATA_DIR", str(tmp_path))
    # A fresh disk must not restore another test's shared database mirror.
    monkeypatch.setattr(identity.jsonstore, "_fetch", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(identity.jsonstore, "_upsert", lambda *_a, **_k: True)
    monkeypatch.setattr(master_links.jsonstore, "_upsert", lambda *_a, **_k: True)


def test_source_record_points_to_master_without_copying_source(monkeypatch, tmp_path):
    _isolated(monkeypatch, tmp_path)
    client = identity.ensure_entity(
        entity_kind="organization",
        canonical_name="Monogram Homes",
        roles=["client"],
        natural_key="client:d:monogramhomes.net",
    )
    row = master_links.link(
        master_id=client["master_id"],
        system="quickbooks",
        record_type="customer",
        record_id="1638",
        role="client",
        source_label="Monogram Homes",
        evidence="QuickBooks customer id",
    )
    assert row["master_id"] == client["master_id"]
    assert master_links.resolve(
        system="quickbooks", record_type="customer", record_id="1638", role="client"
    )["master_id"] == client["master_id"]
    assert master_links.for_master(client["master_id"])[0]["record_id"] == "1638"


def test_source_record_cannot_be_silently_stolen(monkeypatch, tmp_path):
    _isolated(monkeypatch, tmp_path)
    a = identity.ensure_entity(
        entity_kind="organization", canonical_name="Client A", roles=["client"],
        natural_key="client:a"
    )
    b = identity.ensure_entity(
        entity_kind="organization", canonical_name="Client B", roles=["client"],
        natural_key="client:b"
    )
    master_links.link(
        master_id=a["master_id"], system="suite", record_type="location",
        record_id="loc-123", role="client"
    )
    with pytest.raises(identity.IdentityConflict):
        master_links.link(
            master_id=b["master_id"], system="suite", record_type="location",
            record_id="loc-123", role="client"
        )


def test_one_record_can_reference_client_and_salesperson(monkeypatch, tmp_path):
    _isolated(monkeypatch, tmp_path)
    client = identity.ensure_entity(
        entity_kind="organization", canonical_name="Client", roles=["client"],
        natural_key="client:one"
    )
    rep = identity.ensure_salesperson(
        "Rep", natural_key="hub-user:9", emails=["rep@smart1marketing.com"]
    )
    master_links.link(
        master_id=client["master_id"], system="sales_builder", record_type="quote",
        record_id="Q-10200", role="client"
    )
    master_links.link(
        master_id=rep["master_id"], system="sales_builder", record_type="quote",
        record_id="Q-10200", role="salesperson"
    )
    assert len(master_links.for_master(client["master_id"])) == 1
    assert len(master_links.for_master(rep["master_id"])) == 1
