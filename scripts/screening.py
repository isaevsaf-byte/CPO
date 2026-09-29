#!/usr/bin/env python3
"""US trade-screening signals for the supplier watchlist.

Three free, keyless sources, fetched in parallel:

- US Consolidated Screening List (CSL): one CSV from data.trade.gov merging
  twelve US sanctions and export-control lists, among them OFAC SDN, the BIS
  Entity List, the Military End User list and Treasury's Chinese military
  companies (CMIC) list. Supplier names, aliases, legal names and parent
  companies are matched whole-word against each entry's name and alt_names.
- DHS UFLPA Entity List: companies whose goods are presumed to be made with
  forced labour and are stopped at the US border. HTML tables only.
- Federal Register API: rules and notices from the last 30 days on saved
  topics that move supplier risk before any list does (Entity List changes,
  Section 301/232 tariffs, UFLPA notices, FDA rules on e-cigarettes, and
  anti-dumping cases on the watchlist's materials).

Entry point: screen_suppliers(suppliers, name_terms, now=None). It never
raises: each source reports "ok", "empty" or "failed" with a reason, and the
whole call is held to TOTAL_BUDGET_SEC.
"""

import csv
import io
import json
import logging
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path

import requests

try:
    import name_matching
except ModuleNotFoundError:  # loaded by file path (tests) rather than run from scripts/
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import name_matching

logger = logging.getLogger("intel_harvester.screening")

CSL_URL = "https://data.trade.gov/downloadable_consolidated_screening_list/v1/consolidated.csv"
UFLPA_URL = "https://www.dhs.gov/uflpa-entity-list"
FEDERAL_REGISTER_API = "https://www.federalregister.gov/api/v1/documents.json"
PARENTS_FILE = Path(__file__).resolve().parent.parent / "data" / "supplier_parents.json"

# The whole call, all three sources together. The CSL is ~17 MB; it took
# 3.6 s to download and 0.2 s to parse when this was written.
TOTAL_BUDGET_SEC = 35
CONNECT_TIMEOUT = 10
READ_TIMEOUT = 20
CSL_MAX_BYTES = 60_000_000
FR_LOOKBACK_DAYS = 30
FR_PER_PAGE = 100
MAX_HITS_PER_SUPPLIER = 10

HEADERS = {"User-Agent": "SupplyChainWatchtower/1.0 (+https://cpo-watchtower.co.uk)"}
PAGE_HEADERS = {
    **HEADERS,
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-GB,en;q=0.9",
}

# Short labels for the CSL "source" column.
CSL_LIST_LABELS = {
    "Specially Designated Nationals (SDN) - Treasury Department": "OFAC SDN",
    "Entity List (EL) - Bureau of Industry and Security": "BIS Entity List",
    "Military End User (MEU) List - Bureau of Industry and Security": "BIS Military End User List",
    "Unverified List (UVL) - Bureau of Industry and Security": "BIS Unverified List",
    "Denied Persons List (DPL) - Bureau of Industry and Security": "BIS Denied Persons List",
    "Non-SDN Chinese Military-Industrial Complex Companies List (CMIC) - Treasury Department":
        "Treasury Chinese Military Companies (CMIC)",
    "Sectoral Sanctions Identifications List (SSI) - Treasury Department": "OFAC Sectoral Sanctions (SSI)",
    "Non-SDN Menu-Based Sanctions List (NS-MBS List) - Treasury Department": "OFAC Menu-Based Sanctions",
    "Capta List (CAP) - Treasury Department": "OFAC CAPTA List",
    "Palestinian Legislative Council List (PLC) - Treasury Department": "OFAC PLC List",
    "ITAR Debarred (DTC) - State Department": "State Dept ITAR Debarred",
    "Nonproliferation Sanctions (ISN) - State Department": "State Dept Nonproliferation Sanctions",
}

# Rows of these types are never compared with a supplier. Every supplier is a
# company, and a person's name sharing a word with one is coincidence: the
# SDN list carries an individual in the Philippines whose alias is
# "DELLOSA, Redendo Cain Jabil", which whole-word matching reads as Jabil.
# The BIS lists leave "type" blank, so their rows are all screened.
CSL_SKIPPED_TYPES = {"Individual", "Vessel", "Aircraft"}

UFLPA_SECTIONS = {
    "i": "UFLPA (i): produces in Xinjiang with forced labour",
    "ii": "UFLPA (ii): works with the Xinjiang government on forced labour",
    "iii": "UFLPA (iii): products made in Xinjiang with forced labour",
    "iv": "UFLPA (iv): exports forced-labour goods to the US",
    "v": "UFLPA (v): sources from Xinjiang or its labour-transfer schemes",
}

# ---------------------------------------------------------------------------
# Federal Register saved topics
#
# A bare full-text search is unusable here. Searched on its own for the 30
# days to 2026-09-29, "section 301" returned 33 documents, led by
# Foreign-Trade Zone production notices (whose boilerplate cites the Section
# 301 duties) and an FDA food-additive rule (section 301 of the Food, Drug,
# and Cosmetic Act is a different statute). "lithium-ion" returned only
# hazardous-materials permits and a Superfund tax notice. So every topic
# names the agencies that act on it, and the title has to say what the
# document is about before it is kept.
#
# affected_categories are watchlist categories. For the tariff topics they
# come from what the title names rather than a fixed list: a Section 301
# action is aimed at one economy, so it affects the categories sourced from
# that country, and a Section 232 action is aimed at one material.
# ---------------------------------------------------------------------------
_PAPER_CATEGORIES = ("Printing Substrates", "Printed Packaging", "Fine Papers", "Filter Materials")

FR_TOPICS = (
    {
        "topic": "bis_entity_list",
        "agencies": ("industry-and-security-bureau",),
        "term": '"entity list"',
        # Drops the BIS denial orders that only cite the Entity List in
        # their text ("In the Matter of: Hans De Geetere, ...", 2026-05-29).
        "title_any": ("entity list",),
        "categories": ("EMS", "Batteries", "EE Component"),
    },
    {
        "topic": "section_301",
        "agencies": ("trade-representative-office-of-united-states",),
        "term": '"section 301"',
        "by_country": True,
    },
    {
        "topic": "section_232",
        "agencies": ("industry-and-security-bureau", "executive-office-of-the-president"),
        "term": '"section 232"',
        # Section 232 actions published July to September 2026 also covered
        # drones, commercial aircraft, pharmaceuticals and anthracite coal; a
        # title naming none of these materials affects no watchlist category.
        "title_categories": {
            "aluminum": ("Printed Packaging", "Batteries", "EMS"),
            "aluminium": ("Printed Packaging", "Batteries", "EMS"),
            "steel": ("Batteries", "Mechanical"),
            "copper": ("Batteries", "EMS", "EE Component"),
            "polysilicon": ("EE Component",),
            "semiconductor": ("EE Component", "EMS"),
            "semiconductors": ("EE Component", "EMS"),
            "critical minerals": ("Batteries",),
            "lithium": ("Batteries",),
            "graphite": ("Batteries",),
            "timber": _PAPER_CATEGORIES,
            "lumber": _PAPER_CATEGORIES,
            "wood products": _PAPER_CATEGORIES,
            "pulp": _PAPER_CATEGORIES,
        },
    },
    {
        "topic": "uflpa",
        "agencies": ("homeland-security-department",),
        "term": '"Uyghur Forced Labor Prevention Act" | UFLPA',
        "title_any": ("uyghur forced labor", "uflpa", "forced labor"),
        "categories": ("Batteries", "EMS", "EE Component"),
    },
    {
        "topic": "fda_ends",
        "agencies": ("food-and-drug-administration",),
        "term": ('"electronic nicotine delivery" | "e-cigarette" | "e-cigarettes" | '
                 '"tobacco product" | "tobacco products" | nicotine'),
        # The same search also returns FDA's international drug-scheduling
        # notices, which mention nicotine in passing.
        "title_any": ("electronic nicotine delivery", "ends", "e cigarette", "e cigarettes",
                      "vape", "vapes", "vaping", "nicotine", "tobacco product", "tobacco products"),
        "categories": ("EMS", "Batteries", "Nicotine", "Modern/Traditional Oral Fleece"),
    },
    {
        "topic": "adcvd",
        "agencies": ("international-trade-administration", "international-trade-commission"),
        "term": ('"cellulose acetate" | "acetate tow" | "acetate flake" | "dissolving pulp" | '
                 '"cigarette paper" | "tipping paper" | "lithium-ion" | "lithium ion" | '
                 '"lithium hexafluorophosphate" | "anode material" | "anode materials" | '
                 'paperboard | cartonboard | "folding carton" | "folding cartons"'),
        "title_categories": {
            "cellulose acetate": ("Filter Materials",),
            "acetate tow": ("Filter Materials",),
            "acetate flake": ("Filter Materials",),
            "dissolving pulp": ("Filter Materials",),
            "cigarette paper": ("Fine Papers",),
            "tipping paper": ("Fine Papers",),
            "lithium ion": ("Batteries",),
            # The electrolyte salt in lithium-ion cells; the ITC opened AD/CVD
            # investigations on it from China in March 2026.
            "lithium hexafluorophosphate": ("Batteries",),
            # Graphite anode material; Commerce issued final AD/CVD
            # determinations on it from China in February 2026.
            "anode material": ("Batteries",),
            "anode materials": ("Batteries",),
            "paperboard": ("Printed Packaging", "Printing Substrates"),
            "cartonboard": ("Printed Packaging", "Printing Substrates"),
            "folding carton": ("Printed Packaging",),
            "folding cartons": ("Printed Packaging",),
        },
    },
)

FR_TOPIC_LABELS = {
    "bis_entity_list": "BIS Entity List changes",
    "section_301": "Section 301 tariff action",
    "section_232": "Section 232 tariff action",
    "uflpa": "UFLPA forced-labour notice",
    "fda_ends": "FDA tobacco and e-cigarette rules",
    "adcvd": "Anti-dumping / countervailing duty case",
}

FR_FIELDS = ("title", "publication_date", "html_url", "agencies", "type", "document_number")

# Paperwork-burden notices share the vocabulary of every topic. BIS's
# "Agency Information Collection Activities; ... Entity List ..." (2026-05-21)
# and FDA's "... Potential Tobacco Product Violations Reporting Form"
# (2026-08-31) both passed the title checks and neither changes anything.
FR_NOISE_PREFIXES = ("agency information collection activities",)

# How a watchlist country is named in a Section 301 title, beyond its own
# name ("China's Acts, Policies, and Practices ..."). The USA is absent on
# purpose: a US tariff does not fall on goods made in the US.
COUNTRY_TITLE_ALIASES = {
    "China": ("chinese", "prc"),
    "South Korea": ("korea", "korean"),
    "Japan": ("japanese",),
    "India": ("indian",),
    "Germany": ("german",),
    "Switzerland": ("swiss",),
    "Austria": ("austrian",),
    "Sweden": ("swedish",),
    "Finland": ("finnish",),
    "Netherlands": ("dutch",),
}
NOT_TARIFFED_LOCATIONS = {"USA", "United States", "Unknown"}
EU_MEMBER_STATES = {
    "Austria", "Belgium", "Bulgaria", "Croatia", "Cyprus", "Czech Republic", "Denmark",
    "Estonia", "Finland", "France", "Germany", "Greece", "Hungary", "Ireland", "Italy",
    "Latvia", "Lithuania", "Luxembourg", "Malta", "Netherlands", "Poland", "Portugal",
    "Romania", "Slovakia", "Slovenia", "Spain", "Sweden",
}
# Section 301 actions aimed at a group of economies without naming them in
# the title (2026-07-28: "... Various Economies Related to the Failure of
# Each Economy To Impose and Effectively Enforce a Prohibition on the
# Importation of Goods Produced With Forced Labor"). Kept, unattributed.
MULTI_ECONOMY_PHRASES = ("various economies", "certain economies", "multiple economies", "trading partners")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _as_utc(now) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    if now.tzinfo is None:
        return now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc)


def _timeout(deadline: float, read: float = READ_TIMEOUT) -> tuple:
    remaining = deadline - time.monotonic()
    if remaining < 1:
        raise TimeoutError("time budget used up before the request could start")
    return (min(CONNECT_TIMEOUT, remaining), min(read, remaining))


def _describe(error: Exception) -> str:
    text = str(error) or error.__class__.__name__
    return f"{error.__class__.__name__}: {text}"[:200]


def _phrase_in(folded: str, phrase: str) -> bool:
    return f" {name_matching.normalize(phrase).strip()} " in folded


# ---------------------------------------------------------------------------
# Screening terms: watchlist names and aliases, plus legal and parent names
# ---------------------------------------------------------------------------
def load_supplier_parents(path: Path | None = None) -> dict:
    """The "suppliers" mapping from data/supplier_parents.json.

    Raises on a malformed file; screen_suppliers turns that into a reported
    source failure rather than silently screening without the parents.
    """
    path = path or PARENTS_FILE
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    suppliers = raw.get("suppliers")
    if not isinstance(suppliers, dict):
        raise ValueError(f"{Path(path).name} has no 'suppliers' mapping")
    return suppliers


def expand_name_terms(name_terms: dict, parents: dict | None = None) -> dict:
    """name_terms plus each supplier's own legal names from supplier_parents.json.

    Parents are not added: this is for sources where a hit must be the
    supplier itself, such as ransomware leak sites. It is how CNT, whose
    watchlist name is too short to match safely, gets "CONTRAF NICOTEX".
    """
    if parents is None:
        try:
            parents = load_supplier_parents()
        except (OSError, ValueError) as e:
            logger.warning(f"supplier_parents.json not used: {_describe(e)}")
            parents = {}
    expanded = {}
    for supplier, terms in (name_terms or {}).items():
        extra = (parents.get(supplier) or {}).get("match_terms") or []
        combined = [str(t).upper() for t in list(terms or []) + list(extra)]
        expanded[supplier] = list(dict.fromkeys(combined))
    return expanded


def build_screening_terms(name_terms: dict, parents: dict | None) -> dict:
    """supplier -> [(term, via, parent_name)] ready for whole-word matching.

    via is "name" for the supplier's own names and aliases, "parent" for a
    parent company. The short-name guard applies to both (see name_matching).
    """
    parents = parents or {}
    by_supplier = {}
    for supplier, terms in name_terms.items():
        config = parents.get(supplier) or {}
        own = name_matching.usable_terms(list(terms or []) + list(config.get("match_terms") or []))
        combined = [(term, "name", None) for term in own]
        seen = set(own)
        for parent in config.get("parents") or []:
            for term in name_matching.usable_terms(parent.get("match_terms")):
                if term not in seen:
                    seen.add(term)
                    combined.append((term, "parent", parent.get("name")))
        by_supplier[supplier] = combined
    return by_supplier


def match_entries(entries: list, screening_terms: dict, limit: int = MAX_HITS_PER_SUPPLIER) -> dict:
    """supplier -> hits, one per matching list entry (first matching term wins)."""
    hits = {}
    for supplier, terms in screening_terms.items():
        if not terms:
            continue
        found, seen = [], set()
        for entry in entries:
            text = entry["_text"]
            for term, via, parent in terms:
                if f" {term} " not in text:
                    continue
                key = (entry["list"], entry["entity"])
                if key not in seen:
                    seen.add(key)
                    hit = {k: v for k, v in entry.items() if not k.startswith("_")}
                    hit.update({"matched_term": term, "via": via, "parent": parent})
                    found.append(hit)
                break
            if len(found) >= limit:
                break
        if found:
            hits[supplier] = found
    return hits


# ---------------------------------------------------------------------------
# Consolidated Screening List
# ---------------------------------------------------------------------------
def _address_countries(addresses) -> str:
    """ISO codes ending each address ("... Xian, China, CN; ..., HK" -> "CN, HK")."""
    codes = []
    for address in (addresses or "").split(";"):
        last = address.rsplit(",", 1)[-1].strip()
        if len(last) == 2 and last.isalpha() and last.isupper() and last not in codes:
            codes.append(last)
    return ", ".join(codes)


def parse_csl(text: str) -> tuple:
    """(screenable entries, total rows) from the CSL CSV.

    Individuals, vessels and aircraft are counted but not returned (see
    CSL_SKIPPED_TYPES). A file without a "name" column parses to nothing.
    """
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames or "name" not in reader.fieldnames:
        return [], 0
    entries, total = [], 0
    for row in reader:
        total += 1
        if (row.get("type") or "").strip() in CSL_SKIPPED_TYPES:
            continue
        name = (row.get("name") or "").strip()
        if not name:
            continue
        alt_names = [a.strip() for a in (row.get("alt_names") or "").split(";") if a.strip()]
        source = (row.get("source") or "").strip()
        entries.append({
            "list": CSL_LIST_LABELS.get(source, source or "Consolidated Screening List"),
            "entity": name,
            "programs": [p.strip() for p in (row.get("programs") or "").split(";") if p.strip()],
            "country": _address_countries(row.get("addresses")),
            "source_url": (row.get("source_list_url") or row.get("source_information_url") or CSL_URL).strip(),
            "listed_since": (row.get("start_date") or "").strip(),
            # "|" never occurs in a folded term, so no term can straddle two names.
            "_text": "|".join(name_matching.normalize(n) for n in [name] + alt_names),
        })
    return entries, total


def fetch_csl_text(session, deadline: float) -> str:
    """Download the CSL CSV, abandoning it if it runs past the deadline."""
    with session.get(CSL_URL, headers=HEADERS, timeout=_timeout(deadline), stream=True) as response:
        response.raise_for_status()
        chunks, size = [], 0
        for chunk in response.iter_content(chunk_size=1 << 18):
            size += len(chunk)
            if size > CSL_MAX_BYTES:
                raise ValueError(f"CSL passed {CSL_MAX_BYTES // 1_000_000} MB, refusing the rest")
            if time.monotonic() > deadline:
                raise TimeoutError(f"CSL download ran out of time after {size / 1e6:.1f} MB")
            chunks.append(chunk)
    return b"".join(chunks).decode("utf-8-sig", errors="replace")


def _screen_csl(session, screening_terms: dict, deadline: float) -> dict:
    started = time.monotonic()
    text = fetch_csl_text(session, deadline)
    entries, total = parse_csl(text)
    elapsed = time.monotonic() - started
    if not entries:
        return {"source": {"status": "empty", "detail": (
            f"downloaded {len(text) / 1e6:.1f} MB but found no company entries "
            f"({total} rows) - the file layout may have changed")}, "hits": {}}
    hits = match_entries(entries, screening_terms)
    matched = sum(len(v) for v in hits.values())
    return {"source": {"status": "ok", "detail": (
        f"{total:,} entries, {len(entries):,} screened (people, vessels and aircraft skipped) "
        f"in {elapsed:.1f}s; {matched} possible match(es) across {len(hits)} supplier(s)")}, "hits": hits}


# ---------------------------------------------------------------------------
# UFLPA Entity List
# ---------------------------------------------------------------------------
_SECTION_LABEL = re.compile(r"Section\s*2\(d\)\(2\)\(B\)\((i{1,3}|iv|v)\)")

# Where the aliases start inside a UFLPA name cell. Cutting at the first "("
# would be wrong: "Hoshine Silicon Industry (Shanshan) Co., Ltd (including
# one alias: ...)" keeps its place name in brackets.
_UFLPA_ALIAS_START = re.compile(
    r"\s*\((?=\s*(?:and\s|also\s|formerly\s|including\s|a\.?k\.?a|f\.?k\.?a))", re.IGNORECASE)


class _TableCollector(HTMLParser):
    """Rows of every top-level <table>, each tagged with the UFLPA section
    label ("Section 2(d)(2)(B)(v)") last seen before the table opened."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables = []
        self._depth = 0
        self._section = ""
        self._row = None
        self._cell = None
        self._cell_is_header = False

    def _close_cell(self):
        if self._cell is not None and self._row is not None:
            self._row.append((" ".join("".join(self._cell).split()), self._cell_is_header))
        self._cell = None

    def _close_row(self):
        self._close_cell()
        if self._row and self.tables:
            self.tables[-1]["rows"].append(self._row)
        self._row = None

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self._depth += 1
            if self._depth == 1:
                self.tables.append({"section": self._section, "rows": []})
        elif self._depth == 1 and tag == "tr":
            self._close_row()
            self._row = []
        elif self._depth == 1 and tag in ("td", "th") and self._row is not None:
            self._close_cell()
            self._cell = []
            self._cell_is_header = tag == "th"
        elif tag == "br" and self._cell is not None:
            self._cell.append(" ")

    def handle_endtag(self, tag):
        if self._depth == 1 and tag in ("td", "th"):
            self._close_cell()
        elif self._depth == 1 and tag == "tr":
            self._close_row()
        elif tag == "table" and self._depth:
            if self._depth == 1:
                self._close_row()
            self._depth -= 1

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)
        elif not self._depth:
            match = _SECTION_LABEL.search(" ".join(data.split()))
            if match:
                self._section = match.group(1)


def _uflpa_primary_name(cell: str) -> str:
    match = _UFLPA_ALIAS_START.search(cell)
    return (cell[:match.start()] if match else cell).strip().rstrip(",;")


def _uflpa_date(text: str) -> str:
    try:
        return datetime.strptime(" ".join(text.split()), "%B %d, %Y").date().isoformat()
    except ValueError:
        return ""


def parse_uflpa(html: str) -> list:
    """Entity rows from the DHS page, with aliases kept in the matched text."""
    collector = _TableCollector()
    collector.feed(html)
    collector.close()
    entries = []
    for table in collector.tables:
        section = table["section"]
        for row in table["rows"]:
            if all(is_header for _, is_header in row):
                continue
            cells = [text for text, _ in row]
            name_cell = cells[0] if cells else ""
            if not name_cell or name_cell.lower() in ("name of entity", "entity name"):
                continue
            entries.append({
                "list": "DHS UFLPA Entity List",
                "entity": _uflpa_primary_name(name_cell),
                "programs": [UFLPA_SECTIONS.get(section, "UFLPA Entity List")],
                # The list names entities in the People's Republic of China;
                # the page gives no per-row country.
                "country": "CN",
                "source_url": UFLPA_URL,
                "listed_since": _uflpa_date(cells[1]) if len(cells) > 1 else "",
                "_text": name_matching.normalize(name_cell),
            })
    return entries


def fetch_uflpa_html(session, deadline: float) -> str:
    response = session.get(UFLPA_URL, headers=PAGE_HEADERS, timeout=_timeout(deadline))
    response.raise_for_status()
    return response.text


def _screen_uflpa(session, screening_terms: dict, deadline: float) -> dict:
    started = time.monotonic()
    html = fetch_uflpa_html(session, deadline)
    entries = parse_uflpa(html)
    elapsed = time.monotonic() - started
    if not entries:
        return {"source": {"status": "empty", "detail": (
            f"page fetched ({len(html):,} bytes) but no entity tables were found - "
            f"DHS may have changed the layout")}, "hits": {}}
    hits = match_entries(entries, screening_terms)
    matched = sum(len(v) for v in hits.values())
    return {"source": {"status": "ok", "detail": (
        f"{len(entries)} entity rows screened in {elapsed:.1f}s; "
        f"{matched} possible match(es) across {len(hits)} supplier(s)")}, "hits": hits}


# ---------------------------------------------------------------------------
# Federal Register
# ---------------------------------------------------------------------------
def _countries_named(folded_title: str, locations: set) -> set:
    # "Korea" alone means South Korea in a trade title; North Korea must not.
    for other in ("north korea", "democratic people s republic of korea"):
        folded_title = folded_title.replace(f" {other.upper()} ", " ")
    named = set()
    for location in locations:
        if location in NOT_TARIFFED_LOCATIONS:
            continue
        words = (location,) + COUNTRY_TITLE_ALIASES.get(location, ())
        if any(_phrase_in(folded_title, w) for w in words):
            named.add(location)
    if _phrase_in(folded_title, "european union"):
        named.update(loc for loc in locations if loc in EU_MEMBER_STATES)
    return named


def notice_categories(topic: dict, title: str, suppliers: list):
    """Watchlist categories a notice affects, or None if it should be dropped."""
    folded = name_matching.normalize(title)
    watch_categories = {s.get("category") for s in suppliers if s.get("category")}

    def on_watchlist(categories):
        return sorted({c for c in categories if not watch_categories or c in watch_categories})

    if topic.get("title_any") and not any(_phrase_in(folded, p) for p in topic["title_any"]):
        return None
    if topic.get("title_categories"):
        categories = set()
        for phrase, phrase_categories in topic["title_categories"].items():
            if _phrase_in(folded, phrase):
                categories.update(phrase_categories)
        return on_watchlist(categories) if categories else None
    if topic.get("by_country"):
        locations = {s.get("location") for s in suppliers if s.get("location")}
        named = _countries_named(folded, locations)
        if named:
            return sorted({s.get("category") for s in suppliers
                           if s.get("location") in named and s.get("category")})
        if any(_phrase_in(folded, p) for p in MULTI_ECONOMY_PHRASES):
            return []
        # Aimed at an economy the watchlist does not source from
        # ("Brazil's Acts, Policies, and Practices ...", 2026-07-20).
        return None
    return on_watchlist(topic.get("categories") or ())


def select_notices(topic: dict, documents: list, suppliers: list) -> list:
    notices = []
    for doc in documents or []:
        if not isinstance(doc, dict):
            continue
        title = " ".join((doc.get("title") or "").split())
        if not title or title.lower().startswith(FR_NOISE_PREFIXES):
            continue
        categories = notice_categories(topic, title, suppliers)
        if categories is None:
            continue
        agencies = [a.get("name") or a.get("raw_name") for a in doc.get("agencies") or []
                    if isinstance(a, dict) and (a.get("name") or a.get("raw_name"))]
        notices.append({
            "title": title,
            "date": doc.get("publication_date") or "",
            "url": doc.get("html_url") or "",
            "agencies": agencies,
            "topic": topic["topic"],
            "affected_categories": categories,
            "type": doc.get("type") or "",
            "document_number": doc.get("document_number") or "",
        })
    return notices


def fetch_federal_register_topic(session, topic: dict, since, until, deadline: float) -> list:
    params = [
        ("per_page", str(FR_PER_PAGE)),
        ("order", "newest"),
        ("conditions[publication_date][gte]", since.isoformat()),
        ("conditions[publication_date][lte]", until.isoformat()),
        ("conditions[term]", topic["term"]),
    ]
    params += [("conditions[agencies][]", slug) for slug in topic["agencies"]]
    params += [("fields[]", field) for field in FR_FIELDS]
    response = session.get(FEDERAL_REGISTER_API, params=params, headers=HEADERS,
                           timeout=_timeout(deadline, read=15))
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        raise ValueError("unexpected Federal Register response")
    if (data.get("count") or 0) > FR_PER_PAGE:
        logger.warning(f"Federal Register topic {topic['topic']}: {data['count']} documents, "
                       f"only the newest {FR_PER_PAGE} read")
    return data.get("results") or []


def _federal_register_notices(session, suppliers: list, now: datetime, deadline: float) -> dict:
    started = time.monotonic()
    until = now.date()
    since = until - timedelta(days=FR_LOOKBACK_DAYS)
    pool = ThreadPoolExecutor(max_workers=len(FR_TOPICS))
    futures = {pool.submit(fetch_federal_register_topic, session, t, since, until, deadline): t
               for t in FR_TOPICS}
    done, pending = wait(futures, timeout=max(0.0, deadline - time.monotonic()))
    pool.shutdown(wait=False, cancel_futures=True)

    notices, seen, counts, failures = [], set(), {}, []
    for future, topic in futures.items():  # dict order = FR_TOPICS order
        if future in pending:
            failures.append(f"{topic['topic']} (timed out)")
            continue
        try:
            documents = future.result()
        except Exception as e:
            failures.append(f"{topic['topic']} ({_describe(e)})")
            continue
        for notice in select_notices(topic, documents, suppliers):
            key = notice["document_number"] or notice["url"]
            if key in seen:
                continue
            seen.add(key)
            notices.append(notice)
            counts[topic["topic"]] = counts.get(topic["topic"], 0) + 1
    notices.sort(key=lambda n: n["date"], reverse=True)

    elapsed = time.monotonic() - started
    breakdown = ", ".join(f"{k}: {v}" for k, v in counts.items()) or "none"
    detail = (f"{len(FR_TOPICS) - len(failures)}/{len(FR_TOPICS)} topics read in {elapsed:.1f}s; "
              f"{len(notices)} notice(s) since {since.isoformat()} ({breakdown})")
    if failures:
        # A topic that could not be read is not a quiet topic, so the source
        # is reported failed even though the others are returned.
        detail += "; failed: " + "; ".join(failures)
        return {"source": {"status": "failed", "detail": detail[:500]}, "notices": notices}
    return {"source": {"status": "ok", "detail": detail}, "notices": notices}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def screen_suppliers(suppliers: list, name_terms: dict, now=None, *, session=None,
                     parents: dict | None = None) -> dict:
    """Screen the watchlist against the CSL and UFLPA lists and collect
    Federal Register notices.

    suppliers: dicts with at least "name", "category" and "location", as in
        data/suppliers.json. Category and location decide which watchlist
        categories a Federal Register notice affects.
    name_terms: supplier name -> uppercase search terms (name plus aliases),
        e.g. {name: supplier_search_terms(name)} in update_intel.py. A
        supplier missing from it is screened on its bare name.
    parents: the "suppliers" mapping of data/supplier_parents.json; read
        from disk when None.

    Returns {"fetched_at", "hits", "notices", "sources", "unscreened",
    "duration_seconds"}. hits holds only suppliers with at least one match;
    an absent supplier means no match only if both list sources are "ok".
    """
    started = time.monotonic()
    deadline = started + TOTAL_BUDGET_SEC
    now = _as_utc(now)
    result = {"fetched_at": now.isoformat(), "hits": {}, "notices": [], "sources": {},
              "unscreened": [], "duration_seconds": 0.0}
    try:
        suppliers = [s for s in (suppliers or []) if isinstance(s, dict) and s.get("name")]
        if parents is None:
            try:
                parents = load_supplier_parents()
            except (OSError, ValueError) as e:
                parents = {}
                result["sources"]["supplier_parents"] = {
                    "status": "failed",
                    "detail": f"{PARENTS_FILE.name} not used, parents unscreened: {_describe(e)}"}
        terms_by_supplier = {s["name"]: (name_terms or {}).get(s["name"]) or [s["name"]]
                             for s in suppliers}
        screening_terms = build_screening_terms(terms_by_supplier, parents)
        result["unscreened"] = sorted(name for name, terms in screening_terms.items() if not terms)

        http = session or requests.Session()
        # Each source gets a slightly earlier deadline than this wait, so a
        # source that runs out of time reports what it has (the Federal
        # Register keeps the topics it did read) instead of being cut off.
        source_deadline = deadline - 2.0
        pool = ThreadPoolExecutor(max_workers=3)
        futures = {
            pool.submit(_screen_csl, http, screening_terms, source_deadline): "consolidated_screening_list",
            pool.submit(_screen_uflpa, http, screening_terms, source_deadline): "uflpa_entity_list",
            pool.submit(_federal_register_notices, http, suppliers, now, source_deadline): "federal_register",
        }
        done, pending = wait(futures, timeout=max(0.0, deadline - time.monotonic()))
        pool.shutdown(wait=False, cancel_futures=True)

        for future, name in futures.items():
            if future in pending:
                result["sources"][name] = {"status": "failed",
                                           "detail": f"no answer within the {TOTAL_BUDGET_SEC}s budget"}
                continue
            try:
                payload = future.result()
            except Exception as e:
                result["sources"][name] = {"status": "failed", "detail": _describe(e)}
                continue
            result["sources"][name] = payload["source"]
            for supplier, hits in (payload.get("hits") or {}).items():
                merged = result["hits"].setdefault(supplier, [])
                merged.extend(hits[:max(0, MAX_HITS_PER_SUPPLIER - len(merged))])
            result["notices"].extend(payload.get("notices") or [])
    except Exception as e:  # the contract is to never raise into the harvest
        logger.exception("screening failed")
        result["sources"]["screening"] = {"status": "failed", "detail": _describe(e)}

    result["duration_seconds"] = round(time.monotonic() - started, 2)
    for name, source in result["sources"].items():
        log = logger.info if source["status"] == "ok" else logger.warning
        log(f"[{name}] {source['status']}: {source['detail']}")
    return result
