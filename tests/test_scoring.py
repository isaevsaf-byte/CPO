"""Tests for the harvester's scoring and classification logic.

Everything here is pure: no network, no yfinance, no snapshot on disk. These
cover the rules that decide what a CPO sees on the board — which headline
counts as a risk signal, which price move is unusual, what reaches the change
feed — because those are the rules that were quietly wrong in production.
"""

import pytest


# ---------------------------------------------------------------------------
# Keyword matching
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "text,keyword,expected",
    [
        # Substring matching used to fire on ordinary words. Each of these was
        # a live false positive class.
        ("Q3 emissions report published", "miss", False),
        ("New refinery opens in Texas", "fine", False),
        ("Download the annual report", "down", False),
        ("Haircut for bondholders agreed", "cut", False),
        ("Bombshell report on the CEO", "bombs ", False),
        # ...while the real thing still matches.
        ("Earnings miss for the quarter", "miss", True),
        ("Regulator issues fine", "fine", True),
        ("Shares down 5% after results", "down", True),
        ("Company cut 200 jobs", "cut", True),
        ("Russia bombs Kyiv", "bombs ", True),
        # Multi-word phrases and numerals keep working.
        ("Plant to shut down next month", "shut down", True),
        ("Supplier files for chapter 11", "chapter 11", True),
        # Prefix keywords match their whole family.
        ("Group restructuring announced", "restructur", True),
        ("Tensions escalating on the border", "tensions escalat", True),
        # Negation still suppresses a hit.
        ("Supplier avoids bankruptcy after refinancing", "bankruptcy", False),
        ("Supplier files for bankruptcy", "bankruptcy", True),
    ],
)
def test_keyword_hit(harvester, text, keyword, expected):
    assert harvester._keyword_hit(text.lower(), keyword) is expected


@pytest.mark.parametrize(
    "text,subject,expected",
    [
        ("chinatown restaurant raided", "china", False),
        ("china tightens export controls", "china", True),
        ("prussia in the 18th century", "russia", False),
    ],
)
def test_mentions_subject_is_whole_word(harvester, text, subject, expected):
    assert harvester._mentions_subject(text, subject) is expected


# ---------------------------------------------------------------------------
# Price-move classification
# ---------------------------------------------------------------------------

def test_daily_sigma_needs_enough_history(harvester):
    assert harvester.daily_sigma_from_closes([100, 101, 99]) is None


def test_daily_sigma_measures_spread(harvester):
    steady = [100 + i * 0.1 for i in range(60)]
    jumpy = [100 * (1.05 if i % 2 else 0.95) ** 1 + i for i in range(60)]
    assert harvester.daily_sigma_from_closes(steady) < harvester.daily_sigma_from_closes(jumpy)


def test_ignores_gaps_and_bad_values(harvester):
    assert harvester.daily_sigma_from_closes([float("nan")] * 30) is None
    assert harvester.daily_sigma_from_closes([0] * 30) is None


@pytest.mark.parametrize(
    "change,sigma,expected",
    [
        # A 3% fall is a normal day for a stock that moves 3% a day...
        (-3.0, 3.0, "quiet"),
        # ...and a serious one for a stock that usually moves 0.8%.
        (-3.0, 0.8, "severe"),
        (-2.2, 1.0, "notable"),
        # Never flag a small move, however calm the listing.
        (-1.0, 0.1, "quiet"),
        # Rises are never a risk signal.
        (7.0, 1.0, "quiet"),
        # No sigma available: fall back to absolute thresholds.
        (-4.5, None, "notable"),
        (-8.0, None, "severe"),
        (-3.0, None, "quiet"),
        (None, 1.0, "quiet"),
    ],
)
def test_classify_price_move(harvester, change, sigma, expected):
    assert harvester.classify_price_move(change, sigma) == expected


def test_hysteresis_holds_an_already_flagged_supplier(harvester):
    """A move just under the bar clears only if the supplier wasn't flagged.

    This is what stopped Texas Instruments and Jabil oscillating LOW → MEDIUM →
    LOW every six hours and filling the change feed with the same two names.
    """
    borderline, sigma = -1.9, 1.0
    assert harvester.classify_price_move(borderline, sigma, already_flagged=False) == "quiet"
    assert harvester.classify_price_move(borderline, sigma, already_flagged=True) == "notable"


def test_describe_price_move_includes_yardstick(harvester):
    text = harvester.describe_price_move(-3.0, 1.0)
    assert "-3.0%" in text and "3.0×" in text
    assert harvester.describe_price_move(-3.0, None) == "-3.0% today"


# ---------------------------------------------------------------------------
# Macro pillar scoring
# ---------------------------------------------------------------------------

def test_macro_rag_is_green_on_ordinary_days(harvester):
    economy = {r: {"market_severity": "quiet"} for r in ("us", "eu", "china")}
    assert harvester.score_macro_rag(economy) == ("GREEN", [])


def test_macro_rag_escalates_on_unusual_moves(harvester):
    economy = {
        "us": {"market_severity": "quiet"},
        "eu": {"market_severity": "notable"},
        "china": {"market_severity": "quiet"},
    }
    score, drivers = harvester.score_macro_rag(economy)
    assert (score, drivers) == ("AMBER", ["eu"])

    economy["us"] = {"market_severity": "severe"}
    score, drivers = harvester.score_macro_rag(economy)
    assert score == "RED" and drivers == ["us"]


def test_macro_rag_survives_missing_data(harvester):
    assert harvester.score_macro_rag({})[0] == "GREEN"


@pytest.mark.parametrize(
    "kind,change,severity,expected",
    [
        ("fx", -1.0, "notable", "Weakening"),
        ("fx", 1.0, "notable", "Strengthening"),
        # USD/CNY rising means a weaker yuan.
        ("fx_inverted", 1.0, "notable", "Declining"),
        ("index", -1.0, "notable", "Declining"),
        # An ordinary move is Stable regardless of direction.
        ("fx", -0.6, "quiet", "Stable"),
    ],
)
def test_region_trend(harvester, kind, change, severity, expected):
    assert harvester._region_trend(kind, change, severity) == expected


# ---------------------------------------------------------------------------
# Supplier name screening
# ---------------------------------------------------------------------------

def test_short_and_ambiguous_terms_are_dropped_when_safe(harvester):
    # "TI" alone matched inside "authoriza-TI-on"; "EASTMAN" alone is at least
    # as likely to mean Eastman Kodak as our Eastman Chemical.
    assert "TI" not in harvester.supplier_search_terms("Texas Instruments")
    assert "EASTMAN" not in harvester.supplier_search_terms("Eastman")
    # But a supplier is never left with no screening at all.
    assert harvester.supplier_search_terms("CNT") == ["CNT"]


def test_supplier_terms_hit_is_whole_word(harvester):
    assert harvester.supplier_terms_hit("AUTHORIZATION BYPASS", ["TI"]) is False
    assert harvester.supplier_terms_hit("JABIL INC RECALL", ["JABIL"]) is True


def test_sanctions_screening_is_conservative(harvester):
    names = ["JABIL SOMETHING LLC", "UNRELATED ENTITY"]
    assert harvester.match_supplier_sanctions("Jabil", names) == ["JABIL SOMETHING LLC"]
    # Short names are too generic to screen on.
    assert harvester.match_supplier_sanctions("ITC", names) == []


# ---------------------------------------------------------------------------
# GDELT query construction
# ---------------------------------------------------------------------------

def test_usa_is_queried_by_source_country(harvester):
    """"USA" is three characters and GDELT rejects quoted phrases that short;
    "United States" is a valid phrase whose corpus is too large to return at
    all. Reading US-published coverage is the only form that answers."""
    query, mode, timespan = harvester.gdelt_query_spec("USA")
    assert query == "sourcecountry:US"
    assert mode == "domestic_press"
    # Even as a source-country query the US corpus will not aggregate over
    # three days before the request times out; one day answers.
    assert timespan == "1d"


def test_other_countries_keep_the_mention_query(harvester):
    query, mode, timespan = harvester.gdelt_query_spec("Germany")
    assert query == '"Germany"'
    assert mode == "mentions"
    assert timespan == harvester.GDELT_DEFAULT_TIMESPAN


@pytest.mark.parametrize(
    "body,expected",
    [
        ("The specified phrase is too short.", "query_rejected"),
        ("Please limit requests to one every 5 seconds", "rate_limited"),
        ('{"tonechart": []}', None),
    ],
)
def test_plain_text_rejections_are_classified(harvester, body, expected):
    """GDELT answers some rejections with HTTP 200 and one line of prose, so
    .json() raises and the real reason is lost as a generic "error"."""
    assert harvester._gdelt_text_status(body) == expected


def test_gdelt_queue_favours_countries_with_more_suppliers(harvester):
    """The circuit breaker gives up after three consecutive failures, so queue
    position decides who is attempted at all on a bad day. Among countries that
    have never returned data, the one holding five suppliers should go before
    the one holding one."""
    countries = ["Switzerland", "USA", "Sweden"]
    attempts = {}  # none has ever succeeded
    counts = {"USA": 5, "Switzerland": 2, "Sweden": 1}

    assert harvester.order_gdelt_countries(countries, attempts, counts)[0] == "USA"


def test_gdelt_queue_still_puts_staleness_first(harvester):
    """Supplier weight only breaks ties — a country that just succeeded does
    not jump the queue over one that has been waiting."""
    countries = ["USA", "Sweden"]
    attempts = {"USA": {"last_success": "2026-08-30T20:00:00+00:00"}}
    counts = {"USA": 5, "Sweden": 1}

    assert harvester.order_gdelt_countries(countries, attempts, counts)[0] == "Sweden"


# ---------------------------------------------------------------------------
# GDELT headline relevance
# ---------------------------------------------------------------------------

US_SUPPLIERS = [
    {"name": "GPI", "category": "Printed Packaging"},
    {"name": "Eastman", "category": "Filter Materials"},
    {"name": "SWM (Mativ)", "category": "Fine Papers"},
    {"name": "Texas Instruments", "category": "EE Component"},
    {"name": "Jabil", "category": "Mechanical"},
]
SAPPI = [{"name": "Sappi", "category": "Printing Substrates"}]
STORA_ENSO = [{"name": "Stora Enso", "category": "Printing Substrates"}]


def _relevance(harvester, title, country, suppliers, domestic=False):
    names, keywords = harvester.gdelt_relevance_terms(suppliers)
    country_terms = None if domestic else harvester.gdelt_country_terms(country)
    return harvester.gdelt_headline_relevance(title, names, keywords, country_terms)


def test_outlet_credit_is_not_part_of_the_headline(harvester):
    assert harvester.strip_source_credit(
        "Blotter : Harshing the vibes - Charleston City Paper",
        "https://charlestoncitypaper.com/2026/09/24/blotter-harshing-the-vibes/",
    ) == "Blotter : Harshing the vibes"
    assert harvester.strip_source_credit(
        "Hanwha Ocean , HD Hyundai Remain Locked In Strike Disputes | Hellenic Shipping News Worldwide",
        "https://www.hellenicshippingnews.com/hanwha-ocean-hd-hyundai/",
    ) == "Hanwha Ocean , HD Hyundai Remain Locked In Strike Disputes"
    # Without a URL, a short trailing segment is still read as the credit.
    assert harvester.strip_source_credit(
        "Blotter : Harshing the vibes - Charleston City Paper"
    ) == "Blotter : Harshing the vibes"
    assert harvester.strip_source_credit("SA manufacturing sector shrinks again") == (
        "SA manufacturing sector shrinks again"
    )


def test_a_spaced_hyphen_inside_the_headline_is_not_a_credit(harvester):
    """GDELT writes "post-quantum" as "Post - Quantum"; the words after it are
    the story, not the outlet, unless they match the site the story is on."""
    title = "Canton of Jura to Establish a Swiss Post - Quantum Semiconductor and Cybersecurity Center"
    url = "https://www.finanznachrichten.de/nachrichten-2026-09/wisekey.htm"
    assert harvester.strip_source_credit(title, url) == title


@pytest.mark.parametrize(
    "title,country,suppliers,domestic",
    [
        # Each of these shipped on the live /geopolitical page as
        # supply-chain news.
        ("Blotter : Harshing the vibes - Charleston City Paper", "USA", US_SUPPLIERS, True),
        ("Chip Foose and Fox Factory Vehicles Are Teaming Up on a Custom Truck", "USA", US_SUPPLIERS, True),
        ("Leading Wrongful Death Lawyer in Port St . Lucie , FL , Shares", "USA", US_SUPPLIERS, True),
        ("Ed Davey serves chips on Brighton Pier ahead of conference", "South Africa", SAPPI, False),
        ("Idaho has plenty at risk in our current trade war with Canada", "Switzerland",
         [{"name": "AMCOR", "category": "Printed Packaging"}, {"name": "CNT", "category": "Nicotine"}], False),
    ],
)
def test_live_false_positives_are_not_supply_chain_news(harvester, title, country, suppliers, domestic):
    assert _relevance(harvester, title, country, suppliers, domestic) == 0


@pytest.mark.parametrize(
    "title,country,suppliers,expected",
    [
        ("Stora Enso to close paper mill in Oulu - Reuters", "Finland", STORA_ENSO, 3),
        ("Pulp prices jump as Nordic mills cut output", "Finland", STORA_ENSO, 2),
        ("China tightens export controls on rare earths - Reuters", "China", [], 1),
        ("Dockworkers walk out at Rotterdam", "Netherlands", [], 1),
        ("SA manufacturing sector shrinks 1 . 5 %... again . What comes next ?", "South Africa", [], 1),
    ],
)
def test_real_supply_chain_news_still_ranks(harvester, title, country, suppliers, expected):
    assert _relevance(harvester, title, country, suppliers) == expected


def test_carried_forward_reading_drops_headlines_that_no_longer_qualify(harvester):
    """A rate-limited country keeps its last reading for days, so the
    relevance rules have to reach the headlines already stored in it."""
    entry = {
        "article_count": 700, "avg_tone": -0.9, "query_mode": "mentions", "has_relevant": True,
        "articles": [
            {"title": "Ed Davey serves chips on Brighton Pier ahead of conference", "url": "u1", "tone": -5},
            {"title": "Sappi shuts pulp line at Saiccor mill", "url": "u2", "tone": -3},
        ],
    }
    out = harvester.refilter_gdelt_articles(entry, "South Africa", SAPPI)
    assert [a["url"] for a in out["articles"]] == ["u2"]
    assert out["has_relevant"] is True
    # The country reading itself is left alone.
    assert out["avg_tone"] == -0.9 and out["article_count"] == 700


def test_carried_forward_reading_with_nothing_left_says_so(harvester):
    entry = {
        "query_mode": "mentions", "has_relevant": True,
        "articles": [{"title": "Ed Davey serves chips on Brighton Pier", "url": "u", "tone": -5}],
    }
    out = harvester.refilter_gdelt_articles(entry, "South Africa", SAPPI)
    assert out["articles"] == []
    assert out["has_relevant"] is False


# ---------------------------------------------------------------------------
# Macro data contract
# ---------------------------------------------------------------------------

def test_macro_economy_emits_the_fields_the_dashboard_reads(harvester, monkeypatch):
    """Pin the shape the macro cards render.

    The snapshot is a flat file on a six-hour cycle, so a deploy always serves
    the new UI against the previous harvest's output for a while. When this
    contract drifts, the card falls back to a "waiting for the next harvest"
    state — but only if the fields it keys off are actually the ones produced
    here, which is what this test holds in place.
    """
    monkeypatch.setattr(harvester, "fetch_price_reading", lambda *a, **k: {
        "daily_change_pct": -0.4, "current_price": 100.0,
        "headlines": [], "daily_sigma_pct": 0.8,
    })
    monkeypatch.setattr(harvester, "fetch_fred_observation", lambda *a, **k: None)

    economy = harvester.fetch_macro_economy()

    assert set(economy) == {"us", "eu", "china"}
    required = {
        "cpi", "cpi_as_of", "rate", "rate_as_of", "rate_label",
        "market_label", "market_change_pct", "market_sigma_pct",
        "market_severity", "trend", "summary", "sources",
    }
    for region, data in economy.items():
        missing = required - set(data)
        assert not missing, f"{region} is missing {sorted(missing)}"
        # market_label is what the frontend uses to tell a current snapshot
        # from one written before this shape existed.
        assert data["market_label"]
        assert data["market_severity"] in {"quiet", "notable", "severe"}

    # With no FRED key the statistics read as absent, never as a stale number.
    assert economy["us"]["cpi"] is None
    assert "no live inflation" in economy["us"]["summary"].lower()


def test_macro_summary_never_invents_commentary(harvester, monkeypatch):
    """The summary states measurements. It used to pick one of three canned
    paragraphs about Fed policy and industrial output from the sign of a single
    day's index move."""
    monkeypatch.setattr(harvester, "fetch_price_reading", lambda *a, **k: {
        "daily_change_pct": -0.25, "current_price": 100.0,
        "headlines": [], "daily_sigma_pct": 0.85,
    })
    monkeypatch.setattr(harvester, "fetch_fred_observation", lambda *a, **k: None)

    summary = harvester.fetch_macro_economy()["us"]["summary"]

    assert "-0.25%" in summary
    for invented in ("Fed ", "industrial output", "consumer", "outlook remains"):
        assert invented.lower() not in summary.lower()
