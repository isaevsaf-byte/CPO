"""Tests for how the harvester reads its sources and reports on them.

Each section pins a rule that was quietly wrong in production:

- The FRED key travelled inside exception text into the harvest log.
- Every pillar reported "success" whatever happened underneath, and no
  failure ever reached the exit code.
- Only the first ten KEVs of the CISA window were screened.
- A price reading carried no date, so yfinance serving an older session read
  as a fresh move. GPK's Friday -5.39% at $9.31 sat unchanged in ten snapshots
  from Saturday to Monday, called "today" in every one and logged three times;
  IFX.DE's Monday -7.72% came back on Wednesday from a lagging copy of the
  series and flipped the supplier pillar GREEN → AMBER → GREEN.
- Currency pairs were judged on the share-price rule, whose 2% floor no major
  pair clears on an ordinary bad day.

No network: yfinance, HTTP and the clock-dependent parts are all stubbed.
"""

import json
import logging
from datetime import datetime, timedelta, timezone

import pytest
import requests


# ---------------------------------------------------------------------------
# Fakes and fixtures
# ---------------------------------------------------------------------------

class FakeSeries:
    def __init__(self, values):
        self._values = values

    def tolist(self):
        return list(self._values)


class FakeHistory:
    """Just enough of a yfinance history frame: len(), ['Close'], .index."""

    def __init__(self, rows):
        self.index = [datetime.fromisoformat(day) for day, _ in rows]
        self._closes = [close for _, close in rows]

    def __len__(self):
        return len(self._closes)

    def __getitem__(self, column):
        assert column == "Close"
        return FakeSeries(self._closes)


class FakeTicker:
    def __init__(self, rows=(), headlines=(), history_error=None, news_error=None):
        self._rows = list(rows)
        self._headlines = list(headlines)
        self._history_error = history_error
        self._news_error = news_error

    def history(self, period):
        if self._history_error:
            raise self._history_error
        return FakeHistory(self._rows)

    @property
    def news(self):
        if self._news_error:
            raise self._news_error
        return [{"content": {"title": title}} for title in self._headlines]


class FakeYF:
    def __init__(self, ticker):
        self._ticker = ticker

    def Ticker(self, symbol):
        return self._ticker


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class NoWait:
    def wait_if_needed(self):
        pass


@pytest.fixture
def health(harvester, monkeypatch):
    """A clean registry, stats and breaker for each test — they are module
    globals in the harvester, and the other test files share them."""
    registry = harvester.SourceHealth()
    monkeypatch.setattr(harvester, "source_health", registry)
    monkeypatch.setattr(harvester, "harvest_stats", harvester.HarvestStats())
    monkeypatch.setattr(harvester, "yfinance_circuit_breaker",
                        harvester.CircuitBreaker(failure_threshold=5, reset_timeout=120))
    monkeypatch.setattr(harvester, "rate_limiter", NoWait())
    return registry


def watch_one(harvester, monkeypatch, name, ticker, exposure="High", location="USA"):
    """Reduce the watchlist to one listed supplier with no country floor."""
    monkeypatch.setattr(harvester, "WATCHLIST_DATA", [{"name": name, "category": "Test"}])
    monkeypatch.setattr(harvester, "SUPPLIER_PROFILES", {
        name: {"bat_exposure": exposure, "segment": "Combustibles", "location": location,
               "stock_ticker": ticker, "url": None},
    })
    monkeypatch.setattr(harvester, "GEOPOLITICAL_RISK_MAP", {})


def stub_reading(harvester, monkeypatch, **reading):
    base = {"daily_change_pct": None, "current_price": None, "headlines": [],
            "daily_sigma_pct": None, "price_as_of": None}
    base.update(reading)
    monkeypatch.setattr(harvester, "fetch_price_reading", lambda *a, **k: dict(base))


def change_log_state(suppliers, change_log=()):
    return {
        "suppliers": {"suppliers": suppliers},
        "peer_group": [],
        "macro_economy": {},
        "overall_rag": {"score": "GREEN", "pillar_scores": {}},
        "change_log": list(change_log),
    }


def diff(harvester, before_rows, after_rows, change_log=()):
    return harvester.compute_changes(
        change_log_state(before_rows, change_log), {"suppliers": after_rows},
        [], {}, {}, {"score": "GREEN", "driven_by": []}, "2026-09-21T02:05:00+00:00",
    )


# GPK on Friday 18 Sep: what yfinance kept serving until Monday afternoon.
GPI_FRIDAY = {"daily_change_pct": -5.39, "current_price": 9.31,
              "daily_sigma_pct": 2.58, "price_as_of": "2026-09-18"}

# IFX.DE's Tuesday reading, then Monday's fall served again on Wednesday.
INFINEON_TUESDAY = {"daily_change_pct": 0.54, "current_price": 54.33,
                    "daily_sigma_pct": 3.68, "price_as_of": "2026-09-15"}
INFINEON_LAGGING_MONDAY = {"daily_change_pct": -7.72, "current_price": 54.04,
                           "daily_sigma_pct": 3.68, "price_as_of": "2026-09-14"}


# ---------------------------------------------------------------------------
# The FRED key never reaches a log line or a recorded error
# ---------------------------------------------------------------------------

FAKE_KEY = "0123456789abcdef0123456789abcdef"


def fred_url(series_id):
    return (f"https://api.stlouisfed.org/fred/series/observations?series_id={series_id}"
            f"&api_key={FAKE_KEY}&file_type=json&sort_order=desc&limit=1&units=pc1")


@pytest.mark.parametrize(
    "error,described",
    [
        (lambda url: requests.HTTPError(
            f"400 Client Error: Bad Request for url: {url}",
            response=type("R", (), {"status_code": 400, "url": url})()), "HTTP 400"),
        (lambda url: requests.ConnectionError(
            f"HTTPSConnectionPool(host='api.stlouisfed.org', port=443): Max retries exceeded "
            f"with url: {url[30:]}"), "unreachable"),
        (lambda url: ValueError(f"could not parse the answer from {url}"), "ValueError"),
    ],
)
def test_a_fred_failure_never_carries_the_key(harvester, monkeypatch, caplog, health, error, described):
    monkeypatch.setattr(harvester, "FRED_API_KEY", FAKE_KEY)

    def fail(url, **kwargs):
        raise error(url)

    monkeypatch.setattr(harvester, "fetch_with_retry", fail)
    with caplog.at_level(logging.DEBUG):
        assert harvester.fetch_fred_observation("CPIAUCSL", "pc1") is None

    assert FAKE_KEY not in caplog.text
    assert FAKE_KEY not in json.dumps(health.snapshot())
    assert health.status("fred") == "failed"
    assert f"CPIAUCSL ({described}" in health.detail("fred")


def test_the_log_formatter_scrubs_any_line(harvester, monkeypatch):
    """The last line of defence: a third-party logger (urllib3 logs request
    paths at DEBUG) passes through the same formatter."""
    monkeypatch.setattr(harvester, "FRED_API_KEY", FAKE_KEY)
    formatter = harvester.RedactingFormatter("%(message)s")
    record = logging.LogRecord("urllib3.connectionpool", logging.DEBUG, __file__, 1,
                               '"GET %s HTTP/1.1" 200', (fred_url("FEDFUNDS")[30:],), None)

    line = formatter.format(record)

    assert FAKE_KEY not in line
    assert "api_key=[redacted]" in line


def test_recorded_errors_are_scrubbed(harvester, monkeypatch):
    monkeypatch.setattr(harvester, "FRED_API_KEY", FAKE_KEY)
    stats = harvester.HarvestStats()
    stats.record_error("fred", f"400 Client Error for url: {fred_url('CPIAUCSL')}")
    stats.record_warning("fred", f"retrying {FAKE_KEY}")
    assert FAKE_KEY not in json.dumps(stats.summary())
    # An api_key parameter is scrubbed even when its value is not a known key.
    assert harvester.redact_secrets("x?api_key=someotherkey&y=1") == "x?api_key=[redacted]&y=1"


def test_no_key_is_empty_not_failed(harvester, monkeypatch, health):
    monkeypatch.setattr(harvester, "FRED_API_KEY", None)
    assert harvester.fetch_fred_observation("CPIAUCSL", "pc1") is None
    assert health.status("fred") == "empty"
    assert "FRED_API_KEY not set" in health.detail("fred")


# ---------------------------------------------------------------------------
# Source health
# ---------------------------------------------------------------------------

def test_a_counted_source_reads_as_a_sentence(harvester):
    registry = harvester.SourceHealth()
    for _ in range(11):
        registry.count("yfinance_prices", "listed suppliers", "ok")
    registry.count("yfinance_prices", "listed suppliers", "failed", "SAP.JO (no closes returned)")
    for _ in range(4):
        registry.count("yfinance_prices", "peers", "ok")

    entry = registry.snapshot()["yfinance_prices"]

    assert entry["status"] == "failed"
    assert entry["detail"] == "prices for 11/12 listed suppliers, 4/4 peers; SAP.JO (no closes returned)"
    assert entry["checked_at"]


def test_the_snapshot_names_every_source(harvester):
    registry = harvester.SourceHealth()
    registry.record("cisa", "ok", "7 KEVs added in the last 7 days, all screened (catalog of 1728)")

    snapshot = registry.snapshot()

    assert list(snapshot) == list(harvester.SOURCE_NAMES)
    assert all(set(entry) == {"status", "detail", "checked_at"} for entry in snapshot.values())
    assert snapshot["cisa"]["status"] == "ok"
    # A source nothing asked this cycle has nothing on the board either.
    assert snapshot["sec"] == {"status": "empty", "detail": "not checked this cycle", "checked_at": None}


def test_repeated_failures_are_summarised(harvester):
    registry = harvester.SourceHealth()
    for _ in range(3):
        registry.count("google_news", "searches", "failed", "timed out")
    registry.count("google_news", "searches", "ok")
    assert registry.detail("google_news") == "headlines for 1/4 searches; timed out ×3"


def test_a_news_source_with_nothing_at_all_is_empty_not_ok(harvester):
    registry = harvester.SourceHealth()
    for _ in range(12):
        registry.count("yfinance_news", "listed suppliers", "empty")
    assert registry.status("yfinance_news") == "empty"
    registry.count("yfinance_news", "listed suppliers", "ok")
    assert registry.status("yfinance_news") == "ok"


def test_a_pillar_is_degraded_only_by_its_own_sources(harvester):
    registry = harvester.SourceHealth()
    registry.record("fred", "ok", "current readings for 4/4 series")
    registry.record("ecb", "ok", "EUR/USD reference rate 1.1352")
    for _ in range(3):
        registry.count("yfinance_prices", "markets", "ok")
    registry.count("yfinance_prices", "listed suppliers", "failed", "SAP.JO (timed out)")

    # A supplier ticker failing says nothing about the S&P 500.
    assert registry.pillar_status("macro") == "success"
    assert registry.pillar_status("suppliers") == "degraded"

    registry.record("fred", "empty", "FRED_API_KEY not set")
    assert registry.pillar_status("macro") == "degraded"


def test_pillar_rollups_report_their_derived_status(harvester, monkeypatch, health):
    peers = [{"name": "Philip Morris Int.", "risk_level": "LOW", "sec_red_signals": 0, "sec_amber_signals": 0}]
    for _ in range(4):
        health.count("yfinance_prices", "peers", "ok")
        health.count("yfinance_news", "peers", "ok")
    health.count("sec", "US-listed peers", "ok")
    assert harvester.fetch_peers_overview(peers)["status"] == "success"

    health.count("sec", "US-listed peers", "failed", "Philip Morris Int. (HTTP 503)")
    assert harvester.fetch_peers_overview(peers)["status"] == "degraded"


def test_any_failed_source_exits_2(harvester):
    stats = harvester.HarvestStats()
    registry = harvester.SourceHealth()
    registry.record("fred", "empty", "FRED_API_KEY not set")
    registry.record("claude", "empty", "ANTHROPIC_API_KEY not set")
    assert harvester.harvest_exit_code(stats, registry) == 0

    registry.count("google_news", "searches", "failed", "timed out")
    assert harvester.harvest_exit_code(stats, registry) == 2


def test_the_version_hash_ignores_source_health(harvester):
    state = {"last_updated": "x", "macro": {"status": "success"}}
    with_health = dict(state, source_health={"cisa": {"status": "ok", "detail": "", "checked_at": "now"}})
    assert harvester.calculate_data_hash(state) == harvester.calculate_data_hash(with_health)


def test_gdelt_fails_only_when_no_country_answered(harvester):
    before = {"Germany": {"last_attempt": "2026-09-28T02:00:00+00:00"},
              "China": {"last_attempt": "2026-09-28T02:01:00+00:00"}}
    rate_limited = {"last_attempt": "2026-09-29T02:00:00+00:00", "last_status": "http_429"}
    answered = {"last_attempt": "2026-09-29T02:01:00+00:00", "last_status": "ok"}
    countries = ["China", "Germany", "USA"]

    status, detail = harvester.gdelt_source_health(
        {"China": {}}, {"Germany": rate_limited, "China": answered}, before, countries)
    assert status == "ok"
    assert detail == ("fresh readings for 1/3 countries; 1 rate-limited; "
                      "earlier readings kept for the rest")

    status, detail = harvester.gdelt_source_health(
        {}, {"Germany": rate_limited, "China": dict(rate_limited, last_status="timeout")}, before, countries)
    assert status == "failed"
    assert "1 rate-limited" in detail and "1 timed out" in detail

    assert harvester.gdelt_source_health({}, before, before, countries)[0] == "empty"


# ---------------------------------------------------------------------------
# What used to fail silently
# ---------------------------------------------------------------------------

def test_an_open_breaker_is_recorded_not_silent(harvester, monkeypatch, health):
    monkeypatch.setattr(harvester, "yf", FakeYF(FakeTicker(rows=[("2026-09-18", 9.31)])))
    for _ in range(5):
        harvester.yfinance_circuit_breaker.record_failure()

    reading = harvester.fetch_price_reading("GPK", source_label="supplier_stock_GPK", scope="listed suppliers")

    assert reading == harvester.EMPTY_STOCK_READING
    assert health.status("yfinance_prices") == "failed"
    assert "GPK (skipped, circuit breaker open)" in health.detail("yfinance_prices")
    assert harvester.harvest_stats.warnings[-1]["source"] == "supplier_stock_GPK"


def test_no_closes_counts_as_a_failed_price(harvester, monkeypatch, health):
    monkeypatch.setattr(harvester, "yf", FakeYF(FakeTicker(rows=[])))

    reading = harvester.fetch_price_reading("SAP.JO", scope="listed suppliers")

    assert reading["current_price"] is None
    assert health.detail("yfinance_prices") == "prices for 0/1 listed suppliers; SAP.JO (no closes returned)"


def test_a_failed_news_fetch_is_recorded(harvester, monkeypatch, health):
    monkeypatch.setattr(harvester, "yf", FakeYF(FakeTicker(
        rows=[("2026-09-17", 9.85), ("2026-09-18", 9.31)],
        news_error=requests.HTTPError("429", response=type("R", (), {"status_code": 429})()),
    )))

    harvester.fetch_price_reading("GPK", scope="peers")

    assert health.status("yfinance_prices") == "ok"
    assert health.status("yfinance_news", "peers") == "failed"
    assert "GPK (HTTP 429)" in health.detail("yfinance_news")


def test_market_listings_do_not_count_toward_news(harvester, monkeypatch, health):
    monkeypatch.setattr(harvester, "yf", FakeYF(FakeTicker(rows=[("2026-09-17", 1.1), ("2026-09-18", 1.2)])))
    harvester.fetch_price_reading("EURUSD=X", scope="markets")
    assert "yfinance_news" not in health.tallies


def test_a_failed_peer_reading_no_longer_resets_the_breaker(harvester, monkeypatch, health):
    """The peer loop recorded a breaker success after every reading, failed or
    not, so an outage could never open the breaker from there."""
    monkeypatch.setattr(harvester, "yf", FakeYF(FakeTicker(history_error=requests.ConnectionError("down"))))
    monkeypatch.setattr(harvester, "fetch_sec_filings_for_peer", lambda name: {
        "status": "skipped", "reason": "Not US-listed", "filings": [], "red_signals": 0, "amber_signals": 0,
    })

    peers = harvester.fetch_peer_group()

    assert harvester.yfinance_circuit_breaker.failure_count == len(harvester.PEERS_CONFIG)
    assert all(p["current_price"] is None and p["price_as_of"] is None for p in peers)
    assert health.detail("yfinance_prices").startswith(f"prices for 0/{len(peers)} peers")
    assert not harvester.harvest_stats.successes


def test_a_failed_google_news_search_is_a_warning_and_a_failure(harvester, monkeypatch, caplog, health):
    def timed_out(*args, **kwargs):
        raise requests.Timeout("read timed out")

    monkeypatch.setattr(harvester.requests, "get", timed_out)
    with caplog.at_level(logging.DEBUG):
        assert harvester.fetch_google_news_rss('"Weener" (supply chain)') == []

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("Google News" in r.getMessage() for r in warnings)
    assert health.status("google_news") == "failed"
    assert health.detail("google_news") == "headlines for 0/1 searches; timed out"


def test_a_listed_supplier_without_a_price_says_so(harvester, monkeypatch, health):
    watch_one(harvester, monkeypatch, "Infineon", "IFX.DE", location="Germany")
    stub_reading(harvester, monkeypatch)

    row = harvester.process_suppliers({"recent_vulnerabilities": []})["suppliers"][0]

    assert row["risk_level"] == "LOW"
    assert row["last_signal"].startswith("No share-price move could be read for IFX.DE")
    assert "Normal operations" not in row["last_signal"]
    assert row["price_as_of"] is None and row["price_severity"] is None


def test_every_kev_in_the_window_is_screened(harvester, monkeypatch, health):
    """Only the first ten KEVs of the window were kept, in feed order. The
    seven days to 14 Sep held fifteen."""
    today = datetime.now(timezone.utc).date()
    window = [
        {"cveID": f"CVE-2026-{1000 + i}", "vendorProject": "Acme", "product": "Router",
         "vulnerabilityName": "Acme Router flaw", "dateAdded": (today - timedelta(days=i % 5)).isoformat()}
        for i in range(15)
    ]
    # Last in feed order, and older than the rest of the window.
    infineon = {"cveID": "CVE-2026-9999", "vendorProject": "Infineon", "product": "OPTIGA TPM",
                "vulnerabilityName": "Infineon OPTIGA TPM flaw", "dateAdded": (today - timedelta(days=5)).isoformat()}
    outside = {"cveID": "CVE-2025-0001", "vendorProject": "Infineon", "product": "Old",
               "vulnerabilityName": "old", "dateAdded": (today - timedelta(days=30)).isoformat()}
    monkeypatch.setattr(harvester, "fetch_with_retry",
                        lambda url, **k: FakeResponse({"vulnerabilities": window + [outside, infineon]}))

    cyber = harvester.fetch_cisa_kev()

    assert cyber["recent_count"] == 16
    assert len(cyber["recent_vulnerabilities"]) == 16
    dates = [v["dateAdded"] for v in cyber["recent_vulnerabilities"]]
    assert dates == sorted(dates, reverse=True)
    assert health.status("cisa") == "ok"

    watch_one(harvester, monkeypatch, "Infineon", "IFX.DE", location="Germany")
    stub_reading(harvester, monkeypatch)
    row = harvester.process_suppliers(cyber)["suppliers"][0]
    assert row["cyber_risk"] is True
    assert [v["cveID"] for v in row["matching_vulnerabilities"]] == ["CVE-2026-9999"]


# ---------------------------------------------------------------------------
# A price reading knows which session it is
# ---------------------------------------------------------------------------

def test_reading_carries_the_session_of_its_latest_close(harvester, monkeypatch, health):
    monkeypatch.setattr(harvester, "yf", FakeYF(FakeTicker(
        rows=[("2026-09-17", 9.85), ("2026-09-18", 9.31)], headlines=["GPK news"],
    )))

    reading = harvester.fetch_price_reading("GPK", scope="listed suppliers")

    assert reading["price_as_of"] == "2026-09-18"
    assert reading["daily_change_pct"] == pytest.approx(-5.48, abs=0.01)
    assert health.status("yfinance_prices", "listed suppliers") == "ok"
    assert health.status("yfinance_news", "listed suppliers") == "ok"


def test_a_trailing_gap_dates_the_reading_to_the_older_session(harvester, monkeypatch, health):
    """A session that has not printed a close yet comes back as a NaN row. The
    two closes before it are Friday's move, and that is what the reading must
    say — not Monday's."""
    monkeypatch.setattr(harvester, "yf", FakeYF(FakeTicker(
        rows=[("2026-09-17", 9.85), ("2026-09-18", 9.31), ("2026-09-21", float("nan"))],
    )))

    reading = harvester.fetch_price_reading("GPK", scope="listed suppliers")

    assert reading["price_as_of"] == "2026-09-18"
    assert reading["current_price"] == 9.31


@pytest.mark.parametrize(
    "as_of,today,expected",
    [
        ("2026-09-18", "2026-09-18", "-5.4% today (2.1× its normal daily range of ±2.6%)"),
        ("2026-09-18", "2026-09-19", "-5.4% on Fri 18 Sep (2.1× its normal daily range of ±2.6%)"),
        ("2026-09-18", "2026-09-21", "-5.4% on Fri 18 Sep (2.1× its normal daily range of ±2.6%)"),
        # No session known: the old wording, rather than an invented date.
        (None, "2026-09-21", "-5.4% today (2.1× its normal daily range of ±2.6%)"),
    ],
)
def test_a_move_is_described_by_its_session(harvester, as_of, today, expected):
    assert harvester.describe_price_move(-5.39, 2.58, as_of, today=today) == expected


def test_single_digit_days_read_naturally(harvester):
    assert harvester.session_label("2026-09-08") == "Tue 8 Sep"


def test_an_older_session_is_set_aside(harvester):
    previous_row = dict(INFINEON_TUESDAY, price_severity="quiet")
    reading = dict(INFINEON_LAGGING_MONDAY, headlines=["Infineon news"])

    kept = harvester.prefer_latest_session(reading, previous_row)

    assert kept["price_as_of"] == "2026-09-15"
    assert kept["daily_change_pct"] == 0.54
    assert kept["current_price"] == 54.33
    assert kept["severity"] == "quiet"
    # Headlines do not come from the price series and stay fresh.
    assert kept["headlines"] == ["Infineon news"]


def test_the_same_or_a_newer_session_is_used_as_read(harvester):
    reading = dict(GPI_FRIDAY, headlines=[])
    assert harvester.prefer_latest_session(reading, dict(GPI_FRIDAY, daily_change_pct=-5.49)) is reading
    assert harvester.prefer_latest_session(reading, {"price_as_of": "2026-09-17"}) is reading
    # A previous snapshot written before sessions were recorded has nothing to compare.
    assert harvester.prefer_latest_session(reading, {"daily_change_pct": 1.0}) is reading


def test_a_lagging_monday_fall_does_not_flip_the_supplier(harvester, monkeypatch, health):
    """IFX.DE on Wednesday 16 Sep 02:05 UTC: Monday's -7.72% came back after
    Tuesday's +0.54% had been read. It took the pillar GREEN → AMBER → GREEN
    and put the fall in the change feed a second time."""
    watch_one(harvester, monkeypatch, "Infineon", "IFX.DE", location="Germany")
    stub_reading(harvester, monkeypatch, **INFINEON_LAGGING_MONDAY)
    tuesday_row = dict(INFINEON_TUESDAY, name="Infineon", risk_level="LOW",
                       price_move_only=False, price_severity="quiet", bat_exposure="High")

    result = harvester.process_suppliers({"recent_vulnerabilities": []},
                                         previous_suppliers=[tuesday_row])
    row = result["suppliers"][0]

    assert row["risk_level"] == "LOW"
    assert row["price_move_only"] is False
    assert (row["daily_change_pct"], row["price_as_of"]) == (0.54, "2026-09-15")
    assert result["rag_score"] == "GREEN"
    assert diff(harvester, [tuesday_row], [row]) == []


def test_the_same_reading_without_a_newer_session_on_record_is_flagged(harvester, monkeypatch, health):
    """The guard only sets aside what is *older* than what was shown — the
    Monday fall itself, read on Monday, is a real unusual move."""
    watch_one(harvester, monkeypatch, "Infineon", "IFX.DE", location="Germany")
    stub_reading(harvester, monkeypatch, **INFINEON_LAGGING_MONDAY)

    row = harvester.process_suppliers({"recent_vulnerabilities": []})["suppliers"][0]

    assert row["risk_level"] == "HIGH"
    assert row["price_severity"] == "notable"
    assert row["price_as_of"] == "2026-09-14"


def test_a_friday_fall_holds_over_the_weekend_but_is_not_new(harvester, monkeypatch, health):
    """GPK's Friday fall may keep GPI flagged through Saturday — it is still
    the latest thing the market has said — but it reads as Friday's, and the
    feed that logged it on Friday does not log it again."""
    watch_one(harvester, monkeypatch, "GPI", "GPK")
    stub_reading(harvester, monkeypatch, **GPI_FRIDAY)
    friday_row = dict(GPI_FRIDAY, name="GPI", risk_level="HIGH", price_move_only=True,
                      price_severity="notable", bat_exposure="High")
    logged_friday = {"at": "2026-09-18T20:06:40+00:00", "kind": "price_move", "entity": "GPI",
                     "price_as_of": "2026-09-18",
                     "headline": "GPI -5.5% on Fri 18 Sep (2.1× its normal daily range), no corroborating signal"}

    row = harvester.process_suppliers({"recent_vulnerabilities": []},
                                      previous_suppliers=[friday_row])["suppliers"][0]

    assert row["risk_level"] == "HIGH"
    assert "on Fri 18 Sep" in row["last_signal"]
    assert "today" not in row["last_signal"]
    assert diff(harvester, [friday_row], [row], change_log=[logged_friday]) == []


def test_a_lagging_market_reading_keeps_the_shown_session(harvester, monkeypatch, health):
    stub_reading(harvester, monkeypatch, daily_change_pct=-1.5, current_price=1.1,
                 daily_sigma_pct=0.27, price_as_of="2026-09-25")
    monkeypatch.setattr(harvester, "fetch_fred_observation", lambda *a, **k: None)
    shown = {"eu": {"price_as_of": "2026-09-28", "market_change_pct": -0.06,
                    "market_sigma_pct": 0.27, "market_severity": "quiet"}}

    eu = harvester.fetch_macro_economy(shown)["eu"]

    assert (eu["market_change_pct"], eu["market_severity"], eu["price_as_of"]) == (-0.06, "quiet", "2026-09-28")


# ---------------------------------------------------------------------------
# One change-log entry per session
# ---------------------------------------------------------------------------

def test_a_move_is_logged_once_per_session_however_long_it_is_served(harvester):
    """The 24-hour window let GPI's Friday fall through at 20:06 UTC Friday,
    02:04 Sunday and 02:05 Monday."""
    before = [dict(GPI_FRIDAY, name="GPI", risk_level="LOW", price_move_only=False, bat_exposure="High")]
    after = [dict(GPI_FRIDAY, name="GPI", risk_level="HIGH", price_move_only=True, bat_exposure="High")]
    three_days_ago = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    logged = {"at": three_days_ago, "kind": "price_move", "entity": "GPI", "price_as_of": "2026-09-18"}

    assert diff(harvester, before, after, change_log=[logged]) == []


def test_a_new_session_is_logged_and_dated(harvester):
    before = [dict(GPI_FRIDAY, name="GPI", risk_level="LOW", price_move_only=False, bat_exposure="High")]
    thursday = dict(GPI_FRIDAY, daily_change_pct=-4.35, price_as_of="2026-09-24")
    after = [dict(thursday, name="GPI", risk_level="HIGH", price_move_only=True, bat_exposure="High")]
    logged_friday = {"at": "2026-09-18T20:06:40+00:00", "kind": "price_move", "entity": "GPI",
                     "price_as_of": "2026-09-18"}

    entries = diff(harvester, before, after, change_log=[logged_friday])

    assert [e["kind"] for e in entries] == ["price_move"]
    assert entries[0]["price_as_of"] == "2026-09-24"
    assert entries[0]["headline"].startswith("GPI -4.3% on Thu 24 Sep")


def test_an_entry_written_before_sessions_still_blocks_for_a_day(harvester):
    """The first harvest after this change must not re-log a move the feed
    reported hours earlier under the old rule."""
    before = [dict(GPI_FRIDAY, name="GPI", risk_level="HIGH", price_move_only=True, bat_exposure="High")]
    after = [dict(GPI_FRIDAY, name="GPI", risk_level="HIGH", price_move_only=True, bat_exposure="High")]
    six_hours_ago = (datetime.now(timezone.utc) - timedelta(hours=6)).isoformat()
    legacy = {"at": six_hours_ago, "kind": "price_move", "entity": "GPI"}

    assert diff(harvester, before, after, change_log=[legacy]) == []


# ---------------------------------------------------------------------------
# Currency pairs have their own rule
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "change,sigma,expected",
    [
        (-1.5, 0.27, "severe"),     # EUR/USD's 5.6σ day, which used to score quiet
        (-0.9, 0.27, "notable"),    # 3.3σ and over half a percent
        (0.6, 0.08, "notable"),     # USD/CNY 7.5σ, but under the 1% severe floor
        (-1.1, 0.10, "severe"),
        (-0.45, 0.10, "quiet"),     # 4.5σ, too small to reach anyone's costs
        (-0.15, 0.08, "quiet"),     # 1.9σ
        (-1.5, None, "quiet"),      # no σ, nothing to say it is unusual for the pair
        (None, 0.27, "quiet"),
    ],
)
def test_fx_moves_are_judged_on_their_own_scale(harvester, change, sigma, expected):
    assert harvester.classify_move(change, sigma, "fx") == expected
    assert harvester.classify_move(change, sigma, "fx_inverted") == expected


def test_shares_and_the_index_keep_their_rule(harvester):
    # The S&P 500 still needs the 2% floor...
    assert harvester.classify_move(-1.5, 0.27, "index") == "quiet"
    assert harvester.classify_move(-1.5, 0.27) == "quiet"
    # ...and still reads a 3σ+ fall above it as severe.
    assert harvester.classify_move(-3.0, 0.8, "index") == "severe"


@pytest.mark.parametrize(
    "label,change,sigma,severity,expected",
    [
        ("USD/CNY", -0.15, 0.08, "quiet",
         "USD/CNY -0.15% today, about 1.9× its normal daily move, below the level treated as unusual."),
        ("EUR/USD", -0.06, 0.27, "quiet",
         "EUR/USD -0.06% today, within its normal range (±0.27% a day)."),
        ("EUR/USD", -0.9, 0.27, "notable",
         "EUR/USD -0.90% today, about 3.3× its normal daily move, an unusual move for this market."),
        ("EUR/USD", -1.5, 0.27, "severe",
         "EUR/USD -1.50% today, about 5.6× its normal daily move, a sharp move for this market."),
        ("S&P 500", -0.4, None, "quiet", "S&P 500 -0.40% today."),
        ("S&P 500", None, 0.7, "quiet", "S&P 500: no reading available this cycle."),
    ],
)
def test_macro_sentence_follows_from_the_z_score(harvester, label, change, sigma, severity, expected):
    assert harvester.describe_market_move(label, change, sigma, severity, "2026-09-29",
                                          today="2026-09-29") == expected


def test_macro_sentence_dates_an_earlier_session(harvester):
    text = harvester.describe_market_move("S&P 500", -0.77, 0.7, "quiet", "2026-09-25",
                                          today="2026-09-28")
    assert text.startswith("S&P 500 -0.77% on Fri 25 Sep, about 1.1×")


def test_macro_markets_use_the_fx_rule_and_carry_their_session(harvester, monkeypatch, health):
    stub_reading(harvester, monkeypatch, daily_change_pct=-1.5, current_price=1.1,
                 daily_sigma_pct=0.27, price_as_of="2026-09-25")
    monkeypatch.setattr(harvester, "fetch_fred_observation", lambda *a, **k: None)

    economy = harvester.fetch_macro_economy()

    assert economy["us"]["market_severity"] == "quiet"      # the index keeps the 2% floor
    assert economy["eu"]["market_severity"] == "severe"
    assert economy["china"]["market_severity"] == "severe"
    assert {row["price_as_of"] for row in economy.values()} == {"2026-09-25"}
    assert economy["eu"]["summary"].startswith("EUR/USD -1.50% on Fri 25 Sep")
