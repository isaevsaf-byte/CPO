"""Tests for scripts/filings_asia.py: HKEXnews and CNINFO announcements.

Fixtures are responses recorded from both sites on 2026-09-29 (Smoore's
2026-07-03 sell-down notice, EVE Energy's pledge notices), trimmed. No network.
"""

import json
from datetime import datetime, timedelta, timezone

import pytest
import requests

NOW = datetime(2026, 7, 10, 0, 0, tzinfo=timezone.utc)  # window 2026-06-26 .. 2026-07-10 HKT
CST = timezone(timedelta(hours=8))

HKEX_PREFIX_285 = ('callback({"more":"1","stockInfo":[{"stockId":20247,"code":"00285","name":"BYD ELECTRONIC"},'
                   '{"stockId":166831,"code":"02858","name":"YIXIN"},'
                   '{"stockId":1000301301,"code":"28500","name":"HU-YOFC@EC2703A"}]});\n')
HKEX_PREFIX_6969 = ('callback({"more":"1","stockInfo":[{"stockId":1000044670,"code":"06969","name":"SMOORE INTL"},'
                    '{"stockId":1000232792,"code":"69690","name":"BP#HSI  RC2708G"}]});\n')

SMOORE_EN = [
    {"DATE_TIME": "06/07/2026 18:00", "STOCK_CODE": "06969", "LONG_TEXT": "Monthly Returns",
     "TITLE": "MONTHLY RETURN OF EQUITY ISSUER ON MOVEMENTS IN SECURITIES FOR THE MONTH ENDED 30 JUNE 2026 (Revised)",
     "FILE_LINK": "/listedco/listconews/sehk/2026/0706/2026070601428.pdf"},
    {"DATE_TIME": "03/07/2026 20:30", "STOCK_CODE": "06969",
     "LONG_TEXT": "Announcements and Notices - [Inside Information]",
     "TITLE": "REDUCTION PLAN OF CONTROLLING SHAREHOLDER OF THE COMPANY EXPIRES AND SUBSEQUENT REDUCTION PLAN",
     "FILE_LINK": "/listedco/listconews/sehk/2026/0703/2026070302379.pdf"},
    {"DATE_TIME": "03/07/2026 18:00", "STOCK_CODE": "06969", "LONG_TEXT": "Monthly Returns",
     "TITLE": "MONTHLY RETURN OF EQUITY ISSUER ON MOVEMENTS IN SECURITIES FOR THE MONTH ENDED 30 JUNE 2026",
     "FILE_LINK": "/listedco/listconews/sehk/2026/0703/2026070301743.pdf"},
    {"DATE_TIME": "19/06/2026 21:26", "STOCK_CODE": "06969", "LONG_TEXT": "Monthly Returns",
     "TITLE": "AN ANNOUNCEMENT BEFORE THE WINDOW", "FILE_LINK": "/listedco/listconews/sehk/2026/0619/x.pdf"},
]
SMOORE_ZH = [
    {"DATE_TIME": "06/07/2026 18:00", "LONG_TEXT": "月報表",
     "TITLE": "截至二零二六年六月三十日止之股份發行人的證券變動月報表（經修訂）"},
    {"DATE_TIME": "03/07/2026 20:30", "LONG_TEXT": "公告及通告 - [內幕消息]",
     "TITLE": "關於本公司控股股東減持計劃期限屆滿及後續減持計劃的公告"},
    {"DATE_TIME": "03/07/2026 18:00", "LONG_TEXT": "月報表",
     "TITLE": "截至二零二六年六月三十日止之股份發行人的證券變動月報表"},
]


def hkex_payload(items):
    return {"result": json.dumps(items, ensure_ascii=False) if items else "null", "hasNextRow": False,
            "rowRange": 100, "loadedRecord": len(items), "recordCnt": len(items)}


def ms(*args):
    return int(datetime(*args, tzinfo=CST).timestamp() * 1000)


EVE_ANNOUNCEMENTS = [
    {"announcementId": "1225409733", "secCode": "300014", "secName": "亿纬锂能",
     "announcementTitle": "关于控股股东部分股份解除质押、实际控制人部分股份质押的公告",
     "announcementTime": ms(2026, 7, 4), "adjunctUrl": "finalpage/2026-07-04/1225409733.PDF"},
    {"announcementId": "1225413937", "secCode": "300014", "secName": "亿纬锂能",
     "announcementTitle": "关于<em>子公司</em>完成工商变更登记的公告",
     "announcementTime": ms(2026, 7, 7, 17, 5), "adjunctUrl": "finalpage/2026-07-07/1225413937.PDF"},
    {"announcementId": "1225300000", "secCode": "300014", "secName": "亿纬锂能",
     "announcementTitle": "关于控股股东部分股份解除质押的公告",
     "announcementTime": ms(2026, 6, 20, 18, 0), "adjunctUrl": "finalpage/2026-06-20/1225300000.PDF"},
]


class FakeResponse:
    def __init__(self, status_code=200, json_data=None, text=""):
        self.status_code = status_code
        self._json = json_data
        self.text = text

    def json(self):
        if self._json is None:
            raise ValueError("not JSON")
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} Client Error")


class FakeExchanges:
    """Both sites, answering from the fixtures above."""

    def __init__(self, fail=()):
        self.fail = set(fail)
        self.requests = []

    def _answer(self, name, response_factory):
        if name in self.fail:
            return FakeResponse(status_code=403)
        return response_factory()

    def get(self, url, params=None, **kwargs):
        params = params or {}
        self.requests.append((url, dict(params)))
        if url.endswith("/search/prefix.do"):
            text = HKEX_PREFIX_285 if params["name"] == "00285" else HKEX_PREFIX_6969
            return self._answer("hkex_prefix", lambda: FakeResponse(text=text))
        if url.endswith("/search/titleSearchServlet.do"):
            smoore = params["stockId"] == "1000044670"
            items = (SMOORE_EN if params["lang"] == "E" else SMOORE_ZH) if smoore else []
            return self._answer(f"hkex_{params['lang']}", lambda: FakeResponse(json_data=hkex_payload(items)))
        raise AssertionError(f"unexpected GET {url}")

    def post(self, url, data=None, **kwargs):
        data = data or {}
        self.requests.append((url, dict(data)))
        if url.endswith("/topSearch/query"):
            org = {"300014": "9900008311", "300363": "9900022740"}[data["keyWord"]]
            return self._answer("cninfo_lookup", lambda: FakeResponse(json_data=[
                {"code": data["keyWord"], "category": "A股", "orgId": org, "zwjc": "x"}]))
        if url.endswith("/hisAnnouncement/query"):
            anns = EVE_ANNOUNCEMENTS if data["stock"].startswith("300014") else []
            return self._answer("cninfo_query", lambda: FakeResponse(json_data={
                "announcements": anns or None, "hasMore": False, "totalAnnouncement": len(anns)}))
        raise AssertionError(f"unexpected POST {url}")


# ---------------------------------------------------------------------------
# Flags
# ---------------------------------------------------------------------------
def test_english_flags_are_whole_word_and_case_blind(filings_asia):
    assert filings_asia.flag_texts(["PROFIT WARNING"]) == (["profit warning"], "high")
    assert filings_asia.flag_texts(["Announcements and Notices - [Trading Halt]"]) == (["trading halt"], "high")
    assert filings_asia.flag_texts(["Announcements and Notices - [Inside Information]"]) == (
        ["inside information"], "medium")
    assert filings_asia.flag_texts(["RESUMPTION OF TRADING", "Monthly Returns"]) == ([], "none")


def test_traditional_chinese_titles_are_flagged(filings_asia):
    # HKEXnews titles are traditional; "減持" is not the simplified "减持".
    title = "關於本公司控股股東減持計劃期限屆滿及後續減持計劃的公告"
    assert filings_asia.flag_texts([title]) == (["shareholder selling (减持)"], "medium")


def test_a_released_pledge_is_not_a_pledge(filings_asia):
    assert filings_asia.flag_texts(["关于控股股东部分股份解除质押的公告"]) == ([], "none")
    assert filings_asia.flag_texts(["关于控股股东部分股份解除质押及质押的公告"]) == (["share pledge (质押)"], "medium")
    assert filings_asia.flag_texts(["關於實際控制人部分股份解除質押的公告"]) == ([], "none")


def test_severity_is_the_worst_flag(filings_asia):
    # Made-up title carrying two flags.
    flags, severity = filings_asia.flag_texts(["关于控股股东股份质押暨公司股票停牌的公告"])
    assert flags == ["trading suspended (停牌)", "share pledge (质押)"]
    assert severity == "high"


# ---------------------------------------------------------------------------
# HKEXnews
# ---------------------------------------------------------------------------
def test_stock_lookup_takes_the_exact_code_not_warrants(filings_asia):
    assert filings_asia.parse_hkex_prefix(HKEX_PREFIX_285, "00285") == "20247"
    assert filings_asia.parse_hkex_prefix(HKEX_PREFIX_6969, "06969") == "1000044670"
    assert filings_asia.parse_hkex_prefix(HKEX_PREFIX_6969, "00285") is None
    with pytest.raises(ValueError):
        filings_asia.parse_hkex_prefix("<html>blocked</html>", "00285")


def test_title_search_result_is_a_json_string(filings_asia):
    assert filings_asia.parse_hkex_result(hkex_payload([])) == []
    assert len(filings_asia.parse_hkex_result(hkex_payload(SMOORE_EN))) == 4
    with pytest.raises(ValueError):
        filings_asia.parse_hkex_result({"error": "busy"})


def test_hkex_filings_use_both_languages_and_the_window(filings_asia):
    filings = filings_asia.hkex_filings(SMOORE_EN, SMOORE_ZH, "06969", "2026-06-26")
    assert [f["date"] for f in filings] == ["2026-07-06", "2026-07-03", "2026-07-03"]
    selldown = filings[1]
    assert selldown == {
        "title": "REDUCTION PLAN OF CONTROLLING SHAREHOLDER OF THE COMPANY EXPIRES AND SUBSEQUENT REDUCTION PLAN",
        "date": "2026-07-03",
        "url": "https://www1.hkexnews.hk/listedco/listconews/sehk/2026/0703/2026070302379.pdf",
        "flags": ["inside information", "shareholder selling (减持)"],
        "severity": "medium",
        "exchange": "HKEX",
        "stock_code": "06969",
    }
    assert filings[2]["flags"] == []  # the monthly return at 18:00 is paired with its own twin


def test_hkex_filings_without_chinese_still_flag_the_category(filings_asia):
    filings = filings_asia.hkex_filings(SMOORE_EN, None, "06969", "2026-06-26")
    assert filings[1]["flags"] == ["inside information"]


# ---------------------------------------------------------------------------
# CNINFO
# ---------------------------------------------------------------------------
def test_cninfo_filings_are_dated_in_china_time(filings_asia):
    filings = filings_asia.cninfo_filings(EVE_ANNOUNCEMENTS * 2, "300014", "9900008311", "2026-06-26")
    assert [(f["date"], f["flags"]) for f in filings] == [
        ("2026-07-04", ["share pledge (质押)"]),  # midnight in Shenzhen, still the 3rd in UTC
        ("2026-07-07", []),
    ]
    assert filings[0]["url"] == "https://static.cninfo.com.cn/finalpage/2026-07-04/1225409733.PDF"
    assert filings[1]["title"] == "关于子公司完成工商变更登记的公告"  # highlight tags stripped


def test_cninfo_lookup_picks_the_code_asked_for(filings_asia):
    rows = [{"code": "300013", "orgId": "1"}, {"code": "300014", "orgId": "9900008311"}]
    assert filings_asia.parse_cninfo_top_search(rows, "300014") == "9900008311"
    assert filings_asia.parse_cninfo_top_search([], "300014") is None


# ---------------------------------------------------------------------------
# fetch_asia_filings
# ---------------------------------------------------------------------------
def test_fetch_asia_filings_contract(filings_asia):
    session = FakeExchanges()
    result = filings_asia.fetch_asia_filings(NOW, session=session)
    assert set(result) == {"fetched_at", "window_days", "filings", "listings", "sources", "duration_seconds"}
    assert result["window_days"] == 14
    assert {k: v["status"] for k, v in result["sources"].items()} == {"hkexnews": "ok", "cninfo": "ok"}
    assert set(result["filings"]) == {"Smoore", "Huizhou BYD Electronic", "EVE Energy", "Porton"}
    assert result["filings"]["Huizhou BYD Electronic"] == []
    assert result["filings"]["Porton"] == []
    assert [f["date"] for f in result["filings"]["EVE Energy"]] == ["2026-07-07", "2026-07-04"]
    assert result["listings"]["Smoore"] == {
        "exchange": "HKEX", "code": "06969", "listed_entity": "Smoore International Holdings Limited",
        "id": "1000044670", "id_source": "live"}
    assert result["listings"]["EVE Energy"]["id"] == "9900008311"
    searches = [p for url, p in session.requests if url.endswith("titleSearchServlet.do")]
    assert {(p["fromDate"], p["toDate"]) for p in searches} == {("20260626", "20260710")}
    json.dumps(result, ensure_ascii=False)


def test_a_failed_id_lookup_falls_back_to_the_known_id(filings_asia):
    result = filings_asia.fetch_asia_filings(NOW, session=FakeExchanges(fail={"hkex_prefix", "cninfo_lookup"}))
    assert result["sources"]["hkexnews"]["status"] == "ok"
    assert result["listings"]["Smoore"]["id_source"] == "fallback"
    assert result["listings"]["Smoore"]["id"] == "1000044670"
    assert "stock lookup failed" in result["sources"]["hkexnews"]["detail"]
    assert result["listings"]["Porton"] == {
        "exchange": "SZSE", "code": "300363", "listed_entity": "Porton Pharma Solutions Ltd.",
        "id": "9900022740", "id_source": "fallback"}


def test_missing_chinese_titles_are_noted_not_fatal(filings_asia):
    result = filings_asia.fetch_asia_filings(NOW, session=FakeExchanges(fail={"hkex_ZH"}))
    assert result["sources"]["hkexnews"]["status"] == "ok"
    assert "flagged on English only" in result["sources"]["hkexnews"]["detail"]
    assert result["filings"]["Smoore"][1]["flags"] == ["inside information"]


def test_one_exchange_failing_leaves_the_other(filings_asia):
    result = filings_asia.fetch_asia_filings(NOW, session=FakeExchanges(fail={"cninfo_query"}))
    assert result["sources"]["cninfo"]["status"] == "failed"
    assert "HTTPError" in result["sources"]["cninfo"]["detail"]
    assert result["sources"]["hkexnews"]["status"] == "ok"
    # Absent means unknown; an empty list would claim a quiet fortnight.
    assert "EVE Energy" not in result["filings"] and "Porton" not in result["filings"]
    assert "Smoore" in result["filings"]


def test_fetch_asia_filings_never_raises(filings_asia):
    result = filings_asia.fetch_asia_filings(NOW, session=object())
    assert result["filings"] == {}
    assert {s["status"] for s in result["sources"].values()} == {"failed"}
