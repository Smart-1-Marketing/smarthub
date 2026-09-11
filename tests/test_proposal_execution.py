"""Proposal Execution Center regression tests.

The first fixture mirrors the categories in the Monogram Homes proposal without
using a client file. The parser must preserve the major channels even when AI is
not configured, and the graph must keep external activation behind handoffs.
"""
from hub import proposal_execution as pe


MONOGRAM_TEXT = """
MONOGRAM HOMES 2026 YEAR-END MARKETING PLAN
$2,250 CURRENT MONTHLY FOUNDATION
$6,525 FALL GROWTH PLAN
$3,175 WINTER PLAN
September & October
Website Retargeting $500
Paid Search $550
Enhanced SEO + AI Optimization $1,400
Stadium to Screen $2,500
Meta In-Market Home Buyers $750
YouTube In-Market Home Buyers $600
Social Posting Outline $125
Monthly YouTube Sales Video $100
YouTube Channel Optimization +$250 one time option
November
SEO + AI Maintenance $1,000
ChatGPT / AI Advertising $400
Ohio State Buckeyes · Columbus, OH DMA · Ohio Stadium
3 custom audio commercials (:15 / :30)
Clickable 300x250 companion banners
Venue Replay stadium geo-fencing with post-game retargeting
"""


def test_monogram_parser_detects_major_channels_without_ai(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    analysis, method = pe.analyze_text(MONOGRAM_TEXT, "Monogram Homes")
    keys = {c["key"] for c in analysis["channels"]}
    assert method == "heuristic"
    assert {"retargeting", "paid_search", "seo_ai", "stadium_audio", "meta",
            "youtube_ads", "social", "youtube_video", "youtube_optimization",
            "ai_ads"}.issubset(keys)
    assert analysis["facts"]["team"] == "Ohio State Buckeyes"
    assert analysis["facts"]["venue"] == "Ohio Stadium"
    assert analysis["facts"]["market"] == "Columbus, OH DMA"
    assert analysis["headline_budgets"]["fall"] == "$6,525"


def test_monogram_graph_has_parallel_departments_and_handoffs(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    analysis, _ = pe.analyze_text(MONOGRAM_TEXT, "Monogram Homes")
    specs = pe.build_task_specs(analysis)
    by_key = {s["key"]: s for s in specs}
    assert "stadium_audio_scripts" in by_key
    assert by_key["stadium_audio_scripts"]["adapter"] == "radio_scripts"
    assert "meta_carousel" in by_key
    assert "paid_search_activation" in by_key
    assert "seo_implementation" in by_key
    assert "social_posts" in by_key
    # External changes/spend are packets, never a background publish.
    activation = [s for s in specs if s["task_type"] in {"activation", "scheduling", "implementation", "production"}]
    assert activation
    assert all(s["adapter"] == "launch_packet" and s["mode"] == "handoff"
               for s in activation)


def test_shared_input_is_requested_once_even_when_many_tasks_use_it(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    analysis, _ = pe.analyze_text(MONOGRAM_TEXT, "Monogram Homes")
    specs = pe.build_task_specs(analysis)
    consumers = [s["title"] for s in specs if "landing_url" in s["needs"]]
    assert len(consumers) >= 5
    # The catalog is one canonical key that fans out to every consumer.
    assert "landing_url" in pe.INPUT_CATALOG


def test_empty_arrays_remain_arrays_after_route_hardening():
    # The route layer installs the MVP serialization fix before a run is ever
    # created. Importing it here makes that contract explicit.
    from hub import proposal_execution_routes  # noqa: F401
    assert pe._loads(pe._dumps([]), []) == []
    assert pe._loads(pe._dumps({}), {}) == {}
