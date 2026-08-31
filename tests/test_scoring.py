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
    query, mode = harvester.gdelt_query_spec("USA")
    assert query == "sourcecountry:US"
    assert mode == "domestic_press"


def test_other_countries_keep_the_mention_query(harvester):
    query, mode = harvester.gdelt_query_spec("Germany")
    assert query == '"Germany"'
    assert mode == "mentions"


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
