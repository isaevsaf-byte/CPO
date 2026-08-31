#!/usr/bin/env python3
"""
Supply Chain Intelligence Harvester - CPO Three Core Pillars
Fetches data from official government endpoints and saves to JSON.
Runs via GitHub Actions every 6 hours.
"""

import requests
import json
import math
import re
import csv
import io
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote
import sys
import time
import random
import hashlib
import logging
import os
import shutil

try:
    import yfinance as yf
except ImportError:  # pragma: no cover - exercised only where yfinance is absent
    # Optional at import time so the pure scoring/classification helpers in
    # this module can be imported and unit-tested (see tests/) without
    # installing the market-data stack. Every caller checks for None and
    # degrades the same way it already degrades on a fetch failure.
    yf = None

# ============================================================================
# CONFIGURATION
# ============================================================================
USER_AGENT = {'User-Agent': 'SupplyChainIntelligence contact@mycompany.com'}

def utc_now_iso() -> str:
    """Current UTC time as ISO-8601 *with* an explicit +00:00 offset.

    Every timestamp in the snapshot is read by the browser via `new Date(...)`,
    and JavaScript resolves an offset-less date-time string as LOCAL time. A
    bare `utc_now_iso()` therefore rendered as "6:22 PM GMT+1"
    for an 18:22 UTC harvest, and shifted every age calculation built on it
    (staleness badge, "changed since your last visit") by the reader's UTC
    offset. Carrying the offset removes the ambiguity at the source.
    """
    return datetime.now(timezone.utc).isoformat()

TIMEOUT = int(os.getenv("FETCH_TIMEOUT", 15))
MAX_RETRIES = int(os.getenv("MAX_RETRIES", 3))
STALE_THRESHOLD_HOURS = int(os.getenv("STALE_THRESHOLD", 24))
# Free key from https://fred.stlouisfed.org/docs/api/api_key.html — US CPI/Fed
# funds rate were previously hardcoded static strings; optional, falls back
# to those static values when unset so nothing breaks without it.
FRED_API_KEY = os.getenv("FRED_API_KEY")

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stderr)]
)
logger = logging.getLogger('intel_harvester')

# ============================================================================
# UTILITY CLASSES
# ============================================================================

class HarvestStats:
    """Track errors and warnings during harvest for aggregated reporting"""
    def __init__(self):
        self.errors = []
        self.warnings = []
        self.successes = []
        self.start_time = datetime.now(timezone.utc)

    def record_error(self, source: str, error: str):
        self.errors.append({
            "source": source,
            "error": str(error)[:200],
            "time": utc_now_iso()
        })
        logger.error(f"[{source}] {error}")

    def record_warning(self, source: str, warning: str):
        self.warnings.append({
            "source": source,
            "warning": str(warning)[:200],
            "time": utc_now_iso()
        })
        logger.warning(f"[{source}] {warning}")

    def record_success(self, source: str):
        self.successes.append({
            "source": source,
            "time": utc_now_iso()
        })
        logger.info(f"[{source}] Success")

    def should_alert(self) -> bool:
        """Determine if errors are critical enough to warrant alerting"""
        # ofac_sdn added: a silently-failing sanctions feed is arguably the
        # single highest-stakes gap this pipeline could have. Matched with
        # startswith rather than equality: recorded source names are
        # suffixed per-entity (e.g. "sec_edgar_Philip Morris Int."), so the
        # previous exact-match check against bare "sec_edgar" never fired —
        # SEC EDGAR failures were silently never considered alert-worthy.
        critical_sources = ['cisa_kev', 'sec_edgar', 'ecb_fx', 'ofac_sdn']
        return (
            len(self.errors) >= 3 or
            any(e['source'].startswith(cs) for e in self.errors for cs in critical_sources)
        )

    def summary(self) -> dict:
        return {
            "total_errors": len(self.errors),
            "total_warnings": len(self.warnings),
            "total_successes": len(self.successes),
            "errors": self.errors[-10:],
            "warnings": self.warnings[-5:],
            "duration_seconds": (datetime.now(timezone.utc) - self.start_time).total_seconds()
        }

class RateLimiter:
    """Rate limiter to prevent API throttling"""
    def __init__(self, calls_per_minute: int = 20):
        self.calls_per_minute = calls_per_minute
        self.calls = []

    def wait_if_needed(self):
        now = time.time()
        # Remove calls older than 60 seconds
        self.calls = [t for t in self.calls if now - t < 60]
        if len(self.calls) >= self.calls_per_minute:
            sleep_time = 60 - (now - self.calls[0]) + 0.1
            if sleep_time > 0:
                logger.info(f"Rate limiting: sleeping for {sleep_time:.1f}s")
                time.sleep(sleep_time)
        self.calls.append(time.time())

class CircuitBreaker:
    """Circuit breaker pattern to prevent cascading failures"""
    def __init__(self, failure_threshold: int = 3, reset_timeout: int = 300):
        self.failure_count = 0
        self.failure_threshold = failure_threshold
        self.reset_timeout = reset_timeout
        self.last_failure_time = None
        self.state = "closed"  # closed, open, half-open

    def record_success(self):
        self.failure_count = 0
        self.state = "closed"

    def record_failure(self):
        self.failure_count += 1
        self.last_failure_time = time.time()
        if self.failure_count >= self.failure_threshold:
            self.state = "open"
            logger.warning(f"Circuit breaker OPEN after {self.failure_count} failures")

    def can_execute(self) -> bool:
        if self.state == "closed":
            return True
        if self.state == "open":
            if time.time() - self.last_failure_time > self.reset_timeout:
                self.state = "half-open"
                logger.info("Circuit breaker transitioning to half-open")
                return True
            return False
        return True  # half-open allows one attempt

# Global instances
harvest_stats = HarvestStats()
rate_limiter = RateLimiter(calls_per_minute=20)
yfinance_circuit_breaker = CircuitBreaker(failure_threshold=5, reset_timeout=120)

# ============================================================================
# RETRY AND FETCH UTILITIES
# ============================================================================

def fetch_with_retry(url: str, max_retries: int = None, headers: dict = None,
                     timeout: int = None) -> requests.Response:
    """
    Fetch URL with exponential backoff retry logic.

    Args:
        url: URL to fetch
        max_retries: Maximum number of retry attempts (default from config)
        headers: Optional headers dict
        timeout: Per-request timeout in seconds (default from config). GDELT
            routinely takes 25-40s to answer a tonechart query, so it passes a
            longer one — the shared 15-20s default was cutting off responses
            that were on their way.

    Returns:
        requests.Response object

    Raises:
        requests.RequestException after all retries exhausted
    """
    if max_retries is None:
        max_retries = MAX_RETRIES
    if headers is None:
        headers = USER_AGENT
    if timeout is None:
        timeout = TIMEOUT

    last_exception = None
    for attempt in range(max_retries):
        try:
            response = requests.get(url, headers=headers, timeout=timeout)
            response.raise_for_status()
            return response
        except requests.RequestException as e:
            last_exception = e
            if attempt == max_retries - 1:
                raise
            delay = min(2 ** attempt, 30)  # Cap at 30 seconds
            logger.warning(f"Retry {attempt + 1}/{max_retries} for {url[:50]}... after {delay}s: {e}")
            time.sleep(delay)

    raise last_exception


# An official statistic that stopped updating is worse than no statistic: it
# reads as current on the board. Several OECD-sourced FRED series for China
# are more than a year stale, which is exactly how "China CPI 0.5%" ended up
# rendered as a live indicator. Anything older than this is dropped.
FRED_MAX_OBSERVATION_AGE_DAYS = 150


def fetch_fred_observation(series_id: str, units: str = "lin") -> dict | None:
    """
    Latest observation for a FRED (Federal Reserve Economic Data) series —
    free public API, no cost, one API key. units='pc1' returns year-over-year
    percent change (for CPI, since the raw index isn't directly meaningful);
    'lin' returns the raw series value (for FEDFUNDS, which is already a rate).

    Returns {"value": float, "date": "YYYY-MM-DD"} or None. The date matters:
    CPI and policy rates are monthly, so the board must be able to say "3.3%
    as of Jul 2026" rather than implying it was measured today. Observations
    older than FRED_MAX_OBSERVATION_AGE_DAYS are discarded as stale.

    Best-effort: returns None if FRED_API_KEY isn't set or the fetch/parse
    fails, so the harvest never blocks on it.
    """
    if not FRED_API_KEY:
        return None
    try:
        url = (
            "https://api.stlouisfed.org/fred/series/observations"
            f"?series_id={series_id}&api_key={FRED_API_KEY}&file_type=json"
            f"&sort_order=desc&limit=1&units={units}"
        )
        response = fetch_with_retry(url, max_retries=1)
        observations = response.json().get("observations", [])
        if not observations:
            return None
        value = observations[0].get("value")
        date_str = observations[0].get("date", "")
        if value in (None, ".", ""):
            return None
        if date_str:
            observed = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            age_days = (datetime.now(timezone.utc) - observed).days
            if age_days > FRED_MAX_OBSERVATION_AGE_DAYS:
                logger.warning(
                    f"FRED series {series_id} last observed {date_str} "
                    f"({age_days}d ago) — too stale to publish"
                )
                return None
        return {"value": float(value), "date": date_str}
    except (requests.RequestException, ValueError, KeyError) as e:
        logger.warning(f"FRED fetch failed for {series_id}: {e}")
        return None

def calculate_data_hash(data: dict) -> str:
    """Generate a short hash of the data for version checking"""
    # Exclude fields that change every run by construction, so this hash
    # reflects actual data changes rather than firing every single harvest.
    data_copy = json.loads(json.dumps(data))  # Deep copy
    if 'last_updated' in data_copy:
        del data_copy['last_updated']
    if 'harvest_stats' in data_copy:
        del data_copy['harvest_stats']
    # rag_history always gets a new timestamped entry appended every run
    # (see main()) — leaving it in means version changes on every harvest
    # regardless of whether anything else changed, which defeats the
    # frontend's "New data available" check (useDataFreshness compares
    # version to detect real changes worth refreshing for).
    if 'rag_history' in data_copy:
        del data_copy['rag_history']
    # change_log is append-only and timestamped — same reasoning as
    # rag_history. Anything that appends to it also changed the underlying
    # data that this hash is computed over, so nothing is missed by
    # excluding it.
    if 'change_log' in data_copy:
        del data_copy['change_log']
    # executive_summary is LLM-generated prose that varies slightly between
    # runs even when the underlying data is identical — including it here
    # would flip `version` on every harvest and defeat the frontend's
    # "new data available" staleness check (see useDataFreshness).
    if 'executive_summary' in data_copy:
        del data_copy['executive_summary']
    # geopolitical_intel (GDELT) article counts/tone drift slightly between
    # runs even with nothing meaningfully new — same reasoning as above.
    if 'geopolitical_intel' in data_copy:
        del data_copy['geopolitical_intel']
    # Attempt bookkeeping records a timestamp for every country tried, every
    # run — same reasoning as above.
    if 'geopolitical_attempts' in data_copy:
        del data_copy['geopolitical_attempts']

    json_str = json.dumps(data_copy, sort_keys=True)
    return hashlib.md5(json_str.encode()).hexdigest()[:12]

# ============================================================================
# GEOPOLITICAL RISK MAP - Auto-applied based on supplier location
# ============================================================================
# Lives in data/country_risk.json, not here: which countries carry a standing
# risk floor is a judgement that gets revisited as the world changes, and it
# should not require touching this file. Severity acts as a FLOOR for supplier
# risk — it can only elevate, never reduce — and a floor on its own never
# moves the pillar RAG score (see counts_toward_rag), so a country sitting
# here permanently does not permanently redden the board.
COUNTRY_RISK_FILE = Path(__file__).parent.parent / "data" / "country_risk.json"


def _load_country_risk(path: Path) -> dict:
    with open(path) as f:
        countries = json.load(f).get("countries", {})
    for country, entry in countries.items():
        level = entry.get("level")
        if level not in RISK_PRIORITY:
            raise ValueError(
                f"{path}: {country!r} has level {level!r}; expected one of "
                f"{sorted(RISK_PRIORITY)}"
            )
        if not entry.get("reason"):
            raise ValueError(f"{path}: {country!r} has no reason text")
    return countries

# Risk level numeric priority for comparisons
RISK_PRIORITY = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}

# Bound after RISK_PRIORITY, which the loader validates each level against.
GEOPOLITICAL_RISK_MAP = _load_country_risk(COUNTRY_RISK_FILE)

# ============================================================================
# NEWS RISK CLASSIFICATION HELPERS
# Bare substring keywords are noisy on their own: "war" matches "trade war"
# or "war of words"; "strike" matches "strike a deal"; "ban" matches "seize
# the opportunity". These helpers add three cheap but high-leverage filters:
#   1. Negation-awareness — "avoids bankruptcy" should not read as bankruptcy
#   2. Subject relevance — the entity/country must be named IN the headline,
#      not just matched somewhere in the article body by Google's search
#   3. Recency — Google News RSS sometimes returns older evergreen articles
#      for broad country/keyword queries; stale headlines are discounted
# ============================================================================
NEGATION_MARKERS = [
    "no ", "not ", "denies", "denied", "denying", "rules out", "ruled out",
    "avoids", "avoided", "averts", "averted", "unlikely", "rumors of",
    "rumored", "despite", "no longer", "ends ", "ended ", "lifted",
    "lifts ", "resolved", "settles", "settled", "dismisses", "dismissed",
    "cleared of", "clears ", "false reports", "false claims",
]


# Keywords deliberately matched as a word *prefix* rather than a whole word,
# because the tail varies and all variants mean the same thing:
# "restructur(ing|e|ed)", "escalat(ing|ed|es)". Everything else is matched
# whole-word — see _keyword_pattern.
PREFIX_KEYWORDS = {
    "restructur", "tensions escalat", "de-escalat", "ceasefire collapse",
}


@lru_cache(maxsize=512)
def _keyword_pattern(keyword: str) -> re.Pattern:
    """Compiled whole-word matcher for one risk keyword.

    Substring matching was quietly producing false risk signals on ordinary
    words: "miss" fired on *emissions* (a routine word in tobacco coverage),
    "fine" on *refinery*/*defined*, "down" on *download*/*downtown*, "cut" on
    *haircut*. Each of those pushed a peer to MEDIUM or a supplier into a risk
    bucket off a headline that said nothing of the sort.

    Word boundaries are only applied where they make sense: a trailing \\b is
    skipped for PREFIX_KEYWORDS (so "restructur" still matches
    "restructuring"), and boundaries are dropped entirely on an edge that
    isn't a word character, so multi-word phrases and trailing spaces in the
    keyword lists keep working.
    """
    kw = keyword.strip().lower()
    lead = r'\b' if kw[:1].isalnum() else ''
    trail = '' if keyword in PREFIX_KEYWORDS or not kw[-1:].isalnum() else r'\b'
    return re.compile(lead + re.escape(kw) + trail)


def _is_negated_near(text_lower: str, keyword: str, window: int = 45) -> bool:
    """True if a negation/de-escalation marker appears just before the keyword."""
    match = _keyword_pattern(keyword).search(text_lower)
    if match is None:
        return False
    context = text_lower[max(0, match.start() - window):match.start()]
    return any(marker in context for marker in NEGATION_MARKERS)


def _keyword_hit(text_lower: str, keyword: str) -> bool:
    """A keyword only counts if present as a whole word and not negated nearby."""
    return (
        _keyword_pattern(keyword).search(text_lower) is not None
        and not _is_negated_near(text_lower, keyword)
    )


def _mentions_subject(text_lower: str, subject: str) -> bool:
    """Require the entity/country name to appear as a whole word in the
    headline itself — Google News matches query terms against the full
    article, so a returned headline's title may not actually be about the
    search subject. Whole-word, not substring: a bare substring check let
    a "Chinatown" crime headline count as mentioning "China", and a
    "Prussia" history headline count as mentioning "Russia" — the exact
    same class of false positive fixed elsewhere for supplier names
    (see supplier_search_terms/supplier_terms_hit)."""
    return re.search(r'\b' + re.escape(subject.lower()) + r'\b', text_lower) is not None


def _parse_pub_date(pub_date_str: str):
    """RSS pubDate as a timezone-aware UTC datetime, or None.

    Previously this converted to the runner's local zone and then dropped the
    tzinfo, while its only caller compared the result against a naive
    utcnow(). Mixing the two skewed the freshness window by the runner's UTC
    offset in whichever direction that offset ran — keeping headlines an hour
    past the cutoff on a GMT+1 machine, discarding still-fresh ones on a
    negative offset. A feed entry with no zone at all is read as UTC, which is
    what RSS pubDate means by default.
    """
    try:
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(pub_date_str)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _recent_headlines(headlines: list, max_age_days: int = 5) -> list:
    """Drop stale headlines Google News sometimes returns for broad queries."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    recent = []
    for h in headlines:
        dt = _parse_pub_date(h.get("published", ""))
        if dt is None or dt >= cutoff:
            recent.append(h)
    return recent

# ============================================================================
# GOOGLE NEWS RSS — Broader news source for geopolitical & supplier scanning
# Free, no API key, runs on GitHub Actions at $0
# ============================================================================
GOOGLE_NEWS_RSS_BASE = "https://news.google.com/rss/search"

# Geopolitical keywords for country-level scanning
GEO_SEARCH_KEYWORDS = "war OR conflict OR sanctions OR crisis OR attack OR military OR trade war OR embargo"

# Supply chain keywords for supplier-level scanning
SUPPLY_SEARCH_KEYWORDS = "supply chain OR disruption OR shutdown OR bankruptcy OR recall OR strike OR cyber attack"


def fetch_google_news_rss(query, max_results=5):
    """
    Fetch headlines from Google News RSS for a given search query.
    Free, no API key required. Returns list of headline strings.
    """
    import urllib.parse
    try:
        encoded_query = urllib.parse.quote(query)
        url = f"{GOOGLE_NEWS_RSS_BASE}?q={encoded_query}&hl=en&gl=US&ceid=US:en"

        rate_limiter.wait_if_needed()
        response = requests.get(url, headers=USER_AGENT, timeout=TIMEOUT)
        response.raise_for_status()

        # Parse RSS XML
        root = ET.fromstring(response.content)
        headlines = []
        for item in root.findall('.//item')[:max_results]:
            title = item.find('title')
            pub_date = item.find('pubDate')
            if title is not None and title.text:
                headline_data = {
                    "title": title.text.strip(),
                    "published": pub_date.text.strip() if pub_date is not None and pub_date.text else ""
                }
                headlines.append(headline_data)

        return headlines

    except Exception as e:
        logger.debug(f"Google News RSS fetch failed for query '{query[:50]}...': {e}")
        return []


# Direct-target phrases only. A bare "war" matches "trade war", "war of
# words", or any headline about the Ukraine war that merely name-drops a
# NATO neighbor — none of which mean that country itself is under threat.
# These require an actual verb/action aimed at the country.
GEO_CRITICAL_KW = [
    "invades", "invasion of", "declares war on", "declared war on",
    "war breaks out in", "bombing of", "bombs ", "missile strike on",
    "airstrike on", "air strikes on", "under siege", "blockade of",
    "military offensive against", "troops enter", "annexes", "annexation of",
]
GEO_HIGH_KW = [
    "military buildup", "mobilizes troops", "mobilises troops",
    "tensions escalat", "border clash", "imposes sanctions on",
    "new sanctions on", "nuclear threat", "proxy war in",
    "ceasefire collapse", "trade ban on", "export ban on", "embargo on",
]
GEO_MEDIUM_KW = [
    "crisis in", "instability in", "unrest in", "protests in", "trade war",
    "tariffs on", "diplomatic row", "territorial dispute", "border tension",
]
GEO_DEESCALATION_KW = [
    "ceasefire agreed", "peace talks", "peace deal", "sanctions lifted",
    "sanctions eased", "de-escalat", "withdraws troops", "troops withdraw",
    "agreement reached",
]


def scan_country_geopolitical_news(country):
    """
    Scan Google News for geopolitical risk signals directly affecting a
    specific country. Three guardrails against false positives:
      - the country must actually be named in the headline (not just
        matched by Google against the article body)
      - de-escalation headlines (ceasefires, peace talks) are ignored
      - CRITICAL requires 2+ independent corroborating headlines; a single
        sensational headline is downgraded to HIGH instead
    Returns (risk_detected: bool, risk_level: str, headlines: list, reason: str)
    """
    query = f'"{country}" ({GEO_SEARCH_KEYWORDS})'
    headlines = _recent_headlines(fetch_google_news_rss(query, max_results=8))

    if not headlines:
        return False, "LOW", [], ""

    critical_hits, high_hits, medium_hits = [], [], []

    for h in headlines:
        title_lower = h["title"].lower()

        if not _mentions_subject(title_lower, country):
            continue
        if any(kw in title_lower for kw in GEO_DEESCALATION_KW):
            continue

        hit = next((kw for kw in GEO_CRITICAL_KW if _keyword_hit(title_lower, kw)), None)
        if hit:
            critical_hits.append((h["title"], hit))
            continue

        hit = next((kw for kw in GEO_HIGH_KW if _keyword_hit(title_lower, kw)), None)
        if hit:
            high_hits.append((h["title"], hit))
            continue

        hit = next((kw for kw in GEO_MEDIUM_KW if _keyword_hit(title_lower, kw)), None)
        if hit:
            medium_hits.append((h["title"], hit))

    if len(critical_hits) >= 2:
        max_level = "CRITICAL"
        reason = f"Critical geopolitical event: '{critical_hits[0][1]}' corroborated by {len(critical_hits)} headlines"
    elif critical_hits:
        max_level = "HIGH"
        reason = f"Geopolitical event (single-source, downgraded pending corroboration): '{critical_hits[0][1]}' in recent news"
    elif high_hits:
        max_level = "HIGH"
        reason = f"High geopolitical risk: '{high_hits[0][1]}' in recent news"
    elif medium_hits:
        max_level = "MEDIUM"
        reason = f"Elevated geopolitical risk: '{medium_hits[0][1]}' in recent news"
    else:
        max_level = "LOW"
        reason = ""

    risk_detected = max_level != "LOW"
    return risk_detected, max_level, headlines, reason


# Supply-chain vocabulary shared across every country, used as the weakest
# relevance tier when ranking GDELT headlines (see fetch_gdelt_country_intel).
# Country news can matter to a CPO without naming one of the 24 suppliers:
# a port closure, an export control, a tariff round or a factory fire is
# actionable context; a local crime story with the same negative tone is not.
# Matched whole-word, never as substrings: bare "port" inside "importing"
# promoted a drone-smuggling bust to supply-chain news, the same class of
# false positive already fixed for CISA and recall screening (see
# supplier_terms_hit). Plurals are listed explicitly for the same reason —
# a word boundary after "chip" will not match "chips".
GDELT_SUPPLY_KEYWORDS = [
    "supply chain", "supply chains", "export control", "export controls",
    "export ban", "import ban", "tariff", "tariffs", "sanction", "sanctions",
    "embargo", "embargoes", "port", "ports", "shipping", "freight",
    "container", "containers", "factory", "factories", "plant closure",
    "production halt", "shortage", "shortages", "customs", "trade war",
    "trade deal", "manufacturing", "logistics", "raw material",
    "raw materials", "commodity", "commodities", "chip", "chips",
    "semiconductor", "semiconductors",
    # Labour action only. Bare "strike" reads a missile strike as a factory
    # walkout — "strike on mall kills 16" scored as supply-chain relevant.
    "labor strike", "labour strike", "workers strike", "dockworkers",
    "walkout", "walkouts",
]


# How each country is asked for. The default is mention-based: articles that
# name the country. Two things made that impossible for the USA, which is the
# joint-largest supplier country on the watchlist and had never once returned
# a reading:
#
#   1. GDELT rejects a quoted phrase shorter than four characters outright.
#      `"USA"` answers HTTP 200 with the plain-text line "The specified phrase
#      is too short" — never JSON — so the attempt log recorded a generic
#      "error" and the rotation kept retrying a query that could not succeed.
#   2. `"United States"` is a valid phrase and still doesn't come back: the
#      matching corpus is large enough that the tonechart request times out,
#      verified at 60s against a control query for Germany that answers in
#      under two.
#
# So the USA is read from the tone of US-published coverage instead
# (`sourcecountry:US`), which does answer. That is a different measurement —
# how the country's own press is writing, rather than how the world writes
# about it — so the mode travels with the reading and the page labels it.
GDELT_COUNTRY_QUERY = {
    "USA": {"query": "sourcecountry:US", "mode": "domestic_press"},
}


def gdelt_query_spec(country: str) -> tuple:
    """(query fragment, measurement mode) for one country."""
    spec = GDELT_COUNTRY_QUERY.get(country)
    if spec:
        return spec["query"], spec["mode"]
    return f'"{country}"', "mentions"

# GDELT answers some rejections with HTTP 200 and a sentence of plain text
# rather than an error status, so the body has to be read to tell what
# happened. Mapped to short statuses the dashboard can explain to a reader.
GDELT_TEXT_RESPONSES = (
    ("too short", "query_rejected"),
    ("limit requests", "rate_limited"),
    ("timed out", "gdelt_timeout"),
)

# GDELT tonechart queries routinely take 25-40s. The shared 15-20s fetch
# timeout was cutting off answers that were on their way, which is most of what
# the attempt log recorded as "timeout".
GDELT_REQUEST_TIMEOUT = 40


def _gdelt_text_status(body: str) -> str | None:
    """Classify a plain-text GDELT response, or None if it isn't one."""
    lowered = body[:300].lower()
    for marker, status in GDELT_TEXT_RESPONSES:
        if marker in lowered:
            return status
    return None


def fetch_gdelt_country_intel(country: str, relevant_suppliers: list = None, max_attempts: int = 2) -> dict | None:
    """
    Independent geopolitical signal from GDELT (free, no key, globally
    aggregated news monitoring across 65+ languages, updated every 15 min —
    structurally separate from the Google News RSS feed
    scan_country_geopolitical_news() uses above). Experimental: feeds only
    the standalone /geopolitical page for now, not the RAG pipeline.

    Returns (intel_dict, "ok") or (None, status) where status names why it
    failed — best-effort, never blocks the harvest.

    relevant_suppliers ({"name","category"} dicts, for suppliers located
    in this country) ranks the headlines GDELT returns: a country-level
    news search has no idea what a CPO actually cares about, so a plain
    "most negative" list is dominated by generic country news (crime,
    weather, unrelated wire stories) that happens to score negative —
    verified against a live response for Germany/China/South Korea,
    where none of the top-5-by-tone headlines mentioned the actual
    suppliers or their industries. Ranking supplier-name or
    category-keyword matches first (still tone-sorted within each tier)
    surfaces the headlines a reader can actually act on. has_relevant
    tells the frontend whether any match was found at all, so a country
    with zero relevant hits can say so honestly instead of silently
    passing off generic news as supplier-relevant.

    Uses mode=tonechart rather than mode=artlist: GDELT's artlist output
    carries no per-article tone field at all (verified against a live
    response — only url/title/seendate/domain/sourcecountry). tonechart
    returns a histogram of {bin, count, toparts: [{url, title}, ...]} —
    each bucket's example articles inherit that bucket's tone score, which
    is the only way this API actually exposes a tone-attributed article.

    max_attempts controls how many times a 429 is retried before giving
    up — see fetch_gdelt_intel for why this stays low (1-2) even for the
    countries that matter most: a live prod run showed that once GDELT
    starts 429ing a GitHub Actions runner IP, every subsequent request
    fails too, including ones with a 12-24s backoff — this reads as an
    IP-level block for the run's duration, not a short per-request
    limit, so retrying doesn't recover it and only burns time (and once
    cost the entire harvest job its timeout — see fetch_gdelt_intel).
    """
    try:
        query_fragment, query_mode = gdelt_query_spec(country)
        query = requests.utils.quote(f'{query_fragment} sourcelang:english')
        url = (
            "https://api.gdeltproject.org/api/v2/doc/doc"
            f"?query={query}&mode=tonechart&timespan=3d&format=json"
        )
        response = None
        for attempt in range(max_attempts):
            try:
                response = fetch_with_retry(url, max_retries=1, timeout=GDELT_REQUEST_TIMEOUT)
                break
            except requests.HTTPError as e:
                is_last = attempt == max_attempts - 1
                if e.response is not None and e.response.status_code == 429 and not is_last:
                    time.sleep(5)
                    continue
                raise

        # A rejection can arrive as HTTP 200 with one sentence of plain text,
        # in which case .json() raises and the real reason is lost. Read it.
        text_status = _gdelt_text_status(response.text)
        if text_status:
            logger.warning(f"GDELT rejected the {country} query ({text_status}): {response.text[:120].strip()}")
            return None, text_status

        bins = response.json().get("tonechart", [])
        if not bins:
            return None, "empty"


        total_count = sum(b.get("count", 0) for b in bins)
        weighted_tone_sum = sum(b.get("bin", 0) * b.get("count", 0) for b in bins)

        # Relevance terms, strongest first: a supplier name (a headline
        # naming "Infineon" is unambiguous), then that supplier's category
        # vocabulary, then general supply-chain vocabulary. The third tier
        # exists because plenty of country news is squarely a procurement
        # concern without naming any supplier — "China tightens export
        # controls on rare earths" is exactly what this page is for, and
        # the previous two tiers alone dropped it on the floor.
        name_terms = [
            term.lower()
            for s in (relevant_suppliers or []) if s.get("name")
            for term in supplier_search_terms(s["name"])
        ]
        keyword_terms = [
            kw for s in (relevant_suppliers or [])
            for kw in CATEGORY_KEYWORDS.get(s.get("category"), [])
        ]

        def relevance(title: str) -> int:
            t = title.lower()
            if any(_mentions_subject(t, term) for term in name_terms):
                return 3
            if any(_mentions_subject(t, term) for term in keyword_terms):
                return 2
            if any(_mentions_subject(t, term) for term in GDELT_SUPPLY_KEYWORDS):
                return 1
            return 0

        # Pull a wider candidate pool than we'll show (every toparts
        # example across every bin, most negative first) so there's
        # something to rank by relevance instead of just taking whatever
        # the first 5 tone-sorted slots happen to be.
        # GDELT indexes the same wire story from multiple syndicating
        # domains as separate toparts entries — dedupe by title (verified
        # against a live response: the identical "Lahav 433..." headline
        # from two outlets, back to back) or the list reads as padded.
        candidates = []
        seen_titles = set()
        for b in sorted(bins, key=lambda b: b.get("bin", 0)):
            for a in b.get("toparts", []):
                title = a.get("title", "")
                if title in seen_titles:
                    continue
                seen_titles.add(title)
                candidates.append({
                    "title": title,
                    "url": a.get("url", ""),
                    "tone": b.get("bin"),
                    "_relevance": relevance(title),
                })

        # Only relevant headlines ship. Previously the top five by tone went
        # out whatever they were about, which filled a supply-chain page with
        # a salmonella outbreak under Germany and a cancer-survivor study
        # under China — country news that scores negative and means nothing
        # here. The tone average and article volume above are the honest
        # country-level signal; padding it out with unrelated headlines only
        # taught the reader that the page is noise. Where nothing relevant
        # surfaced, the country now says so and shows no headlines at all.
        relevant = [a for a in candidates if a["_relevance"] > 0]
        relevant.sort(key=lambda a: (-a["_relevance"], a["tone"]))
        has_relevant = bool(relevant)
        articles_out = [
            {"title": a["title"], "url": a["url"], "tone": a["tone"]}
            for a in relevant[:5]
        ]

        return {
            "article_count": total_count,
            "avg_tone": round(weighted_tone_sum / total_count, 1) if total_count else None,
            "articles": articles_out,
            "has_relevant": has_relevant,
            # "mentions" = coverage naming this country; "domestic_press" =
            # coverage published in it. Carried so the page can say which,
            # rather than presenting two different measurements as one.
            "query_mode": query_mode,
            "fetched_at": utc_now_iso(),
        }, "ok"
    except Exception as e:
        # The reason is recorded per country and carried in the snapshot, not
        # just logged: eight of twelve countries had never once returned data
        # and the logs from the runs that failed them were long gone, so there
        # was no way to tell a rate limit from a timeout from an empty result.
        status = "error"
        if isinstance(e, requests.HTTPError) and e.response is not None:
            status = f"http_{e.response.status_code}"
        elif isinstance(e, requests.Timeout):
            status = "timeout"
        elif isinstance(e, requests.ConnectionError):
            status = "unreachable"
        logger.warning(f"GDELT fetch failed for {country} ({status}): {e}")
        return None, status


GDELT_TIME_BUDGET_SEC = 210  # hard cap on the whole phase — see fetch_gdelt_intel
GDELT_CIRCUIT_BREAKER_FAILS = 3  # consecutive failures before bailing out early


def order_gdelt_countries(countries: list, attempts: dict, supplier_counts: dict = None) -> list:
    """Rotate which countries get the front of the budget.

    The order used to be a fixed priority pair followed by the rest
    alphabetically, and the circuit breaker abandons the run after three
    consecutive failures. Those two together meant the same tail of the list
    was never reached: across 24 harvests, every country from position six
    onwards — India, Japan, Netherlands, South Africa, South Korea, Sweden,
    Switzerland — had literally never been attempted to a successful result,
    while positions two to five accounted for every success on record. Merging
    results across runs (see fetch_gdelt_intel) cannot accumulate coverage if
    the same countries are the ones always cut off.

    Ordering by staleness fixes that: whoever has gone longest without a
    successful reading goes first, so each cycle attacks a different part of
    the list and coverage fills in over a day or so. Countries that keep
    failing are demoted rather than dropped — a chronic failure would
    otherwise sit at the front forever (never fetched = maximally stale) and
    reintroduce exactly the blockage this replaces — but they still get
    attempted whenever the budget reaches them.
    """
    counts = supplier_counts or {}

    def sort_key(country):
        state = attempts.get(country, {})
        # Bucketed so a country is not shuffled to the back on one bad run;
        # 3+ consecutive failures is a pattern, 1-2 is noise.
        streak = min(state.get("consecutive_failures", 0) // 3, 3)
        last_success = state.get("last_success") or ""
        # Then by how much of the watchlist sits there. The circuit breaker
        # abandons the run after three consecutive failures, so position in
        # this queue decides who actually gets attempted on a bad day — and
        # spending that on Switzerland (one supplier) ahead of the USA (five)
        # is the wrong trade. Countries that have never returned data all tie
        # on last_success, and this is what separates them.
        weight = -counts.get(country, 0)
        # Tie-break on when it was last *tried*. Without this, every country
        # that has never succeeded ties on last_success and falls through to
        # the alphabetical tie-break, so the same handful at the top of the
        # alphabet get retried every cycle until their failure streak finally
        # demotes them — the original blockage, one level down. Never
        # attempted sorts ahead of recently attempted.
        last_attempt = state.get("last_attempt") or ""
        return (streak, last_success, weight, last_attempt, country)

    return sorted(countries, key=sort_key)


def fetch_gdelt_intel(countries: list, attempts: dict = None, suppliers_by_country: dict = None) -> tuple:
    """
    Best-effort GDELT scan across watchlist countries — see
    fetch_gdelt_country_intel.

    A live prod run proved retries are not a safe lever here: a flat 5s
    gap 429'd ~2/3 of countries; a flat 10s gap covered *fewer* (3/12 vs
    4/12); and a 3-attempt/growing-backoff retry for China/USA specifically
    still failed both, then every single country after them also 429'd
    with zero successes — consistent with GDELT blocking the runner IP
    for the rest of the run, not a short per-request limit that patience
    fixes. That attempt made things categorically worse: the phase ran
    long enough to hit the workflow's 10-minute job timeout, and GitHub
    cancelled the run — which lost the *entire* harvest for that cycle
    (macro/peers/suppliers/executive_summary too), not just GDELT.

    So this is now bounded by two hard safety nets instead of leaning on
    retries: a wall-clock budget for the whole phase (GDELT_TIME_BUDGET_SEC)
    that abandons any remaining countries once hit, and a circuit breaker
    that stops after a run of consecutive failures (GDELT_CIRCUIT_BREAKER_FAILS)
    without waiting out the rest of the budget — once the IP looks blocked,
    burning the remaining budget on countries that will also fail wastes
    time this experimental feature is not entitled to at the harvest's
    expense. Whatever countries were reached before the cutoff is what
    ships; the page already handles partial/empty data gracefully.
    """
    start = time.monotonic()
    result = {}
    attempts = dict(attempts or {})
    consecutive_fails = 0
    countries = order_gdelt_countries(
        countries, attempts,
        supplier_counts={c: len(v) for c, v in (suppliers_by_country or {}).items()},
    )
    logger.info(f"GDELT order this cycle: {', '.join(countries)}")
    for i, country in enumerate(countries):
        elapsed = time.monotonic() - start
        if elapsed > GDELT_TIME_BUDGET_SEC:
            logger.warning(
                f"GDELT time budget ({GDELT_TIME_BUDGET_SEC}s) exhausted after "
                f"{i}/{len(countries)} countries — abandoning the rest this cycle."
            )
            break
        if consecutive_fails >= GDELT_CIRCUIT_BREAKER_FAILS:
            logger.warning(
                f"GDELT circuit breaker tripped ({consecutive_fails} consecutive "
                f"failures) — abandoning the remaining {len(countries) - i} countries."
            )
            break
        if i > 0:
            time.sleep(random.uniform(5, 7))
        # Two attempts for whoever leads this cycle — the front of the queue is
        # the stalest country, which is the one worth spending a retry on.
        max_attempts = 2 if i == 0 else 1
        relevant_suppliers = (suppliers_by_country or {}).get(country, [])
        intel, status = fetch_gdelt_country_intel(
            country, relevant_suppliers=relevant_suppliers, max_attempts=max_attempts
        )
        state = dict(attempts.get(country, {}))
        state["last_attempt"] = utc_now_iso()
        state["last_status"] = status
        if intel:
            result[country] = intel
            state["last_success"] = intel["fetched_at"]
            state["consecutive_failures"] = 0
            consecutive_fails = 0
        else:
            state["consecutive_failures"] = state.get("consecutive_failures", 0) + 1
            consecutive_fails += 1
        attempts[country] = state
    logger.info(f"GDELT scan complete. {len(result)}/{len(countries)} countries returned data.")
    return result, attempts


def scan_supplier_news_google(supplier_name, country):
    """
    Scan Google News for supply chain risk signals for a specific supplier.
    Used for suppliers WITHOUT a stock ticker (no yfinance news). Requires
    the supplier's name to actually appear in the headline and ignores
    negated hits ("avoids bankruptcy", "denies fraud").
    Returns (headlines: list, risk_level: str, risk_reason: str)
    """
    query = f'"{supplier_name}" ({SUPPLY_SEARCH_KEYWORDS})'
    headlines = _recent_headlines(fetch_google_news_rss(query, max_results=5))

    if not headlines:
        return [], "LOW", ""

    # Re-use the same keyword sets from the main supply chain scanner
    CRITICAL_KW = ["bankruptcy", "bankrupt", "insolvent", "liquidation",
                   "factory fire", "plant fire", "explosion", "facility closure",
                   "sanction", "ransomware", "cyber attack", "operations halted",
                   "labor strike", "workers strike", "walkout"]
    HIGH_KW = ["fraud investigation", "sec investigation", "major recall",
               "product recall", "ceo fired", "ceo resign"]
    MEDIUM_KW = ["mass layoff", "supply shortage", "supply disruption",
                 "production delay", "restructuring", "credit downgrade"]

    max_level = "LOW"
    reason = ""

    for h in headlines:
        title_lower = h["title"].lower()
        if not _mentions_subject(title_lower, supplier_name):
            continue

        for kw in CRITICAL_KW:
            if _keyword_hit(title_lower, kw):
                max_level = "CRITICAL"
                reason = f"Critical supply risk from news: '{kw}'"
                break
        if max_level == "CRITICAL":
            break

        for kw in HIGH_KW:
            if _keyword_hit(title_lower, kw):
                if RISK_PRIORITY.get("HIGH", 2) > RISK_PRIORITY.get(max_level, 0):
                    max_level = "HIGH"
                    reason = reason or f"High supply risk from news: '{kw}'"
                break

        for kw in MEDIUM_KW:
            if _keyword_hit(title_lower, kw):
                if RISK_PRIORITY.get("MEDIUM", 1) > RISK_PRIORITY.get(max_level, 0):
                    max_level = "MEDIUM"
                    reason = reason or f"Supply concern from news: '{kw}'"
                break

    return [h["title"] for h in headlines], max_level, reason


# Category -> industry keywords, used only to rank news and GDELT headlines
# by relevance — never in the deterministic RAG scoring. Loaded from
# data/suppliers.json alongside category_segments (see _load_watchlist): both
# are per-category facts maintained by hand, and splitting them across a JSON
# file and this module meant adding a category needed edits in two places.

# SUPPLIER WATCHLIST - Pillar 3
# The watchlist itself lives in data/suppliers.json, not here: adding,
# removing or re-tiering a supplier is a procurement decision, and it used to
# require editing four parallel dicts in this file (name->exposure,
# name->location, name->ticker, category->segment) with nothing keeping them
# in step. Loaded once at import; a missing or malformed file raises rather
# than degrading to an empty watchlist, because a harvest that silently
# reports zero suppliers would read as "nothing to worry about".
WATCHLIST_FILE = Path(__file__).parent.parent / "data" / "suppliers.json"


def _load_watchlist(path: Path) -> tuple:
    with open(path) as f:
        raw = json.load(f)

    entries = raw.get("suppliers")
    if not entries:
        raise ValueError(f"{path} contains no suppliers")

    segments = raw.get("category_segments", {})
    keywords = raw.get("category_keywords", {})
    watchlist, profiles = [], {}
    for entry in entries:
        name, category = entry.get("name"), entry.get("category")
        if not name or not category:
            raise ValueError(f"{path}: every supplier needs a name and a category, got {entry!r}")
        if name in profiles:
            raise ValueError(f"{path}: duplicate supplier {name!r}")
        # An unrecognised category silently defaults segment to "Combustibles"
        # and contributes no keywords to news/GDELT relevance ranking — a
        # supplier that looks fully configured while being scanned with the
        # wrong vocabulary. Warn rather than raise: the harvest still works,
        # and a typo should not take the whole board down.
        if category not in segments:
            logger.warning(
                f"{path.name}: {name!r} has category {category!r}, which has no "
                f"category_segments entry — segment defaults to Combustibles"
            )
        if category not in keywords:
            logger.warning(
                f"{path.name}: category {category!r} has no category_keywords entry — "
                f"{name!r} gets no industry keywords for news relevance ranking"
            )
        watchlist.append({"name": name, "category": category})
        profiles[name] = {
            "bat_exposure": entry.get("bat_exposure", "Medium"),
            # Segment follows from the category, so it is declared once per
            # category rather than repeated (and eventually contradicted) on
            # every supplier row.
            "segment": entry.get("segment") or segments.get(category, "Combustibles"),
            # The country of the site that supplies BAT — a strike or a border
            # closure hits the plant, not the registered office. Where the legal
            # headquarters sits elsewhere, hq_country keeps that fact visible
            # rather than forcing one field to mean both.
            "location": entry.get("location", "Unknown"),
            "hq_country": entry.get("hq_country"),
            "stock_ticker": entry.get("stock_ticker", "N/A"),
            "url": entry.get("url"),
        }
    return watchlist, profiles, keywords


WATCHLIST_DATA, SUPPLIER_PROFILES, CATEGORY_KEYWORDS = _load_watchlist(WATCHLIST_FILE)

# PEERS & COMPETITORS - Pillar 2 (Hardcoded Source of Truth)
# match_terms gate which fetched headlines may be attributed to a peer.
# yfinance's ticker.news returns sector-adjacent coverage, not only articles
# about the ticker, so `news[0]` is frequently about someone else entirely —
# an Altria story surfaced under Japan Tobacco, and one shared "AIR Global"
# piece printed as both BAT's and Imperial's latest headline. Whole-word
# matched (see _mentions_subject), so short/ambiguous forms are deliberately
# left out: bare "PM" matches a clock time, bare "BAT" matches the animal.
PEERS_CONFIG = [
    {
        "name": "British American Tobacco",
        "ticker": "BTI",  # Tracking the NYSE ADR for US News visibility
        "region": "Global/US ADR",
        "match_terms": ["british american tobacco", "bat plc", "bti"],
        "default_text": "Primary listing LSE; traded as BTI (NYSE). Monitoring filings."
    },
    {
        "name": "Philip Morris Int.",
        "ticker": "PM",
        "region": "US",
        "match_terms": ["philip morris", "pmi"],
        "default_text": "US-listed (NYSE). Monitoring SEC filings (8-K)."
    },
    {
        "name": "Imperial Brands",
        "ticker": "IMB.L",
        "region": "UK",
        "match_terms": ["imperial brands", "imperial tobacco"],
        "default_text": "UK-listed (LSE). Monitoring regulatory news."
    },
    {
        "name": "Japan Tobacco",
        "ticker": "2914.T",
        "region": "Japan",
        "match_terms": ["japan tobacco", "jt group", "jti"],
        "default_text": "Tokyo listed. Monitoring global press releases."
    }
]

# ============================================================================
# SUPPLIER NAME ALIASES — shared across CISA, CPSC recall, and sanctions
# matching so a supplier registered under a trading name or subsidiary
# still gets flagged (e.g. "Huizhou BYD Electronic" -> "BYD").
# ============================================================================
SUPPLIER_ALIASES = {
    "AMCOR": ["AMCOR", "AMCR"],
    "Jabil": ["JABIL", "JABIL INC"],
    "Texas Instruments": ["TEXAS INSTRUMENTS", "TI"],
    "Infineon": ["INFINEON", "INFINEON TECHNOLOGIES"],
    "Eastman": ["EASTMAN", "EASTMAN CHEMICAL"],
    "Stora Enso": ["STORA ENSO", "STORAENSO"],
    "Smoore": ["SMOORE", "SMOORE INTERNATIONAL"],
    "EVE Energy": ["EVE ENERGY", "EVE"],
    "Huizhou BYD Electronic": ["BYD", "BYD ELECTRONIC", "HUIZHOU BYD"],
    "SWM (Mativ)": ["MATIV", "SWM", "SCHWEITZER-MAUDUIT"],
    "ITC": ["ITC LIMITED", "ITC LTD"],
    "Sappi": ["SAPPI", "SAPPI LIMITED"],
    "GPI": ["GRAPHIC PACKAGING", "GPI", "GRAPHIC PACKAGING INTERNATIONAL"],
    "Daicel": ["DAICEL", "DAICEL CORPORATION"],
}


# Bare names that collide with a much more prominent, unrelated company of
# the exact same name — e.g. "Eastman" alone is at least as likely to mean
# Eastman Kodak (which has its own well-documented ransomware history) as
# it is to mean our actual supplier, Eastman Chemical. "Fuji" has no safer
# alternative on file (no fuller legal name is recorded for this supplier),
# so it's kept as a last resort below, but it's listed here so this
# ambiguity is documented rather than silently assumed to be safe.
AMBIGUOUS_BARE_NAMES = {"EASTMAN", "FUJI"}


def supplier_search_terms(supplier_name: str, min_len: int = 4) -> list:
    """
    Uppercase supplier name plus any known aliases, for whole-word matching
    against vendor/manufacturer/party name fields in external datasets.

    Two safety filters, applied with fallbacks so a supplier never ends up
    with zero coverage just because its only name on file is short/risky:
      1. Terms under min_len characters are dropped when a longer, safer
         term is available. Short aliases like "TI" (Texas Instruments) or
         "EVE" (EVE Energy) are common English letter sequences that turn
         up inside completely unrelated words (e.g. "TI" inside
         "authoriza-TI-on" falsely flagged a Langflow CVE as a Texas
         Instruments vulnerability). If dropping short terms would leave
         nothing (e.g. "CNT" has no longer alias on file), they're kept
         rather than leaving that supplier with no screening at all —
         whole-word matching (see supplier_terms_hit) still applies.
      2. AMBIGUOUS_BARE_NAMES are dropped whenever a safer alternative
         term exists for that supplier, same fallback logic.
    """
    all_terms = [supplier_name.upper()]
    for alias in SUPPLIER_ALIASES.get(supplier_name, []):
        au = alias.upper()
        if au not in all_terms:
            all_terms.append(au)

    safe_terms = [t for t in all_terms if t not in AMBIGUOUS_BARE_NAMES]
    filtered = [t for t in safe_terms if len(t) >= min_len]
    if filtered:
        return filtered
    if safe_terms:
        return safe_terms
    return all_terms


def supplier_terms_hit(text_upper: str, search_terms: list) -> bool:
    """Whole-word match: True if any term appears as a standalone word in
    text_upper (not merely as a substring inside a longer word)."""
    return any(re.search(r'\b' + re.escape(term) + r'\b', text_upper) for term in search_terms)


def fetch_cisa_kev():
    """Fetch CISA Known Exploited Vulnerabilities Catalog with retry logic"""
    source_name = "cisa_kev"
    try:
        url = "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json"
        response = fetch_with_retry(url)
        data = response.json()

        # Filter for vulnerabilities added in last 7 days
        seven_days_ago = datetime.now(timezone.utc) - timedelta(days=7)
        recent_vulns = []
        critical_vulns = []

        for vuln in data.get('vulnerabilities', []):
            date_added_str = vuln.get('dateAdded', '')
            try:
                date_added = datetime.strptime(date_added_str, '%Y-%m-%d').replace(tzinfo=timezone.utc)
                if date_added >= seven_days_ago:
                    recent_vulns.append(vuln)
                    # Check for ransomware and recent (48h)
                    if vuln.get('knownRansomwareCampaignUse', '') == 'true':
                        two_days_ago = datetime.now(timezone.utc) - timedelta(days=2)
                        if date_added >= two_days_ago:
                            critical_vulns.append(vuln)
            except ValueError:
                continue

        harvest_stats.record_success(source_name)
        return {
            "status": "success",
            "total_vulnerabilities": len(data.get('vulnerabilities', [])),
            "recent_count": len(recent_vulns),
            "critical_count": len(critical_vulns),
            "recent_vulnerabilities": recent_vulns[:10],  # Limit for size
            "last_fetched": utc_now_iso()
        }
    except Exception as e:
        harvest_stats.record_error(source_name, str(e))
        return {
            "status": "error",
            "error": str(e),
            "recent_vulnerabilities": [],
            "last_fetched": utc_now_iso()
        }


def fetch_cpsc_recalls():
    """
    Fetch recent CPSC (Consumer Product Safety Commission) recalls via the
    free, no-auth saferproducts.gov REST API. Looks back 90 days — recalls
    are relatively rare events and a supplier match is a real signal, so a
    wider window than the 7-day CISA lookback is reasonable here.
    Degrades gracefully: any failure returns status "error" with an empty
    recall list rather than raising, so one flaky feed doesn't take down
    the rest of the harvest.
    """
    source_name = "cpsc_recalls"
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=90)).strftime('%Y-%m-%d')
        url = f"https://www.saferproducts.gov/RestWebServices/Recall?RecallDateStart={cutoff}&format=json"
        response = fetch_with_retry(url)
        data = response.json()
        recalls = data if isinstance(data, list) else []

        harvest_stats.record_success(source_name)
        return {
            "status": "success",
            "total_recalls": len(recalls),
            "recalls": recalls,
            "last_fetched": utc_now_iso()
        }
    except Exception as e:
        harvest_stats.record_error(source_name, str(e))
        return {
            "status": "error",
            "error": str(e),
            "total_recalls": 0,
            "recalls": [],
            "last_fetched": utc_now_iso()
        }


def match_supplier_recalls(supplier_name: str, recalls: list) -> list:
    """Match a supplier's name/aliases against CPSC recall manufacturer names
    (whole-word, via supplier_search_terms — see its docstring for why).
    Returns matching recall summaries (empty list if none)."""
    search_terms = supplier_search_terms(supplier_name)
    if not search_terms:
        return []
    matches = []
    for recall in recalls:
        manufacturers = recall.get("Manufacturers", []) or []
        names = " | ".join(m.get("Name", "") for m in manufacturers if isinstance(m, dict)).upper()
        if supplier_terms_hit(names, search_terms):
            products = recall.get("Products", []) or []
            product_name = products[0].get("Name") if products and isinstance(products[0], dict) else "product"
            matches.append({
                "recallNumber": recall.get("RecallNumber", ""),
                "recallDate": recall.get("RecallDate", ""),
                "description": (recall.get("Description") or "")[:200],
                "product": product_name,
                "url": recall.get("URL", ""),
            })
    return matches


def fetch_ofac_sdn():
    """
    Fetch the OFAC Specially Designated Nationals (SDN) list — the primary
    US sanctions screening list — via Treasury's free, no-auth bulk CSV.
    (The README's originally-planned "ITA Consolidated Screening List" API
    requires a registered API key; OFAC's SDN bulk file needs none, keeping
    this pipeline's zero-credentials, zero-cost design intact.)
    Tries the current sanctionslistservice.ofac.treas.gov endpoint first,
    falling back to the legacy treasury.gov mirror. Degrades gracefully
    like the other fetchers: failure returns status "error" with an empty
    name list rather than raising.
    """
    source_name = "ofac_sdn"
    urls = [
        "https://sanctionslistservice.ofac.treas.gov/api/download/sdn.csv",
        "https://www.treasury.gov/ofac/downloads/sdn.csv",
    ]
    last_error = None
    for url in urls:
        try:
            response = fetch_with_retry(url)
            reader = csv.reader(io.StringIO(response.text))
            names = [row[1].strip() for row in reader if len(row) > 1 and row[1] and row[1] != '-0-']
            if not names:
                raise ValueError("SDN list fetched but parsed to zero names")

            harvest_stats.record_success(source_name)
            return {
                "status": "success",
                "total_entries": len(names),
                "names": names,
                "last_fetched": utc_now_iso()
            }
        except Exception as e:
            last_error = e
            continue

    harvest_stats.record_error(source_name, str(last_error))
    return {
        "status": "error",
        "error": str(last_error),
        "total_entries": 0,
        "names": [],
        "last_fetched": utc_now_iso()
    }


def match_supplier_sanctions(supplier_name: str, sdn_names: list) -> list:
    """
    Screen a supplier's registered name against the OFAC SDN list using
    whole-word matching. Deliberately does NOT use the CISA/recall alias
    table or substring matching here: a sanctions flag is the single most
    severe (and most reputationally costly if wrong) signal this pipeline
    can raise, so it stays conservative — only the supplier's full name is
    checked, and names under 5 characters are skipped as too short/generic
    to screen reliably (e.g. "ITC", "GPI" would otherwise match countless
    unrelated SDN entries by coincidence).
    """
    if len(supplier_name) < 5:
        return []
    pattern = re.compile(r'\b' + re.escape(supplier_name.upper()) + r'\b')
    return [name for name in sdn_names if pattern.search(name.upper())][:5]

# ============================================================================
# PILLAR 1: MACRO OVERVIEW (US, EU, China)
# ============================================================================

def fetch_macro_us():
    """
    US Macro Economic Indicators — NOT YET WIRED UP to a real source
    (would need FRED API, BLS, etc.). Reports status "placeholder" rather
    than "success": the region previously claimed success with entirely
    hardcoded text, which silently counted toward the Macro pillar's
    healthy-region tally and rendered a green checkmark next to indicators
    nothing had actually checked.
    """
    try:
        return {
            "status": "placeholder",
            "region": "US",
            "indicators": {
                "fx_rate": "Placeholder - USD/EUR",
                "inflation": "Placeholder - CPI data",
                "policy": "Placeholder - Fed policy updates"
            },
            "summary": "US economic indicators not yet integrated (placeholder data)",
            "last_fetched": utc_now_iso()
        }
    except Exception as e:
        print(f"US Macro Error: {e}", file=sys.stderr)
        return {
            "status": "error",
            "region": "US",
            "error": str(e),
            "last_fetched": utc_now_iso()
        }

def fetch_macro_eu():
    """Fetch EU Macro Economic Indicators with retry logic"""
    source_name = "ecb_fx"
    try:
        # Fetch ECB EUR/USD rate
        url = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"
        response = fetch_with_retry(url)

        root = ET.fromstring(response.content)
        namespaces = {'gesmes': 'http://www.gesmes.org/xml/2002-08-01',
                     'ecb': 'http://www.ecb.int/vocabulary/2002-08-01/eurofxref'}

        # Find USD rate
        usd_rate = None
        for cube in root.findall('.//ecb:Cube[@currency="USD"]', namespaces):
            usd_rate = float(cube.get('rate'))
            break

        harvest_stats.record_success(source_name)
        return {
            "status": "success",
            "region": "EU",
            "indicators": {
                "fx_rate": usd_rate if usd_rate else None,
                "inflation": "Placeholder - HICP data",
                "policy": "Placeholder - ECB policy updates"
            },
            "summary": f"EU economic indicators - EUR/USD: {usd_rate if usd_rate else 'N/A'}",
            "last_fetched": utc_now_iso()
        }
    except Exception as e:
        harvest_stats.record_error(source_name, str(e))
        return {
            "status": "error",
            "region": "EU",
            "error": str(e),
            "last_fetched": utc_now_iso()
        }

def fetch_macro_china():
    """
    China Macro Economic Indicators — NOT YET WIRED UP to a real source
    (would need an official PBOC/NBS feed). See fetch_macro_us() for why
    this reports "placeholder" rather than "success".
    """
    try:
        return {
            "status": "placeholder",
            "region": "China",
            "indicators": {
                "fx_rate": "Placeholder - CNY/USD",
                "inflation": "Placeholder - CPI data",
                "policy": "Placeholder - PBOC policy updates"
            },
            "summary": "China economic indicators not yet integrated (placeholder data)",
            "last_fetched": utc_now_iso()
        }
    except Exception as e:
        print(f"China Macro Error: {e}", file=sys.stderr)
        return {
            "status": "error",
            "region": "China",
            "error": str(e),
            "last_fetched": utc_now_iso()
        }

def score_macro_rag(macro_economy: dict) -> tuple:
    """Macro pillar RAG from how unusual today's market moves are.

    Returns (rag_score, drivers) where drivers names the regions that moved.

    The old rule scored this pillar on two things that could not carry it.
    First, a count of "how many regions returned data" — with two of the three
    hardcoded as placeholders, that count could never reach 3, so the pillar
    started every cycle at AMBER by construction. Then it was overwritten by
    the EUR/USD move against the *previous snapshot's* rate — but ECB publishes
    that rate once a day and the harvest runs four times a day, so three runs
    in four compared a number against itself (volatility 0.0%, an automatic
    GREEN) and the fourth compared a full day's move against a 0.5% AMBER line
    that ordinary trading clears constantly. The recorded history shows the
    result: 53 of 80 readings amber, most of them with nothing happening.

    Now: a move is judged against that market's own recent volatility (the
    same yardstick suppliers use), so "unusual" means unusual for EUR/USD or
    the S&P, not "bigger than half a percent".
    """
    severities = {
        region: data.get("market_severity", "quiet")
        for region, data in (macro_economy or {}).items()
    }
    severe = [r for r, s in severities.items() if s == "severe"]
    notable = [r for r, s in severities.items() if s == "notable"]

    if severe:
        return "RED", severe
    if notable:
        return "AMBER", notable
    return "GREEN", []


def fetch_macro_overview(macro_economy: dict = None):
    """Aggregate Macro Overview for US, EU, China.

    Keeps the ECB reference rate as the authoritative EUR/USD print (it is the
    official daily fixing, and the dashboard quotes it directly); the RAG score
    comes from score_macro_rag over the live market readings.
    """
    us_data = fetch_macro_us()
    eu_data = fetch_macro_eu()
    china_data = fetch_macro_china()

    rag_score, drivers = score_macro_rag(macro_economy or {})

    return {
        "status": "success",
        "rag_score": rag_score,
        "rag_drivers": drivers,
        "regions": {
            "us": us_data,
            "eu": eu_data,
            "china": china_data
        },
        # Kept for backward compatibility with older snapshots' shape; the
        # meaningful volatility figure is now per-region market_sigma_pct in
        # macro_economy.
        "volatility_pct": None,
        "last_fetched": utc_now_iso()
    }

# ============================================================================
# PILLAR 2: PEERS & COMPETITORS
# ============================================================================

def fetch_sec_filings_for_peer(peer_name):
    """Fetch SEC 8-K filings for a peer company with retry logic"""
    source_name = f"sec_edgar_{peer_name}"
    # CIK mapping for tobacco companies (simplified). Keyed by the same
    # canonical name used in PEERS_CONFIG so this can be called directly
    # from fetch_peer_group() without a second name-mapping table.
    cik_map = {
        "British American Tobacco": None,  # not US listed
        "Philip Morris Int.": "0001413329",  # Philip Morris International
        "Imperial Brands": None,  # not US listed
        "Japan Tobacco": None,  # not US listed
    }

    cik = cik_map.get(peer_name)
    if not cik:
        return {
            "status": "skipped",
            "reason": "Not US-listed or placeholder",
            "filings": [],
            "red_signals": 0,
            "amber_signals": 0,
            "last_fetched": utc_now_iso()
        }

    try:
        url = f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}&type=8-K&output=atom&count=10"
        response = fetch_with_retry(url)

        root = ET.fromstring(response.content)
        namespaces = {'atom': 'http://www.w3.org/2005/Atom'}

        filings = []
        red_signals = []
        amber_signals = []

        # Only count signals from filings within the last 30 days
        thirty_days_ago = datetime.now(timezone.utc) - timedelta(days=30)

        for entry in root.findall('atom:entry', namespaces):
            title = entry.find('atom:title', namespaces)
            summary = entry.find('atom:summary', namespaces)
            published = entry.find('atom:published', namespaces)

            title_text = title.text if title is not None else ""
            summary_text = summary.text if summary is not None else ""
            published_text = published.text if published is not None else ""

            filing_data = {
                "title": title_text,
                "summary": summary_text[:200] if summary_text else "",
                "published": published_text
            }
            filings.append(filing_data)

            # Extract filing date from summary (format: "Filed: YYYY-MM-DD").
            # Both parses yield timezone-aware UTC so they can be compared
            # against thirty_days_ago; EDGAR publishes these in UTC, and a
            # bare "Filed:" date carries no zone of its own.
            filing_date = None
            date_match = re.search(r'Filed:</b>\s*(\d{4}-\d{2}-\d{2})', summary_text)
            if date_match:
                try:
                    filing_date = datetime.strptime(
                        date_match.group(1), '%Y-%m-%d'
                    ).replace(tzinfo=timezone.utc)
                except ValueError:
                    pass
            # Fallback: try published field
            if filing_date is None and published_text:
                try:
                    parsed = datetime.fromisoformat(published_text.replace('Z', '+00:00'))
                    filing_date = (
                        parsed.replace(tzinfo=timezone.utc)
                        if parsed.tzinfo is None
                        else parsed.astimezone(timezone.utc)
                    )
                except (ValueError, TypeError):
                    pass

            # Only count as signal if filed within last 30 days
            is_recent = filing_date is not None and filing_date >= thirty_days_ago

            # Check for distress signals (only recent filings trigger signals)
            summary_upper = summary_text.upper()
            if "ITEM 1.03" in summary_upper or "ITEM 4.02" in summary_upper:
                if is_recent:
                    red_signals.append(filing_data)
                else:
                    logger.info(f"Ignoring old red signal from {filing_date}: {summary_text[:80]}")
            elif "ITEM 5.02" in summary_upper:
                if is_recent:
                    amber_signals.append(filing_data)
                else:
                    logger.info(f"Ignoring old amber signal from {filing_date}: {summary_text[:80]}")

        harvest_stats.record_success(source_name)
        return {
            "status": "success",
            "filings": filings,
            "red_signals": len(red_signals),
            "amber_signals": len(amber_signals),
            "last_fetched": utc_now_iso()
        }
    except Exception as e:
        harvest_stats.record_error(source_name, str(e))
        return {
            "status": "error",
            "error": str(e),
            "filings": [],
            "red_signals": 0,
            "amber_signals": 0,
            "last_fetched": utc_now_iso()
        }

def generate_peer_summary(peer_name, filings_data):
    """Generate a summary text for a peer, always providing meaningful content"""
    # Check for critical risks first
    red_signals = filings_data.get("red_signals", 0)
    amber_signals = filings_data.get("amber_signals", 0)
    filings = filings_data.get("filings", [])
    status = filings_data.get("status", "unknown")
    
    # RED RISK: Critical distress signals
    if red_signals > 0:
        if "ITEM 1.03" in str(filings).upper():
            return f"CRITICAL: Bankruptcy filing detected (Item 1.03). Immediate attention required."
        elif "ITEM 4.02" in str(filings).upper():
            return f"CRITICAL: Non-reliance on financial statements (Item 4.02). Material accounting issues identified."
        else:
            return f"CRITICAL: {red_signals} distress signal(s) detected in recent SEC filings. Review required."
    
    # Routine leadership-change filings (Item 5.02). SEC filings don't
    # distinguish a planned retirement from a scandal-driven exit, so this
    # is shown as context, not treated as a risk signal (does not affect
    # the peers RAG score).
    if amber_signals > 0:
        return f"Informational: {amber_signals} leadership-change filing(s) noted (director/officer departure, Item 5.02). Not counted as a risk signal."
    
    # NEUTRAL: No risks but provide meaningful summary
    if status == "success" and len(filings) > 0:
        # Check for routine filings
        recent_filing = filings[0] if filings else None
        if recent_filing:
            filing_summary = recent_filing.get("summary", "")
            if "ITEM 2.02" in filing_summary.upper():
                return f"Neutral: Q3 earnings results reported. No material risks identified in last 30 days."
            elif "ITEM 7.01" in filing_summary.upper():
                return f"Neutral: Regulation FD disclosure filed. Routine operational update."
            elif "ITEM 1.01" in filing_summary.upper():
                return f"Neutral: Material definitive agreement entered. Standard business activity."
            else:
                return f"Neutral: {len(filings)} recent filing(s) processed. No material risks in last 30 days."
        else:
            return f"Neutral: Recent filings reviewed. No material risks identified in last 30 days."
    
    elif status == "success" and len(filings) == 0:
        return f"Neutral: No material filings in last 30 days. Standard operational status."
    
    elif status == "skipped":
        reason = filings_data.get("reason", "Not US-listed")
        if peer_name == "Our Company":
            return f"Neutral: Self-reference placeholder. Internal monitoring active."
        elif "Not US-listed" in reason:
            return f"Neutral: Company not US-listed. Monitoring international filings and news sources."
        else:
            return f"Neutral: {reason}. Alternative monitoring sources active."
    
    elif status == "error":
        error_msg = filings_data.get("error", "Unknown error")
        return f"Neutral: Data fetch error encountered ({error_msg[:50]}). Monitoring via alternative sources."
    
    # Default neutral summary
    return f"Neutral: Standard monitoring active. No material risks identified in last 30 days."

def fetch_peers_overview(peer_group):
    """
    Pillar-2 rollup — Peers & Competitors.

    peer_group (from fetch_peer_group()) is now the single source of truth
    for peer data: each entry already carries both live stock/news risk
    AND SEC 8-K filing signals (see fetch_peer_group). This just aggregates
    it into the pillar-level status/rag_score the dashboard card needs.
    Previously this ran its own independent fetch loop over a second,
    differently-named company list (PEERS_LIST) and produced a second,
    mostly-duplicate `peers` array — same 4 companies, doubled maintenance,
    double the SEC EDGAR requests per harvest.
    """
    total_red_signals = sum(p.get("sec_red_signals", 0) for p in peer_group)
    total_amber_signals = sum(p.get("sec_amber_signals", 0) for p in peer_group)
    live_critical = sum(1 for p in peer_group if p.get("risk_level") == "CRITICAL")
    live_high = sum(1 for p in peer_group if p.get("risk_level") in ("HIGH", "CRITICAL"))
    live_medium = sum(1 for p in peer_group if p.get("risk_level") == "MEDIUM")

    # total_amber_signals (routine SEC leadership-change filings) deliberately
    # does NOT drive this rollup — see the sec_amber handling in
    # fetch_peer_group(). Only genuine distress (red signals) or a real
    # market/news move (live_high, or 2+ peers moving at once) counts.
    if total_red_signals > 0 or live_critical > 0:
        rag_score = "RED"
    elif live_high >= 1 or live_medium >= 2:
        rag_score = "AMBER"
    else:
        rag_score = "GREEN"

    return {
        "status": "success",
        "rag_score": rag_score,
        "total_peers": len(peer_group),
        "total_red_signals": total_red_signals,
        "total_amber_signals": total_amber_signals,
        "last_fetched": utc_now_iso()
    }

# ============================================================================
# PILLAR 3: SUPPLIER WATCHLIST
# ============================================================================

def get_supplier_deep_dive_data(supplier_name, category):
    """Hand-maintained reference attributes for a watchlist supplier.

    Reads data/suppliers.json (see _load_watchlist). This used to be four
    parallel dicts inline — name->exposure, name->location, name->ticker,
    category->segment — so adding a supplier meant four edits in this file
    and a miss in any one of them silently produced "Unknown"/"N/A"/"Medium"
    for a real supplier.

    It also used to emit a `latest_news_summary` built from f-string
    templates keyed off the exposure tier — "on-time delivery metrics above
    98%", "regular quality audits completed successfully". None of that was
    ever measured, yet it rendered under a "Live Intelligence" heading, so
    invented text read as sourced intelligence. Real headlines come from
    news_items / google_news_headlines; an empty news feed now shows as empty.
    """
    profile = SUPPLIER_PROFILES.get(supplier_name)
    if profile is None:
        # Reachable only if a caller passes a name that is not on the
        # watchlist; the harvest itself always iterates WATCHLIST_DATA.
        logger.warning(f"No profile for {supplier_name!r} in {WATCHLIST_FILE.name}")
        return {
            "bat_exposure": "Medium",
            "segment": "Combustibles",
            "location": "Unknown",
            "hq_country": None,
            "stock_ticker": "N/A",
            "url": None,
        }
    return dict(profile)



EMPTY_STOCK_READING = {
    "daily_change_pct": None,
    "current_price": None,
    "headlines": [],
    "daily_sigma_pct": None,
}


def daily_sigma_from_closes(closes: list) -> float | None:
    """Standard deviation of daily % returns, as a percentage.

    A fixed -1.5%/-3% threshold treats every listing as equally jumpy, and
    they are not: for Texas Instruments a 2% day is a normal Tuesday, for a
    utility-like packaging name it is unusual. Reading the move against the
    supplier's own recent volatility is what turns "the price moved" into
    "the price moved more than this stock usually moves" — see
    classify_price_move. Needs at least 20 usable closes; returns None
    otherwise, and callers fall back to the absolute thresholds.
    """
    usable = [c for c in closes if isinstance(c, (int, float)) and math.isfinite(c) and c > 0]
    if len(usable) < 21:
        return None
    returns = [
        (usable[i] - usable[i - 1]) / usable[i - 1] * 100
        for i in range(1, len(usable))
    ]
    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    sigma = math.sqrt(variance)
    return sigma if sigma > 0 else None


def fetch_price_reading(ticker_symbol, source_label: str = None) -> dict:
    """
    Fetch a price reading for any listed instrument (supplier share, peer
    share, index, FX pair) using yfinance.

    Returns a dict with daily_change_pct, current_price, headlines (up to 5
    raw titles) and daily_sigma_pct (the listing's own recent daily
    volatility, used to judge whether today's move is actually unusual —
    see daily_sigma_from_closes). Every failure path returns the same shape
    with None/empty values rather than raising.
    """
    source_label = source_label or f"price_{ticker_symbol}"
    if not ticker_symbol or ticker_symbol == "N/A" or yf is None:
        return dict(EMPTY_STOCK_READING)

    # Check circuit breaker
    if not yfinance_circuit_breaker.can_execute():
        return dict(EMPTY_STOCK_READING)

    try:
        rate_limiter.wait_if_needed()
        ticker = yf.Ticker(ticker_symbol)

        # 3 months rather than 5 days: the last two closes give today's move,
        # and the rest gives the baseline volatility that move is judged
        # against. One request, same cost as before.
        hist = ticker.history(period="3mo")

        daily_change_pct = None
        current_price = None
        closes = [float(c) for c in hist['Close'].tolist()] if len(hist) else []
        # Skip over gaps rather than giving up on them. yfinance routinely
        # returns a trailing NaN row for a session that hasn't printed a close
        # yet (non-US listings mid-session, thinly traded lines), and reading
        # only the last two rows meant Infineon, Sappi and Stora Enso showed no
        # daily move at all on a normal trading day — the supplier was scanned
        # for news but silently had no price layer. Comparing the two most
        # recent *actual* closes keeps that layer alive; NaN was also what
        # previously leaked a literal "nan%" into signal text.
        usable = [c for c in closes if math.isfinite(c)]

        if len(usable) >= 2:
            current, previous = usable[-1], usable[-2]
            if previous:
                daily_change_pct = ((current - previous) / previous) * 100
            current_price = current
        elif len(usable) == 1:
            current_price = usable[0]

        # Get up to 5 news headlines for broader risk scanning
        headlines = []
        try:
            news = ticker.news
            if news:
                for item in news[:5]:
                    # yfinance >= 0.2.46 wraps title under item['content']['title']
                    # Fall back to top-level 'title' for older versions
                    title = (
                        item.get('content', {}).get('title')
                        or item.get('title')
                    )
                    if title:
                        headlines.append(title)
        except Exception:
            pass

        yfinance_circuit_breaker.record_success()
        return {
            "daily_change_pct": daily_change_pct,
            "current_price": current_price,
            "headlines": headlines,
            "daily_sigma_pct": daily_sigma_from_closes(closes),
        }

    except Exception as e:
        yfinance_circuit_breaker.record_failure()
        harvest_stats.record_warning(source_label, str(e)[:100])
        return dict(EMPTY_STOCK_READING)


# ============================================================================
# PRICE-MOVE SEVERITY
# ============================================================================
# A fixed percentage threshold reads every listing as equally volatile. The
# live board showed what that costs: Texas Instruments and Jabil crossed the
# old -1.5%/-3% lines and fell back four times in 48 hours, and the change
# feed — the thing meant to answer "what moved since I last looked" — ended
# up as ten entries of the same two names oscillating, plus the overall
# traffic light flipping AMBER→RED→AMBER on the back of it. Nothing had
# happened; the price had wobbled inside its normal range.
#
# So a move is now judged against how much that stock usually moves in a day
# (sigma over ~3 months, see daily_sigma_from_closes), with an absolute floor
# so a very quiet listing can't trip on a rounding error, and an absolute
# fallback for listings with too little history to measure. Thresholds relax
# once a supplier is already flagged (hysteresis), so a move hovering at the
# boundary holds its level instead of flapping every six hours.
PRICE_SIGMA_NOTABLE = 2.0     # standard deviations for "unusual for this stock"
PRICE_SIGMA_SEVERE = 3.5
PRICE_MIN_PCT = 2.0           # never flag a move smaller than this, however quiet the stock
PRICE_ABS_NOTABLE = 4.0       # fallback thresholds when sigma is unmeasurable
PRICE_ABS_SEVERE = 7.0
PRICE_HYSTERESIS = 0.75       # already-flagged suppliers clear at 75% of the entry bar


def classify_price_move(change_pct, sigma_pct=None, already_flagged: bool = False) -> str:
    """Severity of a *downward* daily price move: 'quiet' | 'notable' | 'severe'.

    sigma_pct is the listing's own recent daily volatility; when it is None
    (short history, thinly traded) the absolute fallbacks apply. already_flagged
    relaxes both bars by PRICE_HYSTERESIS so a supplier that was flagged last
    cycle does not clear on a marginal improvement — that oscillation is what
    filled the change feed with noise.
    """
    if change_pct is None or change_pct >= 0:
        return "quiet"
    drop = abs(change_pct)
    scale = PRICE_HYSTERESIS if already_flagged else 1.0

    if drop < PRICE_MIN_PCT * scale:
        return "quiet"

    if sigma_pct:
        z = drop / sigma_pct
        if z >= PRICE_SIGMA_SEVERE * scale:
            return "severe"
        if z >= PRICE_SIGMA_NOTABLE * scale:
            return "notable"
        return "quiet"

    if drop >= PRICE_ABS_SEVERE * scale:
        return "severe"
    if drop >= PRICE_ABS_NOTABLE * scale:
        return "notable"
    return "quiet"


def describe_price_move(change_pct, sigma_pct) -> str:
    """How unusual the move is, in the reader's terms — appended to signals."""
    if change_pct is None:
        return ""
    if not sigma_pct:
        return f"{change_pct:+.1f}% today"
    z = abs(change_pct) / sigma_pct
    return f"{change_pct:+.1f}% today ({z:.1f}× its normal daily range of ±{sigma_pct:.1f}%)"


GEO_ESCALATION_STICKY_HOURS = 48


def process_suppliers(cyber_data, recalls_data=None, sanctions_data=None,
                      previous_suppliers=None, previous_geo_state=None):
    """
    Process supplier watchlist and assess SUPPLY CHAIN RISK to BAT.

    Risk is assessed from SIX layers (each can escalate):
      0. Sanctions screening (OFAC SDN) — an automatic, non-overridable CRITICAL
      1. CISA cyber vulnerabilities (KEV catalog)
      2. CPSC safety recalls (saferproducts.gov)
      3. Stock price movements (yfinance, judged against the listing's own
         volatility — see classify_price_move)
      4. News scanning (yfinance headlines + Google News RSS)
      5. Geopolitical risk (conflict zones, sanctions, instability)

    previous_suppliers is last snapshot's supplier list, used only for
    hysteresis on the price-move layer (see classify_price_move).

    CRITICAL - Immediate threat to supply:
      - Sanctions match (OFAC SDN) — cannot legally transact
      - Bankruptcy, insolvency, liquidation
      - Factory fire, explosion, facility closure
      - Government sanctions, bans, seizure
      - Major cyber attack disrupting operations
      - Labor strike at production facility
      - A severe, unexplained price move (>=3.5 sigma, or >=7% where sigma
        can't be measured)
      - Supplier in active war zone

    HIGH - Serious concern:
      - Fraud/SEC investigation
      - Major product recall
      - An unusual unexplained price move (>=2 sigma / >=4%) on a
        Critical/High exposure supplier
      - Supplier in high-tension region (military buildup, severe sanctions)

    MEDIUM - Watch closely:
      - An unusual unexplained price move on a Medium exposure supplier
      - Major layoffs, restructuring
      - Supply disruption mentions
      - Supplier in region with trade war, instability, or border tensions

    LOW - Normal operations:
      - Price moves inside the stock's normal daily range
      - No negative operational news
      - No geopolitical risk signals
    """
    previous_by_name = {
        s.get("name"): s
        for s in (previous_suppliers or [])
        if s.get("name")
    }
    suppliers = []
    cisa_vulns = cyber_data.get("recent_vulnerabilities", [])
    cpsc_recalls = (recalls_data or {}).get("recalls", [])
    sdn_names = (sanctions_data or {}).get("names", [])

    # ================================================================
    # PRE-SCAN: Batch Google News geopolitical scan per unique country
    # This avoids redundant HTTP requests (one per country, not per supplier)
    # ================================================================
    unique_countries = set()
    for supplier in WATCHLIST_DATA:
        deep_dive_tmp = get_supplier_deep_dive_data(supplier["name"], supplier["category"])
        country = deep_dive_tmp.get("location", "Unknown")
        if country and country != "Unknown":
            unique_countries.add(country)

    logger.info(f"Scanning {len(unique_countries)} unique countries for geopolitical risk...")
    country_news_cache = {}
    geo_escalation_state = {}
    now = datetime.now(timezone.utc)
    for country in unique_countries:
        # Check static risk map
        static_risk = GEOPOLITICAL_RISK_MAP.get(country, None)

        if static_risk:
            static_level = static_risk["level"]
            static_reason = static_risk["reason"]
        else:
            static_level = "LOW"
            static_reason = ""

        # Only run live Google News scan for countries already in the risk map.
        # Scanning stable countries (USA, Switzerland, Japan, etc.) produces
        # false positives because global headlines about "war" or "sanctions"
        # mention every country in passing.
        news_level = "LOW"
        news_headlines = []
        news_reason = ""
        if static_risk:
            news_detected, news_level, news_headlines, news_reason = scan_country_geopolitical_news(country)

        # Final country-level geopolitical risk = max(static, live_news)
        # But cap news escalation to one level above static risk to prevent
        # false positives (e.g. "Finland" + generic "war" headline → CRITICAL).
        LEVEL_ORDER = ["LOW", "MEDIUM", "HIGH", "CRITICAL"]
        if RISK_PRIORITY.get(news_level, 0) > RISK_PRIORITY.get(static_level, 0):
            static_idx = LEVEL_ORDER.index(static_level) if static_level in LEVEL_ORDER else 0
            max_allowed_idx = min(static_idx + 1, len(LEVEL_ORDER) - 1)
            capped_news_level = news_level if LEVEL_ORDER.index(news_level) <= max_allowed_idx else LEVEL_ORDER[max_allowed_idx]
            final_level = capped_news_level
            final_reason = news_reason
        else:
            final_level = static_level
            final_reason = static_reason

        escalated_now = final_level != static_level

        # An escalation is held for GEO_ESCALATION_STICKY_HOURS after the last
        # cycle that saw it. Google News returns the eight most recent matches
        # for a country query, and that set turns over within hours — so an
        # escalation drops out of view long before the situation behind it
        # does. Unheld, that produced ITC going MEDIUM → HIGH → MEDIUM inside
        # ten hours, with a matching pair of entries in the change feed and the
        # whole board flipping RED and back. A border flare-up doesn't resolve
        # in ten hours; the headline just scrolled off.
        prior = (previous_geo_state or {}).get(country) or {}
        if escalated_now:
            geo_escalation_state[country] = {
                "level": final_level,
                "reason": final_reason,
                "since": prior.get("since") or utc_now_iso(),
                "last_seen": utc_now_iso(),
            }
        elif prior.get("last_seen"):
            last_seen = datetime.fromisoformat(prior["last_seen"])
            if last_seen.tzinfo is None:
                last_seen = last_seen.replace(tzinfo=timezone.utc)
            age_hours = (now - last_seen).total_seconds() / 3600
            held_level = prior.get("level", static_level)
            if age_hours < GEO_ESCALATION_STICKY_HOURS and RISK_PRIORITY.get(held_level, 0) > RISK_PRIORITY.get(final_level, 0):
                final_level = held_level
                final_reason = f"{prior.get('reason', '')} (still held, last corroborated {round(age_hours)}h ago)".strip()
                escalated_now = True
                geo_escalation_state[country] = prior

        country_news_cache[country] = {
            "level": final_level,
            "reason": final_reason,
            "headlines": [h["title"] for h in news_headlines] if news_headlines else [],
            "static_risk": static_risk is not None,
            # True only when live news actually pushed the level above the
            # standing structural floor this cycle — i.e. something changed
            # today, as opposed to an always-on baseline like "China: trade
            # tensions" or "Finland: NATO border state" that never varies.
            "escalated_by_live_news": escalated_now,
        }

        if final_level != "LOW":
            logger.info(f"  🌍 {country}: {final_level} — {final_reason}")

    logger.info(f"Geopolitical scan complete. {sum(1 for v in country_news_cache.values() if v['level'] != 'LOW')} countries with elevated risk.")

    # Keywords indicating REAL supply chain risk to BAT
    CRITICAL_SUPPLY_KEYWORDS = [
        "bankruptcy", "bankrupt", "insolvent", "liquidation", "chapter 11",
        "factory fire", "plant fire", "explosion", "plant closure", "facility closure",
        "cease operations", "shut down", "shutting down",
        "sanctioned", "import ban", "export ban", "trade ban", "seized", "embargo",
        "ransomware attack", "cyber attack", "systems down", "operations halted",
        "labor strike", "workers strike", "walkout"
    ]

    HIGH_SUPPLY_KEYWORDS = [
        "fraud investigation", "sec investigation", "fbi investigation",
        "accounting fraud", "securities fraud",
        "major recall", "product recall", "safety recall",
        "ceo fired", "ceo resign", "cfo resign", "executive exodus"
    ]

    MEDIUM_SUPPLY_KEYWORDS = [
        "mass layoff", "major layoff", "workforce reduction",
        "supply shortage", "supply disruption", "production delay", "shipping delay",
        "restructuring", "downsizing",
        "credit downgrade", "debt default"
    ]

    # Check each supplier against CISA alerts
    for supplier in WATCHLIST_DATA:
        supplier_name_upper = supplier["name"].upper()
        supplier_name = supplier["name"]
        category = supplier["category"]
        cyber_risk = False
        matching_vulns = []

        # Sanctions screening (OFAC SDN) — checked first, ahead of every
        # other layer, since it's the one signal here with real legal
        # consequence rather than operational risk.
        sanctions_matches = match_supplier_sanctions(supplier_name, sdn_names)
        sanctions_hit = len(sanctions_matches) > 0

        # Broader CISA matching: include aliases and known product names
        # so we don't only match on exact parent company name
        search_terms = supplier_search_terms(supplier_name)

        # Check if any search term appears (whole-word) in CISA vulnerability
        # fields — see supplier_search_terms()'s docstring for why this is
        # whole-word rather than substring matching.
        for vuln in cisa_vulns:
            vendor = vuln.get('vendorProject', '').upper()
            product = vuln.get('product', '').upper()
            description = vuln.get('vulnerabilityName', '').upper()
            combined = f"{vendor} {product} {description}"

            if supplier_terms_hit(combined, search_terms):
                cyber_risk = True
                matching_vulns.append({
                    "cveID": vuln.get('cveID', ''),
                    "vulnerabilityName": vuln.get('vulnerabilityName', ''),
                    "dateAdded": vuln.get('dateAdded', '')
                })

        # Check for CPSC safety recalls against this supplier
        matching_recalls = match_supplier_recalls(supplier_name, cpsc_recalls)
        recall_risk = len(matching_recalls) > 0

        # Get deep dive data (includes stock ticker)
        deep_dive = get_supplier_deep_dive_data(supplier_name, category)
        stock_ticker = deep_dive.get('stock_ticker', 'N/A')
        bat_exposure = deep_dive.get('bat_exposure', 'Medium')

        # Fetch live stock data for suppliers with tickers
        news_risk = False
        news_items = []
        news_headline = ""
        daily_change_pct = None
        current_price = None
        operational_risk = False  # True supply chain risk, not just stock movement
        risk_reason = ""

        daily_sigma_pct = None
        if stock_ticker and stock_ticker != "N/A":
            reading = fetch_price_reading(stock_ticker, source_label=f"supplier_stock_{stock_ticker}")
            daily_change_pct = reading["daily_change_pct"]
            current_price = reading["current_price"]
            daily_sigma_pct = reading["daily_sigma_pct"]

            # Only headlines that actually name this supplier are attributed to
            # it. yfinance's ticker.news returns sector-adjacent coverage, not
            # only articles about the ticker — the peer pillar already filters
            # this way (see PEERS_CONFIG match_terms), while suppliers did not,
            # so an unrelated "chip export ban" story under a semiconductor
            # ticker could raise a CRITICAL supply-risk flag against a supplier
            # the article never mentioned. Same whole-word matcher used for
            # CISA/recall/sanctions screening.
            headlines_list = [
                h for h in reading["headlines"]
                if h and supplier_terms_hit(h.upper(), search_terms)
            ]

            # Analyze ALL news headlines (up to 5) for SUPPLY CHAIN risk keywords
            for headline in headlines_list:
                if not headline:
                    continue
                if not news_headline:
                    news_headline = headline  # Keep first headline for display
                headline_lower = headline.lower()

                # Check for CRITICAL supply risk keywords
                for kw in CRITICAL_SUPPLY_KEYWORDS:
                    if _keyword_hit(headline_lower, kw):
                        news_risk = True
                        operational_risk = True
                        news_items.append({"headline": headline, "risk": "CRITICAL", "keyword": kw})
                        risk_reason = f"Critical supply risk: '{kw}' detected in news"
                        break

                # Check for HIGH supply risk keywords
                if not operational_risk:
                    for kw in HIGH_SUPPLY_KEYWORDS:
                        if _keyword_hit(headline_lower, kw):
                            news_risk = True
                            operational_risk = True
                            news_items.append({"headline": headline, "risk": "HIGH", "keyword": kw})
                            risk_reason = f"High supply risk: '{kw}' detected in news"
                            break

                # Check for MEDIUM supply risk keywords
                if not operational_risk:
                    for kw in MEDIUM_SUPPLY_KEYWORDS:
                        if _keyword_hit(headline_lower, kw):
                            news_risk = True
                            operational_risk = True
                            news_items.append({"headline": headline, "risk": "MEDIUM", "keyword": kw})
                            risk_reason = f"Supply concern: '{kw}' detected in news"
                            break

                # Stop scanning once we find the highest-severity match
                if operational_risk:
                    break

        # ================================================================
        # LAYER 3: Google News for ticker-less suppliers
        # Suppliers without stock tickers get ZERO news from yfinance.
        # Use Google News RSS to close this blind spot.
        # ================================================================
        google_news_headlines = []
        google_news_risk_level = "LOW"
        google_news_reason = ""

        if (not stock_ticker or stock_ticker == "N/A") and not operational_risk:
            location = deep_dive.get("location", "Unknown")
            google_news_headlines, google_news_risk_level, google_news_reason = scan_supplier_news_google(supplier_name, location)

            # If Google News found operational risk, integrate it
            if google_news_risk_level != "LOW" and google_news_headlines:
                news_risk = True
                operational_risk = True
                risk_reason = google_news_reason
                news_headline = google_news_headlines[0] if google_news_headlines else ""
                news_items.append({
                    "headline": news_headline,
                    "risk": google_news_risk_level,
                    "keyword": google_news_reason,
                    "source": "google_news"
                })

        # Generate slug for URL routing
        slug = supplier_name.lower().replace(" ", "-").replace("(", "").replace(")", "").replace("huizhou-byd-electronic", "byd-electronic")

        # ================================================================
        # RISK LEVEL DETERMINATION - Based on BAT supply chain impact
        # Layers 1-3: Cyber, Stock, News (sets initial risk level)
        # ================================================================
        supplier_risk_level = "LOW"
        last_signal = "No supply chain risks detected."
        risk_analysis = ""

        # Precomputed for Priorities 3-5 below (pure stock-price triggers):
        # whether we actually had headlines to check the move against, so
        # those branches can say "no negative news found" honestly instead
        # of always implying an operational cause is confirmed.
        if news_headline:
            corroboration_note = "No negative news identified alongside this move — likely market/sector-wide rather than company-specific, but a move this size is worth a glance."
        else:
            corroboration_note = "No recent news data was available to check against this move."

        # True only for the >3%/>1.5% HIGH/MEDIUM stock-price branches
        # (Priority 4/5) below — an uncorroborated price move at that
        # tier is common enough that it shouldn't, on its own, carry the
        # same "flip the whole board red" weight as a confirmed cyber
        # breach, sanctions match, or negative-news hit on the same
        # exposure tier (see high_exposure_hit further down). The rarer
        # >5% crash (Priority 3) is left out of this — a move that large
        # is a strong enough signal on its own regardless of exposure.
        price_move_only = False

        # Priority 0: Sanctions match — automatic CRITICAL, takes priority
        # over everything else. Transacting with a sanctioned party is a
        # legal blocker, not a graded operational risk.
        if sanctions_hit:
            supplier_risk_level = "CRITICAL"
            last_signal = f"🚫 SANCTIONS MATCH (OFAC SDN): possible match to '{sanctions_matches[0]}'"
            risk_analysis = f"{supplier_name} name matches an OFAC Specially Designated Nationals list entry ('{sanctions_matches[0]}'). This requires immediate compliance/legal verification before any further transactions — automated name matching can produce false positives and must be confirmed manually."

        # Priority 1: CISA cyber vulnerabilities (critical for IT-dependent suppliers)
        elif cyber_risk:
            supplier_risk_level = "CRITICAL" if len(matching_vulns) >= 2 else "HIGH"
            last_signal = f"🔒 Cyber vulnerability: {len(matching_vulns)} CISA KEV match(es) - {', '.join([v.get('cveID', 'N/A') for v in matching_vulns[:2]])}"
            risk_analysis = f"CISA Known Exploited Vulnerability detected. {supplier_name} systems may be at risk. {bat_exposure} exposure to BAT requires security assessment."

        # Priority 1.5: CPSC safety recall (a real, already-happened event —
        # ranked above news/stock signals but below an active cyber breach)
        elif recall_risk:
            supplier_risk_level = "CRITICAL" if len(matching_recalls) >= 2 else "HIGH"
            first_recall = matching_recalls[0]
            last_signal = f"⚠️ CPSC recall: {first_recall['product']} ({first_recall['recallNumber']})"
            risk_analysis = f"CPSC safety recall on file for {supplier_name}: {first_recall['description']}. {bat_exposure} exposure to BAT requires supplier quality review."

        # Priority 2: News-based operational risk (yfinance + Google News)
        elif operational_risk and news_items:
            news_severity = news_items[0].get('risk', 'MEDIUM')
            if news_severity == "CRITICAL":
                supplier_risk_level = "CRITICAL"
                last_signal = f"🚨 Supply threat: {news_headline[:100]}"
            elif news_severity == "HIGH":
                supplier_risk_level = "HIGH"
                last_signal = f"⚠️ Supply concern: {news_headline[:100]}"
            else:
                supplier_risk_level = "MEDIUM"
                last_signal = f"📋 Monitor: {news_headline[:100]}"
            risk_analysis = f"{risk_reason}. {supplier_name} ({category}) requires monitoring. BAT exposure: {bat_exposure}."

        # Priority 3-4: pure price triggers, reached only when the news scan
        # above found nothing concerning. A price move alone doesn't confirm
        # an operational problem — it may be market/sector-wide noise, or news
        # that hasn't broken yet. Say so explicitly instead of instructing
        # "investigate for operational impacts" as if a cause were already
        # confirmed (EVE Energy's stock dipped 3.4% the same day its own
        # headlines reported a strong, normal quarter).
        #
        # Severity is judged against the listing's own volatility, with
        # hysteresis so a supplier already flagged last cycle doesn't clear on
        # a marginal move (see classify_price_move for what the old fixed
        # thresholds cost).
        elif (price_severity := classify_price_move(
            daily_change_pct,
            daily_sigma_pct,
            already_flagged=previous_by_name.get(supplier_name, {}).get("price_move_only", False),
        )) != "quiet":
            price_move_only = True
            move_text = describe_price_move(daily_change_pct, daily_sigma_pct)
            if price_severity == "severe":
                supplier_risk_level = "CRITICAL"
                last_signal = f"📉 Severe unexplained drop: {move_text}"
                risk_analysis = (
                    f"{supplier_name} fell {move_text} — far outside its normal trading range. "
                    f"{corroboration_note} A move this size warrants direct follow-up regardless. "
                    f"BAT exposure: {bat_exposure}."
                )
            elif bat_exposure in ["Critical", "High"]:
                supplier_risk_level = "HIGH"
                last_signal = f"📉 Unusual drop: {move_text} — {bat_exposure} exposure supplier"
                risk_analysis = (
                    f"Unusual decline for {bat_exposure.lower()}-exposure supplier {supplier_name}: "
                    f"{move_text}. {corroboration_note} BAT exposure: {bat_exposure}."
                )
            else:
                supplier_risk_level = "MEDIUM"
                last_signal = f"📉 Unusual drop: {move_text} — monitoring"
                risk_analysis = (
                    f"Unusual decline for {supplier_name}: {move_text}. {corroboration_note} "
                    f"BAT exposure: {bat_exposure}."
                )

        # Default: Normal operations
        else:
            supplier_risk_level = "LOW"
            if daily_change_pct is not None:
                direction = "+" if daily_change_pct >= 0 else ""
                last_signal = f"✓ Normal operations. Stock: {direction}{daily_change_pct:.1f}%"
            else:
                last_signal = "✓ Normal operations. No risk signals."
            risk_analysis = f"No supply chain risks identified. {supplier_name} ({category}) operating normally. BAT exposure: {bat_exposure}."

        # ================================================================
        # LAYER 4: GEOPOLITICAL RISK OVERLAY
        # Acts as a FLOOR — can only elevate risk, never reduce it.
        # Combines static conflict map + live Google News country scan.
        # ================================================================
        location = deep_dive.get("location", "Unknown")
        geo_data = country_news_cache.get(location, {"level": "LOW", "reason": "", "headlines": [], "static_risk": False, "escalated_by_live_news": False})
        geo_risk_level = geo_data["level"]
        geo_reason = geo_data["reason"]
        geo_headlines = geo_data["headlines"]
        geopolitical_risk = geo_risk_level != "LOW"

        # Elevate risk if geopolitical risk is HIGHER than current assessment
        geo_escalated = False
        # Did the risk_level end up here purely from the standing structural
        # baseline (no cyber/news/stock signal, no live-news escalation)?
        # Used downstream to keep chronic ambient risk from permanently
        # pinning the pillar RAG to AMBER/RED — it's still shown per-supplier.
        geo_baseline_only = False
        if RISK_PRIORITY.get(geo_risk_level, 0) > RISK_PRIORITY.get(supplier_risk_level, 0):
            pre_geo_level = supplier_risk_level
            supplier_risk_level = geo_risk_level
            geo_escalated = True
            geo_baseline_only = not geo_data.get("escalated_by_live_news", False)
            # The displayed risk is now geo-driven, not price-driven — geo
            # has its own counts_toward_rag handling below, so this flag
            # shouldn't still exclude it from the price-move carve-out logic.
            price_move_only = False
            last_signal = f"🌍 Geopolitical: {geo_reason}"
            risk_analysis = f"Geopolitical risk in {location}: {geo_reason}. {supplier_name} ({category}) located in affected region. BAT exposure: {bat_exposure}. Previous risk: {pre_geo_level}."
            logger.info(f"  ↑ {supplier_name}: {pre_geo_level} → {supplier_risk_level} (geopolitical: {location})")

        # A supplier's risk only moves the pillar-level RAG dial when
        # something actually happened this cycle (cyber CVE, adverse news,
        # a stock move, or a live-news-corroborated geopolitical escalation)
        # — not merely because it sits in a country with a standing
        # structural risk floor that hasn't changed (e.g. China trade
        # tensions, Finland's NATO border). Those are still shown on the
        # supplier's own card, just excluded from the rollup so the pillar
        # RAG isn't permanently pinned to AMBER/RED by geography alone.
        counts_toward_rag = not (geo_escalated and geo_baseline_only)

        # Build supplier data
        supplier_data = {
            "name": supplier_name,
            "slug": slug,
            "category": category,
            "sanctions_hit": sanctions_hit,
            "sanctions_matches": sanctions_matches,
            "cyber_risk": cyber_risk,
            "matching_vulnerabilities": matching_vulns[:5],
            "recall_risk": recall_risk,
            "matching_recalls": matching_recalls[:5],
            "news_risk": news_risk,
            "news_items": news_items,
            "operational_risk": operational_risk,
            "daily_change_pct": round(daily_change_pct, 2) if daily_change_pct is not None else None,
            # The listing's own recent daily volatility, so the dashboard can
            # say whether today's move is unusual *for this stock* rather than
            # printing a bare percentage the reader has no yardstick for.
            "daily_sigma_pct": round(daily_sigma_pct, 2) if daily_sigma_pct is not None else None,
            "current_price": round(current_price, 2) if current_price is not None else None,
            "risk_analysis": risk_analysis,
            "risk_level": supplier_risk_level,
            "last_signal": last_signal,
            "counts_toward_rag": counts_toward_rag,
            "price_move_only": price_move_only,
            # Geopolitical risk fields
            "geopolitical_risk": {
                "detected": geopolitical_risk,
                "level": geo_risk_level if geopolitical_risk else None,
                "reason": geo_reason if geopolitical_risk else None,
                "headlines": geo_headlines[:3] if geopolitical_risk else [],
                "escalated": geo_escalated,
                "baseline_only": geo_baseline_only,
            } if geopolitical_risk else None,
            # NEW: Google News headlines for ticker-less suppliers
            "google_news_headlines": google_news_headlines[:3],
            **deep_dive
        }

        suppliers.append(supplier_data)

    # Calculate RAG score based on actual supply risks
    suppliers_at_sanctions_risk = sum(1 for s in suppliers if s["sanctions_hit"])
    suppliers_at_cyber_risk = sum(1 for s in suppliers if s["cyber_risk"])
    suppliers_at_recall_risk = sum(1 for s in suppliers if s["recall_risk"])
    suppliers_at_news_risk = sum(1 for s in suppliers if s["news_risk"])
    suppliers_at_operational_risk = sum(1 for s in suppliers if s.get("operational_risk", False))
    suppliers_at_geopolitical_risk = sum(1 for s in suppliers if s.get("geopolitical_risk") is not None)
    suppliers_geo_escalated = sum(1 for s in suppliers if s.get("geopolitical_risk") and s["geopolitical_risk"].get("escalated", False))

    total_critical = sum(1 for s in suppliers if s["risk_level"] == "CRITICAL")
    total_high = sum(1 for s in suppliers if s["risk_level"] == "HIGH")
    total_medium = sum(1 for s in suppliers if s["risk_level"] == "MEDIUM")

    # ================================================================
    # RAG ROLLUP — actionable signals only, weighted by BAT exposure
    #
    # total_critical/high/medium above count every supplier in that risk
    # bucket, including ones sitting there purely on a standing structural
    # floor (e.g. every China-based supplier is permanently "MEDIUM" for
    # trade-war exposure). Left unfiltered, that ambient baseline alone
    # is enough to permanently pin this pillar to AMBER/RED regardless of
    # whether anything actually happened — which trains the reader to
    # ignore the color. The rollup below only counts suppliers whose
    # current risk_level reflects a real signal (cyber, news, stock move,
    # or a live-news-corroborated geopolitical escalation), and treats a
    # hit on a Critical/High-BAT-exposure supplier as more consequential
    # than the same hit on a Low-exposure one.
    # ================================================================
    actionable = [s for s in suppliers if s.get("counts_toward_rag", True)]
    actionable_critical = sum(1 for s in actionable if s["risk_level"] == "CRITICAL")
    actionable_high = sum(1 for s in actionable if s["risk_level"] == "HIGH")
    actionable_medium = sum(1 for s in actionable if s["risk_level"] == "MEDIUM")
    # price_move_only suppliers (an uncorroborated stock dip — no negative
    # news, cyber match, recall, or sanctions hit behind it, whatever the
    # size) still count toward actionable_high/medium/critical above so
    # they can push AMBER, but they're excluded below from every RED
    # trigger: a confirmed signal (cyber, sanctions, recall, corroborated
    # news) is what should make a CPO's day RED, not "the market moved and
    # we don't know why yet". Otherwise every uncorroborated dip — however
    # large, or however many happen to land the same cycle — trains the
    # reader to treat RED as noise instead of a real page-me signal.
    confirmed = [s for s in actionable if not s.get("price_move_only", False)]
    confirmed_critical = sum(1 for s in confirmed if s["risk_level"] == "CRITICAL")
    confirmed_high = sum(1 for s in confirmed if s["risk_level"] == "HIGH")
    confirmed_medium = sum(1 for s in confirmed if s["risk_level"] == "MEDIUM")
    high_exposure_hit = any(
        s["risk_level"] in ("CRITICAL", "HIGH")
        and s.get("bat_exposure") in ("Critical", "High")
        and not s.get("price_move_only", False)
        for s in actionable
    )

    if confirmed_critical >= 1 or high_exposure_hit or (confirmed_high + confirmed_medium) >= 3:
        rag_score = "RED"
    elif actionable_high >= 1 or actionable_medium >= 1:
        rag_score = "AMBER"
    else:
        rag_score = "GREEN"

    logger.info(f"Supplier risk summary: {total_critical} CRITICAL, {total_high} HIGH, {total_medium} MEDIUM, {len(suppliers) - total_critical - total_high - total_medium} LOW")
    logger.info(f"  of which actionable (excl. structural-only geo floor): {actionable_critical} CRITICAL, {actionable_high} HIGH, {actionable_medium} MEDIUM; high-exposure hit: {high_exposure_hit}")
    logger.info(f"Risk sources: {suppliers_at_sanctions_risk} sanctions, {suppliers_at_cyber_risk} cyber, {suppliers_at_recall_risk} recall, {suppliers_at_news_risk} news, {suppliers_at_geopolitical_risk} geopolitical ({suppliers_geo_escalated} escalated)")
    if suppliers_at_sanctions_risk > 0:
        logger.warning(f"⚠️ {suppliers_at_sanctions_risk} supplier(s) matched OFAC SDN screening — requires immediate manual compliance review")

    return {
        "status": "success",
        "rag_score": rag_score,
        "total_suppliers": len(suppliers),
        "suppliers_at_sanctions_risk": suppliers_at_sanctions_risk,
        "suppliers_at_cyber_risk": suppliers_at_cyber_risk,
        "suppliers_at_recall_risk": suppliers_at_recall_risk,
        "suppliers_at_news_risk": suppliers_at_news_risk,
        "suppliers_at_operational_risk": suppliers_at_operational_risk,
        "suppliers_at_geopolitical_risk": suppliers_at_geopolitical_risk,
        "total_critical": total_critical,
        "total_high": total_high,
        "total_medium": total_medium,
        # Actionable = excludes suppliers whose risk_level reflects only a
        # standing structural/geopolitical floor with no real signal this
        # cycle. This is what actually drives rag_score above; total_* is
        # kept as the plain per-supplier bucket count for the UI filters.
        "actionable_critical": actionable_critical,
        "actionable_high": actionable_high,
        "actionable_medium": actionable_medium,
        "suppliers": suppliers,
        # Carried forward in the snapshot so a live-news geopolitical
        # escalation survives the headline scrolling out of Google News'
        # eight-item window (see GEO_ESCALATION_STICKY_HOURS).
        "geo_escalation_state": geo_escalation_state,
        "last_fetched": utc_now_iso()
    }

# ============================================================================
# MACRO ECONOMY DATA GENERATION (LIVE DATA)
# ============================================================================

def classify_move(change_pct, sigma_pct=None) -> str:
    """Severity of a daily move in either direction: 'quiet' | 'notable' | 'severe'."""
    if change_pct is None:
        return "quiet"
    return classify_price_move(-abs(change_pct), sigma_pct)


# The one genuinely live number each region has, and what it is.
MACRO_MARKETS = {
    "us": {"ticker": "^GSPC", "label": "S&P 500", "kind": "index"},
    "eu": {"ticker": "EURUSD=X", "label": "EUR/USD", "kind": "fx"},
    # CNY=X quotes yuan per dollar, so a rise means the yuan is weakening.
    "china": {"ticker": "CNY=X", "label": "USD/CNY", "kind": "fx_inverted"},
}

# Official statistics, from FRED. A region maps to None where there is no
# free source that still updates — China's OECD-sourced CPI and policy-rate
# series on FRED stopped over a year ago, and publishing a year-old number
# next to a live market move is how "China CPI 0.5%" came to sit on the board
# as if it were this month's reading. Absent is honest; stale is not.
MACRO_SERIES = {
    "us": {
        "cpi": ("CPIAUCSL", "pc1"),
        "rate": ("FEDFUNDS", "lin"),
        "rate_label": "Fed funds rate",
    },
    "eu": {
        "cpi": ("CP0000EZ19M086NEST", "pc1"),
        "rate": ("ECBDFR", "lin"),
        "rate_label": "ECB deposit facility rate",
    },
    "china": {
        "cpi": None,
        "rate": None,
        "rate_label": "PBOC policy rate",
    },
}


def _format_observation_month(date_str: str) -> str:
    """'2026-07-01' -> 'Jul 2026'. Empty string when unparseable."""
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").strftime("%b %Y")
    except (ValueError, TypeError):
        return ""


def _region_trend(kind: str, change_pct, severity: str) -> str:
    """Trend label for a region, from the direction and size of the move.

    'Stable' now means "inside this market's normal daily range", not
    "moved less than half a percent". EUR/USD clears 0.5% on perfectly
    ordinary days, and under the old fixed cut-off that alone pushed the
    macro pillar to AMBER — which is most of why the board spent 66% of its
    recorded history amber with nothing actually wrong.
    """
    if change_pct is None or severity == "quiet":
        return "Stable"
    falling = change_pct < 0
    if kind == "fx":
        return "Weakening" if falling else "Strengthening"
    if kind == "fx_inverted":
        # A rising USD/CNY means a weaker yuan.
        return "Declining" if not falling else "Growing"
    return "Declining" if falling else "Growing"


def fetch_macro_economy():
    """Live market reading plus official statistics for US, EU and China.

    Everything here is either measured or explicitly absent. The previous
    version composed a paragraph of macro commentary from a template chosen
    by the sign of one day's index move — "Fed signals potential rate cuts in
    Q3 as inflation moderates", "Industrial output remains resilient",
    "consumer confidence elevated". None of that was fetched from anywhere.
    It rendered under an "Analyst Summary" heading on the region pages, which
    made invented sentences read as sourced intelligence — the single most
    damaging thing a board like this can do to its own credibility.
    """
    regions = {}

    for region_key, market in MACRO_MARKETS.items():
        reading = fetch_price_reading(market["ticker"], source_label=f"macro_{region_key}")
        change_pct = reading["daily_change_pct"]
        sigma_pct = reading["daily_sigma_pct"]
        severity = classify_move(change_pct, sigma_pct)
        trend = _region_trend(market["kind"], change_pct, severity)

        series = MACRO_SERIES[region_key]
        cpi_obs = fetch_fred_observation(*series["cpi"]) if series["cpi"] else None
        rate_obs = fetch_fred_observation(*series["rate"]) if series["rate"] else None

        if change_pct is None:
            market_sentence = f"{market['label']}: no reading available this cycle."
        else:
            if sigma_pct:
                band = f"its normal daily range of ±{sigma_pct:.1f}%"
                comparison = (
                    f"inside {band}" if severity == "quiet"
                    else f"{abs(change_pct) / sigma_pct:.1f}× {band}"
                )
                market_sentence = f"{market['label']} {change_pct:+.2f}% today, {comparison}."
            else:
                market_sentence = f"{market['label']} {change_pct:+.2f}% today."

        stat_parts = []
        if cpi_obs:
            stat_parts.append(f"CPI {cpi_obs['value']:.1f}% YoY ({_format_observation_month(cpi_obs['date'])})")
        if rate_obs:
            stat_parts.append(f"{series['rate_label']} {rate_obs['value']:.2f}% ({_format_observation_month(rate_obs['date'])})")
        stat_sentence = (
            " ".join(stat_parts) + "."
            if stat_parts
            else "No live inflation or policy-rate feed is connected for this region."
        )

        regions[region_key] = {
            "cpi": f"{cpi_obs['value']:.1f}%" if cpi_obs else None,
            "cpi_as_of": _format_observation_month(cpi_obs["date"]) if cpi_obs else None,
            "rate": f"{rate_obs['value']:.2f}%" if rate_obs else None,
            "rate_as_of": _format_observation_month(rate_obs["date"]) if rate_obs else None,
            "rate_label": series["rate_label"],
            "market_label": market["label"],
            "market_change_pct": round(change_pct, 2) if change_pct is not None else None,
            "market_sigma_pct": round(sigma_pct, 2) if sigma_pct else None,
            "market_severity": severity,
            "trend": trend,
            "summary": f"{market_sentence} {stat_sentence}".strip(),
            "sources": ["Yahoo Finance"] + (["FRED"] if (cpi_obs or rate_obs) else []),
        }

        if change_pct is not None:
            harvest_stats.record_success(f"macro_{region_key}")
        else:
            harvest_stats.record_warning(f"macro_{region_key}", "no market reading this cycle")

    return regions

# ============================================================================
# PEER GROUP DATA GENERATION (LIVE DATA)
# ============================================================================

def fetch_peer_group():
    """
    Fetch real peer group intelligence using yfinance.
    MORE SENSITIVE risk detection - stock movements are a primary signal.
    """
    peer_data = []

    # Risk keywords for news analysis. Bare "strike"/"ban"/"seize" were
    # dropped in favor of specific phrases — they matched too much
    # unrelated coverage ("strike a deal", "bans plastic packaging",
    # "seize the opportunity").
    CRITICAL_KEYWORDS = ["investigation", "fraud", "sanctioned", "bankruptcy", "recall",
                          "labor strike", "workers strike", "import ban", "export ban",
                          "trade ban", "seized", "breach", "hacked", "lawsuit"]
    # Single common words carried no information about a competitor and read
    # as risk anyway: "down" and "drop" fire on every routine "shares down 1%"
    # market wrap (the price move is already scored separately, below), "cut"
    # on a dividend raise headline that happens to mention cutting costs,
    # "fine" on "finest", "miss" on "emissions". Each is replaced by the
    # phrase that actually means something for a peer.
    WARNING_KEYWORDS = [
        "profit warning", "guidance cut", "cuts guidance", "cuts forecast",
        "earnings miss", "misses estimates", "missed estimates",
        "supply shortage", "production delay", "regulatory fine", "fined",
        "layoff", "layoffs", "job cuts", "restructur", "downgrade",
        "market share loss", "sales decline", "profit fall", "profits fall",
    ]

    # Check circuit breaker before making yfinance calls
    if not yfinance_circuit_breaker.can_execute():
        logger.warning("yfinance circuit breaker is OPEN - skipping peer group fetch")
        harvest_stats.record_warning("peer_group", "Circuit breaker open - using fallback data")
        # Return fallback data
        for peer_config in PEERS_CONFIG:
            peer_data.append({
                "name": peer_config["name"],
                "ticker": peer_config["ticker"],
                "region": peer_config.get("region", "Unknown"),
                "sentiment": "N/A",
                "latest_headline": peer_config.get("default_text", "Data temporarily unavailable."),
                "stock_move": "N/A",
                "current_price": None,
                "daily_change_pct": None,
                "risk_level": "LOW",
                "last_signal": peer_config.get("default_text", "Circuit breaker active."),
                "sec_red_signals": 0,
                "sec_amber_signals": 0,
                "summary": peer_config.get("default_text", "Circuit breaker active."),
            })
        return peer_data

    for peer_config in PEERS_CONFIG:
        # Apply rate limiting
        rate_limiter.wait_if_needed()
        try:
            ticker_symbol = peer_config["ticker"]
            ticker = yf.Ticker(ticker_symbol)

            # Price, volatility and headlines come from the same helper the
            # supplier pillar uses, so both pillars judge a move the same way.
            # ticker.info is no longer fetched here: it is a slow, frequently
            # failing endpoint and its only use was a fallback price for the
            # case where no history exists at all.
            reading = fetch_price_reading(ticker_symbol, source_label=f"peer_{ticker_symbol}")
            current_price = reading["current_price"]
            daily_change_pct = reading["daily_change_pct"]
            daily_sigma_pct = reading["daily_sigma_pct"]
            stock_move = f"{daily_change_pct:+.2f}%" if daily_change_pct is not None else "N/A"

            # Get up to 5 news headlines for broader scanning. Only headlines
            # that actually name this peer survive: an unrelated article is
            # both a wrong headline to print and — because the keyword scan
            # below runs over this same list — a false CRITICAL/WARNING
            # signal for a company it isn't about.
            latest_headline = None
            all_headlines = []
            real_headline_found = False
            match_terms = peer_config.get("match_terms") or [peer_config["name"]]
            try:
                news = ticker.news
                if news:
                    fetched = 0
                    for item in news[:10]:
                        # yfinance >= 0.2.46 wraps title under item['content']['title']
                        title = (
                            item.get('content', {}).get('title')
                            or item.get('title')
                        )
                        if not title:
                            continue
                        fetched += 1
                        title_lower = title.lower()
                        if any(_mentions_subject(title_lower, term) for term in match_terms):
                            all_headlines.append(title)
                    if all_headlines:
                        latest_headline = all_headlines[0]
                        real_headline_found = True
                    elif fetched:
                        logger.info(
                            f"  {peer_config['name']}: {fetched} headline(s) fetched, "
                            f"none named the company — falling back to default text"
                        )
            except Exception as e:
                logger.warning(f"News fetch error for {peer_config['name']}: {e}")

            # Use default_text if no real headline found
            if not real_headline_found:
                latest_headline = peer_config.get("default_text", "Monitoring active.")

            # ========================================
            # RISK SCORING - Stock movement is PRIMARY
            # ========================================
            risk_level = "LOW"
            last_signal = ""
            news_risk_detected = False
            stock_risk_detected = False

            # STEP 1: Check ALL news headlines for risk keywords
            if real_headline_found:
                for hl in all_headlines:
                    hl_lower = hl.lower()

                    # Check for CRITICAL keywords
                    has_critical = any(_keyword_hit(hl_lower, keyword) for keyword in CRITICAL_KEYWORDS)
                    if has_critical:
                        risk_level = "CRITICAL"
                        last_signal = f"🚨 News Alert: {hl[:120]}"
                        news_risk_detected = True
                        break

                    # Check for WARNING keywords
                    has_warning = any(_keyword_hit(hl_lower, keyword) for keyword in WARNING_KEYWORDS)
                    if has_warning:
                        risk_level = "MEDIUM"
                        last_signal = f"⚠️ News Alert: {hl[:120]}"
                        news_risk_detected = True
                        # Don't break — keep scanning for CRITICAL in remaining headlines

                if not news_risk_detected:
                    pass  # No risk keywords found in any headline

            # STEP 2: Check stock movement (ALWAYS check, can escalate risk)
            # Peers are competitors, not suppliers — a peer's stock wobbling
            # a point or two on an ordinary day isn't procurement-relevant.
            # Judged against the listing's own volatility, same as suppliers,
            # so a jumpy small-cap and a defensive large-cap aren't held to
            # one shared percentage.
            peer_severity = classify_price_move(daily_change_pct, daily_sigma_pct)
            if peer_severity != "quiet":
                move_text = describe_price_move(daily_change_pct, daily_sigma_pct)
                if peer_severity == "severe":
                    if risk_level != "CRITICAL":
                        risk_level = "CRITICAL"
                        last_signal = f"🚨 Severe market drop: {move_text}"
                    else:
                        last_signal += f" | {move_text}"
                    stock_risk_detected = True
                else:
                    if risk_level == "LOW":
                        risk_level = "MEDIUM"
                        last_signal = f"📉 Unusual market drop: {move_text}"
                    elif risk_level == "MEDIUM" and not news_risk_detected:
                        last_signal = f"📉 Unusual market drop: {move_text}"
                    stock_risk_detected = True

            # STEP 3: Default signal if no risk detected
            if not last_signal:
                if real_headline_found:
                    # Show headline even if no risk keywords
                    last_signal = f"📰 {latest_headline[:100]}"
                elif daily_change_pct is not None:
                    # Show stock movement
                    last_signal = f"Stock: {daily_change_pct:+.2f}% | {peer_config.get('default_text', 'Monitoring active.')}"
                else:
                    last_signal = peer_config.get("default_text", "Monitoring active.")

            # Determine sentiment based on stock movement
            sentiment = "Neutral"
            if daily_change_pct is not None:
                if daily_change_pct > 1.0:
                    sentiment = "Positive"
                elif daily_change_pct < -1.0:
                    sentiment = "Negative"
                else:
                    sentiment = "Neutral"

            yfinance_circuit_breaker.record_success()
            harvest_stats.record_success(f"peer_{peer_config['ticker']}")

            # Fold in SEC 8-K filing signals (was previously a second,
            # independently-fetched "peers" pillar over a differently-named
            # copy of this same company list — see fetch_peers_overview).
            filings_data = fetch_sec_filings_for_peer(peer_config["name"])
            sec_red = filings_data.get("red_signals", 0)
            sec_amber = filings_data.get("amber_signals", 0)
            if sec_red > 0 and RISK_PRIORITY.get(risk_level, 0) < RISK_PRIORITY.get("HIGH", 2):
                risk_level = "HIGH"
                last_signal = f"📄 SEC filing: {sec_red} distress signal(s) (Item 1.03/4.02) | {last_signal}"
            elif sec_amber > 0:
                # Item 5.02 covers ANY officer/director departure — a planned
                # retirement files the same item code as a scandal-driven
                # exit, and the filing itself doesn't say which. Since we
                # can't tell those apart, this is shown as context only and
                # no longer escalates risk_level or the peers RAG score
                # (previously any single filing forced MEDIUM/amber).
                last_signal = f"{last_signal} (note: {sec_amber} routine leadership-change filing(s) also on record)"

            peer_data.append({
                "name": peer_config["name"],
                "ticker": ticker_symbol,
                "region": peer_config.get("region", "Unknown"),
                "sentiment": sentiment,
                "latest_headline": latest_headline if real_headline_found else peer_config.get("default_text", "Monitoring active."),
                "stock_move": stock_move,
                "current_price": current_price,
                "daily_change_pct": round(daily_change_pct, 2) if daily_change_pct is not None else None,
                "daily_sigma_pct": round(daily_sigma_pct, 2) if daily_sigma_pct else None,
                "risk_level": risk_level,
                "last_signal": last_signal,
                "news_risk": news_risk_detected,
                "stock_risk": stock_risk_detected,
                "sec_red_signals": sec_red,
                "sec_amber_signals": sec_amber,
                "summary": generate_peer_summary(peer_config["name"], filings_data),
            })

        except Exception as e:
            yfinance_circuit_breaker.record_failure()
            harvest_stats.record_error(f"peer_{peer_config['ticker']}", str(e))
            # Fallback: Use default_text, LOW risk
            peer_data.append({
                "name": peer_config["name"],
                "ticker": peer_config["ticker"],
                "region": peer_config.get("region", "Unknown"),
                "sentiment": "N/A",
                "latest_headline": peer_config.get("default_text", "Data fetch error."),
                "stock_move": "N/A",
                "current_price": None,
                "daily_change_pct": None,
                "risk_level": "LOW",
                "last_signal": peer_config.get("default_text", "Data fetch error."),
                "news_risk": False,
                "stock_risk": False,
                "sec_red_signals": 0,
                "sec_amber_signals": 0,
                "summary": peer_config.get("default_text", "Data fetch error."),
            })

    return peer_data

# ============================================================================
# MAIN AGGREGATION
# ============================================================================

def validate_dashboard_state(data: dict) -> tuple:
    """
    Validate the dashboard state has required fields.
    Returns (is_valid, error_message)
    """
    required_fields = ['last_updated', 'macro', 'peers', 'suppliers']
    for field in required_fields:
        if field not in data:
            return False, f"Missing required field: {field}"

    # Validate pillars have status and rag_score
    for pillar in ['macro', 'peers', 'suppliers']:
        pillar_data = data.get(pillar, {})
        if 'status' not in pillar_data:
            return False, f"Missing status in {pillar}"
        if 'rag_score' not in pillar_data:
            return False, f"Missing rag_score in {pillar}"

    return True, None


def _sanitize_non_finite_floats(obj):
    """
    Recursively replace NaN/Infinity floats with None.
    Python's json.dump writes these as bare NaN/Infinity tokens by default,
    which are not valid JSON and break strict parsers (e.g. webpack's JSON loader).
    """
    if isinstance(obj, dict):
        return {k: _sanitize_non_finite_floats(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_non_finite_floats(v) for v in obj]
    if isinstance(obj, float) and not math.isfinite(obj):
        return None
    return obj


def save_with_backup(data: dict, output_file: Path) -> bool:
    """
    Save new data with backup of previous version.
    Returns True if saved successfully.
    """
    try:
        # Create backup if current file exists
        if output_file.exists():
            backup_file = output_file.with_suffix('.backup.json')
            shutil.copy(output_file, backup_file)
            logger.info(f"Created backup: {backup_file}")

        # Write new data
        data = _sanitize_non_finite_floats(data)
        with open(output_file, 'w') as f:
            json.dump(data, f, indent=2, allow_nan=False)

        logger.info(f"Saved data to {output_file}")
        return True
    except Exception as e:
        logger.error(f"Failed to save data: {e}")
        return False


# ============================================================================
# CHANGE LOG — what moved since last time
# ============================================================================
# A status board answers "is anything on fire right now", and on most days the
# answer is no. That leaves nothing new to look at, which is a poor reason to
# open the thing tomorrow. The change log answers the other question — "what
# moved since I last looked" — which has an answer on quiet days too.
#
# The snapshot is the database here, so the log lives inside it: each harvest
# diffs itself against the previous snapshot and appends whatever actually
# changed, trimmed to a rolling window. The frontend then slices it by the
# reader's own last-visit time.
# ============================================================================

CHANGE_LOG_MAX_ENTRIES = 200
CHANGE_LOG_MAX_AGE_DAYS = 21
# Report an unexplained price move only when it is large enough to be worth a
# CPO's attention on its own. Anything smaller is ordinary market noise and
# would bury the real signals under a daily wall of ±1% entries.
PRICE_MOVE_REPORT_PCT = 4.0

# Per-supplier boolean signals, in the order they should be named.
SUPPLIER_SIGNAL_LABELS = [
    ("sanctions_hit", "OFAC sanctions match"),
    ("cyber_risk", "CISA cyber vulnerability"),
    ("recall_risk", "CPSC safety recall"),
    ("news_risk", "adverse news"),
]


def _supplier_signals(supplier: dict) -> dict:
    """Active boolean risk signals for one supplier, as {label: True}.

    The standing geopolitical floor is deliberately excluded unless live news
    escalated it this cycle: "China has trade tensions" is true every single
    day, so logging it as a change would put the same nine entries in the feed
    forever and train the reader to skip it.
    """
    signals = {
        label: bool(supplier.get(key))
        for key, label in SUPPLIER_SIGNAL_LABELS
    }
    geo = supplier.get("geopolitical_risk") or {}
    signals["geopolitical escalation"] = bool(
        geo.get("escalated") and not geo.get("baseline_only", True)
    )
    return {label: active for label, active in signals.items() if active}


def _rag_direction(before: str, after: str) -> str:
    order = {"GREEN": 0, "AMBER": 1, "RED": 2}
    return "up" if order.get(after, 0) > order.get(before, 0) else "down"


def compute_changes(previous_state: dict | None, suppliers_data: dict,
                    peer_group: list, macro_economy: dict,
                    pillar_rag_scores: dict, overall_rag: dict,
                    now_iso: str) -> list:
    """Diff this harvest against the previous snapshot.

    Returns a list of change entries, most significant kind first. Returns []
    when there is no previous snapshot — a first run has nothing to compare
    against, and reporting all 24 suppliers as "new" would be noise, not news.
    """
    if not previous_state:
        return []

    changes = []

    def add(kind, direction, entity, headline, detail="", href=None):
        changes.append({
            "at": now_iso,
            "kind": kind,
            "direction": direction,
            "entity": entity,
            "headline": headline,
            "detail": detail,
            "href": href,
        })

    # --- Overall and per-pillar RAG -------------------------------------
    prev_overall = (previous_state.get("overall_rag") or {}).get("score")
    new_overall = overall_rag.get("score")
    if prev_overall and new_overall and prev_overall != new_overall:
        add("overall_rag", _rag_direction(prev_overall, new_overall),
            "Overall status", f"Overall status {prev_overall} → {new_overall}",
            "Driven by: " + ", ".join(overall_rag.get("driven_by", [])) or "")

    prev_pillars = (previous_state.get("overall_rag") or {}).get("pillar_scores", {})
    for pillar, score in pillar_rag_scores.items():
        before = prev_pillars.get(pillar)
        if before and before != score:
            add("pillar_rag", _rag_direction(before, score),
                pillar.capitalize(), f"{pillar.capitalize()} pillar {before} → {score}")

    # --- Suppliers -------------------------------------------------------
    prev_suppliers = {
        s.get("name"): s
        for s in (previous_state.get("suppliers", {}) or {}).get("suppliers", [])
        if s.get("name")
    }

    for supplier in suppliers_data.get("suppliers", []):
        name = supplier.get("name")
        if not name:
            continue
        # Percent-encoded, matching how the dashboard's own links are built
        # (encodeURIComponent). "SWM (Mativ)" otherwise produced a raw
        # "/details/SWM (Mativ)" that only worked by browser leniency.
        href = f"/details/{quote(name, safe='')}"
        before = prev_suppliers.get(name)

        if before is None:
            add("supplier_added", "info", name, f"{name} added to the watchlist",
                f"{supplier.get('category', '')} · {supplier.get('location', '')}", href)
            continue

        old_level = before.get("risk_level")
        new_level = supplier.get("risk_level")
        old_signals = _supplier_signals(before)
        new_signals = _supplier_signals(supplier)
        appeared = [label for label in new_signals if label not in old_signals]
        cleared = [label for label in old_signals if label not in new_signals]

        # A risk level that moved purely because the share price moved is not
        # an event, and logging it as one is what turned this feed into a wall
        # of "Texas Instruments: LOW → MEDIUM" / "MEDIUM → LOW" pairs. The move
        # itself is still reported below, once, as a price_move — which says
        # what actually happened without implying the supplier's risk profile
        # changed.
        price_driven = (
            supplier.get("price_move_only", False) or before.get("price_move_only", False)
        ) and not (appeared or cleared)

        if price_driven:
            move = supplier.get("daily_change_pct")
            if supplier.get("price_move_only") and isinstance(move, (int, float)):
                sigma = supplier.get("daily_sigma_pct")
                yardstick = f" ({abs(move) / sigma:.1f}× its normal daily range)" if sigma else ""
                add("price_move", "info", name,
                    f"{name} {move:+.1f}%{yardstick}, no corroborating signal",
                    f"BAT exposure: {supplier.get('bat_exposure')}. Cause unconfirmed.", href)
        elif old_level and new_level and old_level != new_level:
            direction = "up" if RISK_PRIORITY.get(new_level, 0) > RISK_PRIORITY.get(old_level, 0) else "down"
            detail = supplier.get("last_signal", "")
            if appeared:
                detail = f"New: {', '.join(appeared)}. {detail}".strip()
            add("supplier_risk", direction, name,
                f"{name}: {old_level} → {new_level}", detail, href)
        elif appeared or cleared:
            # Risk level held but the evidence behind it moved — still worth
            # surfacing, e.g. a cyber CVE clearing while a news hit keeps the
            # supplier at the same level.
            if appeared:
                add("supplier_signal", "up", name,
                    f"{name}: {', '.join(appeared)}",
                    supplier.get("last_signal", ""), href)
            if cleared:
                add("supplier_signal", "down", name,
                    f"{name}: {', '.join(cleared)} cleared", "", href)
        else:
            # No risk change at all — report only an outsized, unexplained
            # price move on a supplier that matters to BAT.
            move = supplier.get("daily_change_pct")
            if (
                isinstance(move, (int, float))
                and abs(move) >= PRICE_MOVE_REPORT_PCT
                and supplier.get("bat_exposure") in ("Critical", "High")
            ):
                add("price_move", "info", name,
                    f"{name} {move:+.1f}% with no corroborating signal",
                    f"BAT exposure: {supplier.get('bat_exposure')}. Cause unconfirmed.", href)

    for name in prev_suppliers:
        if not any(s.get("name") == name for s in suppliers_data.get("suppliers", [])):
            add("supplier_removed", "info", name, f"{name} removed from the watchlist")

    # --- Peers -----------------------------------------------------------
    prev_peers = {p.get("name"): p for p in (previous_state.get("peer_group") or []) if p.get("name")}
    for peer in peer_group or []:
        name = peer.get("name")
        before = prev_peers.get(name)
        if not before:
            continue
        old_level, new_level = before.get("risk_level"), peer.get("risk_level")
        if old_level and new_level and old_level != new_level:
            direction = "up" if RISK_PRIORITY.get(new_level, 0) > RISK_PRIORITY.get(old_level, 0) else "down"
            add("peer_risk", direction, name, f"{name}: {old_level} → {new_level}",
                peer.get("last_signal", ""), f"/details/{quote(name, safe='')}")

    # --- Macro trends ----------------------------------------------------
    prev_macro = previous_state.get("macro_economy") or {}
    region_labels = {"us": "US", "eu": "EU", "china": "China"}
    for key, label in region_labels.items():
        before = (prev_macro.get(key) or {}).get("trend")
        after = (macro_economy.get(key) or {}).get("trend")
        if before and after and before != after and "N/A" not in (before, after):
            worsening = after in ("Declining", "Weakening", "Volatile")
            add("macro_trend", "up" if worsening else "down", label,
                f"{label} outlook {before} → {after}", "", f"/macro/{key}")

    return changes


def trim_change_log(entries: list) -> list:
    """Keep the log bounded by both age and count, newest last."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=CHANGE_LOG_MAX_AGE_DAYS)
    kept = []
    for entry in entries:
        raw = entry.get("at")
        if not raw:
            continue
        try:
            stamp = datetime.fromisoformat(raw)
        except ValueError:
            continue
        # Entries written before timestamps carried an offset are UTC too.
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        if stamp >= cutoff:
            kept.append(entry)
    return kept[-CHANGE_LOG_MAX_ENTRIES:]


def generate_executive_summary(overall_rag: dict, pillar_rag_scores: dict,
                                 suppliers_data: dict, peer_group: list,
                                 macro_data: dict, changes_this_cycle: list = None,
                                 change_log: list = None) -> dict | None:
    """
    Narrate the already-computed RAG rollup into a CPO-facing executive
    summary via a single Claude API call. This never influences the RAG
    score itself (that stays deterministic, above) — it only connects
    signals across suppliers/pillars that the per-supplier f-string
    templates report individually (e.g. five China-exposure suppliers
    flagged separately vs. recognized as one concentrated risk).

    changes_this_cycle and change_log are what moved (see compute_changes),
    and they lead the payload. Without them the brief could only describe a
    standing position, so on the many cycles where the position is unchanged
    it reworded the same paragraph and read as new information — while the
    one thing that genuinely was new that cycle sat unmentioned in the feed
    directly above it. A large unexplained price move on a Critical-exposure
    supplier is the clearest case: it deliberately does not move the RAG
    score, so nothing else in this payload would have surfaced it.

    Returns a structured {headline, next_step, context} dict rather than
    one free-text paragraph — a single dense wall of text doesn't scan
    well inside the status card, so the model is constrained to a fixed
    shape the frontend can give real visual hierarchy to (bold takeaway,
    a distinct action line, smaller supporting detail).

    Best-effort: returns None (no summary rendered) if no API key is
    configured or the call fails for any reason, so the harvest never
    blocks on an external dependency.
    """
    if not os.getenv("ANTHROPIC_API_KEY"):
        logger.info("ANTHROPIC_API_KEY not set — skipping executive summary")
        return None

    try:
        import anthropic

        # Only suppliers whose risk_level reflects an actual signal this
        # cycle (counts_toward_rag) — the same filter the deterministic
        # rollup uses. A permanent structural geopolitical floor (every
        # China-based supplier is always at least MEDIUM) is real context
        # but isn't new information, and handing it to the model without
        # this filter drowns out whichever pillar (often peers/macro) is
        # actually driving the current RAG score — see the mislabeled
        # "peers"-driven AMBER this filter was added to fix.
        suppliers = suppliers_data.get("suppliers", [])
        actionable_suppliers = [
            {
                "name": s.get("name"),
                "category": s.get("category"),
                "bat_exposure": s.get("bat_exposure"),
                "location": s.get("location"),
                "risk_level": s.get("risk_level"),
                "last_signal": s.get("last_signal"),
                "risk_analysis": s.get("risk_analysis"),
                # price_move_only=True means this is an unexplained stock
                # move with no corroborating news/event — the model must
                # not invent a specific cause (e.g. "liquidity stress",
                # "margin pressure") for these; see confirmed=False rule
                # in the system prompt.
                "confirmed": not s.get("price_move_only", False),
                "geopolitical": s.get("geopolitical_risk") is not None,
            }
            for s in suppliers
            if s.get("risk_level") != "LOW" and s.get("counts_toward_rag", True)
        ]

        # peer_group (not the peers pillar summary dict, which has no
        # per-company detail) is what actually explains a peers-driven
        # RAG score — without risk_level/last_signal here the model has
        # nothing to reason about when "driven_by" is peers.
        actionable_peers = [
            {
                "name": p.get("name"),
                "region": p.get("region"),
                "sentiment": p.get("sentiment"),
                "risk_level": p.get("risk_level"),
                "last_signal": p.get("last_signal"),
            }
            for p in (peer_group or [])
            if p.get("risk_level") not in (None, "LOW")
        ]

        # Trimmed to what the model needs to name a change; hrefs and
        # timestamps are frontend concerns.
        def _compact(entries):
            return [
                {
                    "kind": c.get("kind"),
                    "direction": c.get("direction"),
                    "entity": c.get("entity"),
                    "headline": c.get("headline"),
                    "detail": c.get("detail", ""),
                }
                for c in (entries or [])
            ]

        # A week of prior changes, so a third consecutive move on one supplier
        # can be recognised as a trend rather than reported as an isolated
        # event. Excludes this cycle's own entries, which are listed above.
        week_ago = datetime.now(timezone.utc) - timedelta(days=7)
        this_cycle_ids = {id(c) for c in (changes_this_cycle or [])}
        recent = []
        for entry in (change_log or []):
            if id(entry) in this_cycle_ids:
                continue
            raw = entry.get("at")
            if not raw:
                continue
            try:
                stamp = datetime.fromisoformat(raw)
            except ValueError:
                continue
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=timezone.utc)
            if stamp >= week_ago:
                recent.append(entry)

        payload = {
            "changes_this_cycle": _compact(changes_this_cycle),
            "changes_previous_7_days": _compact(recent[-25:]),
            "overall_rag": overall_rag,
            "pillar_rag_scores": pillar_rag_scores,
            "actionable_suppliers": actionable_suppliers,
            "actionable_peers": actionable_peers,
            "macro": {
                region: {"status": data.get("status"), "summary": data.get("summary")}
                for region, data in (macro_data.get("regions") or {}).items()
            },
        }

        client = anthropic.Anthropic()
        response = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=500,
            system=(
                "You write terse executive briefs for a CPO (Chief Procurement Officer) "
                "reviewing a supply chain risk dashboard. You are given an already-decided "
                "RAG status (RED/AMBER/GREEN) and its underlying signals — do not restate "
                "or second-guess the color, only explain what it means and what's actionable. "
                "changes_this_cycle is what actually moved since the previous check, and it "
                "is the most important thing in this payload: the reader sees that same list "
                "rendered directly above your brief. When it is non-empty, your headline must "
                "be about what changed — a supplier's risk moving, a signal appearing or "
                "clearing, an unexplained price move — not about the standing position. "
                "changes_previous_7_days is there so you can tell a one-off from a pattern: "
                "say so when the same entity has moved repeatedly. When changes_this_cycle is "
                "empty, do not manufacture novelty; say plainly that nothing moved and use the "
                "brief to explain what is still standing and worth watching. Note that a "
                "kind of 'price_move' is by design NOT reflected in the RAG color — treat it "
                "as real and worth naming even when everything reads GREEN. "
                "overall_rag.driven_by names which pillar(s) — macro, peers, and/or suppliers — "
                "actually produced the current score; your headline and next_step MUST be about "
                "that pillar's data specifically, even if another pillar's payload is larger or "
                "more detailed. Do not default to writing about suppliers just because that "
                "section has more entries — if driven_by is ['peers'], the story is in "
                "actionable_peers, not actionable_suppliers. Prioritize: connect signals that "
                "share a root cause (same country, same sector, same time window) instead of "
                "listing entities one by one. Call out what's genuinely urgent vs. what can wait. "
                "Every supplier entry has confirmed: true/false. confirmed=false means an "
                "unexplained stock move with no corroborating news — for those, describe only "
                "the observable fact (direction, size, exposure) and say the cause is unconfirmed. "
                "NEVER invent a specific mechanism (liquidity stress, margin pressure, demand "
                "collapse, etc.) to explain a confirmed=false move — that fabricates certainty "
                "the data doesn't have. If you connect a confirmed=false supplier signal to "
                "another pillar's real news into one narrative, state it as a hypothesis to "
                "verify ('worth checking whether X and Y share a cause'), not as an established "
                "fact — match your confidence in each sentence to the evidence behind it."
            ),
            output_config={
                "format": {
                    "type": "json_schema",
                    "schema": {
                        "type": "object",
                        "properties": {
                            "headline": {
                                "type": "string",
                                "description": "One short sentence, the core takeaway — what kind of risk this is and why, not a restatement of the RAG color.",
                            },
                            "next_step": {
                                "type": "string",
                                "description": "One concrete, time-boxed action for the CPO to take next. Empty string if genuinely nothing is actionable right now.",
                            },
                            "context": {
                                "type": "string",
                                "description": "1-2 short supporting sentences: which specific suppliers/signals drive this and why they're linked. Plain prose, no bullet points.",
                            },
                        },
                        "required": ["headline", "next_step", "context"],
                        "additionalProperties": False,
                    },
                }
            },
            messages=[{"role": "user", "content": json.dumps(payload)}],
        )
        text = "".join(block.text for block in response.content if block.type == "text").strip()
        summary = json.loads(text)
        if not summary.get("headline"):
            return None
        return summary
    except Exception as e:
        logger.warning(f"Executive summary generation failed (non-blocking): {e}")
        return None


def main():
    """Main aggregation function - Three Core Pillars"""
    logger.info("Starting CPO intelligence harvest (Three Core Pillars)...")

    # Load previous state — price-move hysteresis, geopolitical escalation
    # memory, the change log, RAG history and the fallback all read from it.
    data_dir = Path(__file__).parent.parent / "data"
    previous_state = None
    previous_file = data_dir / "intel_snapshot.json"

    if previous_file.exists():
        try:
            with open(previous_file, "r") as f:
                previous_state = json.load(f)
            logger.info("Loaded previous state successfully")
        except Exception as e:
            harvest_stats.record_warning("previous_state", f"Could not load: {e}")

    # Fetch supporting data
    cyber_data = fetch_cisa_kev()
    recalls_data = fetch_cpsc_recalls()
    sanctions_data = fetch_ofac_sdn()

    # PILLAR 1: Macro Overview. The live market readings are fetched first
    # because the pillar's RAG score is derived from them (see score_macro_rag)
    # rather than from a region-count that could never reach GREEN.
    macro_economy = fetch_macro_economy()
    macro_data = fetch_macro_overview(macro_economy)

    # PILLAR 2: Peers & Competitors — peer_group (live stock/news + SEC
    # filing signals, all merged in fetch_peer_group) is now the single
    # source of truth; fetch_peers_overview just rolls it up into the
    # pillar-level status the dashboard card needs. No separate fetch, no
    # separate cross-pillar escalation merge required anymore.
    peer_group = fetch_peer_group()
    peers_data = fetch_peers_overview(peer_group)

    # PILLAR 3: Supplier Watchlist
    suppliers_data = process_suppliers(
        cyber_data, recalls_data, sanctions_data,
        # Last cycle's supplier rows drive price-move hysteresis, and its
        # geopolitical escalation state keeps a flare-up flagged after the
        # headline behind it scrolls out of the news window.
        previous_suppliers=(previous_state or {}).get("suppliers", {}).get("suppliers", []),
        previous_geo_state=(previous_state or {}).get("suppliers", {}).get("geo_escalation_state", {}),
    )

    # Experimental GDELT geopolitical signal — feeds only the standalone
    # /geopolitical page (see fetch_gdelt_intel), not the RAG pipeline above.
    suppliers_by_country = {}
    for s in suppliers_data.get("suppliers", []):
        loc = s.get("location")
        if loc and loc != "Unknown":
            suppliers_by_country.setdefault(loc, []).append(
                {"name": s.get("name"), "category": s.get("category")}
            )
    all_gdelt_countries = set(suppliers_by_country.keys())
    # Order is decided per cycle from the attempt history rather than being
    # fixed (see order_gdelt_countries): a fixed order plus the circuit
    # breaker meant the same tail of the list was cut off every single run.
    previous_attempts = {
        country: state
        for country, state in (previous_state or {}).get("geopolitical_attempts", {}).items()
        if country in all_gdelt_countries
    }
    fresh_geo, gdelt_attempts = fetch_gdelt_intel(
        sorted(all_gdelt_countries),
        attempts=previous_attempts,
        suppliers_by_country=suppliers_by_country,
    )
    # Merge onto last run's results instead of replacing wholesale — GDELT's
    # rate limiting means only a handful of countries succeed on any given
    # run, and which ones is essentially random (whichever get through
    # before the circuit breaker trips). Discarding yesterday's Austria
    # just because today's run didn't reach Austria made the page flicker
    # between arbitrary subsets each cycle instead of steadily filling in.
    # A country only disappears here if it's no longer a supplier location
    # at all, not because this one run happened to miss it.
    previous_geo = {
        country: entry
        for country, entry in (previous_state or {}).get("geopolitical_intel", {}).items()
        if country in all_gdelt_countries
    }
    geopolitical_intel = {**previous_geo, **fresh_geo}

    # The macro pillar's RAG score is computed in score_macro_rag from these
    # same readings (fetched at the top of main), so there is no second
    # trend-count escalation pass here any more — that one fired on any single
    # region merely labelled "Weakening", which a 0.6% EUR/USD day satisfied.

    # Calculate overall health status
    pillar_statuses = [
        macro_data.get('status'),
        peers_data.get('status'),
        suppliers_data.get('status')
    ]
    success_count = sum(1 for s in pillar_statuses if s == 'success')

    if success_count == 3:
        overall_status = "healthy"
    elif success_count >= 1:
        overall_status = "partial"
    else:
        overall_status = "degraded"

    # ================================================================
    # OVERALL RAG — single "should I worry today" rollup across all three
    # pillars, computed AFTER the cross-pillar escalation merges above so
    # it reflects each pillar's final score. This is distinct from
    # `status`/`overall_status` above, which tracks fetch health (did the
    # data sources respond), not risk severity. Without this, a CPO has to
    # mentally combine three separate cards on every visit.
    # ================================================================
    RAG_PRIORITY_ORDER = {"GREEN": 0, "AMBER": 1, "RED": 2}
    pillar_rag_scores = {
        "macro": macro_data.get("rag_score", "GREEN"),
        "peers": peers_data.get("rag_score", "GREEN"),
        "suppliers": suppliers_data.get("rag_score", "GREEN"),
    }
    worst_rag = max(pillar_rag_scores.values(), key=lambda v: RAG_PRIORITY_ORDER.get(v, 0))
    overall_rag = {
        "score": worst_rag,
        "driven_by": [pillar for pillar, score in pillar_rag_scores.items() if score == worst_rag],
        "pillar_scores": pillar_rag_scores,
    }

    # ================================================================
    # RAG HISTORY — every harvest overwrote the last with no memory of
    # what came before, so there was no way to tell whether today's RED
    # was new or has been sitting there for a week. The "database" here
    # is the flat JSON file itself, so the trend is carried forward inside
    # it: each run appends its scores to whatever history the previous
    # snapshot already had, trimmed to a rolling window.
    # ================================================================
    MAX_RAG_HISTORY = 80  # ~20 days at the 6-hour harvest cadence
    rag_history = list((previous_state or {}).get("rag_history", []))
    rag_history.append({
        "timestamp": utc_now_iso(),
        "macro": pillar_rag_scores["macro"],
        "peers": pillar_rag_scores["peers"],
        "suppliers": pillar_rag_scores["suppliers"],
        "overall": worst_rag,
    })
    rag_history = rag_history[-MAX_RAG_HISTORY:]

    # ================================================================
    # CHANGE LOG — diff this harvest against the previous snapshot and
    # append whatever moved. Carried forward inside the snapshot itself
    # (same "the JSON file is the database" pattern as rag_history), so
    # the dashboard can open on "what changed since you last looked"
    # rather than only "what is on fire right now".
    # ================================================================
    harvest_timestamp = utc_now_iso()
    new_changes = compute_changes(
        previous_state, suppliers_data, peer_group, macro_economy,
        pillar_rag_scores, overall_rag, harvest_timestamp,
    )
    change_log = trim_change_log(
        list((previous_state or {}).get("change_log", [])) + new_changes
    )
    if new_changes:
        logger.info(f"Change log: {len(new_changes)} change(s) this cycle")
        for change in new_changes:
            logger.info(f"  [{change['direction']}] {change['headline']}")
    else:
        logger.info("Change log: nothing changed since the previous snapshot")

    executive_summary = generate_executive_summary(
        overall_rag, pillar_rag_scores, suppliers_data, peer_group, macro_data,
        changes_this_cycle=new_changes, change_log=change_log,
    )

    # Build dashboard state with three core pillars + additional intelligence
    dashboard_state = {
        "last_updated": utc_now_iso(),
        "version": "",  # Will be set after hash calculation
        "status": overall_status,
        "overall_rag": overall_rag,
        "executive_summary": executive_summary,
        "rag_history": rag_history,
        "change_log": change_log,
        "macro": macro_data,
        "peers": peers_data,
        "suppliers": suppliers_data,
        "macro_economy": macro_economy,
        "peer_group": peer_group,
        "geopolitical_intel": geopolitical_intel,
        "geopolitical_attempts": gdelt_attempts,
        "harvest_stats": harvest_stats.summary(),
        "health": {
            "pillars": {
                "macro": macro_data.get('status', 'unknown'),
                "peers": peers_data.get('status', 'unknown'),
                "suppliers": suppliers_data.get('status', 'unknown')
            },
            "errors_count": len(harvest_stats.errors),
            "warnings_count": len(harvest_stats.warnings),
            "circuit_breaker_state": yfinance_circuit_breaker.state
        }
    }

    # Calculate version hash
    dashboard_state["version"] = calculate_data_hash(dashboard_state)

    # Validate before saving
    is_valid, validation_error = validate_dashboard_state(dashboard_state)
    if not is_valid:
        logger.error(f"Validation failed: {validation_error}")
        if previous_state:
            logger.warning("Using previous state as fallback")
            # Keep previous data but update timestamp and add error info
            previous_state["last_updated"] = utc_now_iso()
            previous_state["status"] = "fallback"
            previous_state["harvest_stats"] = harvest_stats.summary()
            dashboard_state = previous_state

    # Save to data directory
    data_dir.mkdir(parents=True, exist_ok=True)
    output_file = data_dir / "intel_snapshot.json"

    if not save_with_backup(dashboard_state, output_file):
        logger.error("Failed to save dashboard state")
        sys.exit(1)

    # Summary output
    logger.info(f"Intelligence harvest complete. Version: {dashboard_state.get('version', 'N/A')}")
    logger.info(f"Overall Status: {overall_status}")
    logger.info("Three Core Pillars:")
    logger.info(f"  1. Macro: {macro_data.get('rag_score', 'UNKNOWN')} ({macro_data.get('status', 'unknown')})")
    logger.info(f"  2. Peers: {peers_data.get('rag_score', 'UNKNOWN')} ({peers_data.get('status', 'unknown')})")
    logger.info(f"  3. Suppliers: {suppliers_data.get('rag_score', 'UNKNOWN')} ({suppliers_data.get('status', 'unknown')})")

    # Print detailed summaries
    if peers_data.get('status') == 'success':
        logger.info(f"  Peers: {peers_data.get('total_peers', 0)} tracked, {peers_data.get('total_red_signals', 0)} red, {peers_data.get('total_amber_signals', 0)} amber signals")

    if suppliers_data.get('status') == 'success':
        logger.info(f"  Suppliers: {suppliers_data.get('total_suppliers', 0)} total, {suppliers_data.get('suppliers_at_cyber_risk', 0)} cyber risk, {suppliers_data.get('suppliers_at_news_risk', 0)} news risk")

    # Harvest stats summary
    stats = harvest_stats.summary()
    logger.info(f"Harvest Stats: {stats['total_successes']} successes, {stats['total_errors']} errors, {stats['total_warnings']} warnings")
    logger.info(f"Duration: {stats['duration_seconds']:.2f}s")

    # Check if we should alert
    if harvest_stats.should_alert():
        logger.warning("ALERT: Critical errors detected during harvest!")
        for error in harvest_stats.errors:
            logger.warning(f"  - [{error['source']}] {error['error']}")
        # Exit with error code to trigger GitHub Actions failure notification
        sys.exit(2)


if __name__ == "__main__":
    main()
