"""Tests for scripts/screening.py: Consolidated Screening List, UFLPA Entity
List and Federal Register notices.

Fixtures are rows, page fragments and titles taken from the live sources on
2026-09-29, except where a test says a row is made up. No network.
"""

import csv
import io
import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest
import requests

NOW = datetime(2026, 9, 29, 8, 0, tzinfo=timezone.utc)
DATA_DIR = Path(__file__).resolve().parent.parent / "data"

SUPPLIERS = [
    {"name": "AMCOR", "category": "Printed Packaging", "location": "Switzerland"},
    {"name": "GPI", "category": "Printed Packaging", "location": "USA"},
    {"name": "IP Sun", "category": "Printing Substrates", "location": "China"},
    {"name": "Cerdia", "category": "Filter Materials", "location": "Germany"},
    {"name": "CNT", "category": "Nicotine", "location": "Switzerland"},
    {"name": "ITC", "category": "Nicotine", "location": "India"},
    {"name": "Porton", "category": "Nicotine", "location": "China"},
    {"name": "Tenowo", "category": "Modern/Traditional Oral Fleece", "location": "Germany"},
    {"name": "Huizhou BYD Electronic", "category": "EMS", "location": "China"},
    {"name": "Smoore", "category": "EMS", "location": "China"},
    {"name": "EVE Energy", "category": "Batteries", "location": "China"},
    {"name": "Infineon", "category": "EE Component", "location": "Germany"},
    {"name": "Jabil", "category": "Mechanical", "location": "USA"},
    {"name": "Fuji", "category": "Capsules", "location": "Japan"},
]

# What update_intel.supplier_search_terms returns for these suppliers.
NAME_TERMS = {
    "AMCOR": ["AMCOR", "AMCR"],
    "GPI": ["GRAPHIC PACKAGING", "GRAPHIC PACKAGING INTERNATIONAL"],
    "IP Sun": ["IP SUN"],
    "Cerdia": ["CERDIA"],
    "CNT": ["CNT"],
    "ITC": ["ITC LIMITED", "ITC LTD"],
    "Porton": ["PORTON"],
    "Tenowo": ["TENOWO"],
    "Huizhou BYD Electronic": ["HUIZHOU BYD ELECTRONIC", "BYD ELECTRONIC", "HUIZHOU BYD"],
    "Smoore": ["SMOORE", "SMOORE INTERNATIONAL"],
    "EVE Energy": ["EVE ENERGY"],
    "Infineon": ["INFINEON", "INFINEON TECHNOLOGIES"],
    "Jabil": ["JABIL", "JABIL INC"],
    "Fuji": ["FUJI"],
}

PARENTS = {
    "Huizhou BYD Electronic": {
        "match_terms": ["HUIZHOU BYD ELECTRONIC"],
        "parents": [
            {"name": "BYD Electronic (International) Company Limited", "match_terms": ["BYD ELECTRONIC"]},
            {"name": "BYD Company Limited", "match_terms": ["BYD COMPANY", "BYD CO"]},
        ],
    },
    "CNT": {"match_terms": ["CONTRAF NICOTEX TOBACCO", "CONTRAF NICOTEX"], "parents": []},
}

CSL_COLUMNS = [
    "_id", "source", "entity_number", "type", "programs", "name", "title", "addresses",
    "federal_register_notice", "start_date", "end_date", "standard_order", "license_requirement",
    "license_policy", "call_sign", "vessel_type", "gross_tonnage", "gross_registered_tonnage",
    "vessel_flag", "vessel_owner", "remarks", "source_list_url", "alt_names", "citizenships",
    "dates_of_birth", "nationalities", "places_of_birth", "source_information_url", "ids",
]
SDN = "Specially Designated Nationals (SDN) - Treasury Department"
ENTITY_LIST = "Entity List (EL) - Bureau of Industry and Security"


def csl_csv(*rows) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=CSL_COLUMNS)
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return buffer.getvalue()


CSL_ROWS = [
    {"source": SDN, "type": "Individual", "programs": "SDGT", "name": "DELLOSA, Redendo Cain",
     "addresses": "3111 Ma. Bautista Street, Punta, Santa Ana, Manila, PH",
     "alt_names": "DELLOSA Y CAIN, Redendo; DELLOSA, Redendo Cain Jabil; AKMAL, Hakid",
     "source_list_url": "https://sanctionslist.ofac.treas.gov/Home/SdnList"},
    {"source": SDN, "type": "Aircraft", "programs": "IRAN", "name": "EP-ITC"},
    {"source": SDN, "type": "Entity", "programs": "RUSSIA-EO14024", "name": "ITC CONSULTANTS CYPRUS LIMITED",
     "addresses": "Christodoylou Chatzipaylou 221 Helios Court, Floor No. 1, Limassol, 3036, CY"},
    {"source": SDN, "type": "Entity", "programs": "RUSSIA-EO14024",
     "name": "A.M. PROKHOROV GENERAL PHYSICS INSTITUTE RUSSIAN ACADEMY OF SCIENCES",
     "addresses": "d. 38, ul. Vavilova, Moscow, 119991, RU",
     "alt_names": "PROKHOROV GENERAL PHYSICS INSTITUTE OF RAS; GPI RAS; IOF RAN"},
    {"source": ENTITY_LIST, "type": "",
     "name": "China Aerospace Science and Technology Corporation (CASC) 1st Academy 702 Research Institute",
     "addresses": "No. 30 Wanyuan Road, Beijing, CN", "start_date": "1999-05-28",
     "alt_names": "702nd Institute; Beijing Institute of Structure and Environmental Engineering (BISE) ",
     "source_list_url": "https://www.bis.gov/regulations/ear/744#supplement-4-744"},
]

# Made up: BYD is not on any list. It stands in for a listing of a parent.
MADE_UP_PARENT_ROW = {
    "source": ENTITY_LIST, "type": "", "programs": "", "name": "BYD Co., Ltd.",
    "addresses": "No. 3009, BYD Road, Pingshan, Shenzhen, CN; 1 Example Road, Hong Kong, HK",
    "start_date": "2026-09-01", "alt_names": "Build Your Dreams",
    "source_list_url": "https://www.bis.gov/regulations/ear/744#supplement-4-744",
}


class FakeResponse:
    def __init__(self, status_code=200, json_data=None, text=""):
        self.status_code = status_code
        self._json = json_data
        self.text = text
        self.content = text.encode("utf-8")

    def json(self):
        if self._json is None:
            raise ValueError("not JSON")
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} Server Error")

    def iter_content(self, chunk_size=1):
        for start in range(0, len(self.content), chunk_size):
            yield self.content[start:start + chunk_size]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeSession:
    def __init__(self, handler):
        self.handler = handler

    def get(self, url, params=None, **kwargs):
        return self.handler(url, params or [])


# ---------------------------------------------------------------------------
# Name matching
# ---------------------------------------------------------------------------
def test_whole_word_matching_after_folding_punctuation(screening):
    nm = screening.name_matching
    # The DHS page separates words with &nbsp;.
    assert nm.first_match(nm.normalize("Xinjiang\xa0Goens\xa0Energy Technology Co., Ltd."),
                          ["XINJIANG GOENS"]) == "XINJIANG GOENS"
    # A domain-style title folds into words.
    assert nm.first_match(nm.normalize("jabil.com"), ["JABIL"]) == "JABIL"
    # Inside another word is not a match.
    assert nm.first_match(nm.normalize("SPORTON International Inc."), ["PORTON"]) is None
    assert nm.first_match(nm.normalize("PESA Bydgoszcz"), ["BYD ELECTRONIC", "HUIZHOU BYD"]) is None
    # A trailing bracket no longer defeats the match.
    assert nm.usable_terms(["SWM (MATIV)"]) == ["SWM MATIV"]


def test_short_terms_are_never_screened_alone(screening):
    nm = screening.name_matching
    assert nm.usable_terms(["ITC LIMITED", "ITC LTD", "ITC"]) == ["ITC LIMITED", "ITC LTD"]
    assert nm.usable_terms(["CNT"]) == []
    assert nm.usable_terms(["FUJI"]) == []
    assert nm.usable_terms(["AMCOR", "AMCR"]) == ["AMCOR"]


def test_covering_terms_drop_searches_a_shorter_term_already_covers(screening):
    nm = screening.name_matching
    assert nm.covering_terms(["SWM MATIV", "MATIV", "SCHWEITZER MAUDUIT"]) == ["MATIV", "SCHWEITZER MAUDUIT"]
    assert set(nm.covering_terms(["HUIZHOU BYD ELECTRONIC", "BYD ELECTRONIC", "HUIZHOU BYD"])) == {
        "BYD ELECTRONIC", "HUIZHOU BYD"}


def test_legal_names_rescue_a_supplier_whose_watchlist_name_is_too_short(screening):
    terms = screening.build_screening_terms(NAME_TERMS, PARENTS)
    assert terms["CNT"] == [("CONTRAF NICOTEX TOBACCO", "name", None), ("CONTRAF NICOTEX", "name", None)]
    assert terms["Fuji"] == []
    assert ("BYD CO", "parent", "BYD Company Limited") in terms["Huizhou BYD Electronic"]
    # BYD ELECTRONIC is already the supplier's own alias, so it stays a name match.
    assert ("BYD ELECTRONIC", "name", None) in terms["Huizhou BYD Electronic"]


def test_expand_name_terms_adds_legal_names_but_not_parents(screening):
    expanded = screening.expand_name_terms({"CNT": ["CNT"], "Huizhou BYD Electronic": ["BYD ELECTRONIC"]}, PARENTS)
    assert expanded["CNT"] == ["CNT", "CONTRAF NICOTEX TOBACCO", "CONTRAF NICOTEX"]
    assert "BYD CO" not in expanded["Huizhou BYD Electronic"]


# ---------------------------------------------------------------------------
# Consolidated Screening List
# ---------------------------------------------------------------------------
def test_people_vessels_and_aircraft_are_not_compared_with_suppliers(screening):
    entries, total = screening.parse_csl(csl_csv(*CSL_ROWS))
    assert total == 5
    assert [e["entity"] for e in entries] == [
        "ITC CONSULTANTS CYPRUS LIMITED",
        "A.M. PROKHOROV GENERAL PHYSICS INSTITUTE RUSSIAN ACADEMY OF SCIENCES",
        "China Aerospace Science and Technology Corporation (CASC) 1st Academy 702 Research Institute",
    ]
    hits = screening.match_entries(entries, screening.build_screening_terms(NAME_TERMS, PARENTS))
    # Jabil was an SDN individual's alias; ITC and GPI are short-name collisions.
    assert hits == {}


def test_bare_short_names_would_have_matched(screening):
    """Why the guard exists: without it, ITC and GPI match unrelated entities."""
    entries, _ = screening.parse_csl(csl_csv(*CSL_ROWS))
    unguarded = {"ITC": [("ITC", "name", None)], "GPI": [("GPI", "name", None)]}
    hits = screening.match_entries(entries, unguarded)
    assert hits["ITC"][0]["entity"] == "ITC CONSULTANTS CYPRUS LIMITED"
    assert hits["GPI"][0]["entity"].startswith("A.M. PROKHOROV")


def test_a_listed_parent_is_reported_as_the_parent(screening):
    entries, _ = screening.parse_csl(csl_csv(*CSL_ROWS, MADE_UP_PARENT_ROW))
    hits = screening.match_entries(entries, screening.build_screening_terms(NAME_TERMS, PARENTS))
    assert list(hits) == ["Huizhou BYD Electronic"]
    hit = hits["Huizhou BYD Electronic"][0]
    assert hit == {
        "list": "BIS Entity List",
        "entity": "BYD Co., Ltd.",
        "programs": [],
        "country": "CN, HK",
        "source_url": "https://www.bis.gov/regulations/ear/744#supplement-4-744",
        "listed_since": "2026-09-01",
        "matched_term": "BYD CO",
        "via": "parent",
        "parent": "BYD Company Limited",
    }


def test_parent_term_does_not_reach_inside_a_longer_word(screening):
    row = dict(MADE_UP_PARENT_ROW, name="BYD Company Limited", alt_names="")
    entries, _ = screening.parse_csl(csl_csv(row))
    hits = screening.match_entries(entries, {"x": [("BYD CO", "parent", "BYD Company Limited")]})
    assert hits == {}  # "BYD CO" is not "BYD COMPANY"; that needs its own term
    hits = screening.match_entries(entries, {"x": [("BYD COMPANY", "parent", "BYD Company Limited")]})
    assert hits["x"][0]["entity"] == "BYD Company Limited"


def test_programs_and_countries_are_split(screening):
    row = {"source": SDN, "type": "Entity", "name": "JSC VTB BANK",
           "programs": "UKRAINE-EO13662; RUSSIA-EO14024; IRAN-EO13902",
           "addresses": "29, Bolshaya Morskaya str., St. Petersburg, 190000, RU; "
                        "3 Gagarinsky per., Moscow, 119034, RU; Limassol, CY"}
    entries, _ = screening.parse_csl(csl_csv(row))
    assert entries[0]["programs"] == ["UKRAINE-EO13662", "RUSSIA-EO14024", "IRAN-EO13902"]
    assert entries[0]["country"] == "RU, CY"
    assert entries[0]["list"] == "OFAC SDN"


def test_a_csl_without_its_columns_parses_to_nothing(screening):
    assert screening.parse_csl("<html>Service Unavailable</html>") == ([], 0)
    assert screening.parse_csl("") == ([], 0)


class TricklingResponse(FakeResponse):
    """A download that arrives slower than the time left for it."""

    def iter_content(self, chunk_size=1):
        for _ in range(10):
            time.sleep(0.3)
            yield b"x" * 1000


def test_csl_download_is_abandoned_mid_stream_at_the_deadline(screening):
    session = FakeSession(lambda url, params: TricklingResponse())
    started = time.monotonic()
    with pytest.raises(TimeoutError, match="ran out of time"):
        screening.fetch_csl_text(session, deadline=started + 1.5)
    assert time.monotonic() - started < 2.5


def test_csl_download_is_not_started_without_time_for_it(screening):
    session = FakeSession(lambda url, params: FakeResponse(text=csl_csv(*CSL_ROWS)))
    with pytest.raises(TimeoutError):
        screening.fetch_csl_text(session, deadline=time.monotonic() + 0.5)
    assert screening.fetch_csl_text(session, deadline=time.monotonic() + 30).startswith("_id,source")


# ---------------------------------------------------------------------------
# UFLPA Entity List
# ---------------------------------------------------------------------------
UFLPA_HTML = """
<div class="field--name-body"><p>The UFLPA Entity List is available here.</p>
<p class="text-align-center"><strong>A list of entities in Xinjiang that mine, produce, or manufacture
wholly or in part any goods, wares, articles and merchandise with forced labor</strong><br>
<a href="https://www.govinfo.gov/app/details/PLAW-117publ78">Section 2(d)(2)(B)(i)</a></p>
<table><thead><tr><th>Name of Entity</th><th>Effective Date</th></tr></thead><tbody>
<tr><td>Hoshine Silicon Industry (Shanshan) Co., Ltd (including one alias: Hesheng Silicon Industry
(Shanshan) Co.) and subsidiaries</td><td>June 21, 2022</td></tr>
<tr><td>Xinjiang&nbsp;Goens&nbsp;Energy Technology Co., Ltd. (formerly known as Xinjiang GCL New Energy
Material Technology Co., Ltd.; and Xinjiang GCL New Energy Materials Technology Co., Ltd.) </td>
<td>August 3, 2026</td></tr>
</tbody></table>
<p class="text-align-center"><strong>A list of facilities and entities, including the Xinjiang
Production and Construction Corps, that source material from Xinjiang</strong><br>
<a href="https://www.govinfo.gov/app/details/PLAW-117publ78">Section 2(d)(2)(B)(v)</a></p>
<table><thead><tr><th>Entity Name</th><th>Effective Date</th></tr></thead><tbody>
<tr><td>Shihezi Xinren Battery Aluminum Foil Technology Co., Ltd. </td><td>August 3, 2026</td></tr>
</tbody></table></div>
"""


def test_uflpa_rows_are_read_with_their_section_and_date(screening):
    entries = screening.parse_uflpa(UFLPA_HTML)
    assert [(e["entity"], e["listed_since"], e["programs"][0][:9]) for e in entries] == [
        ("Hoshine Silicon Industry (Shanshan) Co., Ltd", "2022-06-21", "UFLPA (i)"),
        ("Xinjiang Goens Energy Technology Co., Ltd.", "2026-08-03", "UFLPA (i)"),
        ("Shihezi Xinren Battery Aluminum Foil Technology Co., Ltd.", "2026-08-03", "UFLPA (v)"),
    ]
    assert all(e["source_url"] == "https://www.dhs.gov/uflpa-entity-list" for e in entries)


def test_uflpa_aliases_and_nbsp_names_are_matched(screening):
    entries = screening.parse_uflpa(UFLPA_HTML)
    terms = {
        "by name": [("XINJIANG GOENS", "name", None)],
        "by former name": [("XINJIANG GCL NEW ENERGY", "name", None)],
        "by truncated word": [("GOENS ENERGY TECH", "name", None)],
    }
    hits = screening.match_entries(entries, terms)
    assert hits["by name"][0]["entity"] == "Xinjiang Goens Energy Technology Co., Ltd."
    assert hits["by former name"][0]["entity"] == "Xinjiang Goens Energy Technology Co., Ltd."
    assert "by truncated word" not in hits  # TECH is not TECHNOLOGY


def test_a_page_without_tables_is_reported_empty(screening, monkeypatch):
    monkeypatch.setattr(screening, "fetch_uflpa_html", lambda session, deadline: "<html><p>Moved</p></html>")
    outcome = screening._screen_uflpa(None, {}, time.monotonic() + 30)
    assert outcome["source"]["status"] == "empty"


# ---------------------------------------------------------------------------
# Federal Register
# ---------------------------------------------------------------------------
def topic(screening, name):
    return next(t for t in screening.FR_TOPICS if t["topic"] == name)


def fr_doc(title, number="2026-00001", date="2026-09-24"):
    return {"title": title, "publication_date": date, "type": "Notice", "document_number": number,
            "html_url": f"https://www.federalregister.gov/d/{number}",
            "agencies": [{"name": "Commerce Department", "slug": "commerce-department"}]}


def kept(screening, topic_name, titles):
    notices = screening.select_notices(topic(screening, topic_name), [fr_doc(t) for t in titles], SUPPLIERS)
    return {n["title"]: n["affected_categories"] for n in notices}


def test_entity_list_topic_keeps_only_list_changes(screening):
    result = kept(screening, "bis_entity_list", [
        "Revisions to the Entity List",
        "In the Matter of: Hans De Geetere, Paul Parmentierlaan 121, 8300 Knokke Heist, Belgium",
        "Agency Information Collection Activities; Submission to the Office of Management and Budget "
        "(OMB) for Review and Approval; Comment Request; Entity List",
    ])
    assert result == {"Revisions to the Entity List": ["Batteries", "EE Component", "EMS"]}


def test_section_301_follows_the_country_it_targets(screening):
    china = ("Notice of Conforming Amendments to Product Exclusions: China's Acts, Policies, and Practices "
             "Related to Technology Transfer, Intellectual Property, and Innovation")
    germany = ("Initiation of Section 301 Investigation; Hearing; and Request for Public Comments: "
               "Germany's Persistent Underpayment for Innovative Pharmaceutical Products")
    various = ("Notice of Actions in Section 301 Investigations of Acts, Policies, and Practices of Various "
               "Economies Related to the Failure of Each Economy To Impose and Effectively Enforce a "
               "Prohibition on the Importation of Goods Produced With Forced Labor")
    brazil = ("Notice of Action: Brazil's Acts, Policies, and Practices Related to Digital Trade and "
              "Electronic Payment Services")
    result = kept(screening, "section_301", [china, germany, various, brazil])
    assert result == {
        china: ["Batteries", "EMS", "Nicotine", "Printing Substrates"],
        germany: ["EE Component", "Filter Materials", "Modern/Traditional Oral Fleece"],
        various: [],
    }


def test_korea_means_south_korea_unless_the_title_says_north(screening):
    # Made-up titles; the watchlist's South Korean supplier is a filter maker.
    suppliers = [{"name": "Tae Young Filters", "category": "Filter Materials", "location": "South Korea"}]
    section_301 = topic(screening, "section_301")
    assert screening.notice_categories(
        section_301, "Korea's Acts, Policies, and Practices Related to Digital Trade", suppliers) == ["Filter Materials"]
    assert screening.notice_categories(
        section_301, "Actions Concerning North Korea's Acts, Policies, and Practices", suppliers) is None


def test_section_232_keeps_only_watchlist_materials(screening):
    polysilicon = "Measures To Restrict Stockpiling of Polysilicon and Polysilicon Derivatives Under Proclamation 11052"
    metals = ("Request for Public Comments on the Proposed Implementation of Duties on Additional Aluminum, "
              "Steel, and Copper Derivative Articles Under Section 232")
    result = kept(screening, "section_232", [
        polysilicon, metals,
        "Adjusting Imports of Unmanned Aircraft Systems and Unmanned Aircraft Systems Components Into the United States",
        "Guidance and Procedures for Implementing Tariff Adjustments for Specialty Pharmaceuticals and "
        "Associated Pharmaceutical Ingredients and Technical Corrections",
    ])
    assert result == {
        polysilicon: ["EE Component"],
        metals: ["Batteries", "EE Component", "EMS", "Mechanical", "Printed Packaging"],
    }


def test_fda_topic_drops_paperwork_and_drug_scheduling(screening):
    ends = ("Flavored Electronic Nicotine Delivery Systems (ENDS) Premarket Applications-Considerations "
            "Related to Youth Risk; Draft Guidance for Industry; Availability")
    result = kept(screening, "fda_ends", [
        ends,
        "Agency Information Collection Activities; Submission for Office of Management and Budget Review; "
        "Comment Request; Potential Tobacco Product Violations Reporting Form",
        "International Drug Scheduling; Convention on Psychotropic Substances; Single Convention on "
        "Narcotic Drugs; Scheduling Recommendations",
    ])
    assert result == {ends: ["Batteries", "EMS", "Modern/Traditional Oral Fleece", "Nicotine"]}


def test_trade_remedy_topic_maps_products_to_categories(screening):
    anode = "Active Anode Material From the People's Republic of China: Final Affirmative Countervailing Duty Determination"
    salt = ("Lithium Hexafluorophosphate From China; Institution of Antidumping and Countervailing Duty "
            "Investigations and Scheduling of Preliminary Phase Investigations")
    result = kept(screening, "adcvd", [
        anode, salt,
        "Tin Mill Products From China, Taiwan, and Turkey; Scheduling of the Final Phase of Countervailing "
        "Duty and Antidumping Duty Investigations",
    ])
    assert result == {anode: ["Batteries"], salt: ["Batteries"]}


def test_uflpa_topic(screening):
    title = "Notice Regarding the Uyghur Forced Labor Prevention Act Entity List"
    assert kept(screening, "uflpa", [title]) == {title: ["Batteries", "EE Component", "EMS"]}


def test_notice_shape(screening):
    doc = fr_doc("Revisions to the Entity List", number="2026-18001", date="2026-08-24")
    doc["agencies"].append({"name": "Industry and Security Bureau", "slug": "industry-and-security-bureau"})
    doc["type"] = "Rule"
    notice = screening.select_notices(topic(screening, "bis_entity_list"), [doc], SUPPLIERS)[0]
    assert notice == {
        "title": "Revisions to the Entity List",
        "date": "2026-08-24",
        "url": "https://www.federalregister.gov/d/2026-18001",
        "agencies": ["Commerce Department", "Industry and Security Bureau"],
        "topic": "bis_entity_list",
        "affected_categories": ["Batteries", "EE Component", "EMS"],
        "type": "Rule",
        "document_number": "2026-18001",
    }


def fr_handler(documents_by_topic, failing=()):
    """Answer a Federal Register request from the topic its term belongs to."""
    def handler(url, params):
        term = dict(params)["conditions[term]"]
        name = documents_by_topic["_term_to_topic"][term]
        if name in failing:
            return FakeResponse(status_code=503)
        docs = documents_by_topic.get(name, [])
        return FakeResponse(json_data={"count": len(docs), "results": docs} if docs else {"count": 0})
    return handler


def fr_fixture(screening):
    shared = fr_doc("Revisions to the Entity List; Polysilicon Derivatives Under Section 232",
                    number="2026-19000", date="2026-09-20")
    return {
        "_term_to_topic": {t["term"]: t["topic"] for t in screening.FR_TOPICS},
        "bis_entity_list": [shared],
        "section_232": [shared, fr_doc("Adjusting Imports of Polysilicon and Its Derivatives Into the United States",
                                       number="2026-17000", date="2026-09-10")],
        "uflpa": [fr_doc("Notice Regarding the Uyghur Forced Labor Prevention Act Entity List",
                         number="2026-16000", date="2026-09-01")],
    }


def test_federal_register_notices_are_deduplicated_and_newest_first(screening):
    session = FakeSession(fr_handler(fr_fixture(screening)))
    outcome = screening._federal_register_notices(session, SUPPLIERS, NOW, time.monotonic() + 30)
    assert outcome["source"]["status"] == "ok"
    assert [(n["document_number"], n["topic"]) for n in outcome["notices"]] == [
        ("2026-19000", "bis_entity_list"),  # first topic to claim it keeps it
        ("2026-17000", "section_232"),
        ("2026-16000", "uflpa"),
    ]


def test_a_failed_topic_fails_the_source_but_keeps_the_rest(screening):
    session = FakeSession(fr_handler(fr_fixture(screening), failing={"uflpa"}))
    outcome = screening._federal_register_notices(session, SUPPLIERS, NOW, time.monotonic() + 30)
    assert outcome["source"]["status"] == "failed"
    assert "uflpa" in outcome["source"]["detail"]
    assert {n["topic"] for n in outcome["notices"]} == {"bis_entity_list", "section_232"}


def test_federal_register_window_is_the_last_30_days(screening):
    seen = []

    def handler(url, params):
        seen.append(dict(params))
        return FakeResponse(json_data={"count": 0})

    screening._federal_register_notices(FakeSession(handler), SUPPLIERS, NOW, time.monotonic() + 30)
    assert {p["conditions[publication_date][gte]"] for p in seen} == {"2026-08-30"}
    assert {p["conditions[publication_date][lte]"] for p in seen} == {"2026-09-29"}


# ---------------------------------------------------------------------------
# screen_suppliers
# ---------------------------------------------------------------------------
@pytest.fixture
def offline(screening, monkeypatch):
    monkeypatch.setattr(screening, "fetch_csl_text",
                        lambda session, deadline: csl_csv(*CSL_ROWS, MADE_UP_PARENT_ROW))
    monkeypatch.setattr(screening, "fetch_uflpa_html", lambda session, deadline: UFLPA_HTML)
    fixture = fr_fixture(screening)
    monkeypatch.setattr(
        screening, "fetch_federal_register_topic",
        lambda session, t, since, until, deadline: fixture.get(t["topic"], []))
    return screening


def test_screen_suppliers_contract(offline):
    result = offline.screen_suppliers(SUPPLIERS, NAME_TERMS, now=NOW, parents=PARENTS)
    assert set(result) == {"fetched_at", "hits", "notices", "sources", "unscreened", "duration_seconds"}
    assert result["fetched_at"] == "2026-09-29T08:00:00+00:00"
    assert {name: s["status"] for name, s in result["sources"].items()} == {
        "consolidated_screening_list": "ok", "uflpa_entity_list": "ok", "federal_register": "ok"}
    assert list(result["hits"]) == ["Huizhou BYD Electronic"]
    assert result["hits"]["Huizhou BYD Electronic"][0]["via"] == "parent"
    assert result["unscreened"] == ["Fuji"]
    assert len(result["notices"]) == 3
    json.dumps(result)  # the snapshot is JSON


def test_a_csl_download_with_no_rows_is_reported_empty_not_clean(offline, monkeypatch):
    monkeypatch.setattr(offline, "fetch_csl_text", lambda session, deadline: "<html>Service Unavailable</html>")
    result = offline.screen_suppliers(SUPPLIERS, NAME_TERMS, now=NOW, parents=PARENTS)
    assert result["sources"]["consolidated_screening_list"]["status"] == "empty"
    assert result["hits"] == {}


def test_one_broken_source_does_not_take_the_others_down(offline, monkeypatch):
    def broken(session, deadline):
        raise requests.ConnectionError("Max retries exceeded with url: /consolidated.csv")

    monkeypatch.setattr(offline, "fetch_csl_text", broken)
    result = offline.screen_suppliers(SUPPLIERS, NAME_TERMS, now=NOW, parents=PARENTS)
    assert result["sources"]["consolidated_screening_list"]["status"] == "failed"
    assert "ConnectionError" in result["sources"]["consolidated_screening_list"]["detail"]
    assert result["sources"]["uflpa_entity_list"]["status"] == "ok"
    assert result["hits"] == {}
    assert len(result["notices"]) == 3


def test_a_hung_source_is_abandoned_at_the_budget(offline, monkeypatch):
    release = threading.Event()
    monkeypatch.setattr(offline, "TOTAL_BUDGET_SEC", 3)
    monkeypatch.setattr(offline, "fetch_uflpa_html", lambda session, deadline: release.wait(10) and "")
    started = time.monotonic()
    try:
        result = offline.screen_suppliers(SUPPLIERS, NAME_TERMS, now=NOW, parents=PARENTS)
    finally:
        release.set()
    assert time.monotonic() - started < 5
    assert result["sources"]["uflpa_entity_list"] == {"status": "failed",
                                                      "detail": "no answer within the 3s budget"}
    assert result["sources"]["consolidated_screening_list"]["status"] == "ok"


def test_screen_suppliers_never_raises(screening):
    result = screening.screen_suppliers("not a list", None, now=NOW, session=object(), parents={})
    assert result["hits"] == {} and result["notices"] == []
    result = screening.screen_suppliers(SUPPLIERS, NAME_TERMS, now=NOW, session=object(), parents=PARENTS)
    assert {s["status"] for s in result["sources"].values()} == {"failed"}


def test_a_broken_parents_file_is_reported(offline, tmp_path, monkeypatch):
    broken = tmp_path / "supplier_parents.json"
    broken.write_text("{not json")
    monkeypatch.setattr(offline, "PARENTS_FILE", broken)
    result = offline.screen_suppliers(SUPPLIERS, NAME_TERMS, now=NOW)
    assert result["sources"]["supplier_parents"]["status"] == "failed"
    assert result["sources"]["consolidated_screening_list"]["status"] == "ok"


# ---------------------------------------------------------------------------
# data/supplier_parents.json
# ---------------------------------------------------------------------------
def test_parents_file_names_real_suppliers_with_usable_terms(screening):
    parents = screening.load_supplier_parents(DATA_DIR / "supplier_parents.json")
    watchlist = {s["name"] for s in json.loads((DATA_DIR / "suppliers.json").read_text())["suppliers"]}
    assert set(parents) <= watchlist
    for supplier, config in parents.items():
        assert config.get("legal_name"), supplier
        own = config.get("match_terms") or []
        assert screening.name_matching.usable_terms(own) == [
            screening.name_matching.normalize(t).strip() for t in own], supplier
        for parent in config.get("parents") or []:
            assert parent.get("name") and parent.get("match_terms"), supplier
            terms = parent["match_terms"]
            assert len(screening.name_matching.usable_terms(terms)) == len(terms), (supplier, parent["name"])
