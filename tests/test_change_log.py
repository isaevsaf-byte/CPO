"""Tests for what reaches the change feed.

The feed is the reason to open the dashboard on a quiet day, so what it does
*not* say matters as much as what it does. On the live board it had become ten
entries of two supplier names oscillating between LOW and MEDIUM as their share
prices wobbled — with the overall traffic light flipping AMBER → RED → AMBER
behind them, and nothing having actually happened.
"""

from datetime import datetime, timedelta, timezone

import pytest

NOW = "2026-08-30T12:00:00+00:00"


def supplier(name="Texas Instruments", **overrides):
    base = {
        "name": name,
        "risk_level": "LOW",
        "category": "EE Component",
        "location": "USA",
        "bat_exposure": "High",
        "last_signal": "✓ Normal operations.",
        "daily_change_pct": None,
        "daily_sigma_pct": 1.0,
        "price_move_only": False,
        "sanctions_hit": False,
        "cyber_risk": False,
        "recall_risk": False,
        "news_risk": False,
        "geopolitical_risk": None,
    }
    base.update(overrides)
    return base


def previous_state(suppliers, **overrides):
    state = {
        "suppliers": {"suppliers": suppliers},
        "peer_group": [],
        "macro_economy": {},
        "overall_rag": {"score": "GREEN", "pillar_scores": {}},
    }
    state.update(overrides)
    return state


def changes(harvester, before, after, **kwargs):
    return harvester.compute_changes(
        previous_state(before),
        {"suppliers": after},
        kwargs.get("peer_group", []),
        kwargs.get("macro_economy", {}),
        kwargs.get("pillar_rag_scores", {}),
        kwargs.get("overall_rag", {"score": "GREEN", "driven_by": []}),
        NOW,
    )


def test_first_run_reports_nothing(harvester):
    result = harvester.compute_changes(
        None, {"suppliers": [supplier()]}, [], {}, {}, {"score": "GREEN"}, NOW
    )
    assert result == []


def test_price_driven_move_is_not_reported_as_a_risk_change(harvester):
    """The wobble that filled the feed: LOW → MEDIUM on price alone."""
    before = [supplier(risk_level="LOW")]
    after = [supplier(risk_level="MEDIUM", price_move_only=True,
                      daily_change_pct=-3.1, daily_sigma_pct=1.0)]
    result = changes(harvester, before, after)

    kinds = [c["kind"] for c in result]
    assert "supplier_risk" not in kinds
    assert kinds == ["price_move"]
    # And it says what actually happened, with a yardstick.
    assert "-3.1%" in result[0]["headline"]
    assert "normal daily range" in result[0]["headline"]


def test_price_driven_recovery_is_silent(harvester):
    """Coming back down from a price-only flag is not news either."""
    before = [supplier(risk_level="MEDIUM", price_move_only=True, daily_change_pct=-3.1)]
    after = [supplier(risk_level="LOW", daily_change_pct=-0.4)]
    assert changes(harvester, before, after) == []


def test_confirmed_signal_still_reports_a_risk_change(harvester):
    """A real event is exactly what the feed is for."""
    before = [supplier(risk_level="LOW")]
    after = [supplier(risk_level="HIGH", cyber_risk=True,
                      last_signal="🔒 Cyber vulnerability: 1 CISA KEV match")]
    result = changes(harvester, before, after)

    assert [c["kind"] for c in result] == ["supplier_risk"]
    assert result[0]["direction"] == "up"
    assert "CISA cyber vulnerability" in result[0]["detail"]


def test_signal_change_at_the_same_risk_level_is_reported(harvester):
    before = [supplier(risk_level="HIGH", cyber_risk=True, news_risk=True)]
    after = [supplier(risk_level="HIGH", cyber_risk=False, news_risk=True)]
    result = changes(harvester, before, after)
    assert [c["kind"] for c in result] == ["supplier_signal"]
    assert "cleared" in result[0]["headline"]


def test_standing_geographic_exposure_is_never_a_change(harvester):
    """Every China supplier is permanently MEDIUM; that is not news."""
    geo = {"detected": True, "level": "MEDIUM", "reason": "US-China trade war",
           "headlines": [], "escalated": True, "baseline_only": True}
    before = [supplier(name="Smoore", risk_level="MEDIUM", geopolitical_risk=geo)]
    after = [supplier(name="Smoore", risk_level="MEDIUM", geopolitical_risk=geo)]
    assert changes(harvester, before, after) == []


def test_live_geopolitical_escalation_is_a_change(harvester):
    base = {"detected": True, "level": "MEDIUM", "reason": "trade war",
            "headlines": [], "escalated": True, "baseline_only": True}
    escalated = dict(base, level="HIGH", reason="border clash", baseline_only=False)
    before = [supplier(name="ITC", risk_level="MEDIUM", geopolitical_risk=base)]
    after = [supplier(name="ITC", risk_level="HIGH", geopolitical_risk=escalated)]
    result = changes(harvester, before, after)
    assert [c["kind"] for c in result] == ["supplier_risk"]
    assert "geopolitical escalation" in result[0]["detail"]


def test_new_and_removed_suppliers_are_reported(harvester):
    before = [supplier(name="Old Supplier")]
    after = [supplier(name="New Supplier")]
    kinds = sorted(c["kind"] for c in changes(harvester, before, after))
    assert kinds == ["supplier_added", "supplier_removed"]


def test_trim_change_log_bounds_by_age_and_count(harvester):
    old = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
    fresh = datetime.now(timezone.utc).isoformat()
    entries = [{"at": old, "headline": "ancient"}] + [
        {"at": fresh, "headline": f"entry {i}"} for i in range(300)
    ]
    kept = harvester.trim_change_log(entries)

    assert len(kept) == harvester.CHANGE_LOG_MAX_ENTRIES
    assert all(entry["headline"] != "ancient" for entry in kept)
    assert kept[-1]["headline"] == "entry 299"


def test_trim_change_log_drops_unparseable_entries(harvester):
    kept = harvester.trim_change_log([{"headline": "no timestamp"}, {"at": "not-a-date"}])
    assert kept == []
