"""Tests for how the harvester reads its sources and reports on them.

Each section pins a rule that was quietly wrong in production:

- The FRED key travelled inside exception text into the harvest log.
- Every pillar reported "success" whatever happened underneath, and no
  failure ever reached the exit code.
- Only the first ten KEVs of the CISA window were screened.

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
            "daily_sigma_pct": None}
    base.update(reading)
    monkeypatch.setattr(harvester, "fetch_price_reading", lambda *a, **k: dict(base))


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
    assert all(p["current_price"] is None for p in peers)
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
