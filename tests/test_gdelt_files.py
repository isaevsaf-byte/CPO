"""Tests for scripts/gdelt_files.py — no network.

Event rows are built inline in GDELT's 61-column layout and zipped in memory,
so the parser, the tallies and the headline rules are exercised exactly as the
live files exercise them, and fetch_gdelt_from_files runs over a fake session.
"""

import importlib.util
import io
import json
import sys
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import requests

ROOT = Path(__file__).resolve().parent.parent
NOW = datetime(2026, 9, 29, 8, 40, tzinfo=timezone.utc)
LATEST = "20260929083000"

READING_KEYS = {
    # What the /geopolitical page renders today...
    "article_count", "avg_tone", "articles", "has_relevant", "query_mode", "window", "fetched_at",
    # ...and what the event files add.
    "event_count", "event_mix", "goldstein_avg",
}


@pytest.fixture(scope="module")
def gf():
    spec = importlib.util.spec_from_file_location("gdelt_files", ROOT / "scripts" / "gdelt_files.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["gdelt_files"] = module
    spec.loader.exec_module(module)
    return module


def row(country="GM", root="14", code="141", goldstein="-6.5", num_articles="1", tone="-4.0",
        url="https://example.com/world/hamburg-dockworkers-strike-halts-container-terminals",
        added="20260929080000"):
    cols = [""] * 61
    cols[0], cols[1] = "1270000001", added[:8]
    cols[26], cols[27], cols[28] = code, code[:3], root
    cols[30], cols[33], cols[34] = goldstein, num_articles, tone
    cols[53], cols[59], cols[60] = country, added, url
    return "\t".join(cols)


def zipped(rows, stamp="20260929080000"):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(f"{stamp}.export.CSV", "\n".join(rows) + "\n")
    return buffer.getvalue()


WINDOW = ("20260928083000", "20260929083000")


# ---------------------------------------------------------------------------
# Headlines from URLs — every case is a URL shape seen in the live files
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("url,title", [
    ("https://www.aol.com/articles/unicredits-andrea-orcel-moves-seize-050724000.html",
     "Unicredits andrea orcel moves seize"),
    ("https://aninews.in/news/national/politics/raj-and-uddhav-thackeray-announce-protest-march-against-ec-"
     "on-october-4-invite-opposition-parties-to-join20260929122440/",
     "Raj and uddhav thackeray announce protest march against ec on october 4 invite opposition parties to join"),
    ("https://www.postbulletin.com/obituaries/obits/sylvia-ann-swede-w3zgdnuvzbscpf9ltbl4",
     "Sylvia ann swede"),
    ("https://www.weny.com/news/harrisburg/state-reps-attempt-force-of-gift-ban-vote-in-harrisburg/"
     "article_59d82d4a-8bfb-5b23-aea0-d03bced381c8.html",
     "State reps attempt force of gift ban vote in harrisburg"),
    ("https://www.swissinfo.ch/eng/business/american-farmers-pursue-syngenta-over-herbicide-s-link-to-"
     "parkinson-s-disease/47099082",
     "American farmers pursue syngenta over herbicide's link to parkinson's disease"),
    ("https://thetyee.ca/News/2026/09/29/Pump-Brakes-West-Coast-Oil-Pipeline/",
     "Pump Brakes West Coast Oil Pipeline"),
    ("https://article.wn.com/view/2026/09/28/Germany_backs_ICC_as_Trump_steps_up_sanctions_threat/",
     "Germany backs ICC as Trump steps up sanctions threat"),
    ("https://www.hindustantimes.com/india-news/indias-own-list-of-top-10-terrorists-goldie-brar-on-56-"
     "101790644820639.html",
     "Indias own list of top 10 terrorists goldie brar on 56"),
    ("https://www.reuters.com/world/china/china-tightens-rare-earth-export-controls-2026-09-28/",
     "China tightens rare earth export controls"),
    ("https://example.com/2026/09/28/20260928-port-strike-spreads-to-rotterdam", "Port strike spreads to rotterdam"),
    ("https://example.com/news/we-don-t-expect-a-quick-reopening", "We don't expect a quick reopening"),
    ("https://example.com/world/u-s-and-china-agree-tariff-truce", "US and china agree tariff truce"),
])
def test_headline_from_slug(gf, url, title):
    assert gf.title_from_url(url) == title


@pytest.mark.parametrize("url", [
    "https://allafrica.com/stories/202609290003.html",
    "https://www.koreaherald.com/article/10887733",
    "https://example.com/news.php?id=123",
    "https://example.com/tech/ai-rules",
    "not a url at all",
])
def test_no_headline_without_a_readable_slug(gf, url):
    assert gf.title_from_url(url) is None


# ---------------------------------------------------------------------------
# Parsing and tallying
# ---------------------------------------------------------------------------

def test_parse_keeps_only_target_countries_inside_the_window(gf):
    tallies = {"Germany": gf.CountryTally(), "Switzerland": gf.CountryTally()}
    rows = [
        row(country="GM"),
        row(country="SZ", url="https://example.com/a/swiss-franc-strength-weighs-on-exporters"),
        row(country="AS"),                        # Australia, which is not Austria
        row(country="GM", added="20260927080000"),  # older than the window
        "\t".join(["x"] * 60),                    # a truncated line
    ]
    parsed = gf.parse_export(zipped(rows), {"GM": "Germany", "SZ": "Switzerland"}, tallies, WINDOW)
    assert parsed["kept"] == 2 and parsed["malformed"] == 1
    assert (parsed["first_added"], parsed["last_added"]) == ("20260929080000", "20260929080000")
    assert tallies["Germany"].events == 1 and tallies["Switzerland"].events == 1


def test_event_mix_counts_the_cameo_families(gf):
    tally = gf.CountryTally()
    for root, code in [("14", "141"), ("14", "145"), ("16", "163"), ("17", "172"),
                       ("18", "180"), ("19", "190"), ("20", "202"), ("04", "042")]:
        tally.add(root, code, -5.0, 1, -2.0, f"https://example.com/x/{code}")
    assert tally.reading(None, "t", "1d")["event_mix"] == {
        "protest": 2, "sanctions_embargo": 1, "coerce": 1, "assault": 1, "fight": 1, "mass_violence": 1,
    }


def test_tone_is_weighted_by_coverage_and_articles_are_counted_once(gf):
    tally = gf.CountryTally()
    url_a = "https://example.com/world/strike-closes-port-of-hamburg"
    tally.add("14", "141", -6.5, 3, -10.0, url_a)
    tally.add("19", "190", -10.0, 3, -10.0, url_a)   # a second event from the same article
    tally.add("04", "042", 1.9, 1, 2.0, "https://example.com/world/ministers-meet-in-berlin-on-trade")
    reading = tally.reading(None, "t", "1d")
    assert reading["article_count"] == 2
    assert reading["event_count"] == 3
    assert reading["avg_tone"] == pytest.approx((-10 * 3 - 10 * 3 + 2 * 1) / 7, abs=0.05)
    assert reading["goldstein_avg"] == pytest.approx((-6.5 * 3 - 10 * 3 + 1.9) / 7, abs=0.05)


def test_only_relevant_articles_are_listed_best_first(gf):
    tally = gf.CountryTally()
    stories = {
        "https://a.com/n/infineon-halts-dresden-fab-after-power-cut": -3.0,
        "https://b.com/n/semiconductor-exports-slow-in-germany": -6.0,
        "https://c.com/n/semiconductor-exports-slow-in-germany": -6.5,   # same story, second outlet
        "https://d.com/n/germany-port-strike-enters-third-day": -8.0,
        "https://e.com/n/local-football-club-wins-derby-match": -9.0,
    }
    for url, tone in stories.items():
        tally.add("14", "141", -6.5, 1, tone, url)

    def relevance(title, url):
        title = title.lower()
        if "infineon" in title:
            return 3
        if "semiconductor" in title:
            return 2
        if "port strike" in title:
            return 1
        return 0

    reading = tally.reading(relevance, "2026-09-29T08:40:00+00:00", "1d")
    assert reading["has_relevant"] is True
    assert [a["url"] for a in reading["articles"]] == [
        "https://a.com/n/infineon-halts-dresden-fab-after-power-cut",
        "https://c.com/n/semiconductor-exports-slow-in-germany",   # more negative copy kept
        "https://d.com/n/germany-port-strike-enters-third-day",
    ]
    assert reading["articles"][0] == {
        "title": "Infineon halts dresden fab after power cut",
        "url": "https://a.com/n/infineon-halts-dresden-fab-after-power-cut",
        "tone": -3.0,
    }
    assert reading["article_count"] == 5


def test_top_five_only(gf):
    tally = gf.CountryTally()
    for i in range(8):
        tally.add("14", "141", -6.5, 1, -float(i), f"https://x.com/n/port-strike-day-number-{'abcdefgh'[i]}-continues")
    reading = tally.reading(lambda title, url: 1, "t", "1d")
    assert len(reading["articles"]) == gf.TOP_ARTICLES
    assert [a["tone"] for a in reading["articles"]] == [-7.0, -6.0, -5.0, -4.0, -3.0]


def test_a_failing_or_missing_relevance_check_lists_nothing(gf):
    tally = gf.CountryTally()
    tally.add("14", "141", -6.5, 1, -4.0, "https://x.com/n/port-strike-spreads-across-the-country")

    def broken(title, url):
        raise RuntimeError("boom")

    for relevance in (broken, None):
        reading = tally.reading(relevance, "t", "1d")
        assert reading["articles"] == [] and reading["has_relevant"] is False
        assert reading["avg_tone"] == -4.0


@pytest.mark.parametrize("hours,label", [(24, "1d"), (72, "3d"), (6, "6h")])
def test_window_label_speaks_the_pages_language(gf, hours, label):
    assert gf.window_label(hours) == label


# ---------------------------------------------------------------------------
# fetch_gdelt_from_files over a fake session
# ---------------------------------------------------------------------------

class FakeResponse:
    def __init__(self, status=200, content=b"", text=""):
        self.status_code, self.content, self.text = status, content, text

    def raise_for_status(self):
        if self.status_code >= 400:
            response = requests.Response()
            response.status_code = self.status_code
            raise requests.HTTPError(f"{self.status_code}", response=response)


class FakeSession:
    """Serves lastupdate.txt and a window of export files from memory."""

    def __init__(self, files, lastupdate=LATEST, lastupdate_status=200):
        self.files, self.lastupdate, self.lastupdate_status = files, lastupdate, lastupdate_status
        self.requested = []

    def get(self, url, timeout=None):
        self.requested.append(url)
        if url.endswith("lastupdate.txt"):
            body = (f"60359 3f73 http://data.gdeltproject.org/gdeltv2/{self.lastupdate}.export.CSV.zip\n"
                    f"86339 8543 http://data.gdeltproject.org/gdeltv2/{self.lastupdate}.mentions.CSV.zip\n")
            return FakeResponse(self.lastupdate_status, text=body)
        stamp = url.rsplit("/", 1)[1][:14]
        if stamp in self.files:
            return FakeResponse(200, content=self.files[stamp])
        return FakeResponse(404)

    def close(self):
        pass


def stamps_back(latest: str, count: int) -> list:
    moment = datetime.strptime(latest, "%Y%m%d%H%M%S")
    return [(moment - timedelta(minutes=15 * i)).strftime("%Y%m%d%H%M%S") for i in range(count)]


@pytest.fixture
def one_hour(gf, monkeypatch):
    """A four-file window, so a fake day stays small."""
    monkeypatch.setattr(gf, "WINDOW_HOURS", 1)
    return gf


def day_of_files(stamps, extra_rows=()):
    files = {}
    for stamp in stamps:
        rows = [
            row(country="GM", added=stamp, url=f"https://x.com/n/germany-port-strike-day-{stamp[-6:-2]}-deepens"),
            row(country="SZ", root="04", code="042", tone="1.5", added=stamp,
                url="https://y.com/n/swiss-trade-talks-continue-in-bern"),
        ] + [r.replace("20260929080000", stamp) for r in extra_rows]
        files[stamp] = zipped(rows, stamp)
    return files


def test_reads_the_window_into_the_pages_shape(one_hour, monkeypatch):
    gf = one_hour
    session = FakeSession(day_of_files(stamps_back(LATEST, 4)))
    monkeypatch.setattr(gf, "_open_session", lambda: session)
    relevance = {"Germany": lambda title, url: 1 if "port strike" in title.lower() else 0}

    intel, status = gf.fetch_gdelt_from_files(["Germany", "Switzerland", "Mexico"], relevance, now=NOW)

    assert set(intel) == {"Germany", "Switzerland"}
    germany = intel["Germany"]
    assert set(germany) == READING_KEYS
    assert germany["query_mode"] == "events"
    assert germany["window"] == "1h"
    assert germany["fetched_at"] == NOW.isoformat()
    assert germany["article_count"] == 4 and germany["event_count"] == 4
    assert germany["has_relevant"] is True and len(germany["articles"]) == 4
    assert intel["Switzerland"]["has_relevant"] is False and intel["Switzerland"]["avg_tone"] == 1.5

    assert status["status"] == "ok"
    assert (status["files_expected"], status["files_read"], status["files_missing"]) == (4, 4, 0)
    assert status["latest_file"] == LATEST and status["latest_from"] == "lastupdate.txt"
    assert status["window_start"] == "2026-09-29T07:45:00+00:00"
    assert status["window_end"] == "2026-09-29T08:30:00+00:00"
    assert status["countries"] == {"Germany": "ok", "Switzerland": "ok", "Mexico": "unmapped"}


def test_a_missing_slot_is_tolerated_but_a_thin_window_is_not_published(one_hour, monkeypatch):
    gf = one_hour
    stamps = stamps_back(LATEST, 4)

    files = day_of_files(stamps[:3])   # one slot never published: 3 of 4 is enough
    monkeypatch.setattr(gf, "_open_session", lambda: FakeSession(files))
    intel, status = gf.fetch_gdelt_from_files(["Germany"], {}, now=NOW)
    assert intel and status["status"] == "ok" and status["files_missing"] == 1

    files = day_of_files(stamps[:2])   # half the window: keep the previous reading
    monkeypatch.setattr(gf, "_open_session", lambda: FakeSession(files))
    intel, status = gf.fetch_gdelt_from_files(["Germany"], {}, now=NOW)
    assert intel == {}
    assert status["status"] == "failed" and status["detail"].startswith("too little of the window")
    assert status["countries"] == {"Germany": "failed"}


def test_the_clock_stands_in_when_lastupdate_is_down(one_hour, monkeypatch):
    gf = one_hour
    # 08:52 on the clock: the 08:45 slot may not be written yet, so the
    # window ends one slot earlier, at 08:30.
    session = FakeSession(day_of_files(stamps_back(LATEST, 4)), lastupdate_status=503)
    monkeypatch.setattr(gf, "_open_session", lambda: session)
    now = datetime(2026, 9, 29, 8, 52, tzinfo=timezone.utc)
    intel, status = gf.fetch_gdelt_from_files(["Germany"], {}, now=now)
    assert status["status"] == "ok"
    assert status["latest_file"] == LATEST
    assert status["latest_from"] == "the clock (lastupdate.txt HTTP 503)"
    assert "newest file taken from the clock" in status["detail"]


def test_a_file_listed_ahead_of_the_clock_is_not_waited_for(one_hour, monkeypatch):
    """At 08:26 UTC on 29 Sep 2026 lastupdate.txt already named the 08:30
    file, and fetching it returned 404."""
    gf = one_hour
    now = datetime(2026, 9, 29, 8, 26, tzinfo=timezone.utc)
    session = FakeSession(day_of_files(stamps_back("20260929081500", 4)), lastupdate=LATEST)
    monkeypatch.setattr(gf, "_open_session", lambda: session)
    intel, status = gf.fetch_gdelt_from_files(["Germany"], {}, now=now)
    assert status["latest_file"] == "20260929081500"
    assert (status["files_read"], status["files_missing"]) == (4, 0)


def test_nothing_raises_when_everything_is_down(one_hour, monkeypatch):
    gf = one_hour

    class Down:
        def get(self, url, timeout=None):
            raise requests.ConnectionError("no route to host")

        def close(self):
            pass

    monkeypatch.setattr(gf, "_open_session", lambda: Down())
    intel, status = gf.fetch_gdelt_from_files(["Germany", "China"], {}, now=NOW)
    assert intel == {}
    assert status["status"] == "failed"
    assert status["files_read"] == 0 and status["files_failed"] == 4


def test_nothing_to_do_without_a_mappable_country(gf, monkeypatch):
    def unexpected():
        raise AssertionError("no download should start")

    monkeypatch.setattr(gf, "_open_session", unexpected)
    intel, status = gf.fetch_gdelt_from_files(["Atlantis"], {}, now=NOW)
    assert intel == {} and status["status"] == "empty"
    assert status["countries"] == {"Atlantis": "unmapped"}


def test_every_supplier_country_has_a_fips_code(gf):
    watchlist = json.loads((ROOT / "data" / "suppliers.json").read_text())
    assert {s["location"] for s in watchlist["suppliers"]} <= set(gf.FIPS_CODES)
