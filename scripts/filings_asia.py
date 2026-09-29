#!/usr/bin/env python3
"""Exchange announcements for the watchlist's Hong Kong and Shenzhen listings.

- HKEXnews title search: Smoore (06969) and BYD Electronic (00285), the
  listed parent of Huizhou BYD Electronic.
- CNINFO, the Shenzhen exchange's disclosure site: EVE Energy (300014) and
  Porton (300363).

Both are the websites' own endpoints, not published APIs, so every request
has a 10-second timeout and browser-like headers, and nothing here raises.
The exchanges' internal company ids (HKEX stockId, CNINFO orgId) are looked
up live on each run, with the values found on 2026-09-29 as a fallback.

Entry point: fetch_asia_filings(now=None).
"""

import html
import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime, timedelta, timezone

import requests

logger = logging.getLogger("intel_harvester.filings_asia")

HKEX_BASE = "https://www1.hkexnews.hk"
HKEX_PREFIX_URL = HKEX_BASE + "/search/prefix.do"
HKEX_TITLE_SEARCH_URL = HKEX_BASE + "/search/titleSearchServlet.do"
CNINFO_BASE = "https://www.cninfo.com.cn"
CNINFO_TOP_SEARCH_URL = CNINFO_BASE + "/new/information/topSearch/query"
CNINFO_QUERY_URL = CNINFO_BASE + "/new/hisAnnouncement/query"
CNINFO_PDF_BASE = "https://static.cninfo.com.cn/"

LOOKBACK_DAYS = 14
REQUEST_TIMEOUT = 10
TOTAL_BUDGET_SEC = 30
CNINFO_PAGE_SIZE = 30
CNINFO_MAX_PAGES = 3
# Both exchanges stamp announcements in China Standard Time / HKT (UTC+8, no DST).
EXCHANGE_TZ = timezone(timedelta(hours=8))

# known_id: the id each site returned on 2026-09-29, used only if the live
# lookup fails.
LISTINGS = (
    {"supplier": "Smoore", "exchange": "HKEX", "code": "06969", "known_id": "1000044670",
     "listed_entity": "Smoore International Holdings Limited"},
    {"supplier": "Huizhou BYD Electronic", "exchange": "HKEX", "code": "00285", "known_id": "20247",
     "listed_entity": "BYD Electronic (International) Company Limited"},
    {"supplier": "EVE Energy", "exchange": "SZSE", "code": "300014", "known_id": "9900008311",
     "listed_entity": "EVE Energy Co., Ltd."},
    {"supplier": "Porton", "exchange": "SZSE", "code": "300363", "known_id": "9900022740",
     "listed_entity": "Porton Pharma Solutions Ltd."},
)

_BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
HKEX_HEADERS = {
    "User-Agent": _BROWSER_UA,
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "en-GB,en;q=0.9",
    "Referer": HKEX_BASE + "/search/titlesearch.xhtml?lang=en",
    "X-Requested-With": "XMLHttpRequest",
}
CNINFO_HEADERS = {
    "User-Agent": _BROWSER_UA,
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Origin": CNINFO_BASE,
    "Referer": CNINFO_BASE + "/new/commonUrl/pageOfSearch?url=disclosure/list/search",
    "X-Requested-With": "XMLHttpRequest",
}

# (label shown on the board, severity, English phrases matched whole-word,
# Chinese terms matched anywhere). Each Chinese term is listed in simplified
# characters for CNINFO and traditional for HKEXnews, whose Chinese titles are
# traditional: Smoore's 2026-07-03 notice of its controlling shareholder's
# sell-down reads "減持", which the simplified "减持" does not match.
FLAG_RULES = (
    ("profit warning", "high", ("profit warning",), ()),
    ("trading halt", "high", ("trading halt",), ()),
    ("suspension", "high", ("suspension",), ()),
    ("inside information", "medium", ("inside information",), ()),
    ("production halt (停产)", "high", (), ("停产", "停產")),
    ("trading suspended (停牌)", "high", (), ("停牌",)),
    ("share pledge (质押)", "medium", (), ("质押", "質押")),
    ("litigation (诉讼)", "medium", (), ("诉讼", "訴訟")),
    ("regulator investigation (立案)", "high", (), ("立案",)),
    ("earnings pre-announcement (业绩预告)", "medium", (), ("业绩预告", "業績預告")),
    ("expected loss (预亏)", "high", (), ("预亏", "預虧")),
    ("shareholder selling (减持)", "medium", (), ("减持", "減持")),
)
SEVERITY_RANK = {"none": 0, "medium": 1, "high": 2}

# A pledge being released (解除质押) is the opposite of a new one, so it is
# removed before looking for 质押. EVE Energy's 2026-04-13
# "关于控股股东部分股份解除质押的公告" only releases shares and is not
# flagged; its 2026-09-11 "关于控股股东部分股份解除质押及质押的公告"
# releases one pledge and makes another, and is. EVE's controlling
# shareholder files one or the other every two to three weeks.
CHINESE_RELEASES = ("解除质押", "解除質押")

_TAGS = re.compile(r"<[^>]+>")
_SEPARATORS = re.compile(r"[\W_]+")


def _as_utc(now) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    if now.tzinfo is None:
        return now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc)


def _describe(error: Exception) -> str:
    text = str(error) or error.__class__.__name__
    return f"{error.__class__.__name__}: {text}"[:200]


def _timeout(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining < 1:
        raise TimeoutError("time budget used up before the request could start")
    return min(REQUEST_TIMEOUT, remaining)


def _clean(text) -> str:
    return " ".join(html.unescape(_TAGS.sub("", str(text or ""))).split())


def flag_texts(texts) -> tuple:
    """(flags, severity) for an announcement's title and headline category."""
    joined = " | ".join(t for t in texts if t)
    folded = " " + _SEPARATORS.sub(" ", joined.lower()) + " "
    chinese = joined
    for release in CHINESE_RELEASES:
        chinese = chinese.replace(release, "")
    flags, severity = [], "none"
    for label, rule_severity, english, chinese_terms in FLAG_RULES:
        if any(f" {p} " in folded for p in english) or any(t in chinese for t in chinese_terms):
            flags.append(label)
            if SEVERITY_RANK[rule_severity] > SEVERITY_RANK[severity]:
                severity = rule_severity
    return flags, severity


# ---------------------------------------------------------------------------
# HKEXnews
# ---------------------------------------------------------------------------
def parse_hkex_prefix(text: str, code: str) -> str | None:
    """stockId for an exact stock code from the JSONP prefix lookup.

    The lookup is a prefix search: asked for "285" it also returned 02858
    (Yixin) and derivative warrants numbered 28500 upwards, so only the exact
    five-digit code is accepted.
    """
    start, end = text.find("("), text.rfind(")")
    if start < 0 or end <= start:
        raise ValueError("HKEX stock lookup did not return JSONP")
    data = json.loads(text[start + 1:end])
    for info in data.get("stockInfo") or []:
        if isinstance(info, dict) and str(info.get("code")) == code and info.get("stockId"):
            return str(info["stockId"])
    return None


def parse_hkex_result(data) -> list:
    """Announcements from a title-search response, whose "result" is itself
    a JSON-encoded string ("null" when nothing matched)."""
    if not isinstance(data, dict) or "result" not in data:
        raise ValueError("HKEX title search response has no 'result'")
    raw = data["result"]
    if raw in (None, "", "null"):
        return []
    items = json.loads(raw) if isinstance(raw, str) else raw
    return [item for item in items or [] if isinstance(item, dict)]


def _hkex_date(value: str) -> str:
    """"07/09/2026 18:00" (day first, HKT) -> "2026-09-07"."""
    try:
        return datetime.strptime((value or "").strip(), "%d/%m/%Y %H:%M").date().isoformat()
    except ValueError:
        return ""


def hkex_filings(english: list, chinese: list | None, code: str, since: str) -> list:
    """Filings from the English results, flagged on the English title and
    headline category plus the matching Chinese title and category.

    The two languages are separate documents with separate ids; they are
    paired on their shared timestamp, in order.
    """
    chinese_by_time = {}
    for item in chinese or []:
        chinese_by_time.setdefault(item.get("DATE_TIME"), []).append(item)
    filings = []
    for item in english:
        date = _hkex_date(item.get("DATE_TIME"))
        if not date or date < since:
            continue
        twins = chinese_by_time.get(item.get("DATE_TIME")) or []
        twin = twins.pop(0) if twins else {}
        title = _clean(item.get("TITLE"))
        link = (item.get("FILE_LINK") or "").strip()
        flags, severity = flag_texts([title, _clean(item.get("LONG_TEXT")),
                                      _clean(twin.get("TITLE")), _clean(twin.get("LONG_TEXT"))])
        filings.append({
            "title": title,
            "date": date,
            "url": HKEX_BASE + link if link.startswith("/") else link,
            "flags": flags,
            "severity": severity,
            "exchange": "HKEX",
            "stock_code": code,
        })
    return filings


def _hkex_listing(session, listing: dict, since_day, until_day, deadline: float) -> dict:
    notes = []
    try:
        response = session.get(HKEX_PREFIX_URL, params={
            "callback": "callback", "lang": "EN", "type": "A", "name": listing["code"], "market": "SEHK",
        }, headers=HKEX_HEADERS, timeout=_timeout(deadline))
        response.raise_for_status()
        stock_id = parse_hkex_prefix(response.text, listing["code"])
        id_source = "live" if stock_id else "fallback"
        if not stock_id:
            notes.append(f"{listing['code']} not found by the stock lookup")
    except Exception as e:
        stock_id, id_source = None, "fallback"
        notes.append(f"stock lookup failed ({_describe(e)})")
    stock_id = stock_id or listing["known_id"]

    def search(lang):
        response = session.get(HKEX_TITLE_SEARCH_URL, params={
            "sortDir": "0", "sortByOptions": "DateTime", "category": "0", "market": "SEHK",
            "stockId": stock_id, "documentType": "-1",
            "fromDate": since_day.strftime("%Y%m%d"), "toDate": until_day.strftime("%Y%m%d"),
            "title": "", "searchType": "0", "t1code": "-2", "t2Gcode": "-2", "t2code": "-2",
            "rowRange": "100", "lang": lang,
        }, headers=HKEX_HEADERS, timeout=_timeout(deadline))
        response.raise_for_status()
        return parse_hkex_result(response.json())

    english = search("E")  # without the English list there is nothing to report
    try:
        chinese = search("ZH")
    except Exception as e:
        chinese = None
        notes.append(f"Chinese titles unavailable, flagged on English only ({_describe(e)})")
    return {
        "filings": hkex_filings(english, chinese, listing["code"], since_day.isoformat()),
        "id": stock_id, "id_source": id_source, "notes": notes,
    }


# ---------------------------------------------------------------------------
# CNINFO
# ---------------------------------------------------------------------------
def parse_cninfo_top_search(data, code: str) -> str | None:
    for item in data or []:
        if isinstance(item, dict) and str(item.get("code")) == code and item.get("orgId"):
            return str(item["orgId"])
    return None


def _cninfo_date(epoch_ms) -> str:
    try:
        return datetime.fromtimestamp(int(epoch_ms) / 1000, EXCHANGE_TZ).date().isoformat()
    except (TypeError, ValueError, OverflowError, OSError):
        return ""


def cninfo_filings(announcements: list, code: str, org_id: str, since: str) -> list:
    filings, seen = [], set()
    for item in announcements or []:
        if not isinstance(item, dict):
            continue
        key = item.get("announcementId") or item.get("adjunctUrl")
        if key in seen:
            continue
        seen.add(key)
        date = _cninfo_date(item.get("announcementTime"))
        if not date or date < since:
            continue
        title = _clean(item.get("announcementTitle"))
        adjunct = (item.get("adjunctUrl") or "").lstrip("/")
        url = (CNINFO_PDF_BASE + adjunct) if adjunct else (
            f"{CNINFO_BASE}/new/disclosure/detail?stockCode={code}"
            f"&announcementId={item.get('announcementId', '')}&orgId={org_id}&announcementTime={date}")
        flags, severity = flag_texts([title])
        filings.append({
            "title": title,
            "date": date,
            "url": url,
            "flags": flags,
            "severity": severity,
            "exchange": "SZSE",
            "stock_code": code,
        })
    return filings


def _cninfo_listing(session, listing: dict, since_day, until_day, deadline: float) -> dict:
    notes = []
    try:
        response = session.post(CNINFO_TOP_SEARCH_URL, data={"keyWord": listing["code"], "maxNum": "10"},
                                headers=CNINFO_HEADERS, timeout=_timeout(deadline))
        response.raise_for_status()
        org_id = parse_cninfo_top_search(response.json(), listing["code"])
        id_source = "live" if org_id else "fallback"
        if not org_id:
            notes.append(f"{listing['code']} not found by the company lookup")
    except Exception as e:
        org_id, id_source = None, "fallback"
        notes.append(f"company lookup failed ({_describe(e)})")
    org_id = org_id or listing["known_id"]

    announcements = []
    for page in range(1, CNINFO_MAX_PAGES + 1):
        response = session.post(CNINFO_QUERY_URL, data={
            "pageNum": str(page), "pageSize": str(CNINFO_PAGE_SIZE), "column": "szse",
            "tabName": "fulltext", "plate": "", "stock": f"{listing['code']},{org_id}",
            "searchkey": "", "secid": "", "category": "", "trade": "",
            "seDate": f"{since_day.isoformat()}~{until_day.isoformat()}",
            "sortName": "", "sortType": "", "isHLtitle": "false",
        }, headers=CNINFO_HEADERS, timeout=_timeout(deadline))
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict) or "announcements" not in data:
            raise ValueError("CNINFO response has no 'announcements'")
        batch = data.get("announcements") or []
        announcements.extend(batch)
        total = data.get("totalAnnouncement") or 0
        if not batch or not data.get("hasMore") or len(announcements) >= total:
            break
    return {
        "filings": cninfo_filings(announcements, listing["code"], org_id, since_day.isoformat()),
        "id": org_id, "id_source": id_source, "notes": notes,
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def fetch_asia_filings(now=None, *, session=None, listings=LISTINGS) -> dict:
    """Announcements from the last LOOKBACK_DAYS for the HKEX and SZSE listings.

    Returns {"fetched_at", "window_days", "filings", "listings", "sources",
    "duration_seconds"}. filings maps supplier -> [{"title", "date", "url",
    "flags", "severity", "exchange", "stock_code"}], newest first, and holds
    only suppliers whose exchange answered: an absent supplier is unknown,
    an empty list is a quiet fortnight. severity is "high", "medium" or
    "none" (no flag). Never raises.
    """
    started = time.monotonic()
    deadline = started + TOTAL_BUDGET_SEC
    now = _as_utc(now)
    until_day = now.astimezone(EXCHANGE_TZ).date()
    since_day = until_day - timedelta(days=LOOKBACK_DAYS)
    result = {"fetched_at": now.isoformat(), "window_days": LOOKBACK_DAYS, "filings": {},
              "listings": {}, "sources": {}, "duration_seconds": 0.0}
    try:
        http = session or requests.Session()
        fetchers = {"HKEX": _hkex_listing, "SZSE": _cninfo_listing}
        source_names = {"HKEX": "hkexnews", "SZSE": "cninfo"}
        pool = ThreadPoolExecutor(max_workers=max(1, len(listings)))
        futures = {pool.submit(fetchers[l["exchange"]], http, l, since_day, until_day, deadline - 1.0): l
                   for l in listings}
        done, pending = wait(futures, timeout=max(0.0, deadline - time.monotonic()))
        pool.shutdown(wait=False, cancel_futures=True)

        per_source = {}
        for future, listing in futures.items():
            report = per_source.setdefault(source_names[listing["exchange"]], {"ok": [], "failed": [], "notes": []})
            label = f"{listing['supplier']} ({listing['code']})"
            if future in pending:
                report["failed"].append(f"{label}: no answer within {TOTAL_BUDGET_SEC}s")
                continue
            try:
                outcome = future.result()
            except Exception as e:
                report["failed"].append(f"{label}: {_describe(e)}")
                continue
            filings = sorted(outcome["filings"], key=lambda f: f["date"], reverse=True)
            result["filings"][listing["supplier"]] = filings
            result["listings"][listing["supplier"]] = {
                "exchange": listing["exchange"], "code": listing["code"],
                "listed_entity": listing["listed_entity"],
                "id": outcome["id"], "id_source": outcome["id_source"],
            }
            flagged = sum(1 for f in filings if f["flags"])
            id_name = "stockId" if listing["exchange"] == "HKEX" else "orgId"
            report["ok"].append(f"{label} {id_name} {outcome['id']} ({outcome['id_source']}): "
                                f"{len(filings)} filing(s), {flagged} flagged")
            report["notes"].extend(f"{label}: {n}" for n in outcome["notes"])

        for name, report in per_source.items():
            parts = report["ok"] + report["notes"]
            if report["failed"]:
                detail = "; ".join(["failed: " + "; ".join(report["failed"])] + parts)
                result["sources"][name] = {"status": "failed", "detail": detail[:500]}
            else:
                result["sources"][name] = {"status": "ok", "detail": "; ".join(parts)[:500]}
    except Exception as e:  # the contract is to never raise into the harvest
        logger.exception("Asian filings fetch failed")
        result["sources"]["filings_asia"] = {"status": "failed", "detail": _describe(e)}

    result["duration_seconds"] = round(time.monotonic() - started, 2)
    for name, source in result["sources"].items():
        log = logger.info if source["status"] == "ok" else logger.warning
        log(f"[{name}] {source['status']}: {source['detail']}")
    return result
