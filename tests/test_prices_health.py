"""Tests for how the harvester reads its sources and reports on them.

Each section pins a rule that was quietly wrong in production:

- The FRED key travelled inside exception text into the harvest log.

No network: yfinance, HTTP and the clock-dependent parts are all stubbed.
"""

import json
import logging

import pytest
import requests


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
def test_a_fred_failure_never_carries_the_key(harvester, monkeypatch, caplog, error, described):
    monkeypatch.setattr(harvester, "FRED_API_KEY", FAKE_KEY)

    def fail(url, **kwargs):
        raise error(url)

    monkeypatch.setattr(harvester, "fetch_with_retry", fail)
    with caplog.at_level(logging.DEBUG):
        assert harvester.fetch_fred_observation("CPIAUCSL", "pc1") is None

    assert FAKE_KEY not in caplog.text
    assert f"CPIAUCSL: {described}" in caplog.text


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
