#!/usr/bin/env python3
"""Ransomware leak-site claims naming a watchlist supplier, via RansomLook.

RansomLook (ransomlook.io) mirrors the victim lists that ransomware groups
publish. Two calls are combined:

- /api/recent: the latest 100 posts across all groups, which covered about
  two days when this was written;
- /api/search?q=: every post whose title or description contains the
  query, so a claim made a week ago is still seen.

A post only counts when a supplier's full name or alias appears, whole-word,
in its title. The search matches substrings of titles and descriptions: for
"jabil" it returned Chip 1 Exchange, an electronics distributor whose leak
description lists "sales orders to Jabil Defense"; for "BYD" it returned
"PESA Bydgoszcz"; for "porton", "SPORTON International Inc."; for "mativ",
eleven posts such as "Transformative Healthcare". None of those is a claim
against the supplier.

Entry point: fetch_ransom_claims(name_terms, now=None). Never raises.
"""

import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

import requests

try:
    import name_matching
except ModuleNotFoundError:  # loaded by file path (tests) rather than run from scripts/
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import name_matching

logger = logging.getLogger("intel_harvester.ransom")

RANSOMLOOK_BASE = "https://www.ransomlook.io"
RECENT_URL = RANSOMLOOK_BASE + "/api/recent"
SEARCH_URL = RANSOMLOOK_BASE + "/api/search"
# Claims link to RansomLook's page for the group, never to the post's own
# "link": that is a path on the group's leak site, and some are full .onion
# addresses.
GROUP_PAGE_URL = RANSOMLOOK_BASE + "/group/{}"

# A claim is a disruption signal for weeks, not for the six hours between
# harvests. Older claims found by search are dropped: search for "sappi"
# returns a 2022 Karakurt post, which says nothing about Sappi today.
LOOKBACK_DAYS = 30
REQUEST_TIMEOUT = 15
TOTAL_BUDGET_SEC = 30
SEARCH_WORKERS = 4

HEADERS = {
    "User-Agent": "SupplyChainWatchtower/1.0 (+https://cpo-watchtower.co.uk)",
    "Accept": "application/json",
}


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


def parse_discovered(value) -> datetime | None:
    """RansomLook's "discovered" ("2026-09-29 08:40:30.868685") as an aware datetime.

    The value has no zone and is not UTC: that post was already listed at
    08:05 UTC on 2026-09-29. It is most likely Paris time. It is read as UTC
    anyway, which moves a claim by an hour or two against a 30-day window.
    """
    try:
        parsed = datetime.fromisoformat(str(value).strip())
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def match_claims(posts: list, terms_by_supplier: dict, now: datetime,
                 lookback_days: int = LOOKBACK_DAYS) -> dict:
    """supplier -> claims whose post title names it, newest first.

    terms_by_supplier holds already-guarded terms (name_matching.usable_terms).
    Posts are de-duplicated by group and title, since /recent and /search
    return the same post and groups re-post the same victim.
    """
    cutoff = now - timedelta(days=lookback_days)
    # A day's slack for the feed's clock running ahead of UTC (parse_discovered),
    # but not for posts dated months ahead.
    latest = now + timedelta(days=1)
    dated = []
    for post in posts or []:
        if isinstance(post, dict):
            discovered = parse_discovered(post.get("discovered"))
            if discovered is not None and cutoff <= discovered <= latest:
                dated.append((discovered, post))
    # Newest first, so a victim re-posted by the same group keeps its latest date.
    dated.sort(key=lambda pair: pair[0], reverse=True)

    hits, seen = {}, set()
    for discovered, post in dated:
        title = " ".join(str(post.get("post_title") or "").split())
        group = " ".join(str(post.get("group_name") or "").split())
        if not title or not group:
            continue
        folded = name_matching.normalize(title)
        for supplier, terms in terms_by_supplier.items():
            if not terms or not name_matching.first_match(folded, terms):
                continue
            key = (supplier, group.lower(), folded)
            if key in seen:
                continue
            seen.add(key)
            hits.setdefault(supplier, []).append({
                "group": group,
                "title": title,
                "date": discovered.date().isoformat(),
                "url": GROUP_PAGE_URL.format(quote(group)),
            })
    return hits


def fetch_recent(session, deadline: float) -> list:
    response = session.get(RECENT_URL, headers=HEADERS, timeout=_timeout(deadline))
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, list):
        raise ValueError("RansomLook /api/recent did not return a list")
    return data


def fetch_search(session, query: str, deadline: float) -> list:
    response = session.get(SEARCH_URL, params={"q": query}, headers=HEADERS, timeout=_timeout(deadline))
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        raise ValueError("RansomLook /api/search did not return an object")
    return data.get("posts") or []


def fetch_ransom_claims(name_terms: dict, now=None, *, session=None,
                        lookback_days: int = LOOKBACK_DAYS) -> dict:
    """Ransomware claims from the last lookback_days naming a supplier.

    name_terms: supplier name -> uppercase search terms (name plus aliases),
        e.g. {name: supplier_search_terms(name)}. To add legal names from
        data/supplier_parents.json (how CNT gets "CONTRAF NICOTEX"), pass
        screening.expand_name_terms(name_terms) instead. Terms under five
        characters are ignored; a supplier left with none is listed under
        "unscreened".

    Returns {"fetched_at", "hits", "sources", "unscreened", "duration_seconds"}.
    hits maps supplier -> [{"group", "title", "date", "url"}] and holds only
    suppliers with a claim.
    """
    started = time.monotonic()
    deadline = started + TOTAL_BUDGET_SEC
    source_deadline = deadline - 1.0
    now = _as_utc(now)
    result = {"fetched_at": now.isoformat(), "hits": {}, "sources": {}, "unscreened": [],
              "duration_seconds": 0.0}
    try:
        terms_by_supplier = {supplier: name_matching.usable_terms(terms)
                             for supplier, terms in (name_terms or {}).items()}
        result["unscreened"] = sorted(s for s, terms in terms_by_supplier.items() if not terms)
        queries = name_matching.covering_terms(
            [t for terms in terms_by_supplier.values() for t in terms])

        http = session or requests.Session()
        pool = ThreadPoolExecutor(max_workers=SEARCH_WORKERS)
        recent_future = pool.submit(fetch_recent, http, source_deadline)
        search_futures = {pool.submit(fetch_search, http, q.lower(), source_deadline): q for q in queries}
        all_futures = [recent_future, *search_futures]
        done, pending = wait(all_futures, timeout=max(0.0, deadline - time.monotonic()))
        pool.shutdown(wait=False, cancel_futures=True)

        posts = []
        if recent_future in pending:
            result["sources"]["ransomlook_recent"] = {
                "status": "failed", "detail": f"no answer within {TOTAL_BUDGET_SEC}s"}
        else:
            try:
                recent = recent_future.result()
                posts.extend(recent)
                if recent:
                    oldest = min((d for d in (parse_discovered(p.get("discovered")) for p in recent
                                              if isinstance(p, dict)) if d), default=None)
                    since = f" back to {oldest.isoformat(timespec='minutes')}" if oldest else ""
                    result["sources"]["ransomlook_recent"] = {
                        "status": "ok", "detail": f"{len(recent)} latest posts{since}"}
                else:
                    # RansomLook always has recent posts; none means a broken
                    # feed, not a quiet internet.
                    result["sources"]["ransomlook_recent"] = {
                        "status": "empty", "detail": "the feed returned no posts"}
            except Exception as e:
                result["sources"]["ransomlook_recent"] = {"status": "failed", "detail": _describe(e)}

        failed, found = [], 0
        for future, query in search_futures.items():
            if future in pending:
                failed.append(f"{query.lower()} (timed out)")
                continue
            try:
                batch = future.result()
            except Exception as e:
                failed.append(f"{query.lower()} ({_describe(e)})")
                continue
            found += len(batch)
            posts.extend(batch)
        summary = (f"{len(queries) - len(failed)}/{len(queries)} name searches answered, "
                   f"{found} post(s) mentioning a name anywhere")
        if failed:
            result["sources"]["ransomlook_search"] = {
                "status": "failed", "detail": (summary + "; failed: " + "; ".join(failed))[:500]}
        else:
            result["sources"]["ransomlook_search"] = {"status": "ok", "detail": summary}

        result["hits"] = match_claims(posts, terms_by_supplier, now, lookback_days)
    except Exception as e:  # the contract is to never raise into the harvest
        logger.exception("ransomware claim fetch failed")
        result["sources"]["ransomlook"] = {"status": "failed", "detail": _describe(e)}

    result["duration_seconds"] = round(time.monotonic() - started, 2)
    claims = sum(len(v) for v in result["hits"].values())
    logger.info(f"[ransomlook] {claims} claim(s) naming {len(result['hits'])} supplier(s) "
                f"in the last {lookback_days} days")
    for name, source in result["sources"].items():
        log = logger.info if source["status"] == "ok" else logger.warning
        log(f"[{name}] {source['status']}: {source['detail']}")
    return result
