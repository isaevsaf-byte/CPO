#!/usr/bin/env python3
"""
GDELT country readings built from GDELT's raw 15-minute event files.

The GDELT DOC API answers GitHub's runners with HTTP 429. In the snapshot of
29 Sep 2026 03:05 UTC nine of the eleven supplier countries had last been
refused, and the readings carried forward for them were up to thirteen days
old (Sweden's dated from 16 Sep). GDELT also publishes everything it codes as
static files every 15 minutes, and static files carry no rate limit. This
module reads a day of them and rebuilds, from the events located in each
supplier country, the reading the /geopolitical page already renders.

fetch_gdelt_from_files(countries, relevance_for_country, now=None) is the entry
point. It returns (geopolitical_intel, source_status) and never raises.
"""

import io
import logging
import re
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from concurrent.futures import TimeoutError as FuturesTimeout
from datetime import datetime, timedelta, timezone
from urllib.parse import unquote, urlparse

import requests
from requests.adapters import HTTPAdapter

# A child of the harvester's logger, so these lines come out in its format
# when update_intel.py drives this module, without configuring logging here.
logger = logging.getLogger("intel_harvester.gdelt_files")

USER_AGENT = {"User-Agent": "SupplyChainWatchtower/1.0 (+https://cpo-watchtower.co.uk)"}

# Plain http answers every request here with a 301 to https (checked 29 Sep
# 2026), so asking https directly saves a round trip on each of 96 files.
GDELT_FILES_URL = "https://data.gdeltproject.org/gdeltv2/"
LASTUPDATE_URL = GDELT_FILES_URL + "lastupdate.txt"

# Measured on 29 Sep 2026: a day of export files is 96 files and 6.9 MB zipped
# (43.6 MB unzipped, about 107,000 events), fetched in 1.4 s eight at a time
# and 2.7 s one at a time; three days are 14.3 MB and 4.8 s. Both fit the
# budget, and a day is the window: the harvest runs every six hours, and a
# three-day average would still be describing the previous week two days into
# a crisis. The cost is a thin reading for the smallest countries — Finland
# had 26 articles that day — which is why the article count sits beside the
# tone on the page.
WINDOW_HOURS = 24
SLOT_MINUTES = 15
# Read every Nth file only, should a day ever outgrow the caps below. At the
# sizes measured above a whole day fits five times over, so every file is read.
SAMPLE_EVERY = 1
DOWNLOAD_WORKERS = 8
REQUEST_TIMEOUT = (5, 15)  # (connect, read) seconds, per file
TIME_BUDGET_SEC = 30
MAX_BYTES = 40_000_000
# Below this share of the window's files a day's reading undercounts badly
# enough that the page is better off keeping the previous one.
MIN_COVERAGE = 0.75
TOP_ARTICLES = 5

# ActionGeo_CountryCode is FIPS 10-4, not ISO: Switzerland is SZ, Germany GM,
# South Africa SF, Sweden SW and Austria AU, while AS is Australia. Each code
# was checked against the country name GDELT prints beside it in a live day of
# files on 29 Sep 2026. A supplier country missing here is reported as
# "unmapped" rather than guessed.
FIPS_CODES = {
    "Austria": "AU",
    "China": "CH",
    "Finland": "FI",
    "Germany": "GM",
    "India": "IN",
    "Japan": "JA",
    "Netherlands": "NL",
    "South Africa": "SF",
    "South Korea": "KS",
    "Sweden": "SW",
    "Switzerland": "SZ",
    "USA": "US",
}

# GDELT 2.0 event export: tab-separated, no header row, 61 columns in the
# order of the GDELT Event Codebook V2.0 — 5 date fields, 10 per actor, 10
# describing the action, 8 per geography (Actor1, Actor2, Action), DATEADDED
# and SOURCEURL. Checked against a live day on 29 Sep 2026: all 106,646 rows
# had 61 columns and every DATEADDED matched its file's timestamp.
EXPORT_COLUMNS = 61
COL_EVENT_CODE = 26
COL_EVENT_ROOT_CODE = 28
COL_GOLDSTEIN = 30
COL_NUM_ARTICLES = 33
COL_AVG_TONE = 34
COL_ACTION_COUNTRY = 53
COL_DATE_ADDED = 59
COL_SOURCE_URL = 60

# CAMEO families counted per country. 163, "impose embargo, boycott or
# sanctions", is picked out of family 16 ("reduce relations") on its own,
# because the rest of that family is diplomatic back-and-forth.
EVENT_MIX_ROOTS = {
    "14": "protest",
    "17": "coerce",
    "18": "assault",
    "19": "fight",
    "20": "mass_violence",
}
SANCTIONS_EVENT_CODE = "163"
EVENT_MIX_KEYS = ("protest", "sanctions_embargo", "coerce", "assault", "fight", "mass_violence")


# ============================================================================
# HEADLINES FROM URLS
# ============================================================================
# The event files carry no headline, only the URL of the first article that
# reported each event, so the headline is read back out of the URL's slug.
# Every rule below answers a URL shape seen in the live files on 29 Sep 2026:
#   .../unicredits-andrea-orcel-moves-seize-050724000.html   trailing id
#   .../opposition-parties-to-join20260929122440/            timestamp glued on
#   .../sylvia-ann-swede-w3zgdnuvzbscpf9ltbl4                trailing hash
#   .../gift-ban-vote-in-harrisburg/article_59d82d4a-...html uuid after the slug
#   .../herbicide-s-link-to-parkinson-s-disease/47099082     id after the slug
#   .../stories/202609290003.html, /article/10887733         no slug at all
# A URL with no readable slug gives no headline. It still counts towards the
# country's article count and tone; it just cannot be listed.
_EXTENSION = re.compile(r"\.(s?html?|php|aspx?|jsp|cms|ece)$", re.IGNORECASE)
_TOKEN_SPLIT = re.compile(r"[-_+\s.,]+")
_HASH_TOKEN = re.compile(r"^(?=.*\d)(?=.*[a-z])[a-z0-9]{8,}$", re.IGNORECASE)
_YEAR = re.compile(r"^(19|20)\d\d$")
_CONTRACTED_T = {"don", "won", "can", "isn", "aren", "doesn", "didn", "wasn", "weren",
                 "couldn", "shouldn", "wouldn", "hasn", "haven", "hadn", "ain"}
_TRAILING_NOISE = {"amp", "html", "htm", "php", "index"}


def _slug_tokens(segment: str) -> list:
    segment = _EXTENSION.sub("", segment)
    tokens = [t for t in _TOKEN_SPLIT.split(segment) if t]
    if tokens:
        # A timestamp glued onto the last word: "join20260929122440" -> "join".
        glued = re.match(r"^(.*[a-z])\d{8,}$", tokens[-1], re.IGNORECASE)
        if glued:
            tokens[-1] = glued.group(1)
    # Trailing ids, hashes and dates. A run of number tokens is dropped whole
    # only when it reads as a date ("2026-09-28"); otherwise only long numbers
    # go, so "top-10" and "october-4" keep their numbers.
    while tokens and tokens[-1].lower() in _TRAILING_NOISE:
        tokens.pop()
    while tokens and _HASH_TOKEN.match(tokens[-1]):
        tokens.pop()
    run = 0
    while run < len(tokens) and tokens[-1 - run].isdigit():
        run += 1
    if run >= 2 and any(_YEAR.match(t) for t in tokens[len(tokens) - run:]):
        del tokens[len(tokens) - run:]
    while tokens and tokens[-1].isdigit() and len(tokens[-1]) >= 5:
        tokens.pop()
    while tokens and _HASH_TOKEN.match(tokens[-1]):
        tokens.pop()
    # Leading dates: "20260928-..." or "2026-09-28-...".
    while tokens and tokens[0].isdigit() and len(tokens[0]) >= 5:
        tokens.pop(0)
    if len(tokens) >= 2 and _YEAR.match(tokens[0]) and tokens[1].isdigit() and len(tokens[1]) <= 2:
        while tokens and tokens[0].isdigit():
            tokens.pop(0)
    return tokens


# Slugs spell out dotted abbreviations letter by letter: "u-s-china-talks".
_SPELLED_OUT = {("u", "s"): "US", ("u", "k"): "UK", ("e", "u"): "EU", ("u", "n"): "UN"}


def _rejoin_contractions(tokens: list) -> list:
    """'herbicide s link' -> "herbicide's link", 'don t' -> "don't", 'u s' -> 'US'."""
    out = []
    for token in tokens:
        low = token.lower()
        pair = (out[-1].lower(), low) if out else None
        if pair in _SPELLED_OUT:
            out[-1] = _SPELLED_OUT[pair]
        elif out and low == "s" and len(out[-1]) > 1 and out[-1].isalpha():
            out[-1] += "'s"
        elif out and low == "t" and out[-1].lower() in _CONTRACTED_T:
            out[-1] += "'t"
        else:
            out.append(token)
    return out


def title_from_url(url: str) -> str | None:
    """A readable headline from an article URL's slug, or None."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return None
    segments = [unquote(s) for s in parsed.path.split("/") if s]
    for segment in reversed(segments):
        tokens = _rejoin_contractions(_slug_tokens(segment))
        words = [t for t in tokens if t.replace("'", "").isalpha()]
        # Three real words at least, and mostly words: "article", "stories"
        # and "59d82d4a 8bfb 5b23" are path furniture, not headlines.
        if len(words) < 3 or len(words) * 2 < len(tokens):
            continue
        title = " ".join(tokens)[:200]
        if title == title.lower():
            title = title[0].upper() + title[1:]
        return title
    return None


# ============================================================================
# TALLYING
# ============================================================================

class CountryTally:
    """Everything one country's reading needs, gathered file by file.

    Tone and Goldstein are averaged over events weighted by NumArticles — the
    number of articles carrying the event when GDELT first saw it — so a story
    forty outlets ran counts for more than a single passing mention.
    article_count is the number of distinct source articles: each is a real,
    clickable report, where summing NumArticles would count an article once
    for every event coded out of it.
    """

    def __init__(self):
        self.events = 0
        self.tone_weight = 0.0
        self.tone_sum = 0.0
        self.goldstein_weight = 0.0
        self.goldstein_sum = 0.0
        self.mix = dict.fromkeys(EVENT_MIX_KEYS, 0)
        self.articles = {}  # url -> [tone sum, tone rows, widest coverage]

    def add(self, root_code: str, event_code: str, goldstein, num_articles: int, tone, url: str) -> None:
        self.events += 1
        weight = max(num_articles, 1)
        if tone is not None:
            self.tone_sum += tone * weight
            self.tone_weight += weight
        if goldstein is not None:
            self.goldstein_sum += goldstein * weight
            self.goldstein_weight += weight
        family = EVENT_MIX_ROOTS.get(root_code)
        if family:
            self.mix[family] += 1
        if event_code.startswith(SANCTIONS_EVENT_CODE):
            self.mix["sanctions_embargo"] += 1
        if url:
            entry = self.articles.setdefault(url, [0.0, 0, 0])
            if tone is not None:
                entry[0] += tone
                entry[1] += 1
            entry[2] = max(entry[2], num_articles)

    def reading(self, relevance, fetched_at: str, window: str, country: str = "") -> dict:
        ranked = []
        failures = 0
        for url, (tone_sum, tone_rows, coverage) in self.articles.items():
            if not url.startswith(("http://", "https://")):
                continue
            title = title_from_url(url)
            if not title:
                continue
            score = 0
            if relevance is not None:
                try:
                    score = int(relevance(title, url) or 0)
                except Exception:
                    failures += 1
            if score > 0:
                tone = round(tone_sum / tone_rows, 1) if tone_rows else None
                # Most relevant first, then most negative, then most widely
                # carried; the URL last so a tie always resolves the same way.
                ranked.append((-score, tone if tone is not None else 0.0, -coverage, url, title, tone))
        if failures:
            logger.warning(f"GDELT files: relevance check raised {failures} times for {country or 'a country'}")
        ranked.sort()

        # The same wire story reaches GDELT from several outlets under the
        # same slug, so a title is listed once.
        articles, seen = [], set()
        for _, _, _, url, title, tone in ranked:
            key = title.lower()
            if key in seen:
                continue
            seen.add(key)
            articles.append({"title": title, "url": url, "tone": tone})
            if len(articles) == TOP_ARTICLES:
                break

        return {
            "article_count": len(self.articles),
            "avg_tone": round(self.tone_sum / self.tone_weight, 1) if self.tone_weight else None,
            "articles": articles,
            "has_relevant": bool(articles),
            "query_mode": "events",
            "window": window,
            "fetched_at": fetched_at,
            "event_count": self.events,
            "event_mix": dict(self.mix),
            "goldstein_avg": (
                round(self.goldstein_sum / self.goldstein_weight, 1) if self.goldstein_weight else None
            ),
        }


def _float(text: str):
    try:
        return float(text)
    except ValueError:
        return None


def parse_export(content: bytes, targets: dict, tallies: dict, window: tuple) -> dict:
    """Fold one zipped export file into the country tallies.

    targets maps FIPS code -> country; window is (first, last) DATEADDED,
    as YYYYMMDDHHMMSS strings, that belong in this reading. Returns counts of
    rows kept and skipped and the DATEADDED range seen.
    """
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        members = archive.namelist()
        if not members:
            raise zipfile.BadZipFile("empty archive")
        text = archive.read(members[0]).decode("utf-8", errors="replace")

    kept = malformed = 0
    first_added = last_added = None
    # Rows end in "\n". str.splitlines() would also break a row at a \x1c or
    # \u2028 inside its URL; none of the 106,646 rows read on 29 Sep 2026 had
    # one, but splitting on "\n" alone costs nothing.
    for line in text.split("\n"):
        if not line:
            continue
        cols = line.rstrip("\r").split("\t")
        if len(cols) != EXPORT_COLUMNS:
            malformed += 1
            continue
        country = targets.get(cols[COL_ACTION_COUNTRY])
        if country is None:
            continue
        added = cols[COL_DATE_ADDED]
        if not (window[0] <= added <= window[1]):
            continue
        try:
            num_articles = int(cols[COL_NUM_ARTICLES] or 1)
        except ValueError:
            num_articles = 1
        tallies[country].add(
            cols[COL_EVENT_ROOT_CODE],
            cols[COL_EVENT_CODE],
            _float(cols[COL_GOLDSTEIN]),
            num_articles,
            _float(cols[COL_AVG_TONE]),
            cols[COL_SOURCE_URL].strip(),
        )
        kept += 1
        first_added = added if first_added is None else min(first_added, added)
        last_added = added if last_added is None else max(last_added, added)
    return {"kept": kept, "malformed": malformed, "first_added": first_added, "last_added": last_added}


# ============================================================================
# FETCHING
# ============================================================================

def window_label(hours: int) -> str:
    """The page words a reading's window from this and knows "1d" and "3d"."""
    return f"{hours // 24}d" if hours % 24 == 0 else f"{hours}h"


def window_slots(latest: datetime) -> list:
    """File timestamps for the window, newest first. Each file holds the
    15 minutes up to its own timestamp."""
    count = WINDOW_HOURS * 60 // SLOT_MINUTES
    return [latest - timedelta(minutes=SLOT_MINUTES * i) for i in range(0, count, max(SAMPLE_EVERY, 1))]


def _stamp(moment: datetime) -> str:
    return moment.strftime("%Y%m%d%H%M%S")


MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _when(moment: datetime) -> str:
    """'29 Sep 08:00', whatever the runner's locale."""
    return f"{moment.day} {MONTHS[moment.month - 1]} {moment:%H:%M}"


def _describe_failure(exc: BaseException) -> str:
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        return f"HTTP {exc.response.status_code}"
    if isinstance(exc, requests.Timeout):
        return "timed out"
    if isinstance(exc, requests.ConnectionError):
        return "unreachable"
    return type(exc).__name__


def _open_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(USER_AGENT)
    adapter = HTTPAdapter(pool_connections=1, pool_maxsize=DOWNLOAD_WORKERS)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def latest_file_time(session, now: datetime) -> tuple:
    """(timestamp of the newest export file, where that came from)."""
    try:
        response = session.get(LASTUPDATE_URL, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        for line in response.text.splitlines():
            match = re.search(r"/(\d{14})\.export\.CSV\.zip\s*$", line)
            if match:
                stamp = datetime.strptime(match.group(1), "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
                # The index can name a file ahead of the clock: at 08:26 UTC
                # on 29 Sep 2026 it listed 08:30, and at 08:35 it listed
                # 08:45. Both export files still answered 404 at 08:38,
                # although their mentions files were already up. The window
                # ends at the newest slot the clock has reached, and a listed
                # file that is missing anyway counts as missing.
                while stamp > now:
                    stamp -= timedelta(minutes=SLOT_MINUTES)
                return stamp, "lastupdate.txt"
        reason = "lists no export file"
    except Exception as e:
        reason = _describe_failure(e)
    # Files are stamped on a fixed 15-minute grid, so the clock can stand in
    # for the index. Ending one slot before the current one keeps clear of a
    # file still being written; one missing anyway just counts as missing.
    slot = now.replace(minute=now.minute - now.minute % SLOT_MINUTES, second=0, microsecond=0)
    return slot - timedelta(minutes=SLOT_MINUTES), f"the clock (lastupdate.txt {reason})"


def _fetch_file(session, moment: datetime):
    """The zipped export file for one slot; None when GDELT never wrote it."""
    response = session.get(f"{GDELT_FILES_URL}{_stamp(moment)}.export.CSV.zip", timeout=REQUEST_TIMEOUT)
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return response.content


def _read_window(session, slots: list, targets: dict, tallies: dict, deadline: float) -> dict:
    stats = {"read": 0, "missing": 0, "failed": 0, "bytes": 0, "malformed_rows": 0,
             "first_added": None, "last_added": None, "stopped": None}
    window = (_stamp(slots[-1]), _stamp(slots[0]))
    pool = ThreadPoolExecutor(max_workers=DOWNLOAD_WORKERS, thread_name_prefix="gdelt-files")
    futures = {pool.submit(_fetch_file, session, moment): moment for moment in slots}
    try:
        for future in as_completed(futures, timeout=max(deadline - time.monotonic(), 0.1)):
            try:
                content = future.result()
            except Exception:
                stats["failed"] += 1
                continue
            if content is None:
                stats["missing"] += 1
                continue
            stats["bytes"] += len(content)
            # Parsed here, on the main thread, while the pool keeps downloading.
            try:
                parsed = parse_export(content, targets, tallies, window)
            except (zipfile.BadZipFile, OSError, UnicodeError):
                stats["failed"] += 1
                continue
            stats["read"] += 1
            stats["malformed_rows"] += parsed["malformed"]
            for key, pick in (("first_added", min), ("last_added", max)):
                if parsed[key]:
                    stats[key] = parsed[key] if stats[key] is None else pick(stats[key], parsed[key])
            if stats["bytes"] > MAX_BYTES:
                stats["stopped"] = f"stopped at the {MAX_BYTES // 1_000_000} MB cap"
                break
    except FuturesTimeout:
        stats["stopped"] = f"stopped at the {TIME_BUDGET_SEC}s budget"
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    return stats


def _iso(stamp: str | None) -> str | None:
    if not stamp:
        return None
    return datetime.strptime(stamp, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc).isoformat()


def fetch_gdelt_from_files(countries: list[str], relevance_for_country: dict, now=None) -> tuple:
    """Per-country GDELT readings from the last WINDOW_HOURS of event files.

    relevance_for_country maps a country to relevance(title, url) -> int; an
    article is listed only when that scores above 0, the top TOP_ARTICLES by
    relevance and then most negative tone. A country without a callable gets
    its tone and counts but no headlines.

    Returns (geopolitical_intel, source_status). geopolitical_intel has one
    entry per country with events in the window, in the shape the page
    renders today plus event_count, event_mix and goldstein_avg. Nothing is
    returned for any country when too little of the window could be read, so
    the caller keeps its previous readings. See README "GDELT event files".
    """
    started = time.monotonic()
    now = datetime.now(timezone.utc) if now is None else (
        now.replace(tzinfo=timezone.utc) if now.tzinfo is None else now.astimezone(timezone.utc)
    )
    label = window_label(WINDOW_HOURS)
    countries = list(countries or [])
    relevance_for_country = relevance_for_country or {}
    status = {
        "status": "failed",
        "detail": "",
        "window": label,
        "sample_every": SAMPLE_EVERY,
        "countries": {},
    }
    session = None
    targets, unmapped = {}, []
    try:
        for country in countries:
            code = FIPS_CODES.get(country)
            if code:
                targets[code] = country
            else:
                unmapped.append(country)
        if unmapped:
            logger.warning(f"GDELT files: no FIPS code for {', '.join(unmapped)} — add it to FIPS_CODES")
        if not targets:
            status.update(
                status="empty",
                detail="no requested country has a FIPS code, so nothing was downloaded",
                countries={country: "unmapped" for country in countries},
            )
            return {}, status
        tallies = {country: CountryTally() for country in targets.values()}

        session = _open_session()
        latest, latest_from = latest_file_time(session, now)
        slots = window_slots(latest)
        stats = _read_window(session, slots, targets, tallies, started + TIME_BUDGET_SEC)
        seconds = round(time.monotonic() - started, 1)
        status.update({
            "latest_file": _stamp(latest),
            "latest_from": latest_from,
            "files_expected": len(slots),
            "files_read": stats["read"],
            "files_missing": stats["missing"],
            "files_failed": stats["failed"],
            "bytes": stats["bytes"],
            "seconds": seconds,
            "window_start": _iso(stats["first_added"]),
            "window_end": _iso(stats["last_added"]),
        })
        summary = (
            f"{stats['read']} of {len(slots)} files ({stats['bytes'] / 1e6:.1f} MB) "
            f"up to {_when(latest)} UTC, in {seconds}s"
        )
        extras = [note for note in (
            stats["stopped"],
            f"{stats['missing']} not published" if stats["missing"] else "",
            f"{stats['failed']} failed" if stats["failed"] else "",
            f"{stats['malformed_rows']} malformed rows skipped" if stats["malformed_rows"] else "",
            f"newest file taken from {latest_from}" if latest_from != "lastupdate.txt" else "",
        ) if note]
        age_hours = (now - latest).total_seconds() / 3600
        if age_hours > 2:
            extras.append(f"GDELT's newest file is {age_hours:.0f}h old")
        detail = summary + (f"; {'; '.join(extras)}" if extras else "")

        if stats["read"] < MIN_COVERAGE * len(slots):
            status.update(
                status="failed",
                detail=f"too little of the window to publish: {detail}",
                countries={c: ("unmapped" if c in unmapped else "failed") for c in countries},
            )
            logger.warning(f"GDELT files: {status['detail']}")
            return {}, status

        fetched_at = now.isoformat()
        intel, per_country = {}, {}
        for country in countries:
            if country in unmapped:
                per_country[country] = "unmapped"
                continue
            tally = tallies[country]
            if not tally.events:
                per_country[country] = "empty"
                continue
            intel[country] = tally.reading(relevance_for_country.get(country), fetched_at, label, country)
            per_country[country] = "ok"
        status.update(
            status="ok" if intel else "empty",
            detail=detail,
            countries=per_country,
        )
        logger.info(
            f"GDELT files: {detail}; {len(intel)}/{len(countries)} countries with events"
        )
        return intel, status
    except Exception as e:  # the harvest must go on whatever GDELT does
        status.update(
            status="failed",
            detail=f"unexpected error ({type(e).__name__})",
            countries={c: ("unmapped" if c in unmapped else "failed") for c in countries},
        )
        logger.warning(f"GDELT files: {status['detail']}")
        return {}, status
    finally:
        if session is not None:
            try:
                session.close()
            except Exception:
                pass
