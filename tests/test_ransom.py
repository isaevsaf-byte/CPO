"""Tests for scripts/ransom.py: RansomLook leak-site claims.

Posts are the ones RansomLook returned on 2026-09-29 for /api/recent and for
searches on supplier names, trimmed; the one made-up claim is marked. No
network.
"""

import json
from datetime import datetime, timezone

import requests

NOW = datetime(2026, 9, 29, 8, 0, tzinfo=timezone.utc)

NAME_TERMS = {
    "AMCOR": ["AMCOR", "AMCR"],
    "Sappi": ["SAPPI", "SAPPI LIMITED"],
    "Jabil": ["JABIL", "JABIL INC"],
    "Porton": ["PORTON"],
    "Rosti": ["ROSTI"],
    "SWM (Mativ)": ["SWM (MATIV)", "MATIV", "SCHWEITZER-MAUDUIT"],
    "Huizhou BYD Electronic": ["HUIZHOU BYD ELECTRONIC", "BYD ELECTRONIC", "HUIZHOU BYD"],
    "Fuji": ["FUJI"],
    "CNT": ["CNT"],
}

RECENT = [
    {"post_title": "Netech (Neeser Technik AG)", "discovered": "2026-09-29 08:40:30.868685",
     "group_name": "payload", "link": "/posts/b83c2a4c-304d-4cdb-b0fa-3625978942ec",
     "description": "Swiss engineering and industrial services company"},
    {"post_title": "The Center for Kidney Care", "discovered": "2026-09-28 19:43:50.885921",
     "group_name": "interlock",
     "link": "http://vjc7ovbxy3q27t2wffvvwbjodzdu2i3eonbvsippxdva3awmxwyv52ad.onion/index.php?p="},
    {"post_title": "camorim.com.br", "discovered": "2026-09-28 23:41:33.011524", "group_name": "lockbit5",
     "link": "/post/30c6cb9f5d80f0e5bdc2a414552bea98"},
]

CHIP_1 = {"post_title": "Chip 1 Exchange", "discovered": "2026-09-02 09:38:44.932911", "group_name": "aurora",
          "description": "Chip 1 Exchange - a global independent electronics distributor ... ITAR registration "
                         "and defense customer sales orders to Jabil Defense, Curtis-Wright, GEN3 Defense",
          "link": "/blog/chip-1-exchange-13b819b4"}

SEARCH = {
    "jabil": [CHIP_1],
    "porton": [{"post_title": "SPORTON International Inc.", "discovered": "2026-06-16 10:00:00",
                "group_name": "payload"}],
    "huizhou byd": [],
    "byd electronic": [],
    "amcor": [
        {"post_title": "Amcor", "discovered": "2025-12-12 16:27:05.843666", "group_name": "coinbase cartel",
         "link": "/companies/amcor"},
        {"post_title": "amcor", "discovered": "2025-11-25 22:29:24.812706", "group_name": "coinbase cartel",
         "link": "/companies/amcor"},
        {"post_title": "Amcore", "discovered": "2025-11-25 07:28:12.393009", "group_name": "coinbase cartel",
         "link": "/companies/amcore"},
    ],
    "sappi": [{"post_title": "Sappi", "discovered": "2022-08-18 00:00:00", "group_name": "karakurt"}],
    "rosti": [{"post_title": "Electrostim Medical Services", "discovered": "2023-05-17 00:00:00",
               "group_name": "alphv"}],
    "mativ": [{"post_title": "Transformative Healthcare", "discovered": "2023-04-26 00:00:00",
               "group_name": "alphv"}],
    "schweitzer mauduit": [],
}
# What a search for "BYD" returned.
BYDGOSZCZ = {"post_title": "PESA Bydgoszcz", "discovered": "2023-04-11 00:00:00", "group_name": "play"}

# Made up: no supplier has a claim in the 30 days to 2026-09-29.
MADE_UP_CLAIM = {"post_title": "jabil.com", "discovered": "2026-09-27 12:00:00", "group_name": "made up group",
                 "link": "http://example.onion/jabil"}


class FakeResponse:
    def __init__(self, status_code=200, json_data=None):
        self.status_code = status_code
        self._json = json_data

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} Server Error")


class FakeRansomLook:
    def __init__(self, recent=RECENT, search=SEARCH, fail=()):
        self.recent, self.search, self.fail = recent, search, set(fail)
        self.queries = []

    def get(self, url, params=None, **kwargs):
        if url.endswith("/api/recent"):
            if "recent" in self.fail:
                return FakeResponse(status_code=502)
            return FakeResponse(json_data=self.recent)
        if url.endswith("/api/search"):
            query = params["q"]
            self.queries.append(query)
            if "search" in self.fail:
                return FakeResponse(status_code=429)
            return FakeResponse(json_data={"groups": [], "markets": [], "posts": self.search.get(query, []),
                                           "leaks": [], "notes": []})
        raise AssertionError(f"unexpected GET {url}")


def terms(ransom):
    return {s: ransom.name_matching.usable_terms(t) for s, t in NAME_TERMS.items()}


def test_a_name_in_the_description_is_not_a_claim(ransom):
    assert ransom.match_claims([CHIP_1], terms(ransom), NOW) == {}


def test_names_inside_other_words_are_not_claims(ransom):
    posts = [BYDGOSZCZ, *SEARCH["porton"], *SEARCH["rosti"], *SEARCH["mativ"], SEARCH["amcor"][2]]
    assert ransom.match_claims(posts, terms(ransom), NOW, lookback_days=3650) == {}


def test_old_claims_fall_outside_the_window(ransom):
    posts = SEARCH["amcor"] + SEARCH["sappi"]
    assert ransom.match_claims(posts, terms(ransom), NOW) == {}
    hits = ransom.match_claims(posts, terms(ransom), NOW, lookback_days=3650)
    # The group posted Amcor twice; the newest post is kept.
    assert hits == {
        "AMCOR": [{"group": "coinbase cartel", "title": "Amcor", "date": "2025-12-12",
                   "url": "https://www.ransomlook.io/group/coinbase%20cartel"}],
        "Sappi": [{"group": "karakurt", "title": "Sappi", "date": "2022-08-18",
                   "url": "https://www.ransomlook.io/group/karakurt"}],
    }


def test_domain_titles_match_and_link_to_ransomlook_not_the_leak_site(ransom):
    hits = ransom.match_claims([MADE_UP_CLAIM, *RECENT], terms(ransom), NOW)
    assert hits == {"Jabil": [{"group": "made up group", "title": "jabil.com", "date": "2026-09-27",
                               "url": "https://www.ransomlook.io/group/made%20up%20group"}]}


def test_undated_or_future_posts_are_ignored(ransom):
    posts = [dict(MADE_UP_CLAIM, discovered="not a date"), dict(MADE_UP_CLAIM, discovered="2027-01-01 00:00:00")]
    assert ransom.match_claims(posts, terms(ransom), NOW) == {}


def test_fetch_ransom_claims_contract(ransom):
    site = FakeRansomLook(recent=RECENT + [MADE_UP_CLAIM])
    result = ransom.fetch_ransom_claims(NAME_TERMS, NOW, session=site)
    assert set(result) == {"fetched_at", "hits", "sources", "unscreened", "duration_seconds"}
    assert {k: v["status"] for k, v in result["sources"].items()} == {
        "ransomlook_recent": "ok", "ransomlook_search": "ok"}
    assert list(result["hits"]) == ["Jabil"]
    assert result["unscreened"] == ["CNT", "Fuji"]
    # One search per name, none for a name another search already covers.
    assert sorted(site.queries) == sorted([
        "amcor", "sappi", "jabil", "porton", "rosti", "mativ", "schweitzer mauduit",
        "byd electronic", "huizhou byd"])
    json.dumps(result)


def test_an_empty_recent_feed_is_reported_as_empty(ransom):
    result = ransom.fetch_ransom_claims(NAME_TERMS, NOW, session=FakeRansomLook(recent=[]))
    assert result["sources"]["ransomlook_recent"]["status"] == "empty"


def test_failed_searches_are_reported_and_recent_still_counts(ransom):
    site = FakeRansomLook(recent=RECENT + [MADE_UP_CLAIM], fail={"search"})
    result = ransom.fetch_ransom_claims(NAME_TERMS, NOW, session=site)
    assert result["sources"]["ransomlook_search"]["status"] == "failed"
    assert "HTTPError" in result["sources"]["ransomlook_search"]["detail"]
    assert list(result["hits"]) == ["Jabil"]


def test_fetch_ransom_claims_never_raises(ransom):
    result = ransom.fetch_ransom_claims(NAME_TERMS, NOW, session=object())
    assert result["hits"] == {}
    assert {s["status"] for s in result["sources"].values()} == {"failed"}
    result = ransom.fetch_ransom_claims(None, NOW, session=FakeRansomLook())
    assert result["hits"] == {} and result["unscreened"] == []
