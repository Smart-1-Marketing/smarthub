from modules.seo_intelligence.recommendations import generate


def row(query, page, impressions, clicks, ctr, position):
    return {
        "keys": [query, page],
        "impressions": impressions,
        "clicks": clicks,
        "ctr": ctr,
        "position": position,
    }


def test_low_ctr_top_ten_query_generates_title_meta_opportunity():
    recs = generate([
        row("ac repair columbus", "https://example.com/ac-repair", 5000, 50, .01, 4.2),
    ])
    kinds = {r["kind"] for r in recs}
    assert "title_meta" in kinds
    assert "page_snippet" in kinds


def test_question_query_generates_faq_opportunity():
    recs = generate([
        row("how much does furnace repair cost", "https://example.com/furnace", 900, 20, .022, 8.5),
    ])
    faq = [r for r in recs if r["kind"] == "faq"]
    assert faq
    assert faq[0]["query"] == "how much does furnace repair cost"
    assert "impressions" in faq[0]["evidence"]


def test_multiple_urls_for_same_query_generates_cannibalization():
    recs = generate([
        row("hvac repair columbus", "https://example.com/hvac", 800, 40, .05, 7.0),
        row("hvac repair columbus", "https://example.com/heating-cooling", 500, 20, .04, 12.0),
    ])
    cannibal = [r for r in recs if r["kind"] == "cannibalization"]
    assert cannibal
    assert len(cannibal[0]["evidence"]["urls"]) == 2


def test_striking_distance_query_generates_page_optimization():
    recs = generate([
        row("heat pump installation", "https://example.com/heat-pumps", 1800, 40, .022, 11.4),
    ])
    page = [r for r in recs if r["kind"] == "page_optimization"]
    assert page
    assert page[0]["priority_score"] > 0


def test_low_volume_noise_does_not_generate_query_level_work():
    recs = generate([
        row("very obscure query", "https://example.com/page", 20, 1, .05, 9.0),
    ])
    assert not [r for r in recs if r["kind"] in {"title_meta", "faq", "page_optimization"}]
