"""Tests for scripts/world_signals.py — no network.

Each source's reading is tested as a pure function of a small inline response,
and collect_world_signals is driven through a fake HTTP layer to check the
contract the page will rely on: the shape, the level, and that a dead or
hanging source never takes the rest down with it.
"""

import importlib.util
import json
import sys
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
import requests

ROOT = Path(__file__).resolve().parent.parent
NOW = datetime(2026, 9, 29, 9, 0, tzinfo=timezone.utc)

ITEM_KEYS = {
    "id", "label", "value", "unit", "as_of", "baseline", "change_pct", "severity",
    "headline", "affected_categories", "affected_suppliers", "source", "source_url",
}

ROSTER = [
    {"name": "Cerdia", "category": "Filter Materials", "location": "Germany"},
    {"name": "AMCOR", "category": "Printed Packaging", "location": "Switzerland"},
    {"name": "CNT", "category": "Nicotine", "location": "Switzerland"},
    {"name": "Smoore", "category": "EMS", "location": "China"},
    {"name": "ITC", "category": "Nicotine", "location": "India"},
    {"name": "Stora Enso", "category": "Printing Substrates", "location": "Finland"},
    {"name": "Jabil", "category": "Mechanical", "location": "USA"},
]

LINKS = {
    "commodities": {
        "brent": {
            "series": "DCOILBRENTEU", "label": "Brent crude", "unit": "USD/bbl", "frequency": "daily",
            "feeds": "acetate tow and plastics",
            "categories": ["Filter Materials", "Mechanical", "EMS", "Batteries"],
        },
        "eu_gas": {
            "series": "PNGASEUUSDM", "label": "EU natural gas", "unit": "USD/MMBtu", "frequency": "monthly",
            "feeds": "mill energy", "categories": ["Printing Substrates"],
        },
    },
    "chokepoints": {
        "Strait of Hormuz": {"countries": [], "exposure_from_commodity": "brent", "route": "the oil price"},
        "Suez Canal": {"countries": ["China", "India"], "route": "the Asia-Europe route"},
    },
    "rivers": {"Rhine": {"suppliers": ["Cerdia", "AMCOR", "CNT"]}},
}


@pytest.fixture(scope="module")
def ws():
    spec = importlib.util.spec_from_file_location("world_signals", ROOT / "scripts" / "world_signals.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["world_signals"] = module
    spec.loader.exec_module(module)
    return module


def week(port: str, last: date, per_day: float, days: int = 7) -> list:
    return [
        {"date": (last - timedelta(days=i)).isoformat(), "portname": port, "n_total": per_day}
        for i in range(days)
    ]


# ---------------------------------------------------------------------------
# Chokepoints
# ---------------------------------------------------------------------------

def test_hormuz_collapse_is_severe_and_reaches_every_oil_derived_input(ws):
    """The 28 Sep 2026 case: 3.1 transits a day against 86.0 a year earlier."""
    rows = week("Strait of Hormuz", date(2026, 9, 20), 3.1) + week("Strait of Hormuz", date(2025, 9, 20), 86.0)
    [item] = ws.chokepoint_signals(rows, LINKS, ROSTER, NOW)

    assert ITEM_KEYS <= set(item)
    assert item["severity"] == "severe"
    assert item["value"] == 3.1 and item["baseline"] == 86.0
    assert item["change_pct"] == pytest.approx(-96.4, abs=0.1)
    assert item["as_of"] == "2026-09-20"
    # No country link: the whole exposure comes through Brent's categories,
    # including one no roster supplier currently sits in.
    assert item["affected_suppliers"] == ["Cerdia", "Smoore", "Jabil"]
    assert "Batteries" in item["affected_categories"]
    assert "week to 20 Sep" in item["headline"] and "96% fewer" in item["headline"]


@pytest.mark.parametrize("change,expected", [
    (None, "quiet"), (12.0, "quiet"), (-29.9, "quiet"), (-30.0, "notable"),
    (-59.9, "notable"), (-60.0, "severe"), (-96.3, "severe"),
])
def test_chokepoint_bars(ws, change, expected):
    assert ws.classify_chokepoint(change) == expected


def test_country_link_reaches_suppliers_located_there(ws):
    rows = week("Suez Canal", date(2026, 9, 20), 20.0) + week("Suez Canal", date(2025, 9, 20), 41.0)
    [item] = ws.chokepoint_signals(rows, LINKS, ROSTER, NOW)
    assert item["severity"] == "notable"
    assert item["affected_suppliers"] == ["Smoore", "ITC"]
    assert item["affected_categories"] == ["EMS", "Nicotine"]


def test_a_stalled_feed_does_not_pass_last_years_rows_off_as_this_week(ws):
    rows = week("Strait of Hormuz", date(2025, 9, 20), 86.0)
    assert ws.chokepoint_signals(rows, LINKS, ROSTER, NOW) == []


def test_no_comparison_without_five_days_a_year_earlier(ws):
    rows = week("Suez Canal", date(2026, 9, 20), 40.0) + week("Suez Canal", date(2025, 9, 20), 80.0, days=3)
    [item] = ws.chokepoint_signals(rows, LINKS, ROSTER, NOW)
    assert item["baseline"] is None and item["change_pct"] is None
    assert item["severity"] == "quiet"
    assert "no full week a year earlier" in item["headline"]


def test_year_earlier_survives_a_leap_day(ws):
    assert ws._year_earlier(date(2028, 2, 29)) == date(2027, 2, 28)


def test_percentages_are_never_rounded_across_a_bar(ws):
    """Brent at +49.5% on its median is notable; its text must not say 50%."""
    assert ws._pct_text(49.5) == "49%"
    assert ws._above_below(-29.96, "a year earlier") == "29% below a year earlier"
    assert ws._above_below(0.4, "a year earlier") == "level with a year earlier"


# ---------------------------------------------------------------------------
# Rhine
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value,expected", [
    (208, "quiet"), (80, "quiet"), (79.9, "notable"), (40, "notable"), (39.9, "severe"), (-3, "severe"),
])
def test_kaub_bars(ws, value, expected):
    assert ws.classify_gauge(value, ws.KAUB_NOTABLE_CM, ws.KAUB_SEVERE_CM) == expected


def test_kaub_reading_quotes_the_forecast_and_the_route(ws):
    readings = {"KAUB": {"timestamp": "2026-09-29T10:00:00+02:00", "value": 4.0, "stateMnwMhw": "low"}}
    forecasts = {"KAUB": [
        {"timestamp": "2026-09-29T11:00:00+02:00", "value": 5.0, "type": "forecast"},
        {"timestamp": "2026-09-30T21:00:00+02:00", "value": 2.0, "type": "forecast"},
        # Days three and four are the service's lower-confidence estimate.
        {"timestamp": "2026-10-02T07:00:00+02:00", "value": -20.0, "type": "estimate"},
    ]}
    [item] = ws.river_signals(readings, forecasts, LINKS, ROSTER, NOW)

    assert ITEM_KEYS <= set(item)
    assert item["severity"] == "severe"
    assert item["as_of"] == "2026-09-29T10:00:00+02:00"
    # A gauge height is not a depth, so there is no percentage against normal.
    assert item["baseline"] == 208 and item["change_pct"] is None
    assert (item["forecast_low"], item["forecast_high"]) == (2.0, 5.0)
    assert item["affected_suppliers"] == ["Cerdia", "AMCOR", "CNT"]
    assert "forecast 2-5 cm" in item["headline"]
    assert "Cerdia, AMCOR and CNT" in item["headline"]


def test_maxau_is_judged_on_its_own_bars(ws):
    reading = {"timestamp": "2026-09-29T10:00:00+02:00", "value": 360.0}
    [item] = ws.river_signals({"MAXAU": reading}, {}, LINKS, ROSTER, NOW)
    assert item["label"] == "Rhine at Maxau"
    assert item["severity"] == "notable"
    assert "forecast" not in item["headline"]


def test_a_gauge_that_stopped_reporting_is_withheld(ws):
    result = {"rivers": [], "sources": {}}
    raw = {"gauge:KAUB": {"timestamp": "2026-09-25T10:00:00+02:00", "value": 4.0},
           "gauge:MAXAU": {"timestamp": "2026-09-29T10:00:00+02:00", "value": 278.0}}
    ws._build_rivers(result, raw, {}, LINKS, ROSTER, NOW)
    assert [item["label"] for item in result["rivers"]] == ["Rhine at Maxau"]
    assert "Kaub withheld, last reading 2026-09-25T10:00:00+02:00" in result["sources"]["pegelonline"]["detail"]


# ---------------------------------------------------------------------------
# GDACS
# ---------------------------------------------------------------------------

def gdacs_feature(event_type, event_id, alert, countries, episode_alert=None, episode=1,
                  name="", severity=None, frm="2026-09-22T18:00:00", to="2026-09-24T00:00:00"):
    return {"type": "Feature", "properties": {
        "eventtype": event_type, "eventid": event_id, "episodeid": episode, "eventname": name,
        "name": f"Event {event_id}", "alertlevel": alert, "episodealertlevel": episode_alert or alert,
        "fromdate": frm, "todate": to,
        "affectedcountries": [{"iso3": code, "countryname": code} for code in countries],
        "severitydata": severity or {"severity": 0.0, "severitytext": "Magnitude 0 ", "severityunit": ""},
        "url": {"report": f"https://www.gdacs.org/report.aspx?eventid={event_id}"},
    }}


def test_hazards_match_supplier_countries_and_follow_the_latest_episode(ws):
    features = [
        gdacs_feature("DR", 1, "Orange", ["AUT", "CHE", "DEU", "FRA"],
                      severity={"severity": 1264372.0, "severityunit": "km2"},
                      frm="2025-12-21T00:00:00", to="2026-09-27T00:00:00"),
        # Orange at its peak, green now: listed, but no longer driving the level.
        gdacs_feature("FL", 2, "Orange", ["CHN"], episode_alert="Green",
                      frm="2026-07-31T01:00:00", to="2026-09-27T01:00:00"),
        gdacs_feature("TC", 3, "Orange", ["IND"], name="ONE-26",
                      severity={"severity": 83.3328, "severityunit": "km/h"}),
        # No supplier in Mexico.
        gdacs_feature("TC", 4, "Red", ["MEX"], name="POLO-26"),
        # The same event twice: the later episode wins.
        gdacs_feature("TC", 3, "Green", ["IND"], name="ONE-26", episode=0),
    ]
    items = {item["id"]: item for item in ws.hazard_signals(features, ROSTER)}
    assert set(items) == {"hazard-gdacs-dr-1", "hazard-gdacs-fl-2", "hazard-gdacs-tc-3"}

    drought = items["hazard-gdacs-dr-1"]
    assert ITEM_KEYS <= set(drought)
    assert drought["severity"] == "notable"
    assert drought["label"] == "Drought in Germany and Switzerland (+2 more)"
    assert drought["affected_suppliers"] == ["Cerdia", "AMCOR", "CNT"]
    assert drought["as_of"] == "2026-09-27"
    assert "21 Dec 2025 to 27 Sep 2026" in drought["headline"]

    flood = items["hazard-gdacs-fl-2"]
    assert flood["severity"] == "quiet"
    assert flood["value"] is None
    assert "orange at its peak and green in its latest update" in flood["headline"]

    cyclone = items["hazard-gdacs-tc-3"]
    assert cyclone["severity"] == "notable"
    assert cyclone["label"] == "Tropical cyclone ONE-26"
    assert cyclone["value"] == 83.3
    assert "winds up to 83 km/h; ITC is based there." in cyclone["headline"]


# ---------------------------------------------------------------------------
# FRED
# ---------------------------------------------------------------------------

def test_fred_csv_skips_blank_days_and_rejects_anything_else(ws):
    text = "observation_date,DCOILBRENTEU\n2026-09-21,116.15\n2025-12-25,\n2026-09-22,114.89\n2024-12-26,.\n"
    assert ws.parse_fred_csv(text, "DCOILBRENTEU") == [(date(2026, 9, 21), 116.15), (date(2026, 9, 22), 114.89)]
    with pytest.raises(ws.SourceError):
        ws.parse_fred_csv("<html>Service unavailable</html>", "DCOILBRENTEU")


def daily_series(last: date, values: list) -> list:
    return [(last - timedelta(days=len(values) - 1 - i), v) for i, v in enumerate(values)]


def wobble(n: int, base: float = 100.0, step: float = 0.005) -> list:
    """Prices moving ±0.5% a day around a flat level."""
    return [base * (1 + step if i % 2 else 1 - step) for i in range(n)]


def test_brent_spike_is_judged_against_its_own_volatility(ws):
    prices = wobble(300)
    prices.append(prices[-1] * 1.04)  # +4% on a market that moves about 1% a day
    stats = ws.daily_price_stats(daily_series(date(2026, 9, 22), prices))
    assert stats["z"] >= ws.BRENT_Z_SEVERE
    assert ws.classify_daily_price(stats) == ("severe", "quiet")


def test_a_brent_fall_is_not_a_risk(ws):
    prices = wobble(300)
    prices.append(prices[-1] * 0.96)
    stats = ws.daily_price_stats(daily_series(date(2026, 9, 22), prices))
    assert stats["z"] <= -ws.BRENT_Z_SEVERE
    assert ws.classify_daily_price(stats)[0] == "quiet"


@pytest.mark.parametrize("latest,expected", [(124.9, "quiet"), (125.0, "notable"), (149.9, "notable"), (150.0, "severe")])
def test_brent_level_against_its_one_year_median(ws, latest, expected):
    prices = [100.0] * 300 + [latest]
    stats = ws.daily_price_stats(daily_series(date(2026, 9, 22), prices))
    assert stats["median"] == 100.0
    assert ws.classify_daily_price(stats)[1] == expected


def test_brent_item_names_its_driver(ws):
    prices = [100.0] * 300 + [160.0]
    item = ws.commodity_signal("brent", LINKS["commodities"]["brent"],
                               daily_series(date(2026, 9, 22), prices), ROSTER, NOW)
    assert ITEM_KEYS <= set(item)
    assert item["severity"] == "severe"
    assert item["baseline"] == 100.0 and item["change_pct"] == 60.0
    assert item["source_url"] == "https://fred.stlouisfed.org/series/DCOILBRENTEU"
    assert item["affected_suppliers"] == ["Cerdia", "Smoore", "Jabil"]
    assert "$160.00 a barrel on 22 Sep" in item["headline"]
    assert "60% above its one-year median" in item["_driver"]


def monthly_series(last: date, values: list) -> list:
    out, year, month = [], last.year, last.month
    for v in reversed(values):
        out.append((date(year, month, 1), v))
        month -= 1
        if month == 0:
            year, month = year - 1, 12
    return sorted(out)


@pytest.mark.parametrize("latest,expected", [(119.9, "quiet"), (120.0, "notable"), (139.9, "notable"), (140.0, "severe")])
def test_monthly_series_against_the_same_month_a_year_earlier(ws, latest, expected):
    values = [100.0] + [110.0] * 11 + [latest]
    item = ws.commodity_signal("eu_gas", LINKS["commodities"]["eu_gas"],
                               monthly_series(date(2026, 7, 1), values), ROSTER, NOW)
    assert item["as_of"] == "2026-07-01" and item["baseline"] == 100.0
    assert item["severity"] == expected
    assert "Jul 2026" in item["headline"] and "Jul 2025" in item["headline"]


@pytest.mark.parametrize("key,last", [("brent", date(2026, 9, 1)), ("eu_gas", date(2026, 3, 1))])
def test_stale_series_are_withheld(ws, key, last):
    observations = [(last - timedelta(days=1), 10.0), (last, 11.0)]
    with pytest.raises(ws.StaleData):
        ws.commodity_signal(key, LINKS["commodities"][key], observations, ROSTER, NOW)


# ---------------------------------------------------------------------------
# collect_world_signals end to end, over a fake HTTP layer
# ---------------------------------------------------------------------------

class FakeResponse:
    def __init__(self, payload=None, text=None, status=200):
        self._payload, self.status_code = payload, status
        self.text = text if text is not None else json.dumps(payload)
        self.content = self.text.encode()

    def json(self):
        return json.loads(self.text)

    def raise_for_status(self):
        if self.status_code >= 400:
            response = requests.Response()
            response.status_code = self.status_code
            raise requests.HTTPError(f"{self.status_code} Error", response=response)


def fred_csv(series_id: str, rows: list) -> str:
    return f"observation_date,{series_id}\n" + "".join(f"{d.isoformat()},{v}\n" for d, v in rows)


def fake_web(url, params=None):
    if "arcgis" in url:
        rows = week("Strait of Hormuz", date(2026, 9, 20), 3.1) + week("Strait of Hormuz", date(2025, 9, 20), 86.0)
        return FakeResponse({"features": [{"attributes": r} for r in rows]})
    if "/KAUB/W/" in url:
        return FakeResponse({"timestamp": "2026-09-29T10:00:00+02:00", "value": 4.0})
    if "/MAXAU/W/" in url:
        return FakeResponse({"timestamp": "2026-09-29T10:00:00+02:00", "value": 278.0})
    if "/KAUB/WV/" in url:
        return FakeResponse([{"timestamp": "2026-09-30T10:00:00+02:00", "value": 2.0, "type": "forecast"}])
    if "gdacs" in url:
        return FakeResponse({"type": "FeatureCollection", "features": [gdacs_feature("TC", 3, "Orange", ["IND"], name="ONE-26")]})
    if "fredgraph" in url:
        if params["id"] == "DCOILBRENTEU":
            return FakeResponse(text=fred_csv("DCOILBRENTEU", daily_series(date(2026, 9, 22), [100.0] * 300 + [114.89])))
        return FakeResponse(text=fred_csv(params["id"], monthly_series(date(2026, 7, 1), [11.47] + [12.0] * 11 + [17.93])))
    raise AssertionError(f"unexpected request {url}")


@pytest.fixture
def offline(ws, monkeypatch):
    monkeypatch.setattr(ws, "load_links", lambda path=None: LINKS)
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    return ws


def test_collect_returns_the_contract_the_page_reads(offline, monkeypatch):
    ws = offline
    monkeypatch.setattr(ws, "_get", fake_web)
    result = ws.collect_world_signals(ROSTER, now=NOW)

    assert set(result) == {"fetched_at", "level", "drivers", "commodities", "chokepoints",
                           "rivers", "hazards", "sources"}
    assert result["fetched_at"] == NOW.isoformat()
    assert result["level"] == "severe"
    # Worst first, and physical disruption ahead of prices within a level.
    assert result["drivers"][0].startswith("Strait of Hormuz: transits down 96%")
    assert result["drivers"][1].startswith("Rhine at Kaub 4 cm")
    assert {s["status"] for s in result["sources"].values()} == {"ok"}
    assert set(result["sources"]) == {"imf_portwatch", "pegelonline", "gdacs", "fred"}
    for group in ("commodities", "chokepoints", "rivers", "hazards"):
        assert result[group], group
        for item in result[group]:
            assert ITEM_KEYS <= set(item)
            assert "_driver" not in item
            assert item["severity"] in ("quiet", "notable", "severe")
    json.dumps(result)  # the snapshot is JSON


def test_dead_sources_fail_soft(offline, monkeypatch):
    ws = offline

    def down(url, params=None):
        raise requests.ConnectionError("connection refused")

    monkeypatch.setattr(ws, "_get", down)
    result = ws.collect_world_signals(ROSTER, now=NOW)
    assert result["level"] == "quiet" and result["drivers"] == []
    assert all(not result[g] for g in ("commodities", "chokepoints", "rivers", "hazards"))
    assert {name: s["status"] for name, s in result["sources"].items()} == {
        "imf_portwatch": "failed", "pegelonline": "failed", "gdacs": "failed", "fred": "failed",
    }
    assert result["sources"]["gdacs"]["detail"] == "unreachable"


def test_one_malformed_answer_costs_only_its_own_source(offline, monkeypatch):
    ws = offline

    def web(url, params=None):
        if "gdacs" in url:
            return FakeResponse(text="<html>maintenance</html>")
        return fake_web(url, params)

    monkeypatch.setattr(ws, "_get", web)
    result = ws.collect_world_signals(ROSTER, now=NOW)
    assert result["sources"]["gdacs"]["status"] == "failed"
    assert result["sources"]["imf_portwatch"]["status"] == "ok"
    assert result["chokepoints"] and not result["hazards"]


def test_a_hanging_source_is_abandoned_at_the_budget(offline, monkeypatch):
    ws = offline
    release = threading.Event()

    def web(url, params=None):
        if "gdacs" in url:
            release.wait(5)
        return fake_web(url, params)

    monkeypatch.setattr(ws, "_get", web)
    monkeypatch.setattr(ws, "WORLD_SIGNALS_BUDGET_SEC", 1)
    started = time.monotonic()
    try:
        result = ws.collect_world_signals(ROSTER, now=NOW)
    finally:
        release.set()
    assert time.monotonic() - started < 3
    assert result["sources"]["gdacs"] == {"status": "failed", "detail": "no answer within 1s"}
    assert result["sources"]["imf_portwatch"]["status"] == "ok"


def test_the_fred_key_never_reaches_the_page_or_the_log(offline, monkeypatch, caplog):
    ws = offline
    secret = "k3y-that-must-not-leak"
    monkeypatch.setenv("FRED_API_KEY", secret)

    def web(url, params=None):
        if "fredgraph" in url or "api.stlouisfed.org" in url:
            response = requests.Response()
            response.status_code = 400
            full = f"{url}?series_id={params.get('series_id', params.get('id'))}&api_key={params.get('api_key', '')}"
            raise requests.HTTPError(f"400 Client Error: Bad Request for url: {full}", response=response)
        return fake_web(url, params)

    monkeypatch.setattr(ws, "_get", web)
    with caplog.at_level("DEBUG"):
        result = ws.collect_world_signals(ROSTER, now=NOW)
    assert result["sources"]["fred"]["status"] == "failed"
    assert "HTTP 400" in result["sources"]["fred"]["detail"]
    assert secret not in json.dumps(result)
    assert secret not in caplog.text


def test_unreadable_links_file_is_reported_not_raised(ws, monkeypatch):
    def broken(path=None):
        raise json.JSONDecodeError("Expecting value", "", 0)

    monkeypatch.setattr(ws, "load_links", broken)
    monkeypatch.setattr(ws, "_get", fake_web)
    result = ws.collect_world_signals(ROSTER, now=NOW)
    assert result["sources"]["world_links"]["status"] == "failed"
    # The Rhine gauges and GDACS need no links file to be read.
    assert result["rivers"] and result["hazards"]


# ---------------------------------------------------------------------------
# The hand-maintained link file
# ---------------------------------------------------------------------------

# Every chokepoint name IMF PortWatch served on 29 Sep 2026. A name outside
# this set is a typo, and a typo silently tracks nothing.
PORTWATCH_NAMES = {
    "Suez Canal", "Panama Canal", "Bosporus Strait", "Bab el-Mandeb Strait", "Malacca Strait",
    "Strait of Hormuz", "Cape of Good Hope", "Gibraltar Strait", "Dover Strait", "Oresund Strait",
    "Taiwan Strait", "Korea Strait", "Tsugaru Strait", "Luzon Strait", "Lombok Strait", "Ombai Strait",
    "Bohai Strait", "Torres Strait", "Sunda Strait", "Makassar Strait", "Magellan Strait",
    "Yucatan Channel", "Windward Passage", "Mona Passage", "Balabac Strait", "Bering Strait",
    "Mindoro Strait", "Kerch Strait",
}


def test_world_links_match_the_watchlist(ws):
    links = ws.load_links()
    watchlist = json.loads((ROOT / "data" / "suppliers.json").read_text())
    categories = set(watchlist["category_segments"])
    names = {s["name"] for s in watchlist["suppliers"]}
    locations = {s["location"] for s in watchlist["suppliers"]}

    for key, link in links["commodities"].items():
        assert link["frequency"] in ("daily", "monthly"), key
        assert link.get("series") and link.get("label") and link.get("feeds"), key
        assert set(link["categories"]) <= categories, key
    for name, link in links["chokepoints"].items():
        assert name in PORTWATCH_NAMES, name
        assert set(link.get("countries", [])) <= locations, name
        assert link.get("route"), name
        commodity = link.get("exposure_from_commodity")
        assert commodity is None or commodity in links["commodities"], name
    for river, link in links["rivers"].items():
        assert set(link["suppliers"]) <= names, river
        assert river in {g["river"] for g in ws.RIVER_GAUGES.values()}, river


def test_every_supplier_country_can_match_a_hazard(ws):
    watchlist = json.loads((ROOT / "data" / "suppliers.json").read_text())
    assert {s["location"] for s in watchlist["suppliers"]} <= set(ws.COUNTRY_ISO3)
