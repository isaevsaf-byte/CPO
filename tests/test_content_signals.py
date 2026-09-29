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
