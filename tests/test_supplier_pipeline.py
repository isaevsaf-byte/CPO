"""End-to-end tests for process_suppliers with every network call stubbed.

These cover two behaviours that only show up when the layers run together:
headlines being attributed to the wrong supplier, and a geopolitical
escalation surviving the headline behind it scrolling out of the news feed.
"""

from datetime import datetime, timedelta, timezone

import pytest


@pytest.fixture
def one_supplier(harvester, monkeypatch):
    """Reduce the watchlist to a single listed supplier in a flagged country."""
    monkeypatch.setattr(harvester, "WATCHLIST_DATA",
                        [{"name": "Infineon", "category": "EE Component"}])
    monkeypatch.setattr(harvester, "SUPPLIER_PROFILES", {
        "Infineon": {
            "bat_exposure": "High",
            "segment": "New Categories (Vuse/Glo)",
            "location": "Germany",
            "stock_ticker": "IFX.DE",
            "url": None,
        }
    })
    monkeypatch.setattr(harvester, "GEOPOLITICAL_RISK_MAP", {
        "Germany": {"level": "MEDIUM", "reason": "Energy dependency"}
    })
    monkeypatch.setattr(harvester, "CATEGORY_KEYWORDS", {"EE Component": ["semiconductor"]})
    return harvester


def stub_price(monkeypatch, harvester, headlines, change=0.1, sigma=1.0):
    monkeypatch.setattr(harvester, "fetch_price_reading", lambda *a, **k: {
        "daily_change_pct": change,
        "current_price": 50.0,
        "headlines": headlines,
        "daily_sigma_pct": sigma,
    })


def stub_geo(monkeypatch, harvester, level="LOW", reason=""):
    monkeypatch.setattr(
        harvester, "scan_country_geopolitical_news",
        lambda country: (level != "LOW", level, [{"title": reason}] if reason else [], reason),
    )


def test_sector_headline_does_not_flag_the_supplier(one_supplier, monkeypatch):
    """yfinance returns sector coverage, not only articles about the ticker.

    Verified against the live feed: all five headlines under IFX.DE were about
    Nvidia and "chip stocks". Unfiltered, an export-ban headline about the
    sector raised a CRITICAL supply-risk flag against a supplier the article
    never mentioned.
    """
    harvester = one_supplier
    stub_price(monkeypatch, harvester,
               ["Chip Stocks Slide After New Export Ban On Advanced Semiconductors"])
    stub_geo(monkeypatch, harvester)

    result = harvester.process_suppliers({"recent_vulnerabilities": []})
    row = result["suppliers"][0]

    assert row["news_risk"] is False
    assert row["risk_level"] == "MEDIUM"  # the standing German floor, nothing more
    assert row["counts_toward_rag"] is False


def test_headline_naming_the_supplier_does_flag_it(one_supplier, monkeypatch):
    harvester = one_supplier
    stub_price(monkeypatch, harvester,
               ["Infineon halts production after plant fire at Dresden site"])
    stub_geo(monkeypatch, harvester)

    row = harvester.process_suppliers({"recent_vulnerabilities": []})["suppliers"][0]

    assert row["news_risk"] is True
    assert row["risk_level"] == "CRITICAL"
    assert row["counts_toward_rag"] is True


def test_geopolitical_escalation_is_held_after_the_headline_scrolls_away(one_supplier, monkeypatch):
    """Google News returns the eight most recent matches, and that set turns
    over within hours — so an escalation vanished long before the situation
    did, taking the board RED → AMBER and back with it."""
    harvester = one_supplier
    stub_price(monkeypatch, harvester, [])

    stub_geo(monkeypatch, harvester, level="HIGH", reason="Border clash reported")
    first = harvester.process_suppliers({"recent_vulnerabilities": []})
    assert first["suppliers"][0]["risk_level"] == "HIGH"
    carried_state = first["geo_escalation_state"]
    assert "Germany" in carried_state

    # Next cycle: the headline is gone from the feed, the situation is not.
    stub_geo(monkeypatch, harvester, level="LOW")
    second = harvester.process_suppliers(
        {"recent_vulnerabilities": []}, previous_geo_state=carried_state
    )
    row = second["suppliers"][0]
    assert row["risk_level"] == "HIGH"
    assert "still held" in row["geopolitical_risk"]["reason"]


def test_held_escalation_expires(one_supplier, monkeypatch):
    harvester = one_supplier
    stub_price(monkeypatch, harvester, [])
    stub_geo(monkeypatch, harvester, level="LOW")

    stale = (datetime.now(timezone.utc)
             - timedelta(hours=harvester.GEO_ESCALATION_STICKY_HOURS + 1)).isoformat()
    expired = {"Germany": {"level": "HIGH", "reason": "Border clash",
                           "since": stale, "last_seen": stale}}

    row = harvester.process_suppliers(
        {"recent_vulnerabilities": []}, previous_geo_state=expired
    )["suppliers"][0]

    assert row["risk_level"] == "MEDIUM"  # back to the standing floor


def test_price_hysteresis_reads_the_previous_snapshot(one_supplier, monkeypatch):
    """A borderline move holds its flag only if the supplier already had one."""
    harvester = one_supplier
    stub_geo(monkeypatch, harvester)
    stub_price(monkeypatch, harvester, [], change=-1.9, sigma=1.0)

    # Germany's standing floor would mask a MEDIUM, so drop it for this test.
    monkeypatch.setattr(harvester, "GEOPOLITICAL_RISK_MAP", {})

    fresh = harvester.process_suppliers({"recent_vulnerabilities": []})["suppliers"][0]
    assert fresh["risk_level"] == "LOW"

    already = harvester.process_suppliers(
        {"recent_vulnerabilities": []},
        previous_suppliers=[{"name": "Infineon", "price_move_only": True}],
    )["suppliers"][0]
    assert already["risk_level"] == "HIGH"
    assert already["price_move_only"] is True
