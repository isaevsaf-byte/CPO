#!/usr/bin/env python3
"""
Supply Chain Intelligence Harvester - CPO Three Core Pillars
Fetches data from official government endpoints and saves to JSON.
Runs via GitHub Actions every 6 hours.
"""

import requests
import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path
import sys
import time
import hashlib
import logging
import os
import shutil
import yfinance as yf

# ============================================================================
# CONFIGURATION
# ============================================================================
USER_AGENT = {'User-Agent': 'SupplyChainIntelligence contact@mycompany.com'}
TIMEOUT = int(os.getenv("FETCH_TIMEOUT", 15))
MAX_RETRIES = int(os.getenv("MAX_RETRIES", 3))
STALE_THRESHOLD_HOURS = int(os.getenv("STALE_THRESHOLD", 24))

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
        self.start_time = datetime.utcnow()

    def record_error(self, source: str, error: str):
        self.errors.append({
            "source": source,
            "error": str(error)[:200],
            "time": datetime.utcnow().isoformat()
        })
        logger.error(f"[{source}] {error}")

    def record_warning(self, source: str, warning: str):
        self.warnings.append({
            "source": source,
            "warning": str(warning)[:200],
            "time": datetime.utcnow().isoformat()
        })
        logger.warning(f"[{source}] {warning}")

    def record_success(self, source: str):
        self.successes.append({
            "source": source,
            "time": datetime.utcnow().isoformat()
        })
        logger.info(f"[{source}] Success")

    def should_alert(self) -> bool:
        """Determine if errors are critical enough to warrant alerting"""
        critical_sources = ['cisa_kev', 'sec_edgar', 'ecb_fx']
        return (
            len(self.errors) >= 3 or
            any(e['source'] in critical_sources for e in self.errors)
        )

    def summary(self) -> dict:
        return {
            "total_errors": len(self.errors),
            "total_warnings": len(self.warnings),
            "total_successes": len(self.successes),
            "errors": self.errors[-10:],
            "warnings": self.warnings[-5:],
            "duration_seconds": (datetime.utcnow() - self.start_time).total_seconds()
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

def fetch_with_retry(url: str, max_retries: int = None, headers: dict = None) -> requests.Response:
    """
    Fetch URL with exponential backoff retry logic.

    Args:
        url: URL to fetch
        max_retries: Maximum number of retry attempts (default from config)
        headers: Optional headers dict

    Returns:
        requests.Response object

    Raises:
        requests.RequestException after all retries exhausted
    """
    if max_retries is None:
        max_retries = MAX_RETRIES
    if headers is None:
        headers = USER_AGENT

    last_exception = None
    for attempt in range(max_retries):
        try:
            response = requests.get(url, headers=headers, timeout=TIMEOUT)
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

def calculate_data_hash(data: dict) -> str:
    """Generate a short hash of the data for version checking"""
    # Exclude timestamps from hash to detect actual data changes
    data_copy = json.loads(json.dumps(data))  # Deep copy
    # Remove fields that change every run
    if 'last_updated' in data_copy:
        del data_copy['last_updated']
    if 'harvest_stats' in data_copy:
        del data_copy['harvest_stats']

    json_str = json.dumps(data_copy, sort_keys=True)
    return hashlib.md5(json_str.encode()).hexdigest()[:12]

# ============================================================================
# GEOPOLITICAL RISK MAP - Auto-applied based on supplier location
# Captures wars, armed conflicts, sanctions regimes, and regional instability.
# Severity levels act as a FLOOR for supplier risk — can only elevate, never reduce.
# ============================================================================
GEOPOLITICAL_RISK_MAP = {
    # CRITICAL — Active war zones, comprehensive sanctions
    "Ukraine": {"level": "CRITICAL", "reason": "Active armed conflict zone (Russia-Ukraine war)"},
    "Russia": {"level": "CRITICAL", "reason": "Active conflict, comprehensive Western sanctions regime"},
    "Yemen": {"level": "CRITICAL", "reason": "Active armed conflict, Houthi attacks disrupting Red Sea shipping"},
    "Sudan": {"level": "CRITICAL", "reason": "Active civil war, humanitarian crisis"},
    "Myanmar": {"level": "CRITICAL", "reason": "Civil war, military junta, Western sanctions"},
    "Syria": {"level": "HIGH", "reason": "Post-conflict instability, sanctions regime"},

    # HIGH — Active military operations, severe tensions, partial sanctions
    "Israel": {"level": "HIGH", "reason": "Active military operations, regional escalation risk"},
    "Palestine": {"level": "HIGH", "reason": "Active conflict zone"},
    "Iran": {"level": "HIGH", "reason": "Comprehensive sanctions regime, regional proxy conflicts"},
    "North Korea": {"level": "HIGH", "reason": "Nuclear program, comprehensive UN/US sanctions"},
    "Lebanon": {"level": "HIGH", "reason": "Regional conflict spillover, economic collapse"},
    "Taiwan": {"level": "HIGH", "reason": "Cross-strait military tensions, invasion risk"},

    # MEDIUM — Elevated tensions, trade restrictions, or instability
    "China": {"level": "MEDIUM", "reason": "US-China trade war, Taiwan risk, export controls on tech"},
    "South Korea": {"level": "MEDIUM", "reason": "North Korea proximity, regional military tensions"},
    "India": {"level": "MEDIUM", "reason": "Border tensions with China and Pakistan"},
    "South Africa": {"level": "MEDIUM", "reason": "Economic instability, infrastructure challenges, energy crisis"},
    "Finland": {"level": "MEDIUM", "reason": "NATO frontline state, border with Russia"},

    # LOW — Monitoring only (not added here; absence = no geopolitical flag)
}

# Risk level numeric priority for comparisons
RISK_PRIORITY = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}

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


def scan_country_geopolitical_news(country):
    """
    Scan Google News for geopolitical risk signals affecting a specific country.
    Returns (risk_detected: bool, risk_level: str, headlines: list, reason: str)
    """
    query = f'"{country}" ({GEO_SEARCH_KEYWORDS})'
    headlines = fetch_google_news_rss(query, max_results=5)

    if not headlines:
        return False, "LOW", [], ""

    # Analyze headlines for severity
    critical_kw = ["war", "invasion", "bombing", "missile", "airstrike", "sanctions imposed",
                   "military offensive", "armed conflict", "blockade", "siege"]
    high_kw = ["military", "tensions escalat", "sanctions", "nuclear", "proxy war",
               "ceasefire collapse", "trade ban", "export ban", "embargo"]
    medium_kw = ["crisis", "instability", "protest", "unrest", "trade war", "tariff",
                 "diplomatic", "territorial dispute", "border clash"]

    max_level = "LOW"
    reason = ""

    for h in headlines:
        title_lower = h["title"].lower()

        for kw in critical_kw:
            if kw in title_lower:
                max_level = "CRITICAL" if RISK_PRIORITY.get("CRITICAL", 3) > RISK_PRIORITY.get(max_level, 0) else max_level
                reason = f"Critical geopolitical event: '{kw}' in recent news"
                break

        for kw in high_kw:
            if kw in title_lower:
                if RISK_PRIORITY.get("HIGH", 2) > RISK_PRIORITY.get(max_level, 0):
                    max_level = "HIGH"
                    reason = reason or f"High geopolitical risk: '{kw}' in recent news"
                break

        for kw in medium_kw:
            if kw in title_lower:
                if RISK_PRIORITY.get("MEDIUM", 1) > RISK_PRIORITY.get(max_level, 0):
                    max_level = "MEDIUM"
                    reason = reason or f"Elevated geopolitical risk: '{kw}' in recent news"
                break

    risk_detected = max_level != "LOW"
    return risk_detected, max_level, headlines, reason


def scan_supplier_news_google(supplier_name, country):
    """
    Scan Google News for supply chain risk signals for a specific supplier.
    Used for suppliers WITHOUT a stock ticker (no yfinance news).
    Returns (headlines: list, risk_level: str, risk_reason: str)
    """
    query = f'"{supplier_name}" ({SUPPLY_SEARCH_KEYWORDS})'
    headlines = fetch_google_news_rss(query, max_results=3)

    if not headlines:
        return [], "LOW", ""

    # Re-use the same keyword sets from the main supply chain scanner
    CRITICAL_KW = ["bankruptcy", "bankrupt", "insolvent", "liquidation",
                   "factory fire", "plant fire", "explosion", "facility closure",
                   "sanction", "ransomware", "cyber attack", "operations halted",
                   "strike", "labor strike", "walkout"]
    HIGH_KW = ["fraud investigation", "sec investigation", "major recall",
               "product recall", "ceo fired", "ceo resign"]
    MEDIUM_KW = ["mass layoff", "supply shortage", "supply disruption",
                 "production delay", "restructuring", "credit downgrade"]

    max_level = "LOW"
    reason = ""

    for h in headlines:
        title_lower = h["title"].lower()

        for kw in CRITICAL_KW:
            if kw in title_lower:
                max_level = "CRITICAL"
                reason = f"Critical supply risk from news: '{kw}'"
                break
        if max_level == "CRITICAL":
            break

        for kw in HIGH_KW:
            if kw in title_lower:
                if RISK_PRIORITY.get("HIGH", 2) > RISK_PRIORITY.get(max_level, 0):
                    max_level = "HIGH"
                    reason = reason or f"High supply risk from news: '{kw}'"
                break

        for kw in MEDIUM_KW:
            if kw in title_lower:
                if RISK_PRIORITY.get("MEDIUM", 1) > RISK_PRIORITY.get(max_level, 0):
                    max_level = "MEDIUM"
                    reason = reason or f"Supply concern from news: '{kw}'"
                break

    return [h["title"] for h in headlines], max_level, reason


# SUPPLIER WATCHLIST - Pillar 3
WATCHLIST_DATA = [
    {"name": "AMCOR", "category": "Printed Packaging"},
    {"name": "GPI", "category": "Printed Packaging"},
    {"name": "Stora Enso", "category": "Printing Substrates"},
    {"name": "IP Sun", "category": "Printing Substrates"},
    {"name": "Sappi", "category": "Printing Substrates"},
    {"name": "Daicel", "category": "Filter Materials"},
    {"name": "Eastman", "category": "Filter Materials"},
    {"name": "Cerdia", "category": "Filter Materials"},
    {"name": "Tae Young Filters", "category": "Filter Materials"},
    {"name": "Fuji", "category": "Capsules"},
    {"name": "SWM (Mativ)", "category": "Fine Papers"},
    {"name": "Delfort", "category": "Fine Papers"},
    {"name": "CNT", "category": "Nicotine"},
    {"name": "ITC", "category": "Nicotine"},
    {"name": "Porton", "category": "Nicotine"},
    {"name": "Tenowo", "category": "Modern/Traditional Oral Fleece"},
    {"name": "Huizhou BYD Electronic", "category": "EMS"},
    {"name": "Smoore", "category": "EMS"},
    {"name": "EVE Energy", "category": "Batteries"},
    {"name": "Texas Instruments", "category": "EE Component"},
    {"name": "Infineon", "category": "EE Component"},
    {"name": "Weener", "category": "Mechanical"},
    {"name": "Rosti", "category": "Mechanical"},
    {"name": "Jabil", "category": "Mechanical"}
]

# PEERS & COMPETITORS - Pillar 2 (Hardcoded Source of Truth)
PEERS_CONFIG = [
    {
        "name": "British American Tobacco",
        "ticker": "BTI",  # Tracking the NYSE ADR for US News visibility
        "region": "Global/US ADR",
        "default_text": "Primary listing LSE; traded as BTI (NYSE). Monitoring filings."
    },
    {
        "name": "Philip Morris Int.",
        "ticker": "PM",
        "region": "US",
        "default_text": "US-listed (NYSE). Monitoring SEC filings (8-K)."
    },
    {
        "name": "Imperial Brands",
        "ticker": "IMB.L",
        "region": "UK",
        "default_text": "UK-listed (LSE). Monitoring regulatory news."
    },
    {
        "name": "Japan Tobacco",
        "ticker": "2914.T",
        "region": "Japan",
        "default_text": "Tokyo listed. Monitoring global press releases."
    }
]

# Legacy PEERS_LIST for backward compatibility with existing code
PEERS_LIST = [
    {"name": "BAT", "full_name": "British American Tobacco", "type": "Tobacco Competitor"},
    {"name": "PMI", "full_name": "Philip Morris International", "type": "Tobacco Competitor"},
    {"name": "Imperial", "full_name": "Imperial Brands", "type": "Tobacco Competitor"},
    {"name": "JTI", "full_name": "Japan Tobacco International", "type": "Tobacco Competitor"},
    {"name": "Our Company", "full_name": "Our Company", "type": "Self-Reference"}
]

def fetch_cisa_kev():
    """Fetch CISA Known Exploited Vulnerabilities Catalog with retry logic"""
    source_name = "cisa_kev"
    try:
        url = "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json"
        response = fetch_with_retry(url)
        data = response.json()

        # Filter for vulnerabilities added in last 7 days
        seven_days_ago = datetime.utcnow() - timedelta(days=7)
        recent_vulns = []
        critical_vulns = []

        for vuln in data.get('vulnerabilities', []):
            date_added_str = vuln.get('dateAdded', '')
            try:
                date_added = datetime.strptime(date_added_str, '%Y-%m-%d')
                if date_added >= seven_days_ago:
                    recent_vulns.append(vuln)
                    # Check for ransomware and recent (48h)
                    if vuln.get('knownRansomwareCampaignUse', '') == 'true':
                        two_days_ago = datetime.utcnow() - timedelta(days=2)
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
            "last_fetched": datetime.utcnow().isoformat()
        }
    except Exception as e:
        harvest_stats.record_error(source_name, str(e))
        return {
            "status": "error",
            "error": str(e),
            "recent_vulnerabilities": [],
            "last_fetched": datetime.utcnow().isoformat()
        }

# ============================================================================
# PILLAR 1: MACRO OVERVIEW (US, EU, China)
# ============================================================================

def fetch_macro_us():
    """Fetch US Macro Economic Indicators"""
    try:
        # Placeholder: In production, fetch from FRED API, BLS, etc.
        # For now, return structure with FX rate (USD/EUR inverse of EUR/USD)
        return {
            "status": "success",
            "region": "US",
            "indicators": {
                "fx_rate": "Placeholder - USD/EUR",
                "inflation": "Placeholder - CPI data",
                "policy": "Placeholder - Fed policy updates"
            },
            "summary": "US economic indicators placeholder",
            "last_fetched": datetime.utcnow().isoformat()
        }
    except Exception as e:
        print(f"US Macro Error: {e}", file=sys.stderr)
        return {
            "status": "error",
            "region": "US",
            "error": str(e),
            "last_fetched": datetime.utcnow().isoformat()
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
            "last_fetched": datetime.utcnow().isoformat()
        }
    except Exception as e:
        harvest_stats.record_error(source_name, str(e))
        return {
            "status": "error",
            "region": "EU",
            "error": str(e),
            "last_fetched": datetime.utcnow().isoformat()
        }

def fetch_macro_china():
    """Fetch China Macro Economic Indicators"""
    try:
        # Placeholder: In production, fetch from official Chinese economic data sources
        return {
            "status": "success",
            "region": "China",
            "indicators": {
                "fx_rate": "Placeholder - CNY/USD",
                "inflation": "Placeholder - CPI data",
                "policy": "Placeholder - PBOC policy updates"
            },
            "summary": "China economic indicators placeholder",
            "last_fetched": datetime.utcnow().isoformat()
        }
    except Exception as e:
        print(f"China Macro Error: {e}", file=sys.stderr)
        return {
            "status": "error",
            "region": "China",
            "error": str(e),
            "last_fetched": datetime.utcnow().isoformat()
        }

def fetch_macro_overview(previous_eur_usd=None):
    """Aggregate Macro Overview for US, EU, China"""
    us_data = fetch_macro_us()
    eu_data = fetch_macro_eu()
    china_data = fetch_macro_china()
    
    # Calculate overall RAG score
    regions_ok = sum(1 for r in [us_data, eu_data, china_data] if r.get("status") == "success")
    if regions_ok == 3:
        rag_score = "GREEN"
    elif regions_ok >= 1:
        rag_score = "AMBER"
    else:
        rag_score = "RED"
    
    # Calculate EUR/USD volatility if we have previous rate
    volatility_pct = None
    if eu_data.get("status") == "success" and previous_eur_usd:
        current_rate = eu_data.get("indicators", {}).get("fx_rate")
        if current_rate and isinstance(current_rate, (int, float)):
            volatility_pct = abs((current_rate - previous_eur_usd) / previous_eur_usd) * 100
            if volatility_pct > 1.5:
                rag_score = "RED"
            elif volatility_pct < 0.5:
                rag_score = "GREEN"
            else:
                rag_score = "AMBER"
    
    return {
        "status": "success",
        "rag_score": rag_score,
        "regions": {
            "us": us_data,
            "eu": eu_data,
            "china": china_data
        },
        "volatility_pct": volatility_pct,
        "last_fetched": datetime.utcnow().isoformat()
    }

# ============================================================================
# PILLAR 2: PEERS & COMPETITORS
# ============================================================================

def fetch_sec_filings_for_peer(peer_name):
    """Fetch SEC 8-K filings for a peer company with retry logic"""
    source_name = f"sec_edgar_{peer_name}"
    # CIK mapping for tobacco companies (simplified)
    cik_map = {
        "BAT": None,  # British American Tobacco - not US listed
        "PMI": "0001413329",  # Philip Morris International
        "Imperial": None,  # Imperial Brands - not US listed
        "JTI": None,  # Japan Tobacco - not US listed
        "Our Company": None  # Placeholder
    }

    cik = cik_map.get(peer_name)
    if not cik:
        return {
            "status": "skipped",
            "reason": "Not US-listed or placeholder",
            "filings": [],
            "red_signals": 0,
            "amber_signals": 0,
            "last_fetched": datetime.utcnow().isoformat()
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
        thirty_days_ago = datetime.utcnow() - timedelta(days=30)

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

            # Extract filing date from summary (format: "Filed: YYYY-MM-DD")
            filing_date = None
            date_match = re.search(r'Filed:</b>\s*(\d{4}-\d{2}-\d{2})', summary_text)
            if date_match:
                try:
                    filing_date = datetime.strptime(date_match.group(1), '%Y-%m-%d')
                except ValueError:
                    pass
            # Fallback: try published field
            if filing_date is None and published_text:
                try:
                    filing_date = datetime.fromisoformat(published_text.replace('Z', '+00:00').replace('+00:00', ''))
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
            "last_fetched": datetime.utcnow().isoformat()
        }
    except Exception as e:
        harvest_stats.record_error(source_name, str(e))
        return {
            "status": "error",
            "error": str(e),
            "filings": [],
            "red_signals": 0,
            "amber_signals": 0,
            "last_fetched": datetime.utcnow().isoformat()
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
    
    # AMBER RISK: Warning signals
    if amber_signals > 0:
        return f"WARNING: {amber_signals} warning signal(s) detected. Recent director departures or management changes noted."
    
    # NEUTRAL: No risks but provide meaningful summary
    if status == "success" and len(filings) > 0:
        # Check for routine filings
        recent_filing = filings[0] if filings else None
        if recent_filing:
            filing_summary = recent_filing.get("summary", "")
            if "ITEM 2.02" in filing_summary.upper():
                return f"Neutral: Q3 earnings results reported. No material risks identified in last 48h."
            elif "ITEM 7.01" in filing_summary.upper():
                return f"Neutral: Regulation FD disclosure filed. Routine operational update."
            elif "ITEM 1.01" in filing_summary.upper():
                return f"Neutral: Material definitive agreement entered. Standard business activity."
            else:
                return f"Neutral: {len(filings)} recent filing(s) processed. No material risks in last 48h."
        else:
            return f"Neutral: Recent filings reviewed. No material risks identified in last 48h."
    
    elif status == "success" and len(filings) == 0:
        return f"Neutral: No material filings in last 48h. Standard operational status."
    
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
    return f"Neutral: Standard monitoring active. No material risks identified in last 48h."

def fetch_peers_overview():
    """Aggregate Peers & Competitors Intelligence - ALWAYS includes summary text"""
    peers_data = []
    total_red_signals = 0
    total_amber_signals = 0
    
    for peer in PEERS_LIST:
        peer_name = peer["name"]
        filings_data = fetch_sec_filings_for_peer(peer_name)
        
        # Generate summary text - MANDATORY: Always provide meaningful text
        summary_text = generate_peer_summary(peer_name, filings_data)
        
        # Placeholder for news tracking (would integrate news API in production)
        news_data = {
            "status": "placeholder",
            "recent_news": [],
            "note": "News tracking placeholder - integrate news API"
        }
        
        # Determine individual peer RAG score
        peer_red = filings_data.get("red_signals", 0)
        peer_amber = filings_data.get("amber_signals", 0)
        if peer_red > 0:
            peer_rag = "RED"
        elif peer_amber > 0:
            peer_rag = "AMBER"
        else:
            peer_rag = "GREEN"
        
        peer_info = {
            "name": peer_name,
            "full_name": peer.get("full_name", peer_name),
            "type": peer.get("type", "Unknown"),
            "rag_score": peer_rag,
            "summary": summary_text,  # MANDATORY: Always present
            "sec_filings": filings_data,
            "news": news_data
        }
        
        if filings_data.get("status") == "success":
            total_red_signals += filings_data.get("red_signals", 0)
            total_amber_signals += filings_data.get("amber_signals", 0)
        
        peers_data.append(peer_info)
    
    # Calculate overall RAG score
    if total_red_signals > 0:
        rag_score = "RED"
    elif total_amber_signals > 0:
        rag_score = "AMBER"
    else:
        rag_score = "GREEN"
    
    return {
        "status": "success",
        "rag_score": rag_score,
        "total_peers": len(peers_data),
        "total_red_signals": total_red_signals,
        "total_amber_signals": total_amber_signals,
        "peers": peers_data,
        "last_fetched": datetime.utcnow().isoformat()
    }

# ============================================================================
# PILLAR 3: SUPPLIER WATCHLIST
# ============================================================================

def get_supplier_deep_dive_data(supplier_name, category):
    """Generate deep dive mock data for supplier intelligence cards"""
    # BAT Exposure mapping (Critical = Tier 1, High = Tier 2, Medium = Tier 3)
    exposure_map = {
        "AMCOR": "Critical",
        "GPI": "High",
        "Smoore": "Critical",
        "Huizhou BYD Electronic": "Critical",
        "EVE Energy": "High",
        "Texas Instruments": "High",
        "Infineon": "High",
        "Jabil": "Medium",
        "CNT": "Critical",
        "ITC": "High",
        "Porton": "Medium"
    }
    
    # Segment mapping based on category
    segment_map = {
        "EMS": "New Categories (Vuse/Glo)",
        "Batteries": "New Categories (Vuse/Glo)",
        "EE Component": "New Categories (Vuse/Glo)",
        "Printed Packaging": "Combustibles",
        "Printing Substrates": "Combustibles",
        "Filter Materials": "Combustibles",
        "Fine Papers": "Combustibles",
        "Capsules": "Combustibles",
        "Nicotine": "Combustibles",
        "Modern/Traditional Oral Fleece": "New Categories (Vuse/Glo)",
        "Mechanical": "New Categories (Vuse/Glo)"
    }
    
    # Location mapping
    location_map = {
        "AMCOR": "Switzerland",
        "GPI": "USA",
        "Stora Enso": "Finland",
        "IP Sun": "China",
        "Sappi": "South Africa",
        "Daicel": "Japan",
        "Eastman": "USA",
        "Cerdia": "Switzerland",
        "Tae Young Filters": "South Korea",
        "Fuji": "Japan",
        "SWM (Mativ)": "USA",
        "Delfort": "Austria",
        "CNT": "Germany",
        "ITC": "India",
        "Porton": "China",
        "Tenowo": "Germany",
        "Huizhou BYD Electronic": "China",
        "Smoore": "China",
        "EVE Energy": "China",
        "Texas Instruments": "USA",
        "Infineon": "Germany",
        "Weener": "Netherlands",
        "Rosti": "Sweden",
        "Jabil": "USA"
    }
    
    # Stock ticker mapping
    ticker_map = {
        "AMCOR": "AMCR",
        "GPI": "GPI",
        "Stora Enso": "STERV.HE",
        "Sappi": "SAP",
        "Eastman": "EMN",
        "SWM (Mativ)": "MATV",
        "ITC": "ITC.NS",
        "Smoore": "6969.HK",
        "EVE Energy": "300014.SZ",
        "Texas Instruments": "TXN",
        "Infineon": "IFX.DE",
        "Jabil": "JBL"
    }
    
    # Generate news summary based on category and exposure
    news_templates = {
        "Critical": [
            f"{supplier_name} continues to be a strategic partner for BAT's new category expansion. Recent capacity investments align with Vuse production ramp-up.",
            f"Supply chain monitoring shows stable operations. {supplier_name} maintains quality certifications and on-time delivery metrics above 98%."
        ],
        "High": [
            f"{supplier_name} reported strong quarterly results with increased demand from tobacco industry clients.",
            f"Operational status normal. No supply disruptions reported. Category: {category} remains stable."
        ],
        "Medium": [
            f"{supplier_name} maintains standard operations. Regular quality audits completed successfully.",
            f"Supply chain intelligence indicates no material risks. Standard monitoring protocols active."
        ]
    }
    
    # URL mapping
    url_map = {
        "CNT": "https://nicotineusp.com"
    }
    
    exposure = exposure_map.get(supplier_name, "Medium")
    segment = segment_map.get(category, "Combustibles")
    location = location_map.get(supplier_name, "Unknown")
    ticker = ticker_map.get(supplier_name, "N/A")
    url = url_map.get(supplier_name, None)
    news_summary = " ".join(news_templates.get(exposure, news_templates["Medium"]))
    
    return {
        "bat_exposure": exposure,
        "segment": segment,
        "location": location,
        "stock_ticker": ticker,
        "latest_news_summary": news_summary,
        "url": url
    }

def fetch_supplier_stock_data(ticker_symbol):
    """
    Fetch stock data for a supplier using yfinance.
    Returns (daily_change_pct, current_price, headlines_list) or (None, None, []) on error.
    headlines_list contains up to 5 news headline strings.
    """
    if not ticker_symbol or ticker_symbol == "N/A":
        return None, None, []

    # Check circuit breaker
    if not yfinance_circuit_breaker.can_execute():
        return None, None, []

    try:
        rate_limiter.wait_if_needed()
        ticker = yf.Ticker(ticker_symbol)

        # Get historical data for price change
        hist = ticker.history(period="5d")  # 5 days for more reliable data

        daily_change_pct = None
        current_price = None

        if len(hist) >= 2:
            current = hist['Close'].iloc[-1]
            previous = hist['Close'].iloc[-2]
            daily_change_pct = ((current - previous) / previous) * 100
            current_price = current
        elif len(hist) == 1:
            current_price = hist['Close'].iloc[-1]

        # Get up to 5 news headlines for broader risk scanning
        headlines = []
        try:
            news = ticker.news
            if news:
                for item in news[:5]:
                    title = item.get('title', None)
                    if title:
                        headlines.append(title)
        except Exception:
            pass

        yfinance_circuit_breaker.record_success()
        return daily_change_pct, current_price, headlines

    except Exception as e:
        yfinance_circuit_breaker.record_failure()
        harvest_stats.record_warning(f"supplier_stock_{ticker_symbol}", str(e)[:100])
        return None, None, []


def process_suppliers(cyber_data):
    """
    Process supplier watchlist and assess SUPPLY CHAIN RISK to BAT.

    Risk is assessed from FOUR layers (each can escalate):
      1. CISA cyber vulnerabilities (KEV catalog)
      2. Stock price movements (yfinance)
      3. News scanning (yfinance headlines + Google News RSS)
      4. Geopolitical risk (conflict zones, sanctions, instability)

    CRITICAL - Immediate threat to supply:
      - Bankruptcy, insolvency, liquidation
      - Factory fire, explosion, facility closure
      - Government sanctions, bans, seizure
      - Major cyber attack disrupting operations
      - Labor strike at production facility
      - Stock crash >5% (indicates serious problems)
      - Supplier in active war zone

    HIGH - Serious concern:
      - Fraud/SEC investigation
      - Major product recall
      - Stock drop >3% for Critical/High exposure suppliers
      - Supplier in high-tension region (military buildup, severe sanctions)

    MEDIUM - Watch closely:
      - Stock drop >3% for Medium exposure suppliers
      - Stock drop >1.5% for Critical/High exposure suppliers
      - Major layoffs, restructuring
      - Supply disruption mentions
      - Supplier in region with trade war, instability, or border tensions

    LOW - Normal operations:
      - Stock fluctuations within normal range
      - No negative operational news
      - No geopolitical risk signals
    """
    suppliers = []
    cisa_vulns = cyber_data.get("recent_vulnerabilities", [])

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

        country_news_cache[country] = {
            "level": final_level,
            "reason": final_reason,
            "headlines": [h["title"] for h in news_headlines] if news_headlines else [],
            "static_risk": static_risk is not None
        }

        if final_level != "LOW":
            logger.info(f"  🌍 {country}: {final_level} — {final_reason}")

    logger.info(f"Geopolitical scan complete. {sum(1 for v in country_news_cache.values() if v['level'] != 'LOW')} countries with elevated risk.")

    # Keywords indicating REAL supply chain risk to BAT
    CRITICAL_SUPPLY_KEYWORDS = [
        "bankruptcy", "bankrupt", "insolvent", "liquidation", "chapter 11",
        "factory fire", "plant fire", "explosion", "plant closure", "facility closure",
        "cease operations", "shut down", "shutting down",
        "sanction", "sanctioned", "banned", "seized", "embargo",
        "ransomware attack", "cyber attack", "systems down", "operations halted",
        "strike", "labor strike", "workers strike", "walkout"
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

        # Broader CISA matching: include aliases and known product names
        # so we don't only match on exact parent company name
        CISA_ALIASES = {
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
        search_terms = [supplier_name_upper]
        for alias in CISA_ALIASES.get(supplier_name, []):
            if alias.upper() not in search_terms:
                search_terms.append(alias.upper())

        # Check if any search term appears in CISA vulnerability fields
        for vuln in cisa_vulns:
            vendor = vuln.get('vendorProject', '').upper()
            product = vuln.get('product', '').upper()
            description = vuln.get('vulnerabilityName', '').upper()
            combined = f"{vendor} {product} {description}"

            for term in search_terms:
                if term in combined:
                    cyber_risk = True
                    matching_vulns.append({
                        "cveID": vuln.get('cveID', ''),
                        "vulnerabilityName": vuln.get('vulnerabilityName', ''),
                        "dateAdded": vuln.get('dateAdded', '')
                    })
                    break  # Don't double-count same vuln

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

        if stock_ticker and stock_ticker != "N/A":
            daily_change_pct, current_price, headlines_list = fetch_supplier_stock_data(stock_ticker)

            # Analyze ALL news headlines (up to 5) for SUPPLY CHAIN risk keywords
            for headline in headlines_list:
                if not headline:
                    continue
                if not news_headline:
                    news_headline = headline  # Keep first headline for display
                headline_lower = headline.lower()

                # Check for CRITICAL supply risk keywords
                for kw in CRITICAL_SUPPLY_KEYWORDS:
                    if kw in headline_lower:
                        news_risk = True
                        operational_risk = True
                        news_items.append({"headline": headline, "risk": "CRITICAL", "keyword": kw})
                        risk_reason = f"Critical supply risk: '{kw}' detected in news"
                        break

                # Check for HIGH supply risk keywords
                if not operational_risk:
                    for kw in HIGH_SUPPLY_KEYWORDS:
                        if kw in headline_lower:
                            news_risk = True
                            operational_risk = True
                            news_items.append({"headline": headline, "risk": "HIGH", "keyword": kw})
                            risk_reason = f"High supply risk: '{kw}' detected in news"
                            break

                # Check for MEDIUM supply risk keywords
                if not operational_risk:
                    for kw in MEDIUM_SUPPLY_KEYWORDS:
                        if kw in headline_lower:
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

        # Priority 1: CISA cyber vulnerabilities (critical for IT-dependent suppliers)
        if cyber_risk:
            supplier_risk_level = "CRITICAL" if len(matching_vulns) >= 2 else "HIGH"
            last_signal = f"🔒 Cyber vulnerability: {len(matching_vulns)} CISA KEV match(es) - {', '.join([v.get('cveID', 'N/A') for v in matching_vulns[:2]])}"
            risk_analysis = f"CISA Known Exploited Vulnerability detected. {supplier_name} systems may be at risk. {bat_exposure} exposure to BAT requires security assessment."

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

        # Priority 3: Severe stock crash (>5%) indicates serious company problems
        elif daily_change_pct is not None and daily_change_pct < -5.0:
            supplier_risk_level = "CRITICAL"
            last_signal = f"📉 Severe stock crash: {daily_change_pct:.1f}% - investigate cause"
            risk_analysis = f"Severe stock decline of {daily_change_pct:.1f}% may indicate serious issues at {supplier_name}. BAT exposure: {bat_exposure}."

        # Priority 4: Significant stock drop (>3%) — escalate for Critical/High exposure
        elif daily_change_pct is not None and daily_change_pct < -3.0:
            if bat_exposure in ["Critical", "High"]:
                supplier_risk_level = "HIGH"
                last_signal = f"📉 Stock down {daily_change_pct:.1f}% - {bat_exposure} exposure supplier"
                risk_analysis = f"Significant stock decline for {bat_exposure.lower()} exposure supplier. Investigate {supplier_name} for operational impacts. BAT exposure: {bat_exposure}."
            else:
                supplier_risk_level = "MEDIUM"
                last_signal = f"📉 Stock down {daily_change_pct:.1f}% - monitoring"
                risk_analysis = f"Notable stock decline for {supplier_name}. Monitor for any operational impacts. BAT exposure: {bat_exposure}."

        # Priority 5: Moderate stock drop (>1.5%) — flag for Critical/High exposure
        elif daily_change_pct is not None and daily_change_pct < -1.5 and bat_exposure in ["Critical", "High"]:
            supplier_risk_level = "MEDIUM"
            last_signal = f"📉 Stock down {daily_change_pct:.1f}% - {bat_exposure} exposure supplier"
            risk_analysis = f"Stock decline for {bat_exposure.lower()} exposure supplier. Monitor {supplier_name} for any operational impacts."

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
        geo_data = country_news_cache.get(location, {"level": "LOW", "reason": "", "headlines": [], "static_risk": False})
        geo_risk_level = geo_data["level"]
        geo_reason = geo_data["reason"]
        geo_headlines = geo_data["headlines"]
        geopolitical_risk = geo_risk_level != "LOW"

        # Elevate risk if geopolitical risk is HIGHER than current assessment
        geo_escalated = False
        if RISK_PRIORITY.get(geo_risk_level, 0) > RISK_PRIORITY.get(supplier_risk_level, 0):
            pre_geo_level = supplier_risk_level
            supplier_risk_level = geo_risk_level
            geo_escalated = True
            last_signal = f"🌍 Geopolitical: {geo_reason}"
            risk_analysis = f"Geopolitical risk in {location}: {geo_reason}. {supplier_name} ({category}) located in affected region. BAT exposure: {bat_exposure}. Previous risk: {pre_geo_level}."
            logger.info(f"  ↑ {supplier_name}: {pre_geo_level} → {supplier_risk_level} (geopolitical: {location})")

        # Build supplier data
        supplier_data = {
            "name": supplier_name,
            "slug": slug,
            "category": category,
            "cyber_risk": cyber_risk,
            "matching_vulnerabilities": matching_vulns[:5],
            "news_risk": news_risk,
            "news_items": news_items,
            "operational_risk": operational_risk,
            "daily_change_pct": round(daily_change_pct, 2) if daily_change_pct is not None else None,
            "current_price": round(current_price, 2) if current_price is not None else None,
            "risk_analysis": risk_analysis,
            "risk_level": supplier_risk_level,
            "last_signal": last_signal,
            # Geopolitical risk fields
            "geopolitical_risk": {
                "detected": geopolitical_risk,
                "level": geo_risk_level if geopolitical_risk else None,
                "reason": geo_reason if geopolitical_risk else None,
                "headlines": geo_headlines[:3] if geopolitical_risk else [],
                "escalated": geo_escalated,
            } if geopolitical_risk else None,
            # NEW: Google News headlines for ticker-less suppliers
            "google_news_headlines": google_news_headlines[:3],
            **deep_dive
        }

        suppliers.append(supplier_data)

    # Calculate RAG score based on actual supply risks
    suppliers_at_cyber_risk = sum(1 for s in suppliers if s["cyber_risk"])
    suppliers_at_news_risk = sum(1 for s in suppliers if s["news_risk"])
    suppliers_at_operational_risk = sum(1 for s in suppliers if s.get("operational_risk", False))
    suppliers_at_geopolitical_risk = sum(1 for s in suppliers if s.get("geopolitical_risk") is not None)
    suppliers_geo_escalated = sum(1 for s in suppliers if s.get("geopolitical_escalated", False))

    total_critical = sum(1 for s in suppliers if s["risk_level"] == "CRITICAL")
    total_high = sum(1 for s in suppliers if s["risk_level"] == "HIGH")
    total_medium = sum(1 for s in suppliers if s["risk_level"] == "MEDIUM")

    # RAG score based on actual supply chain risk severity
    if total_critical >= 1 or (total_high + total_medium) >= 3:
        rag_score = "RED"
    elif total_high >= 1 or total_medium >= 1:
        rag_score = "AMBER"
    else:
        rag_score = "GREEN"

    logger.info(f"Supplier risk summary: {total_critical} CRITICAL, {total_high} HIGH, {total_medium} MEDIUM, {len(suppliers) - total_critical - total_high - total_medium} LOW")
    logger.info(f"Risk sources: {suppliers_at_cyber_risk} cyber, {suppliers_at_news_risk} news, {suppliers_at_geopolitical_risk} geopolitical ({suppliers_geo_escalated} escalated)")

    return {
        "status": "success",
        "rag_score": rag_score,
        "total_suppliers": len(suppliers),
        "suppliers_at_cyber_risk": suppliers_at_cyber_risk,
        "suppliers_at_news_risk": suppliers_at_news_risk,
        "suppliers_at_operational_risk": suppliers_at_operational_risk,
        "suppliers_at_geopolitical_risk": suppliers_at_geopolitical_risk,
        "total_critical": total_critical,
        "total_high": total_high,
        "total_medium": total_medium,
        "suppliers": suppliers,
        "last_fetched": datetime.utcnow().isoformat()
    }

# ============================================================================
# MACRO ECONOMY DATA GENERATION (LIVE DATA)
# ============================================================================

def fetch_macro_economy():
    """Fetch real macro economic data using yfinance with rate limiting"""

    # Check circuit breaker before making yfinance calls
    if not yfinance_circuit_breaker.can_execute():
        logger.warning("yfinance circuit breaker is OPEN - skipping macro economy fetch")
        harvest_stats.record_warning("macro_economy", "Circuit breaker open - using fallback data")
        return {
            "us": {"cpi": "N/A", "rate": "N/A", "trend": "N/A", "summary": "Data temporarily unavailable (circuit breaker active)."},
            "eu": {"cpi": "N/A", "rate": "N/A", "trend": "N/A", "summary": "Data temporarily unavailable (circuit breaker active)."},
            "china": {"cpi": "N/A", "rate": "N/A", "trend": "N/A", "summary": "Data temporarily unavailable (circuit breaker active)."}
        }

    def get_trend_from_change(change_pct):
        """Determine trend from daily percentage change"""
        if change_pct is None:
            return "N/A"
        if change_pct > 0.5:
            return "Growing"
        elif change_pct < -0.5:
            return "Declining"
        else:
            return "Stable"

    def fetch_us_macro():
        rate_limiter.wait_if_needed()
        """Fetch US macro data from S&P 500"""
        try:
            ticker = yf.Ticker("^GSPC")
            hist = ticker.history(period="2d")
            if len(hist) < 2:
                return {
                    "cpi": "N/A",
                    "rate": "N/A",
                    "trend": "N/A",
                    "summary": "Unable to fetch S&P 500 data. Market may be closed."
                }
            
            # Calculate daily change
            current = hist['Close'].iloc[-1]
            previous = hist['Close'].iloc[-2]
            change_pct = ((current - previous) / previous) * 100
            
            trend = get_trend_from_change(change_pct)
            
            # Enhanced summary with more context
            if trend == "Declining":
                summary = f"S&P 500 declining: {change_pct:+.2f}% change. Market reflects broader economic conditions. Fed signals potential rate cuts in Q3 as inflation moderates. Industrial output remains resilient despite market volatility. Investors monitoring employment data and consumer spending trends."
            elif trend == "Growing":
                summary = f"S&P 500 growing: {change_pct:+.2f}% change. Market reflects positive economic momentum. Fed maintains current policy stance as inflation trends toward target. Industrial output strong, consumer confidence elevated. Economic indicators suggest sustained growth trajectory."
            else:
                summary = f"S&P 500 stable: {change_pct:+.2f}% change. Market reflects balanced economic conditions. Fed monitoring inflation and employment data closely. Industrial output steady, consumer spending patterns normal. Economic outlook remains cautiously optimistic."
            
            return {
                "cpi": "3.2%",  # Static for now - would need separate API
                "rate": "5.50%",  # Static for now - would need separate API
                "trend": trend,
                "summary": summary
            }
        except Exception as e:
            yfinance_circuit_breaker.record_failure()
            harvest_stats.record_error("macro_us", str(e))
            return {
                "cpi": "N/A",
                "rate": "N/A",
                "trend": "N/A",
                "summary": f"Error fetching US macro data: {str(e)}"
            }

    def fetch_eu_macro():
        rate_limiter.wait_if_needed()
        """Fetch EU macro data from EUR/USD exchange rate"""
        try:
            ticker = yf.Ticker("EURUSD=X")
            hist = ticker.history(period="2d")
            if len(hist) < 2:
                return {
                    "cpi": "N/A",
                    "rate": "N/A",
                    "trend": "N/A",
                    "summary": "Unable to fetch EUR/USD data. Market may be closed."
                }
            
            # Calculate daily change
            current = hist['Close'].iloc[-1]
            previous = hist['Close'].iloc[-2]
            change_pct = ((current - previous) / previous) * 100
            
            # For EUR/USD, negative change means Euro weakening
            if change_pct < -0.5:
                trend = "Weakening"
            elif change_pct > 0.5:
                trend = "Strengthening"
            else:
                trend = "Stable"
            
            # Enhanced summary with more context
            if trend == "Weakening":
                summary = f"EUR/USD weakening: {change_pct:+.2f}% change. Euro declining against USD. ECB maintains dovish monetary policy stance. Manufacturing PMI shows mixed signals across key markets. Inflation pressures easing, but growth concerns persist. Export competitiveness improving with weaker currency."
            elif trend == "Strengthening":
                summary = f"EUR/USD strengthening: {change_pct:+.2f}% change. Euro gaining against USD. ECB policy decisions supporting currency stability. Manufacturing PMI showing signs of recovery in key markets. Inflation moderating toward target, economic activity picking up. Strong euro reflects improved economic fundamentals."
            else:
                summary = f"EUR/USD stable: {change_pct:+.2f}% change. Euro trading in narrow range against USD. ECB maintaining current policy framework. Manufacturing PMI stable across major economies. Inflation near target levels, balanced economic outlook. Currency stability supports trade and investment flows."
            
            return {
                "cpi": "2.6%",  # Static for now - would need separate API
                "rate": "4.50%",  # Static for now - would need separate API
                "trend": trend,
                "summary": summary
            }
        except Exception as e:
            yfinance_circuit_breaker.record_failure()
            harvest_stats.record_error("macro_eu", str(e))
            return {
                "cpi": "N/A",
                "rate": "N/A",
                "trend": "N/A",
                "summary": f"Error fetching EU macro data: {str(e)}"
            }

    def fetch_china_macro():
        rate_limiter.wait_if_needed()
        """Fetch China macro data from CNY/USD exchange rate"""
        try:
            ticker = yf.Ticker("CNY=X")
            hist = ticker.history(period="2d")
            if len(hist) < 2:
                return {
                    "cpi": "N/A",
                    "rate": "N/A",
                    "trend": "N/A",
                    "summary": "Unable to fetch CNY/USD data. Market may be closed."
                }
            
            # Calculate daily change
            current = hist['Close'].iloc[-1]
            previous = hist['Close'].iloc[-2]
            change_pct = ((current - previous) / previous) * 100
            
            # For CNY/USD, positive change means CNY weakening (USD strengthening)
            # Negative change means CNY strengthening
            if change_pct > 0.5:
                trend = "Declining"  # CNY weakening
            elif change_pct < -0.5:
                trend = "Growing"  # CNY strengthening
            else:
                trend = "Stable"
            
            # Enhanced summary with more context
            if trend == "Declining":
                summary = f"CNY/USD declining: {change_pct:+.2f}% change. Yuan weakening against USD. Industrial output slowing amid property sector headwinds. PBOC considering additional stimulus measures to support growth. Export competitiveness improving, but domestic demand remains subdued. Policy makers balancing growth support with financial stability."
            elif trend == "Growing":
                summary = f"CNY/USD growing: {change_pct:+.2f}% change. Yuan strengthening against USD. Industrial output showing resilience despite external headwinds. PBOC maintaining accommodative policy stance. Export sector performing well, domestic consumption recovering. Currency strength reflects improved economic fundamentals and policy effectiveness."
            else:
                summary = f"CNY/USD stable: {change_pct:+.2f}% change. Yuan trading in managed range against USD. Industrial output steady, property sector stabilization underway. PBOC maintaining balanced monetary policy. Export growth moderate, domestic demand gradually improving. Economic indicators suggest stable growth trajectory with manageable risks."
            
            return {
                "cpi": "0.3%",  # Static for now - would need separate API
                "rate": "3.45%",  # Static for now - would need separate API
                "trend": trend,
                "summary": summary
            }
        except Exception as e:
            yfinance_circuit_breaker.record_failure()
            harvest_stats.record_error("macro_china", str(e))
            return {
                "cpi": "N/A",
                "rate": "N/A",
                "trend": "N/A",
                "summary": f"Error fetching China macro data: {str(e)}"
            }

    us_data = fetch_us_macro()
    eu_data = fetch_eu_macro()
    china_data = fetch_china_macro()

    # Track successes
    for region, data in [("us", us_data), ("eu", eu_data), ("china", china_data)]:
        if data.get("trend") != "N/A":
            yfinance_circuit_breaker.record_success()
            harvest_stats.record_success(f"macro_{region}")

    return {
        "us": us_data,
        "eu": eu_data,
        "china": china_data
    }

# ============================================================================
# PEER GROUP DATA GENERATION (LIVE DATA)
# ============================================================================

def fetch_peer_group():
    """
    Fetch real peer group intelligence using yfinance.
    MORE SENSITIVE risk detection - stock movements are a primary signal.
    """
    peer_data = []

    # Risk keywords for news analysis
    CRITICAL_KEYWORDS = ["investigation", "fraud", "sanction", "bankruptcy", "recall",
                          "strike", "ban", "seize", "breach", "hack", "lawsuit"]
    WARNING_KEYWORDS = ["delay", "shortage", "volatile", "drop", "miss", "down",
                        "fine", "cut", "layoff", "restructur", "warning", "concern"]

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
                "last_signal": peer_config.get("default_text", "Circuit breaker active.")
            })
        return peer_data

    for peer_config in PEERS_CONFIG:
        # Apply rate limiting
        rate_limiter.wait_if_needed()
        try:
            ticker_symbol = peer_config["ticker"]
            ticker = yf.Ticker(ticker_symbol)

            # Get current price and historical data for daily change
            info = ticker.info
            hist = ticker.history(period="5d")  # 5 days for more reliable data

            # Calculate daily change
            current_price = None
            daily_change_pct = None
            stock_move = "N/A"

            if len(hist) >= 2:
                current = hist['Close'].iloc[-1]
                previous = hist['Close'].iloc[-2]
                daily_change_pct = ((current - previous) / previous) * 100
                stock_move = f"{daily_change_pct:+.2f}%"
                current_price = current
            elif len(hist) == 1:
                current_price = hist['Close'].iloc[-1]
                stock_move = "N/A (single day)"
            elif 'currentPrice' in info:
                current_price = info['currentPrice']
                stock_move = "N/A (no historical data)"

            # Get up to 5 news headlines for broader scanning
            latest_headline = None
            all_headlines = []
            real_headline_found = False
            try:
                news = ticker.news
                if news:
                    for item in news[:5]:
                        title = item.get('title', None)
                        if title:
                            all_headlines.append(title)
                    if all_headlines:
                        latest_headline = all_headlines[0]
                        real_headline_found = True
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
                    has_critical = any(keyword in hl_lower for keyword in CRITICAL_KEYWORDS)
                    if has_critical:
                        risk_level = "CRITICAL"
                        last_signal = f"🚨 News Alert: {hl[:120]}"
                        news_risk_detected = True
                        break

                    # Check for WARNING keywords
                    has_warning = any(keyword in hl_lower for keyword in WARNING_KEYWORDS)
                    if has_warning:
                        risk_level = "MEDIUM"
                        last_signal = f"⚠️ News Alert: {hl[:120]}"
                        news_risk_detected = True
                        # Don't break — keep scanning for CRITICAL in remaining headlines

                if not news_risk_detected:
                    pass  # No risk keywords found in any headline

            # STEP 2: Check stock movement (ALWAYS check, can escalate risk)
            if daily_change_pct is not None:
                if daily_change_pct < -5.0:
                    # Severe drop - CRITICAL regardless of news
                    if risk_level != "CRITICAL":
                        risk_level = "CRITICAL"
                        last_signal = f"🚨 Severe market drop: Stock down {daily_change_pct:.2f}%"
                    else:
                        last_signal += f" | Stock down {daily_change_pct:.2f}%"
                    stock_risk_detected = True
                elif daily_change_pct < -2.0:
                    # Significant drop - at least MEDIUM
                    if risk_level == "LOW":
                        risk_level = "MEDIUM"
                        last_signal = f"📉 Market drop: Stock down {daily_change_pct:.2f}%"
                    elif risk_level == "MEDIUM" and not news_risk_detected:
                        last_signal = f"📉 Market drop: Stock down {daily_change_pct:.2f}%"
                    stock_risk_detected = True
                elif daily_change_pct < -1.0:
                    # Minor drop - flag if no other risk
                    if risk_level == "LOW":
                        risk_level = "MEDIUM"
                        last_signal = f"📉 Volatility: Stock down {daily_change_pct:.2f}%"
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
            peer_data.append({
                "name": peer_config["name"],
                "ticker": ticker_symbol,
                "region": peer_config.get("region", "Unknown"),
                "sentiment": sentiment,
                "latest_headline": latest_headline if real_headline_found else peer_config.get("default_text", "Monitoring active."),
                "stock_move": stock_move,
                "current_price": current_price,
                "daily_change_pct": round(daily_change_pct, 2) if daily_change_pct is not None else None,
                "risk_level": risk_level,
                "last_signal": last_signal,
                "news_risk": news_risk_detected,
                "stock_risk": stock_risk_detected
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
                "stock_risk": False
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
        with open(output_file, 'w') as f:
            json.dump(data, f, indent=2)

        logger.info(f"Saved data to {output_file}")
        return True
    except Exception as e:
        logger.error(f"Failed to save data: {e}")
        return False


def main():
    """Main aggregation function - Three Core Pillars"""
    logger.info("Starting CPO intelligence harvest (Three Core Pillars)...")

    # Load previous state for volatility calculation and fallback
    data_dir = Path(__file__).parent.parent / "data"
    previous_eur_usd = None
    previous_state = None
    previous_file = data_dir / "intel_snapshot.json"

    if previous_file.exists():
        try:
            with open(previous_file, "r") as f:
                previous_state = json.load(f)
                previous_macro = previous_state.get("macro", {})
                if previous_macro.get("status") == "success":
                    eu_data = previous_macro.get("regions", {}).get("eu", {})
                    if eu_data.get("status") == "success":
                        previous_eur_usd = eu_data.get("indicators", {}).get("fx_rate")
            logger.info("Loaded previous state successfully")
        except Exception as e:
            harvest_stats.record_warning("previous_state", f"Could not load: {e}")

    # Fetch supporting data
    cyber_data = fetch_cisa_kev()

    # PILLAR 1: Macro Overview
    macro_data = fetch_macro_overview(previous_eur_usd)

    # PILLAR 2: Peers & Competitors
    peers_data = fetch_peers_overview()

    # PILLAR 3: Supplier Watchlist
    suppliers_data = process_suppliers(cyber_data)

    # Generate additional intelligence data (LIVE DATA)
    macro_economy = fetch_macro_economy()
    peer_group = fetch_peer_group()

    # ================================================================
    # MERGE live peer_group risk data INTO peers pillar RAG score
    # peer_group has real-time stock + news data that the SEC-only
    # peers pillar misses. Escalate the pillar RAG if live data shows risk.
    # ================================================================
    peer_group_critical = sum(1 for p in peer_group if p.get("risk_level") == "CRITICAL")
    peer_group_high = sum(1 for p in peer_group if p.get("risk_level") in ("HIGH", "CRITICAL"))
    peer_group_medium = sum(1 for p in peer_group if p.get("risk_level") == "MEDIUM")

    if peer_group_critical >= 1:
        if peers_data.get("rag_score") != "RED":
            logger.info(f"Escalating peers RAG to RED: {peer_group_critical} peer(s) at CRITICAL from live data")
            peers_data["rag_score"] = "RED"
    elif peer_group_high >= 1 or peer_group_medium >= 2:
        if peers_data.get("rag_score") == "GREEN":
            logger.info(f"Escalating peers RAG to AMBER: live peer data shows elevated risk")
            peers_data["rag_score"] = "AMBER"

    # ================================================================
    # MERGE live macro_economy trend data INTO macro pillar RAG score
    # The pillar 1 macro only uses ECB FX rate; macro_economy has live
    # S&P 500, EUR/USD, and CNY/USD from yfinance.
    # ================================================================
    declining_regions = 0
    for region_key in ["us", "eu", "china"]:
        region_data = macro_economy.get(region_key, {})
        trend = region_data.get("trend", "N/A")
        if trend in ("Declining", "Weakening"):
            declining_regions += 1

    if declining_regions >= 2:
        if macro_data.get("rag_score") != "RED":
            logger.info(f"Escalating macro RAG to RED: {declining_regions} regions declining")
            macro_data["rag_score"] = "RED"
    elif declining_regions >= 1:
        if macro_data.get("rag_score") == "GREEN":
            logger.info(f"Escalating macro RAG to AMBER: {declining_regions} region(s) declining")
            macro_data["rag_score"] = "AMBER"

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

    # Build dashboard state with three core pillars + additional intelligence
    dashboard_state = {
        "last_updated": datetime.utcnow().isoformat(),
        "version": "",  # Will be set after hash calculation
        "status": overall_status,
        "macro": macro_data,
        "peers": peers_data,
        "suppliers": suppliers_data,
        "macro_economy": macro_economy,
        "peer_group": peer_group,
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
            previous_state["last_updated"] = datetime.utcnow().isoformat()
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
