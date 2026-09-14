from hub import company_identity as ci


def test_name_score_legal_suffix_is_exact():
    assert ci.name_score("Monogram Homes, LLC", "Monogram Homes") == 1.0
    assert ci.name_score("ICON Solar Inc.", "Icon Solar") == 1.0


def test_meaningful_extra_word_is_not_name_only_auto_match():
    score = ci.name_score("Icon Solar Power", "Icon Solar")
    assert 0.90 <= score < 0.96


def test_unrelated_similar_names_are_not_high_confidence():
    assert ci.name_score("Acme Plumbing", "Acme Roofing") < 0.82


def test_augment_registry_alias_inherits_canonical_metadata(monkeypatch):
    monkeypatch.setattr(ci, "aliases", lambda: {
        "icon solar": {
            "alias": "ICON Solar Inc.",
            "canonical_key": "d:iconsolar.com",
            "canonical_name": "Icon Solar Power",
            "source": "db:provider_campaigns",
            "confidence": 1.0,
            "status": "approved",
        }
    })
    rows = [{
        "name": "Icon Solar Power",
        "key": "d:iconsolar.com",
        "url": "https://iconsolar.com",
        "domain": "iconsolar.com",
        "primary_contact": "Jane Client",
        "product_count": 3,
        "products": ["PPC"],
        "running_count": 1,
        "running_products": ["PPC"],
        "is_house": False,
    }]
    got = ci.augment_registry(rows)
    alias = next(r for r in got if r.get("is_alias"))
    assert alias["name"] == "ICON Solar Inc."
    assert alias["canonical_name"] == "Icon Solar Power"
    assert alias["key"] == "d:iconsolar.com"
    assert alias["url"] == "https://iconsolar.com"
    assert alias["primary_contact"] == "Jane Client"
    assert alias["product_count"] == -1


def test_decision_requires_margin_for_name_only_auto():
    ranked = [
        {"name": "Acme Services", "key": "n:acme-services", "score": .98, "evidence": []},
        {"name": "Acme Service", "key": "n:acme-service", "score": .94, "evidence": []},
    ]
    decision, _, margin = ci._decision(ranked)
    assert decision == "review"
    assert margin < .08


def test_domain_evidence_can_auto_link_with_strong_name_score():
    ranked = [
        {"name": "Icon Solar", "key": "d:iconsolar.com", "score": .94,
         "evidence": ["domain"]},
        {"name": "Icon Electric", "key": "d:iconelectric.com", "score": .60,
         "evidence": []},
    ]
    decision, hit, _ = ci._decision(ranked)
    assert decision == "auto"
    assert hit["key"] == "d:iconsolar.com"
