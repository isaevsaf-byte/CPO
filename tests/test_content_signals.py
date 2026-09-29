"""Tests for what the board says a headline, filing or change means.

These pin the content-side rules: which words in a headline count as a risk
signal, which company a headline is actually about, when a country counts as
escalated, what a peer's headline and filing summary may claim, what the
executive summary is given and keeps, and how text reaches Slack and Telegram.
Everything is offline; every fetch is stubbed.
"""

from datetime import datetime, timezone
from email.utils import format_datetime

import pytest


def rss_item(title: str) -> dict:
    """A Google News RSS item published just now."""
    return {"title": title, "published": format_datetime(datetime.now(timezone.utc))}


# ---------------------------------------------------------------------------
# 1. Supplier news matching
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "text,keyword",
    [
        # Whole-word matching made every keyword blind to its own inflections.
        ("CEO resigns amid accounting probe", "ceo resign"),
        ("Jabil confirms CEO resignation", "ceo resign"),
        ("CEO resigned on Friday", "ceo resign"),
        ("Jabil announces mass layoffs", "mass layoff"),
        ("Amcor product recalls widen", "product recall"),
        ("Supply shortages hit Sappi", "supply shortage"),
        ("Production delays at EVE Energy", "production delay"),
        ("US sanctions Smoore", "sanction"),
        ("Smoore sanctioned by Treasury", "sanction"),
        ("Smoore hit by cyberattack", "cyber attack"),
        ("Smoore hit by cyber-attack", "cyber attack"),
        ("Smoore reports cyber attack", "cyber attack"),
        ("Bankruptcies rise across the sector", "bankruptcy"),
        # Labour action, in its usual forms.
        ("Labour strikes at Sappi mill", "labour strike"),
        ("Jabil staff go on strike", "on strike"),
        ("Workers striking at Infineon plant", "workers strike"),
    ],
)
def test_keywords_match_their_inflections(harvester, text, keyword):
    assert harvester._keyword_hit(text.lower(), keyword) is True


@pytest.mark.parametrize(
    "text,keyword",
    [
        # Each negation marker used to fire as a substring inside another word.
        ("Smoore suspends production after explosion", "explosion"),   # "ends"
        ("Infineon extends plant shutdown", "plant shutdown"),         # "ends"
        ("Filipino workers strike at Jabil plant", "workers strike"),   # "no"
        ("Amcor cannot avoid bankruptcy", "bankruptcy"),                # "not"
        ("No. 1 capsule maker hit by explosion", "explosion"),          # a rank
    ],
)
def test_negation_is_read_as_whole_words(harvester, text, keyword):
    text = text.lower()
    assert harvester._is_negated_near(text, keyword) is False
    assert harvester._keyword_hit(text, keyword) is True


@pytest.mark.parametrize(
    "text,keyword",
    [
        ("Supplier avoids bankruptcy after refinancing", "bankruptcy"),
        ("No explosion at the Dresden plant, company says", "explosion"),
        ("Union ends workers strike at Jabil", "workers strike"),
    ],
)
def test_real_negation_still_suppresses_a_hit(harvester, text, keyword):
    assert harvester._keyword_hit(text.lower(), keyword) is False


def test_one_unnegated_occurrence_is_enough(harvester):
    text = "company denies explosion rumour, then confirms second explosion at plant"
    assert harvester._keyword_hit(text, "explosion") is True


def test_the_most_severe_headline_wins(harvester):
    """The scan used to stop at the first headline carrying any keyword."""
    level, keyword, headline = harvester.most_severe_supply_headline([
        "Jabil restructuring plan approved",
        "Jabil plant fire halts output in Chihuahua",
    ])
    assert (level, keyword) == ("CRITICAL", "plant fire")
    assert headline == "Jabil plant fire halts output in Chihuahua"


@pytest.mark.parametrize(
    "headline",
    [
        "ITC Q2 results: net profit rises 5%",
        "ITC shares slump after tax raid",
        "ITC stock hits record high",
        "ITC's FMCG arm grows 8%",
        "ITC’s cigarette volumes rise",
        "ITC Ltd board approves dividend",
        "ITC Hotels demerger record date set",
        "Indian conglomerate ITC to invest in paper plant",
    ],
)
def test_itc_is_matched_in_company_context(harvester, headline):
    assert harvester.headline_names_supplier(headline, "ITC") is True


@pytest.mark.parametrize(
    "headline",
    [
        "US ITC rules against Apple in patent case",
        "U.S. ITC opens probe into disposable vapes",
        "International Trade Commission bans imports of e-cigarettes",
        "Apple wins ITC patent fight with Masimo",
        "ITC ruling bars Samsung phone imports",
        "Tariff complaint filed at the ITC",
        "ITC Holdings to sell transmission assets",
        # Bare "ITC" with no company context is not enough.
        "ITC names new chief financial officer",
    ],
)
def test_itc_is_never_the_trade_commission(harvester, headline):
    assert harvester.headline_names_supplier(headline, "ITC") is False


def test_a_supplier_without_a_news_identity_keeps_its_screening_terms(harvester):
    assert harvester.headline_names_supplier("Infineon halts production after plant fire", "Infineon")
    assert not harvester.headline_names_supplier("Chip stocks slide on export ban", "Infineon")


@pytest.fixture
def listed_supplier(harvester, monkeypatch):
    """Reduce the watchlist to one listed supplier with no country floor."""
    def use(name, category, location, ticker, headlines):
        monkeypatch.setattr(harvester, "WATCHLIST_DATA", [{"name": name, "category": category}])
        monkeypatch.setattr(harvester, "SUPPLIER_PROFILES", {
            name: {"segment": "Combustibles", "location": location,
                   "stock_ticker": ticker, "url": None},
        })
        monkeypatch.setattr(harvester, "GEOPOLITICAL_RISK_MAP", {})
        monkeypatch.setattr(harvester, "fetch_price_reading", lambda *a, **k: {
            "daily_change_pct": 0.1, "current_price": 100.0,
            "headlines": headlines, "daily_sigma_pct": 1.0,
        })
        return harvester.process_suppliers({"recent_vulnerabilities": []})["suppliers"][0]
    return use


def test_a_trade_commission_headline_does_not_flag_itc(listed_supplier):
    row = listed_supplier("ITC", "Nicotine", "India", "ITC.NS",
                          ["US ITC probe into e-cigarette imports sees product recall push"])
    assert row["news_risk"] is False
    assert row["risk_level"] == "LOW"


def test_the_flagged_headline_is_the_one_shown(listed_supplier):
    row = listed_supplier("Jabil", "Mechanical", "USA", "JBL", [
        "Jabil to present at investor conference",
        "Jabil CEO resigns with immediate effect",
    ])
    assert row["risk_level"] == "HIGH"
    assert row["news_items"] == [{
        "headline": "Jabil CEO resigns with immediate effect",
        "risk": "HIGH",
        "keyword": "ceo resign",
    }]
    assert "Jabil CEO resigns" in row["last_signal"]


def test_the_unlisted_scan_shares_the_same_vocabulary(harvester, monkeypatch):
    monkeypatch.setattr(harvester, "fetch_google_news_rss",
                        lambda query, max_results=5: [rss_item("Delfort workers striking at Traun mill")])
    _, level, reason = harvester.scan_supplier_news_google("Delfort", "Austria")
    assert level == "CRITICAL"
    assert "workers strike" in reason


# ---------------------------------------------------------------------------
# 2. Other companies' news under unlisted suppliers
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "supplier,headline",
    [
        # Each of these was stored and shown as the supplier's news.
        ("Fuji", "Japan to charge unprepared Mount Fuji climbers $51 every 5 minutes - WION"),
        ("Fuji", "Fuji Electric HMI Configurator Flaws Expose Industrial Organizations to Hacking"),
        ("Fuji", "Fujifilm raises full-year forecast"),
        ("Porton", "Secret Bases: Porton Down (U.K.) - Grey Dynamics"),
        ("Porton", "UK Defense Ministry Unveils Porton Down Biological Threat Lab"),
        ("CNT", "Centurion price today, CNT to USD live price, marketcap and chart - CoinMarketCap"),
        ("CNT", "CNT union calls strike at tobacco factory in Seville"),
        ("IP Sun", "'India's IT missed the leap': AI threat after Sun Pharma's big bet"),
        ("Huizhou BYD Electronic", "BYD recalls 100,000 EVs over battery fault"),
        ("Tae Young Filters", "Taeyoung E&C enters debt workout"),
        ("Rosti", "Swiss rosti recipe for the weekend"),
        ("Weener", "Police investigate fire in Weener town centre"),
    ],
)
def test_a_namesake_is_not_the_supplier(harvester, supplier, headline):
    assert harvester.headline_names_supplier(headline, supplier) is False


@pytest.mark.parametrize(
    "supplier,headline",
    [
        ("Fuji", "Fuji Capsule plant fire halts output in Fujinomiya"),
        ("Fuji", "Fuji to expand seamless capsule line"),
        ("Porton", "Porton Pharma halts nicotine line after explosion"),
        ("CNT", "CNT nicotine supply disrupted after Siegfried plant fire"),
        ("CNT", "Contraf-Nicotex-Tobacco files for insolvency"),
        ("IP Sun", "IP-Sun cartonboard mill shutdown"),
        ("Huizhou BYD Electronic", "BYD Electronic shares jump on Apple orders"),
        ("Tae Young Filters", "Tae Young Filters plant fire"),
        ("Rosti", "Rosti closes Gislaved plant"),
        ("Weener", "Weener Plastics opens new dispensing plant"),
    ],
)
def test_the_supplier_itself_is_still_found(harvester, supplier, headline):
    assert harvester.headline_names_supplier(headline, supplier) is True


def test_the_search_asks_for_the_supplier_by_its_identifying_names(harvester, monkeypatch):
    asked = []
    monkeypatch.setattr(harvester, "fetch_google_news_rss",
                        lambda query, max_results=5: asked.append(query) or [])
    harvester.scan_supplier_news_google("Porton", "China")
    assert '"Porton Pharma"' in asked[0]
    # A supplier without a news block is searched by its watchlist name.
    harvester.scan_supplier_news_google("Delfort", "Austria")
    assert asked[1].startswith('("Delfort")')


def test_only_headlines_about_the_supplier_are_stored(harvester, monkeypatch):
    monkeypatch.setattr(harvester, "fetch_google_news_rss", lambda query, max_results=5: [
        rss_item("Japan to charge unprepared Mount Fuji climbers $51 every 5 minutes"),
        rss_item("Fuji to add seamless capsule capacity"),
        rss_item("Fuji Capsule plant fire halts output"),
    ])
    headlines, level, _ = harvester.scan_supplier_news_google("Fuji", "Japan")
    # The headline that carried the signal comes first; the mountain is gone.
    assert headlines == ["Fuji Capsule plant fire halts output",
                         "Fuji to add seamless capsule capacity"]
    assert level == "CRITICAL"


def test_nothing_about_the_supplier_means_nothing_stored(harvester, monkeypatch):
    monkeypatch.setattr(harvester, "fetch_google_news_rss", lambda query, max_results=5: [
        rss_item("Explosion reported at Porton Down laboratory"),
    ])
    assert harvester.scan_supplier_news_google("Porton", "China") == ([], "LOW", "")


def test_country_headlines_must_name_the_country(harvester, monkeypatch):
    """Sappi carried DR Congo, Iran and Tigray headlines as South Africa's."""
    monkeypatch.setattr(harvester, "fetch_google_news_rss", lambda query, max_results=8: [
        rss_item("DR Congo, Sudan and Mali lead Africa's military drone imports"),
        rss_item("How the U.S. war with Iran is impacting African economies"),
        rss_item("War has returned to Ethiopia's Tigray"),
        rss_item("South Africa's Transnet dockworkers strike enters second week"),
    ])
    _, _, headlines, _ = harvester.scan_country_geopolitical_news("South Africa")
    assert [h["title"] for h in headlines] == [
        "South Africa's Transnet dockworkers strike enters second week"]


def test_a_country_is_named_by_its_adjective_too(harvester):
    assert harvester.headline_names_country("chinese exports slump in august", "China")
    assert harvester.headline_names_country("seoul demands an apology", "South Korea")
    # ...but North Korea is not South Korea.
    assert not harvester.headline_names_country("north korea fires missile", "South Korea")


def test_a_misspelt_news_key_is_refused(harvester, tmp_path):
    path = tmp_path / "suppliers.json"
    path.write_text('{"suppliers": [{"name": "Fuji", "news": {"names": ["Fuji Capsule"], "exlude": ["Mount Fuji"]}}]}')
    with pytest.raises(ValueError, match="unknown keys"):
        harvester._load_news_identities(path)


# ---------------------------------------------------------------------------
# 3. Peers
# ---------------------------------------------------------------------------

# The four peer headlines on the board on 28 September, all investor content.
LIVE_PEER_CLICKBAIT = [
    "British American Tobacco Following Cash Flow And Buyback Hopes While Fair Value Stays Higher",
    "3 Reasons Growth Investors Will Love Philip Morris (PM)",
    "Are Investors Undervaluing Imperial Tobacco Group (IMBBY) Right Now?",
    "Asian Dividend Stocks Featuring Japan Tobacco And Two More Top Picks",
    # And the one that held the peers pillar RED for days in August.
    "Philip Morris International (PM) Faces Brazil Lawsuit Pressure, Is The Upside Already Priced In?",
    "Philip Morris Stock Price Prediction 2027",
    "Should You Buy Japan Tobacco Before Earnings?",
]


@pytest.mark.parametrize("headline", LIVE_PEER_CLICKBAIT)
def test_investor_content_is_recognised(harvester, headline):
    assert harvester.is_investor_clickbait(headline) is True


@pytest.mark.parametrize(
    "headline",
    [
        "Philip Morris to cut 500 jobs at Dutch plant",
        "Imperial Brands profit falls on weaker UK volumes",
        "Japan Tobacco raises cigarette prices in Russia",
    ],
)
def test_news_is_not_mistaken_for_investor_content(harvester, headline):
    assert harvester.is_investor_clickbait(headline) is False


@pytest.mark.parametrize(
    "headline,expected",
    [
        # Routine litigation is context, not a risk signal...
        ("Philip Morris faces lawsuit over IQOS marketing", "LOW"),
        ("Regulators open investigation into heated tobacco claims", "MEDIUM"),
        ("Imperial Brands named in investigation of distributor", "LOW"),
        # ...and never CRITICAL, even when material.
        ("Philip Morris hit with $2bn damages verdict in lawsuit", "MEDIUM"),
        ("Imperial Brands under criminal investigation in Spain", "MEDIUM"),
        # The rest of the vocabulary is unchanged.
        ("Philip Morris recalls IQOS devices over overheating", "CRITICAL"),
        ("Imperial Brands issues profit warning", "MEDIUM"),
    ],
)
def test_litigation_needs_materiality_and_stays_below_critical(harvester, headline, expected):
    level, _ = harvester.score_peer_headline(headline)
    assert level == expected


def test_the_most_material_headline_is_picked(harvester):
    headline, level = harvester.pick_peer_headline([
        "3 Reasons Growth Investors Will Love Philip Morris (PM)",
        "Philip Morris opens new plant in Greece",
        "Philip Morris issues profit warning on Asian demand",
    ])
    assert headline == "Philip Morris issues profit warning on Asian demand"
    assert level == "MEDIUM"


def test_only_investor_content_means_no_headline(harvester):
    assert harvester.pick_peer_headline(LIVE_PEER_CLICKBAIT) == (None, "LOW")


def test_the_peer_card_carries_no_placeholder_headline(harvester, monkeypatch):
    """With nothing but investor content in the feed, latest_headline is
    None and the Brazil lawsuit column no longer turns the pillar RED."""
    class FakeTicker:
        def __init__(self, symbol):
            self.news = [{"content": {"title": title}} for title in LIVE_PEER_CLICKBAIT]

    class FakeYf:
        Ticker = FakeTicker

    class NoWait:
        def wait_if_needed(self):
            pass

    monkeypatch.setattr(harvester, "yf", FakeYf)
    monkeypatch.setattr(harvester, "rate_limiter", NoWait())
    monkeypatch.setattr(harvester, "yfinance_circuit_breaker", harvester.CircuitBreaker())
    monkeypatch.setattr(harvester, "fetch_price_reading", lambda *a, **k: {
        "daily_change_pct": 0.2, "current_price": 50.0, "headlines": [], "daily_sigma_pct": 1.5,
    })
    monkeypatch.setattr(harvester, "fetch_sec_filings_for_peer", lambda name: {
        "status": "skipped", "reason": "Not US-listed, so there are no SEC filings to scan",
        "filings": [], "red_signals": 0, "amber_signals": 0,
    })

    peers = harvester.fetch_peer_group()

    assert [p["latest_headline"] for p in peers] == [None] * len(peers)
    assert all(p["risk_level"] == "LOW" for p in peers)
    assert harvester.fetch_peers_overview(peers)["rag_score"] == "GREEN"


def _earnings_filing(filed: str) -> dict:
    return {
        "title": "8-K - Current report",
        "summary": f"<b>Filed:</b> {filed} <b>AccNo:</b> 0001413329-26-000001 <b>Size:</b> 1 MB"
                   "<br>Item 2.02: Results of Operations and Financial Condition",
        "published": f"{filed}T06:05:00-04:00",
    }


@pytest.mark.parametrize(
    "filed,quarter",
    [("2026-07-22", "Q2 2026"), ("2026-10-21", "Q3 2026"),
     ("2026-02-05", "Q4 2025"), ("2026-04-23", "Q1 2026")],
)
def test_the_earnings_quarter_comes_from_the_filing(harvester, filed, quarter):
    summary = harvester.generate_peer_summary(
        "Philip Morris Int.",
        {"status": "success", "filings": [_earnings_filing(filed)], "red_signals": 0, "amber_signals": 0},
    )
    assert f"{quarter} results reported" in summary
    assert "Q3 earnings" not in summary or quarter == "Q3 2026"


def test_british_american_tobacco_is_not_called_unlisted(harvester):
    filings = harvester.fetch_sec_filings_for_peer("British American Tobacco")
    summary = harvester.generate_peer_summary("British American Tobacco", filings)
    assert "NYSE-listed as BTI" in summary
    assert "not US-listed" not in summary.lower()
    # Nothing reads non-US filings, so the summary no longer claims to.
    assert "international filings" not in summary


# ---------------------------------------------------------------------------
# 4. Which way a sanction points
# ---------------------------------------------------------------------------

def _china_scan(harvester, monkeypatch, *titles):
    monkeypatch.setattr(harvester, "fetch_google_news_rss",
                        lambda query, max_results=8: [rss_item(t) for t in titles])
    _, level, _, reason = harvester.scan_country_geopolitical_news("China")
    return level, reason


@pytest.mark.parametrize(
    "title",
    [
        # China is the actor in each of these, not the target.
        "China imposes sanctions on US defense firms over Taiwan arms sales",
        "Beijing imposes export ban on rare earths to US",
        "China imposes tariffs on US goods",
        "EU adopts new sanctions on Russia, China objects",
    ],
)
def test_a_country_imposing_a_measure_is_not_escalated(harvester, monkeypatch, title):
    assert _china_scan(harvester, monkeypatch, title) == ("LOW", "")


@pytest.mark.parametrize(
    "title,level",
    [
        ("US imposes sanctions on Chinese refiners over Iran oil", "HIGH"),
        ("US tightens export controls on Chinese chipmakers", "HIGH"),
        ("Washington adds new sanctions on China over Taiwan", "HIGH"),
        ("Trump threatens tariffs on China", "MEDIUM"),
    ],
)
def test_a_country_on_the_receiving_end_is_escalated(harvester, monkeypatch, title, level):
    assert _china_scan(harvester, monkeypatch, title)[0] == level


@pytest.fixture
def china_supplier(harvester, monkeypatch):
    monkeypatch.setattr(harvester, "WATCHLIST_DATA", [{"name": "Smoore", "category": "EMS"}])
    monkeypatch.setattr(harvester, "SUPPLIER_PROFILES", {
        "Smoore": {"segment": "Combustibles", "location": "China", "stock_ticker": "6969.HK", "url": None},
    })
    monkeypatch.setattr(harvester, "GEOPOLITICAL_RISK_MAP", {
        "China": {"level": "MEDIUM", "reason": "US-China trade war"},
    })
    monkeypatch.setattr(harvester, "fetch_price_reading", lambda *a, **k: {
        "daily_change_pct": 0.1, "current_price": 9.0, "headlines": [], "daily_sigma_pct": 3.0,
    })

    def run(title, previous_geo_state=None):
        monkeypatch.setattr(harvester, "fetch_google_news_rss",
                            lambda query, max_results=8: [rss_item(title)] if title else [])
        return harvester.process_suppliers(
            {"recent_vulnerabilities": []}, previous_geo_state=previous_geo_state)
    return run


def test_china_sanctioning_others_leaves_china_suppliers_at_their_floor(china_supplier):
    result = china_supplier("China imposes sanctions on US defense firms over Taiwan arms sales")
    row = result["suppliers"][0]
    assert row["risk_level"] == "MEDIUM"          # the standing floor
    assert row["event_risk_level"] == "LOW"
    assert row["counts_toward_rag"] is False
    assert result["geo_escalation_state"] == {}


def test_a_targeted_escalation_is_still_held_for_48_hours(china_supplier):
    first = china_supplier("US imposes sanctions on Chinese refiners over Iran oil")
    assert first["suppliers"][0]["risk_level"] == "HIGH"
    assert first["suppliers"][0]["counts_toward_rag"] is True

    # Next cycle only the actor headline is left; the escalation is held.
    second = china_supplier("China imposes sanctions on US defense firms",
                            previous_geo_state=first["geo_escalation_state"])
    row = second["suppliers"][0]
    assert row["risk_level"] == "HIGH"
    assert "still held" in row["geopolitical_risk"]["reason"]
