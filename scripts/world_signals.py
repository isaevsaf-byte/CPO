#!/usr/bin/env python3
"""
World signals: the shipping lanes, river, hazards and prices behind the watchlist.

On 28 Sep 2026 the board said "All clear" while the Strait of Hormuz was
carrying 3.9 ships a day against 87.9 in September 2025, Brent stood at
$114.89 and the Rhine gauge at Kaub read 0 cm, two days after a record low of
-3 cm. None of that is news about a named supplier, and every sensor the board
had was pointed at named suppliers, so none of it could register. This module
reads those conditions from the bodies that measure them and says which
suppliers each one reaches, using data/world_links.json.

Sources, all keyless:
  IMF PortWatch  daily transits through maritime chokepoints
  PEGELONLINE    the Rhine at Kaub and Maxau, and the Kaub forecast
  GDACS          orange and red disaster alerts from the last 14 days
  FRED           Brent (daily); EU gas, US wood pulp PPI, aluminium (monthly)

collect_world_signals(suppliers, now=None) is the entry point. It never raises:
a source that fails is named under "sources" and contributes no items, and the
call returns within WORLD_SIGNALS_BUDGET_SEC however the sources behave.
"""

import csv
import io
import json
import logging
import os
import re
import statistics
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import date, datetime, timedelta, timezone
from functools import partial
from pathlib import Path

import requests

# A child of the harvester's logger, so these lines come out in its format
# when update_intel.py drives this module, without configuring logging here.
logger = logging.getLogger("intel_harvester.world_signals")

USER_AGENT = {"User-Agent": "SupplyChainWatchtower/1.0 (+https://cpo-watchtower.co.uk)"}

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
LINKS_FILE = DATA_DIR / "world_links.json"
SUPPLIERS_FILE = DATA_DIR / "suppliers.json"

# The harvest job has a 10-minute ceiling, and the GDELT phase alone is allowed
# 260 seconds of it, so this phase gets a hard wall-clock budget. Every fetch
# runs in its own thread; one still running when the budget expires is
# reported as failed and abandoned rather than waited for.
WORLD_SIGNALS_BUDGET_SEC = 35
REQUEST_TIMEOUT = (5, 20)  # (connect, read) seconds, per request

SEVERITY_ORDER = ("quiet", "notable", "severe")
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

# ============================================================================
# IMF PORTWATCH — daily ship transits through chokepoints
# ============================================================================
PORTWATCH_QUERY_URL = (
    "https://services9.arcgis.com/weJ1QsnbMYJlCHdG/arcgis/rest/services/"
    "Daily_Chokepoints_Data/FeatureServer/0/query"
)
PORTWATCH_PAGE_URL = "https://portwatch.imf.org/"
PORTWATCH_PAGE_SIZE = 1000
# The feed runs about a week behind: on 29 Sep 2026 its newest row was 20 Sep.
# Forty-five days finds the newest week with room to spare if it slips further,
# and the same span a year earlier rides along in the same request — seven
# chokepoints over both spans is about 630 rows, one page.
PORTWATCH_LOOKBACK_DAYS = 45
CHOKEPOINT_WINDOW_DAYS = 7
# Days needed on each side before a week is compared with a year earlier.
CHOKEPOINT_MIN_DAYS = 5
# Transits over the last seven days of data against the same seven days a
# year earlier. A year-on-year comparison is blind to a disruption that was
# already running a year ago: the Suez Canal averaged 41.9 transits a day in
# the week to 20 Sep 2026 and 41.3 a year before that, which reads as normal,
# while the same week of 2023 averaged 72.0. Hormuz at 3.1 against 86.0
# (-96%) is the kind of break these bars are for.
CHOKEPOINT_NOTABLE_PCT = -30.0
CHOKEPOINT_SEVERE_PCT = -60.0

# ============================================================================
# PEGELONLINE — Rhine gauges (WSV). Data licence: DL-DE Zero 2.0 (Datenlizenz
# Deutschland – Zero – Version 2.0), which allows any use without conditions,
# attribution included.
# ============================================================================
PEGELONLINE_API = "https://www.pegelonline.wsv.de/webservices/rest-api/v2/stations"
PEGELONLINE_CHART = "https://pegelonline.wsv.de/webservices/zeitreihe/visualisierung?pegeluuid={}"

# Loading bars in centimetres on the gauge. Tunable: they are judgement calls
# about when low water starts to cost money, not published limits. Kaub's
# notable bar sits at its equivalent low-water level (GlW, 77 cm), where the
# guaranteed 1.90 m channel depth ends and barges start sailing part-loaded;
# by 40 cm they carry a fraction of a normal load. Maxau's bars sit the same
# distance from its own GlW (372 cm), +3 cm and -37 cm, so the two gauges
# mean the same thing by notable. On 29 Sep 2026 Kaub read 4 cm and Maxau
# 278 cm.
KAUB_NOTABLE_CM = 80
KAUB_SEVERE_CM = 40
MAXAU_NOTABLE_CM = 375
MAXAU_SEVERE_CM = 335

# A gauge reading is a height above an arbitrary zero, not a depth, so it is
# never expressed as a percentage: Kaub's 4 cm on 29 Sep still left about
# 1.2 m in the channel, and "-98% against normal" would have described an
# empty river that was not empty. The mean (MW, 2010-2020, from PEGELONLINE's
# characteristic values) is carried as the baseline so the reader has a
# sense of normal, and nothing more.
RIVER_GAUGES = {
    "KAUB": {
        "label": "Rhine at Kaub",
        "river": "Rhine",
        "notable_cm": KAUB_NOTABLE_CM,
        "severe_cm": KAUB_SEVERE_CM,
        "mean_cm": 208,
        "uuid": "1d26e504-7f9e-480a-b52c-5932be6549ab",
        "forecast": True,
    },
    "MAXAU": {
        "label": "Rhine at Maxau",
        "river": "Rhine",
        "notable_cm": MAXAU_NOTABLE_CM,
        "severe_cm": MAXAU_SEVERE_CM,
        "mean_cm": 496,
        "uuid": "b6c6d5c8-e2d5-4469-8dd8-fa972ef7eaea",
        "forecast": False,
    },
}

# PEGELONLINE posts a reading every 15 minutes. A "current" measurement older
# than this means the gauge has stopped reporting, and its last value would be
# read as today's river.
GAUGE_MAX_AGE_HOURS = 48

GAUGE_BAND_TEXT = {
    "severe": "below the {severe_cm} cm mark, where barges carry only a fraction of a normal load",
    "notable": "below the {notable_cm} cm mark, where barges start sailing part-loaded",
    "quiet": "above the {notable_cm} cm mark where low water starts to limit barge loads",
}

# ============================================================================
# GDACS — orange and red disaster alerts
# ============================================================================
GDACS_URL = "https://www.gdacs.org/gdacsapi/api/events/geteventlist/SEARCH"
GDACS_EVENT_TYPES = ("EQ", "TC", "FL", "VO", "DR", "WF")
GDACS_LOOKBACK_DAYS = 14
# Severity follows the alert GDACS gives the event's latest episode, not its
# peak. The query filters on the peak, and floods in China and India came back
# on 29 Sep 2026 as orange events whose latest episode GDACS rated green: they
# are listed, as context, without holding the board at notable for the two
# months each flood has been open.
GDACS_SEVERITY = {"Red": "severe", "Orange": "notable"}
GDACS_TYPE_NAMES = {
    "EQ": "Earthquake",
    "TC": "Tropical cyclone",
    "FL": "Flood",
    "VO": "Volcanic eruption",
    "DR": "Drought",
    "WF": "Wildfire",
}
# GDACS names affected countries by ISO 3166 alpha-3; the watchlist names
# them in words. A supplier country missing here matches no hazard, so add it
# alongside any new location in suppliers.json.
COUNTRY_ISO3 = {
    "Austria": "AUT",
    "China": "CHN",
    "Finland": "FIN",
    "Germany": "DEU",
    "India": "IND",
    "Japan": "JPN",
    "Netherlands": "NLD",
    "South Africa": "ZAF",
    "South Korea": "KOR",
    "Sweden": "SWE",
    "Switzerland": "CHE",
    "USA": "USA",
}

# ============================================================================
# FRED — commodity prices
# ============================================================================
FRED_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"
FRED_API_URL = "https://api.stlouisfed.org/fred/series/observations"
FRED_SERIES_PAGE = "https://fred.stlouisfed.org/series/{}"
# Brent reaches FRED from the EIA a week or so late (on 29 Sep 2026 the newest
# daily price was 22 Sep's $114.89) and the IMF's monthly commodity prices lag
# two months (July was the newest in late September). Past these ages a series
# has stopped updating, and a stale price read as current is worse than none —
# the same rule as FRED_MAX_OBSERVATION_AGE_DAYS in update_intel.py.
FRED_DAILY_MAX_AGE_DAYS = 14
FRED_MONTHLY_MAX_AGE_DAYS = 150
FRED_DAILY_HISTORY_DAYS = 400    # a year of prices for the median, plus slack
FRED_MONTHLY_HISTORY_DAYS = 550  # thirteen months even when two months late

# Brent is judged two ways. A daily move against its own volatility over the
# three months before it (the yardstick the board already uses for share
# prices, see classify_price_move in update_intel.py) catches a spike the day
# it happens; the level against its one-year median catches a price that got
# there slowly and stays. On 22 Sep 2026 the move said nothing (-1.1% on a
# market moving 4.4% a day) and the level said notable ($114.89, 49.5% over a
# $76.87 median). Only rises count: a 4-sigma fall in Brent is relief for every
# buyer of acetate tow and plastics, and scoring it severe would turn good news
# red.
BRENT_Z_NOTABLE = 2.0
BRENT_Z_SEVERE = 3.5
BRENT_LEVEL_NOTABLE_PCT = 25.0
BRENT_LEVEL_SEVERE_PCT = 50.0
BRENT_VOL_WINDOW_DAYS = 92      # "three months" in calendar days
BRENT_VOL_MIN_RETURNS = 20      # fewer daily moves than this and sigma is noise
BRENT_MEDIAN_WINDOW_DAYS = 365
BRENT_MEDIAN_MIN_OBS = 120      # about half a year of trading days

# Monthly series: the latest month against the same month a year earlier.
MONTHLY_YOY_NOTABLE_PCT = 20.0
MONTHLY_YOY_SEVERE_PCT = 40.0


class SourceError(Exception):
    """A source answered, but not with anything usable."""


class StaleData(SourceError):
    """A source answered with data too old to publish as current."""


# ============================================================================
# SMALL HELPERS
# ============================================================================

def _as_utc(now) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    if now.tzinfo is None:
        return now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc)


def _year_earlier(day: date) -> date:
    try:
        return day.replace(year=day.year - 1)
    except ValueError:  # 29 February
        return day.replace(year=day.year - 1, day=28)


def _day_label(day: date) -> str:
    return f"{day.day} {MONTHS[day.month - 1]}"


def _month_label(day: date) -> str:
    return f"{MONTHS[day.month - 1]} {day.year}"


def _span_label(start: date, end: date) -> str:
    """'22-24 Sep', '31 Jul to 27 Sep', '21 Dec 2025 to 27 Sep 2026'."""
    if start == end:
        return _day_label(start)
    if start.year != end.year:
        return f"{_day_label(start)} {start.year} to {_day_label(end)} {end.year}"
    if start.month == end.month:
        return f"{start.day}-{end.day} {MONTHS[end.month - 1]}"
    return f"{_day_label(start)} to {_day_label(end)}"


def _parse_day(value) -> date | None:
    """A PortWatch or GDACS date: 'YYYY-MM-DD[...]' text or epoch milliseconds."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc).date()
    if isinstance(value, str) and len(value) >= 10:
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _number(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _pct_change(value, baseline) -> float | None:
    if value is None or not baseline:
        return None
    return round((value - baseline) / baseline * 100, 1)


def _worst(severities) -> str:
    return max(severities, key=SEVERITY_ORDER.index, default="quiet")


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _join(words: list) -> str:
    if len(words) <= 1:
        return "".join(words)
    return ", ".join(words[:-1]) + " and " + words[-1]


def _who(names: list) -> str:
    """Suppliers as a reader would say them: named when there are three or fewer."""
    if not names:
        return "no watchlist supplier"
    if len(names) <= 3:
        return _join(names)
    return f"{len(names)} watchlist suppliers"


def _is_are(names: list) -> str:
    return "is" if len(names) <= 1 else "are"


def _pct_text(pct: float) -> str:
    """A percentage for reading, truncated rather than rounded, so the text
    never crosses a threshold the number did not. Brent stood 49.5% above its
    one-year median on 22 Sep 2026, which is notable; "50% above" beside it
    would have read as the severe bar."""
    return f"{int(abs(pct))}%"


def _above_below(pct: float, reference: str) -> str:
    if int(abs(pct)) == 0:
        return f"level with {reference}"
    return f"{_pct_text(pct)} {'above' if pct > 0 else 'below'} {reference}"


def _format_value(value: float, unit: str) -> str:
    if unit == "USD/bbl":
        return f"${value:,.2f} a barrel"
    if unit == "USD/MMBtu":
        return f"${value:,.2f} per MMBtu"
    if unit == "USD/t":
        return f"${value:,.0f} a tonne"
    return f"{value:,.1f} ({unit})" if unit else f"{value:,.1f}"


def _describe_failure(exc: BaseException) -> str:
    """Why a fetch failed, in a few words for the page.

    Never the exception text itself: a requests error message carries the full
    request URL, and for the keyed FRED API that URL carries FRED_API_KEY.
    """
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        return f"HTTP {exc.response.status_code}"
    if isinstance(exc, requests.Timeout):
        return "timed out"
    if isinstance(exc, requests.ConnectionError):
        return "unreachable"
    if isinstance(exc, SourceError):
        return _scrub(str(exc))
    if isinstance(exc, (ValueError, KeyError, TypeError, IndexError)):
        return f"unreadable response ({type(exc).__name__})"
    return type(exc).__name__


def _scrub(text: str) -> str:
    key = os.getenv("FRED_API_KEY")
    return text.replace(key, "***") if key else text


def _get(url: str, params: dict | None = None) -> requests.Response:
    response = requests.get(url, params=params, headers=USER_AGENT, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    return response


def _item(*, id, label, value, unit, as_of, baseline, change_pct, severity,
          headline, suppliers, categories, source, source_url, driver, **extra) -> dict:
    item = {
        "id": id,
        "label": label,
        "value": value,
        "unit": unit,
        "as_of": as_of,
        "baseline": baseline,
        "change_pct": change_pct,
        "severity": severity,
        "headline": headline,
        "affected_categories": categories,
        "affected_suppliers": suppliers,
        "source": source,
        "source_url": source_url,
    }
    item.update(extra)
    # Removed before the result is returned; see _summarise.
    item["_driver"] = driver
    return item


# ============================================================================
# EXPOSURE — which suppliers a signal reaches
# ============================================================================

def load_links(path: Path = LINKS_FILE) -> dict:
    with open(path) as f:
        raw = json.load(f)
    return {
        "commodities": raw.get("commodities") or {},
        "chokepoints": raw.get("chokepoints") or {},
        "rivers": raw.get("rivers") or {},
    }


def supplier_roster(suppliers: list | None) -> list:
    """name/category/location for each supplier, from the caller or suppliers.json."""
    rows = suppliers
    if not rows:
        with open(SUPPLIERS_FILE) as f:
            rows = json.load(f).get("suppliers", [])
    return [
        {"name": s["name"], "category": s.get("category"), "location": s.get("location")}
        for s in rows
        if isinstance(s, dict) and s.get("name")
    ]


def exposure(roster: list, categories=(), countries=(), names=()) -> tuple:
    """(supplier names, categories) reached through any of the three kinds of link.

    Names keep the watchlist's order. Categories named by the link are kept
    even when no supplier currently sits in them, so the link stays visible.
    """
    categories, countries, names = set(categories), set(countries), set(names)
    hit = [
        s for s in roster
        if s["category"] in categories or s["location"] in countries or s["name"] in names
    ]
    reached = sorted({s["category"] for s in hit if s["category"]} | categories)
    return [s["name"] for s in hit], reached


# ============================================================================
# CHOKEPOINTS
# ============================================================================

def fetch_portwatch_rows(names: list, now: datetime) -> list:
    """Daily rows for the named chokepoints: recent weeks and the same span a year earlier."""
    today = now.date()
    start = today - timedelta(days=PORTWATCH_LOOKBACK_DAYS)
    quoted = ", ".join("'" + name.replace("'", "''") + "'" for name in names)
    where = (
        f"portname IN ({quoted}) AND ("
        f"(date >= DATE '{start.isoformat()}' AND date <= DATE '{today.isoformat()}') OR "
        f"(date >= DATE '{_year_earlier(start).isoformat()}' "
        f"AND date <= DATE '{_year_earlier(today).isoformat()}'))"
    )
    rows, offset = [], 0
    for _ in range(10):  # a runaway pager is not worth the budget
        payload = _get(PORTWATCH_QUERY_URL, params={
            "where": where,
            "outFields": "date,portname,n_total",
            "returnGeometry": "false",
            "orderByFields": "date",
            "resultOffset": offset,
            "resultRecordCount": PORTWATCH_PAGE_SIZE,
            "f": "json",
        }).json()
        # ArcGIS reports a bad query as HTTP 200 with an "error" body.
        if "error" in payload:
            message = str((payload.get("error") or {}).get("message", ""))[:80]
            raise SourceError(f"query rejected: {message}")
        features = payload.get("features") or []
        rows.extend(f.get("attributes") or {} for f in features)
        if not payload.get("exceededTransferLimit") or not features:
            break
        offset += len(features)
    return rows


def classify_chokepoint(change_pct) -> str:
    if change_pct is None:
        return "quiet"
    if change_pct <= CHOKEPOINT_SEVERE_PCT:
        return "severe"
    if change_pct <= CHOKEPOINT_NOTABLE_PCT:
        return "notable"
    return "quiet"


def chokepoint_signals(rows: list, links: dict, roster: list, now: datetime) -> list:
    today = _as_utc(now).date()
    recent_from = today - timedelta(days=PORTWATCH_LOOKBACK_DAYS)
    series = defaultdict(dict)
    for row in rows:
        day, count = _parse_day(row.get("date")), _number(row.get("n_total"))
        if row.get("portname") and day and count is not None:
            series[row["portname"]][day] = count

    items = []
    for name, link in links.get("chokepoints", {}).items():
        by_day = series.get(name) or {}
        # The newest day must come from the recent span: if the feed stalls for
        # longer than the lookback, the year-earlier rows would otherwise pass
        # for this week.
        recent_days = [d for d in by_day if d >= recent_from]
        if not recent_days:
            continue
        latest = max(recent_days)
        window = [latest - timedelta(days=i) for i in range(CHOKEPOINT_WINDOW_DAYS)]
        recent = [by_day[d] for d in window if d in by_day]
        earlier = [by_day[_year_earlier(d)] for d in window if _year_earlier(d) in by_day]
        value = sum(recent) / len(recent)
        comparable = len(recent) >= CHOKEPOINT_MIN_DAYS and len(earlier) >= CHOKEPOINT_MIN_DAYS
        baseline = sum(earlier) / len(earlier) if comparable else None
        change = _pct_change(value, baseline)
        severity = classify_chokepoint(change)

        categories = list(link.get("categories") or [])
        from_commodity = link.get("exposure_from_commodity")
        if from_commodity:
            categories += links.get("commodities", {}).get(from_commodity, {}).get("categories", [])
        suppliers, reached = exposure(roster, categories=categories, countries=link.get("countries") or [])

        if change is None:
            comparison = "with no full week a year earlier to compare it with"
        elif int(abs(change)) == 0:
            comparison = f"level with the same week a year earlier ({baseline:.1f})"
        else:
            comparison = (
                f"{_pct_text(change)} {'fewer' if change < 0 else 'more'} than the same week "
                f"a year earlier ({baseline:.1f})"
            )
        route = link.get("route") or "this route"
        headline = (
            f"{name} saw {value:.1f} ship transits a day in the week to {_day_label(latest)}, "
            f"{comparison}; {_who(suppliers)} {_is_are(suppliers)} exposed through {route}."
        )
        driver = (
            f"{name}: transits {'down' if (change or 0) < 0 else 'up'} {_pct_text(change or 0)} "
            f"on a year earlier (week to {_day_label(latest)})"
        )
        items.append(_item(
            id=f"chokepoint-{_slug(name)}",
            label=name,
            value=round(value, 1),
            unit="transits/day",
            as_of=latest.isoformat(),
            baseline=round(baseline, 1) if baseline is not None else None,
            change_pct=change,
            severity=severity,
            headline=headline,
            suppliers=suppliers,
            categories=reached,
            source="IMF PortWatch",
            source_url=PORTWATCH_PAGE_URL,
            driver=driver,
        ))
    return items


# ============================================================================
# RIVERS
# ============================================================================

def fetch_gauge_reading(code: str) -> dict:
    return _get(f"{PEGELONLINE_API}/{code}/W/currentmeasurement.json").json()


def fetch_gauge_forecast(code: str) -> list:
    payload = _get(f"{PEGELONLINE_API}/{code}/WV/measurements.json").json()
    if not isinstance(payload, list):
        raise SourceError("forecast was not a list")
    return payload


def classify_gauge(value_cm: float, notable_cm: float, severe_cm: float) -> str:
    if value_cm < severe_cm:
        return "severe"
    if value_cm < notable_cm:
        return "notable"
    return "quiet"


def forecast_range(points: list | None) -> dict | None:
    """Low and high of the gauge forecast, or None.

    PEGELONLINE marks the first two days "forecast" and the two after
    "estimate"; only the forecast is quoted, the estimate being the
    service's own lower-confidence extension.
    """
    values = []
    last_at = None
    for p in points or []:
        v = _number(p.get("value")) if isinstance(p, dict) else None
        if v is None or p.get("type", "forecast") != "forecast":
            continue
        values.append(v)
        last_at = p.get("timestamp") or last_at
    if not values:
        return None
    return {"low": min(values), "high": max(values), "until": last_at}


def gauge_age_hours(reading: dict, now: datetime) -> float | None:
    try:
        measured = datetime.fromisoformat(str(reading.get("timestamp")))
    except ValueError:
        return None
    if measured.tzinfo is None:
        measured = measured.replace(tzinfo=timezone.utc)
    return (_as_utc(now) - measured).total_seconds() / 3600


def river_signals(readings: dict, forecasts: dict, links: dict, roster: list, now: datetime) -> list:
    items = []
    for code, gauge in RIVER_GAUGES.items():
        reading = readings.get(code) or {}
        value = _number(reading.get("value"))
        if value is None:
            continue
        age = gauge_age_hours(reading, now)
        if age is None or age > GAUGE_MAX_AGE_HOURS:
            continue
        link = links.get("rivers", {}).get(gauge["river"], {})
        suppliers, reached = exposure(roster, names=link.get("suppliers") or [])
        severity = classify_gauge(value, gauge["notable_cm"], gauge["severe_cm"])
        band = GAUGE_BAND_TEXT[severity].format(**gauge)
        outlook = forecast_range(forecasts.get(code))
        outlook_text = ""
        if outlook:
            low, high = outlook["low"], outlook["high"]
            spread = f"{low:.0f} cm" if low == high else f"{low:.0f}-{high:.0f} cm"
            outlook_text = f", forecast {spread} over the next two days"
        headline = (
            f"The {gauge['label']} reads {value:.0f} cm (normal {gauge['mean_cm']} cm{outlook_text}), "
            f"{band}; it is the route for {_who(suppliers)}."
        )
        threshold = gauge["severe_cm"] if severity == "severe" else gauge["notable_cm"]
        extra = {}
        if outlook:
            extra = {
                "forecast_low": outlook["low"],
                "forecast_high": outlook["high"],
                "forecast_until": outlook["until"],
            }
        items.append(_item(
            id=f"river-{_slug(gauge['label'])}",
            label=gauge["label"],
            value=value,
            unit="cm",
            as_of=reading.get("timestamp"),
            baseline=gauge["mean_cm"],
            change_pct=None,
            severity=severity,
            headline=headline,
            suppliers=suppliers,
            categories=reached,
            source="PEGELONLINE (WSV)",
            source_url=PEGELONLINE_CHART.format(gauge["uuid"]),
            driver=f"{gauge['label']} {value:.0f} cm, below the {threshold} cm mark",
            **extra,
        ))
    return items


# ============================================================================
# HAZARDS
# ============================================================================

def fetch_gdacs_features(now: datetime) -> list:
    today = now.date()
    # Built by hand rather than through params: the API's lists are
    # semicolon-separated, and requests would percent-encode the separators.
    url = (
        f"{GDACS_URL}?eventlist={';'.join(GDACS_EVENT_TYPES)}&alertlevel=Orange;Red"
        f"&fromDate={(today - timedelta(days=GDACS_LOOKBACK_DAYS)).isoformat()}"
        f"&toDate={today.isoformat()}"
    )
    response = _get(url)
    if not response.content.strip():
        return []
    payload = response.json()
    features = payload.get("features") if isinstance(payload, dict) else None
    if features is None:
        raise SourceError("no features in the response")
    return features


def _hazard_magnitude(severity_data: dict | None) -> str:
    """GDACS's own measure in reader's terms, or '' where it has none.

    Floods come back as "Magnitude 0" with no unit, which says nothing.
    """
    data = severity_data or {}
    value, unit = _number(data.get("severity")), (data.get("severityunit") or "").strip()
    if not value or not unit:
        return ""
    if unit == "km/h":
        return f", winds up to {value:.0f} km/h"
    if unit == "M":
        return f", magnitude {value:.1f}"
    if unit == "km2":
        return f", across {value:,.0f} km²"
    if unit == "ha":
        return f", {value:,.0f} ha burnt"
    return ""


def hazard_signals(features: list, roster: list) -> list:
    # One feature per event is the norm, but keep only the latest episode if
    # an event ever comes back more than once.
    events = {}
    for feature in features:
        props = (feature or {}).get("properties") or {}
        key = (props.get("eventtype"), props.get("eventid"))
        if None in key:
            continue
        held = events.get(key)
        if held is None or (props.get("episodeid") or 0) > (held.get("episodeid") or 0):
            events[key] = props

    supplier_countries = sorted({s["location"] for s in roster if s.get("location")})
    items = []
    for (event_type, event_id), props in events.items():
        listed = props.get("affectedcountries") or []
        iso3 = {c.get("iso3") for c in listed if isinstance(c, dict) and c.get("iso3")}
        if not iso3 and props.get("iso3"):
            iso3 = {part.strip() for part in str(props["iso3"]).split(",") if part.strip()}
        countries = [c for c in supplier_countries if COUNTRY_ISO3.get(c) in iso3]
        if not countries:
            continue
        suppliers, reached = exposure(roster, countries=countries)

        peak = props.get("alertlevel") or ""
        latest = props.get("episodealertlevel") or peak
        severity = GDACS_SEVERITY.get(latest, "quiet")
        type_name = GDACS_TYPE_NAMES.get(event_type, "Hazard")
        others = len(iso3) - len(countries)
        where = _join(countries) + (f" (+{others} more)" if others > 0 else "")
        # Multi-country droughts are named by GDACS as the full country list,
        # 25 names long for the European drought open on 29 Sep 2026, so the
        # label is rebuilt around the supplier countries. Cyclones keep their
        # own name, which is how the news will refer to them.
        storm = props.get("eventname") if event_type == "TC" else None
        label = f"{type_name} {storm}" if storm else f"{type_name} in {where}"

        start, end = _parse_day(props.get("fromdate")), _parse_day(props.get("todate"))
        span = f", {_span_label(start, end)}" if start and end else ""
        what = f"{type_name.lower()} {storm}" if storm else type_name.lower()
        if severity == "quiet" and peak in GDACS_SEVERITY:
            status = f"GDACS rated it {peak.lower()} at its peak and {(latest or 'green').lower()} in its latest update"
            lead = f"{type_name}{' ' + storm if storm else ''} in {where}{span}: {status}"
        else:
            lead = f"{latest} alert for {what} in {where}{span}{_hazard_magnitude(props.get('severitydata'))}"
        place = "there" if len(countries) == 1 and others <= 0 else "in the affected countries"
        headline = f"{lead}; {_who(suppliers)} {_is_are(suppliers)} based {place}."

        severity_data = props.get("severitydata") or {}
        unit = (severity_data.get("severityunit") or "").strip() or None
        value = _number(severity_data.get("severity")) if unit else None
        value = round(value, 1) if value is not None else None
        items.append(_item(
            id=f"hazard-gdacs-{str(event_type).lower()}-{event_id}",
            label=label,
            value=value,
            unit=unit,
            as_of=end.isoformat() if end else None,
            baseline=None,
            change_pct=None,
            severity=severity,
            headline=headline,
            suppliers=suppliers,
            categories=reached,
            source="GDACS",
            source_url=(props.get("url") or {}).get("report") or "https://www.gdacs.org/",
            driver=f"{latest} {type_name.lower()} alert in {where}",
            event_type=event_type,
            alert_level=latest or None,
            peak_alert_level=peak or None,
            countries=countries,
        ))
    return items


# ============================================================================
# COMMODITIES
# ============================================================================

def parse_fred_csv(text: str, series_id: str) -> list:
    """[(date, value)] in date order from FRED's graph CSV.

    The header names the series in its second column ("observation_date,
    DCOILBRENTEU"; older exports used "DATE"), which also tells a real CSV
    from an HTML error page served with status 200. Days without a price are
    blank — sixteen in Brent's last two years, holidays like 25 Dec — and
    older exports wrote "."; both are skipped.
    """
    reader = csv.reader(io.StringIO(text))
    header = next(reader, None)
    if not header or len(header) < 2 or header[1].strip() != series_id:
        raise SourceError("not a FRED CSV for this series")
    observations = []
    for row in reader:
        if len(row) < 2:
            continue
        try:
            observations.append((date.fromisoformat(row[0].strip()), float(row[1])))
        except ValueError:
            continue
    observations.sort()
    return observations


def parse_fred_json(payload: dict) -> list:
    observations = []
    for obs in (payload or {}).get("observations", []):
        try:
            observations.append((date.fromisoformat(obs["date"]), float(obs["value"])))
        except (KeyError, TypeError, ValueError):
            continue
    observations.sort()
    return observations


def fetch_fred_series(series_id: str, start: date) -> list:
    """Observations since `start`, from the keyless CSV endpoint.

    With FRED_API_KEY set, the keyed JSON API is the fallback when the CSV
    endpoint fails. The key travels in that request's URL, which is why
    failures are only ever reported through _describe_failure.
    """
    try:
        response = _get(FRED_CSV_URL, params={"id": series_id, "cosd": start.isoformat()})
        return parse_fred_csv(response.text, series_id)
    except Exception as csv_failure:
        key = os.getenv("FRED_API_KEY")
        if not key:
            raise
        logger.info(
            f"FRED CSV failed for {series_id} ({_describe_failure(csv_failure)}); trying the keyed API"
        )
        response = _get(FRED_API_URL, params={
            "series_id": series_id,
            "api_key": key,
            "file_type": "json",
            "observation_start": start.isoformat(),
        })
        return parse_fred_json(response.json())


def fred_history_start(link: dict, now: datetime) -> date:
    days = FRED_DAILY_HISTORY_DAYS if link.get("frequency") == "daily" else FRED_MONTHLY_HISTORY_DAYS
    return now.date() - timedelta(days=days)


def daily_price_stats(observations: list) -> dict:
    """Latest price, its day's move against three months of daily moves, and
    its level against the median of the year before it."""
    latest_day, latest = observations[-1]
    previous = observations[-2][1]
    move_pct = (latest - previous) / previous * 100 if previous else None

    before = observations[:-1]
    window = [v for d, v in before if d > latest_day - timedelta(days=BRENT_VOL_WINDOW_DAYS)]
    returns = [(b - a) / a * 100 for a, b in zip(window, window[1:]) if a]
    sigma = statistics.stdev(returns) if len(returns) >= BRENT_VOL_MIN_RETURNS else None
    z = move_pct / sigma if (sigma and move_pct is not None) else None

    year = [v for d, v in before if d > latest_day - timedelta(days=BRENT_MEDIAN_WINDOW_DAYS)]
    median = statistics.median(year) if len(year) >= BRENT_MEDIAN_MIN_OBS else None
    return {
        "day": latest_day,
        "value": latest,
        "move_pct": round(move_pct, 2) if move_pct is not None else None,
        "sigma_pct": round(sigma, 2) if sigma else None,
        "z": round(z, 1) if z is not None else None,
        "median": round(median, 2) if median else None,
        "level_pct": _pct_change(latest, median),
    }


def classify_daily_price(stats: dict) -> tuple:
    """(severity of the day's move, severity of the level). Rises only."""
    move = "quiet"
    z, move_pct = stats.get("z"), stats.get("move_pct")
    if z is not None and move_pct is not None and move_pct > 0:
        if z >= BRENT_Z_SEVERE:
            move = "severe"
        elif z >= BRENT_Z_NOTABLE:
            move = "notable"
    level = "quiet"
    level_pct = stats.get("level_pct")
    if level_pct is not None:
        if level_pct >= BRENT_LEVEL_SEVERE_PCT:
            level = "severe"
        elif level_pct >= BRENT_LEVEL_NOTABLE_PCT:
            level = "notable"
    return move, level


def classify_monthly(change_pct) -> str:
    if change_pct is None:
        return "quiet"
    if change_pct >= MONTHLY_YOY_SEVERE_PCT:
        return "severe"
    if change_pct >= MONTHLY_YOY_NOTABLE_PCT:
        return "notable"
    return "quiet"


def commodity_signal(key: str, link: dict, observations: list, roster: list, now: datetime) -> dict:
    """One commodity item. Raises StaleData when the newest observation is too
    old to publish, SourceError when there is too little to judge."""
    if len(observations) < 2:
        raise SourceError("fewer than two observations")
    daily = link.get("frequency") == "daily"
    latest_day, latest = observations[-1]
    age = (_as_utc(now).date() - latest_day).days
    limit = FRED_DAILY_MAX_AGE_DAYS if daily else FRED_MONTHLY_MAX_AGE_DAYS
    if age > limit:
        raise StaleData(f"newest observation {latest_day.isoformat()} is {age} days old")

    suppliers, reached = exposure(roster, categories=link.get("categories") or [])
    label, unit, series = link.get("label", key), link.get("unit", ""), link.get("series", "")
    feeds = link.get("feeds") or "these inputs"
    who = _who(suppliers)
    extra = {}

    if daily:
        stats = daily_price_stats(observations)
        move_sev, level_sev = classify_daily_price(stats)
        severity = _worst((move_sev, level_sev))
        baseline, change = stats["median"], stats["level_pct"]
        parts = [f"{label} was {_format_value(latest, unit)} on {_day_label(latest_day)}"]
        if change is not None:
            parts.append(_above_below(change, f"its one-year median of {_format_value(stats['median'], unit)}"))
        if stats["move_pct"] is not None:
            if stats["z"] is not None:
                parts.append(
                    f"after a {stats['move_pct']:+.1f}% day ({abs(stats['z']):.1f}x its usual daily move)"
                )
            else:
                parts.append(f"after a {stats['move_pct']:+.1f}% day")
        headline = ", ".join(parts) + f"; it feeds into {feeds} at {who}."
        if move_sev != "quiet" and SEVERITY_ORDER.index(move_sev) >= SEVERITY_ORDER.index(level_sev):
            driver = (
                f"{label} up {stats['move_pct']:.1f}% on {_day_label(latest_day)}, "
                f"{stats['z']:.1f}x its usual daily move"
            )
        else:
            driver = f"{label} at {_format_value(latest, unit)}, {_above_below(change or 0, 'its one-year median')}"
        extra = {"daily_change_pct": stats["move_pct"], "daily_z": stats["z"]}
    else:
        earlier = dict(observations).get(_year_earlier(latest_day))
        baseline, change = earlier, _pct_change(latest, earlier)
        severity = classify_monthly(change)
        month, year_before = _month_label(latest_day), _month_label(_year_earlier(latest_day))
        if change is None:
            comparison = f"with no figure for {year_before} to compare it with"
        else:
            comparison = _above_below(change, year_before)
        headline = (
            f"{label} was {_format_value(latest, unit)} in {month}, {comparison}; "
            f"it feeds into {feeds} at {who}."
        )
        driver = f"{label} {_above_below(change or 0, year_before)}"

    return _item(
        id=f"commodity-{_slug(key)}",
        label=label,
        value=round(latest, 2),
        unit=unit,
        as_of=latest_day.isoformat(),
        baseline=round(baseline, 2) if baseline is not None else None,
        change_pct=change,
        severity=severity,
        headline=headline,
        suppliers=suppliers,
        categories=reached,
        source="FRED",
        source_url=FRED_SERIES_PAGE.format(series),
        driver=driver,
        **extra,
    )


# ============================================================================
# ORCHESTRATION
# ============================================================================

def _run_tasks(tasks: dict, budget_sec: float) -> tuple:
    """Run every fetch at once; ({name: result}, {name: why it failed})."""
    results, failures = {}, {}
    if not tasks:
        return results, failures
    pool = ThreadPoolExecutor(max_workers=len(tasks), thread_name_prefix="world-signals")
    futures = {pool.submit(fn): name for name, fn in tasks.items()}
    done, pending = wait(futures, timeout=budget_sec)
    for future in done:
        name = futures[future]
        try:
            results[name] = future.result()
        except Exception as e:
            failures[name] = e
    for future in pending:
        failures[futures[future]] = SourceError(f"no answer within {budget_sec:.0f}s")
    # Abandon stragglers rather than wait: each ends on its own request timeout.
    pool.shutdown(wait=False, cancel_futures=True)
    return results, failures


def _source(status: str, detail: str) -> dict:
    return {"status": status, "detail": detail}


def _build_chokepoints(result, raw, failures, links, roster, now) -> None:
    if not links["chokepoints"]:
        result["sources"]["imf_portwatch"] = _source("empty", "no chokepoints in world_links.json")
        return
    if "portwatch" in failures:
        result["sources"]["imf_portwatch"] = _source("failed", _describe_failure(failures["portwatch"]))
        return
    items = chokepoint_signals(raw.get("portwatch") or [], links, roster, now)
    result["chokepoints"] = items
    if not items:
        result["sources"]["imf_portwatch"] = _source("empty", "no recent rows for the tracked chokepoints")
        return
    missing = [n for n in links["chokepoints"] if n not in {i["label"] for i in items}]
    newest = max(i["as_of"] for i in items)
    detail = f"{len(items)} chokepoints, data to {newest}"
    if missing:
        detail += f"; no rows for {', '.join(missing)}"
    result["sources"]["imf_portwatch"] = _source("ok", detail)


def _build_rivers(result, raw, failures, links, roster, now) -> None:
    readings, forecasts, notes = {}, {}, []
    for code in RIVER_GAUGES:
        name = f"gauge:{code}"
        if name in raw:
            readings[code] = raw[name]
            age = gauge_age_hours(raw[name] or {}, now)
            if age is None or age > GAUGE_MAX_AGE_HOURS:
                stamp = (raw[name] or {}).get("timestamp") or "no timestamp"
                notes.append(f"{code.title()} withheld, last reading {stamp}")
        elif name in failures:
            notes.append(f"{code.title()} failed ({_describe_failure(failures[name])})")
        forecast_name = f"forecast:{code}"
        if forecast_name in raw:
            forecasts[code] = raw[forecast_name]
        elif forecast_name in failures:
            notes.append(f"{code.title()} forecast failed ({_describe_failure(failures[forecast_name])})")
    items = river_signals(readings, forecasts, links, roster, now)
    result["rivers"] = items
    if not items:
        result["sources"]["pegelonline"] = _source("failed", "; ".join(notes) or "no gauge readings")
        return
    read = ", ".join(f"{i['label']} {i['value']:.0f} cm at {i['as_of']}" for i in items)
    result["sources"]["pegelonline"] = _source("ok", "; ".join([read] + notes))


def _build_hazards(result, raw, failures, roster) -> None:
    if "gdacs" in failures:
        result["sources"]["gdacs"] = _source("failed", _describe_failure(failures["gdacs"]))
        return
    features = raw.get("gdacs") or []
    items = hazard_signals(features, roster)
    result["hazards"] = items
    if not features:
        result["sources"]["gdacs"] = _source(
            "empty", f"no orange or red alerts anywhere in the last {GDACS_LOOKBACK_DAYS} days"
        )
        return
    result["sources"]["gdacs"] = _source(
        "ok", f"{len(features)} orange/red events worldwide, {len(items)} in supplier countries"
    )


def _build_commodities(result, raw, failures, links, roster, now) -> None:
    items, ok, stale, failed = [], [], [], []
    for key, link in links["commodities"].items():
        name, series = f"fred:{key}", link.get("series", key)
        if name in failures:
            failed.append(f"{series} {_describe_failure(failures[name])}")
            continue
        try:
            item = commodity_signal(key, link, raw.get(name) or [], roster, now)
        except StaleData as e:
            stale.append(f"{series} {_describe_failure(e)}")
            continue
        except Exception as e:
            failed.append(f"{series} {_describe_failure(e)}")
            continue
        items.append(item)
        ok.append(f"{series} to {item['as_of']}")
    result["commodities"] = items
    detail = "; ".join(
        part for part in (
            ", ".join(ok),
            ("stale: " + ", ".join(stale)) if stale else "",
            ("failed: " + ", ".join(failed)) if failed else "",
        ) if part
    ) or "no series configured"
    if items:
        status = "ok"
    elif stale and not failed:
        status = "empty"
    else:
        status = "failed"
    result["sources"]["fred"] = _source(status, detail)


def _summarise(result) -> None:
    """Overall level, drivers worst-first, and each list worst-first.

    Physical disruption leads within a severity: a closed lane or a dry river
    stops goods, while a price only makes them dearer.
    """
    groups = ("chokepoints", "rivers", "commodities", "hazards")
    items = [item for group in groups for item in result[group]]
    result["level"] = _worst(tuple(item["severity"] for item in items))
    flagged = sorted(
        (item for item in items if item["severity"] != "quiet"),
        key=lambda item: -SEVERITY_ORDER.index(item["severity"]),
    )
    # Every flagged item, uncapped: nine were flagged on 29 Sep 2026, and a cap
    # here would drop one silently. How many to show is the page's call.
    result["drivers"] = [item["_driver"] for item in flagged]
    for item in items:
        item.pop("_driver", None)
    for group in groups:
        result[group].sort(key=lambda item: -SEVERITY_ORDER.index(item["severity"]))


def collect_world_signals(suppliers: list[dict], now: datetime | None = None) -> dict:
    """Read every world source once and say what it means for the watchlist.

    suppliers are rows with name, category and location — suppliers.json's
    entries or the harvester's processed rows; when empty, suppliers.json is
    read directly. Returns {"fetched_at", "level", "drivers", "commodities",
    "chokepoints", "rivers", "hazards", "sources"}; see README "World signals".
    """
    now = _as_utc(now)
    result = {
        "fetched_at": now.isoformat(),
        "level": "quiet",
        "drivers": [],
        "commodities": [],
        "chokepoints": [],
        "rivers": [],
        "hazards": [],
        "sources": {},
    }
    try:
        try:
            links = load_links()
        except Exception as e:
            links = {"commodities": {}, "chokepoints": {}, "rivers": {}}
            result["sources"]["world_links"] = _source(
                "failed", f"data/world_links.json unreadable ({type(e).__name__})"
            )
        try:
            roster = supplier_roster(suppliers)
        except Exception as e:
            roster = []
            result["sources"]["suppliers"] = _source(
                "failed", f"no supplier list, so nothing is linked ({type(e).__name__})"
            )

        tasks = {}
        if links["chokepoints"]:
            tasks["portwatch"] = partial(fetch_portwatch_rows, list(links["chokepoints"]), now)
        for code, gauge in RIVER_GAUGES.items():
            tasks[f"gauge:{code}"] = partial(fetch_gauge_reading, code)
            if gauge["forecast"]:
                tasks[f"forecast:{code}"] = partial(fetch_gauge_forecast, code)
        tasks["gdacs"] = partial(fetch_gdacs_features, now)
        for key, link in links["commodities"].items():
            tasks[f"fred:{key}"] = partial(fetch_fred_series, link.get("series", key), fred_history_start(link, now))

        raw, failures = _run_tasks(tasks, WORLD_SIGNALS_BUDGET_SEC)

        # Each builder is independent: one tripping over a malformed response
        # must not cost the others their items.
        builders = (
            ("imf_portwatch", lambda: _build_chokepoints(result, raw, failures, links, roster, now)),
            ("pegelonline", lambda: _build_rivers(result, raw, failures, links, roster, now)),
            ("gdacs", lambda: _build_hazards(result, raw, failures, roster)),
            ("fred", lambda: _build_commodities(result, raw, failures, links, roster, now)),
        )
        for source_name, build in builders:
            try:
                build()
            except Exception as e:
                logger.warning(f"World signals: could not read {source_name} ({type(e).__name__})")
                result["sources"][source_name] = _source("failed", f"unreadable response ({type(e).__name__})")

        _summarise(result)
    except Exception as e:  # pragma: no cover - a last guard; the harvest must go on
        logger.warning(f"World signals failed unexpectedly ({type(e).__name__})")
        result["sources"]["world_signals"] = _source("failed", f"unexpected error ({type(e).__name__})")
        for group in ("chokepoints", "rivers", "commodities", "hazards"):
            for item in result[group]:
                item.pop("_driver", None)

    for name, source in result["sources"].items():
        if source["status"] != "ok":
            logger.warning(f"World signals: {name} {source['status']} — {source['detail']}")
    logger.info(
        f"World signals: {result['level']}"
        + (f" — {'; '.join(result['drivers'])}" if result["drivers"] else "")
    )
    return result
