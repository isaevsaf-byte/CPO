#!/usr/bin/env python3
"""
Push the dashboard's own change feed to where the reader already is.

A dashboard only works if someone opens it, and nothing about a URL makes that
happen. This sends the same "what moved" feed the front page leads with, to
Slack or Telegram, in two modes:

  --mode daily   One brief a day, whether or not anything moved. A quiet day
                 gets one short line — that is the point: the habit survives
                 quiet days, and "nothing moved" is itself a useful answer.

  --mode alert   Fires only on a genuine escalation in the cycle that just ran
                 (a confirmed CRITICAL/HIGH signal appearing, or the overall
                 status going RED). Everything else stays silent, so an alert
                 keeps meaning something.

Both are opt-in: with no webhook or bot token configured the script prints what
it would have sent and exits 0, so the harvest workflow is unaffected.

Environment:
  SLACK_WEBHOOK_URL     Slack incoming webhook
  TELEGRAM_BOT_TOKEN    Telegram bot token, with TELEGRAM_CHAT_ID
  TELEGRAM_CHAT_ID
  DASHBOARD_URL         Link included in the message
"""

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib import request
from urllib.error import URLError

SNAPSHOT = Path(__file__).parent.parent / "data" / "intel_snapshot.json"
DASHBOARD_URL = os.getenv("DASHBOARD_URL", "")

# Change kinds that justify interrupting someone's day. A price move
# deliberately does not: it is real and it is in the daily brief, but by design
# it does not move the RAG colour, and paging a CPO about an unexplained 3%
# dip is how alerting stops being read.
ALERT_KINDS = {"supplier_risk", "supplier_signal", "peer_risk", "overall_rag"}
ALERT_LEVELS = ("CRITICAL", "HIGH", "RED")


def parse_time(raw: str) -> datetime:
    stamp = datetime.fromisoformat(raw)
    return stamp.replace(tzinfo=timezone.utc) if stamp.tzinfo is None else stamp


def load_snapshot() -> dict:
    with open(SNAPSHOT) as f:
        return json.load(f)


def entries_since(snapshot: dict, hours: float) -> list:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    kept = []
    for entry in snapshot.get("change_log", []):
        try:
            if parse_time(entry["at"]) >= cutoff:
                kept.append(entry)
        except (KeyError, ValueError):
            continue
    return kept


def entries_this_cycle(snapshot: dict) -> list:
    """Changes stamped with the harvest that just ran."""
    latest = snapshot.get("last_updated")
    if not latest:
        return []
    # compute_changes stamps every entry of a cycle with one timestamp taken
    # moments before last_updated, so match on the harvest window rather than
    # on string equality.
    return entries_since(snapshot, hours=0.25)


def is_escalation(entry: dict) -> bool:
    if entry.get("kind") not in ALERT_KINDS:
        return False
    if entry.get("direction") != "up":
        return False
    headline = entry.get("headline", "")
    return any(level in headline for level in ALERT_LEVELS)


def format_message(snapshot: dict, entries: list, mode: str) -> str:
    rag = (snapshot.get("overall_rag") or {}).get("score", "UNKNOWN")
    light = {"RED": "🔴", "AMBER": "🟡", "GREEN": "🟢"}.get(rag, "⚪")
    summary = snapshot.get("executive_summary") or {}

    if mode == "alert":
        lines = [f"{light} *Supply chain alert — status {rag}*", ""]
    else:
        lines = [f"{light} *Supply chain brief — status {rag}*", ""]

    if entries:
        lines.append(f"*{len(entries)} change{'s' if len(entries) > 1 else ''}:*")
        for entry in entries[:10]:
            marker = "▲" if entry.get("direction") == "up" else "▼" if entry.get("direction") == "down" else "•"
            lines.append(f"{marker} {entry.get('headline', '')}")
        if len(entries) > 10:
            lines.append(f"…and {len(entries) - 10} more")
    else:
        lines.append("Nothing moved since yesterday. Standing position unchanged.")

    if summary.get("headline"):
        lines += ["", f"_{summary['headline']}_"]
    if summary.get("next_step"):
        lines.append(f"*Next step:* {summary['next_step']}")

    if DASHBOARD_URL:
        lines += ["", DASHBOARD_URL]

    return "\n".join(lines)


def post_json(url: str, payload: dict) -> bool:
    req = request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with request.urlopen(req, timeout=15) as response:
            return 200 <= response.status < 300
    except (URLError, OSError) as e:
        print(f"delivery failed for {url.split('/')[2]}: {e}", file=sys.stderr)
        return False


def deliver(message: str) -> int:
    """Send to every configured channel. Returns the number that accepted it."""
    delivered = 0

    slack_url = os.getenv("SLACK_WEBHOOK_URL")
    if slack_url and post_json(slack_url, {"text": message}):
        delivered += 1

    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if token and chat_id:
        # Telegram's Markdown dialect differs from Slack's; the shared plain
        # text degrades acceptably, so it is sent without a parse mode rather
        # than risking a 400 on an unescaped character in a headline.
        if post_json(
            f"https://api.telegram.org/bot{token}/sendMessage",
            {"chat_id": chat_id, "text": message.replace("*", "").replace("_", ""),
             "disable_web_page_preview": True},
        ):
            delivered += 1

    return delivered


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["daily", "alert"], default="daily")
    parser.add_argument("--hours", type=float, default=24.0,
                        help="Window for the daily brief (default: 24)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print the message instead of sending it")
    args = parser.parse_args()

    try:
        snapshot = load_snapshot()
    except (OSError, json.JSONDecodeError) as e:
        print(f"cannot read snapshot: {e}", file=sys.stderr)
        return 1

    if args.mode == "alert":
        candidates = [e for e in entries_this_cycle(snapshot) if is_escalation(e)]
        if not candidates:
            print("No escalation this cycle — staying silent.")
            return 0
        entries = candidates
    else:
        entries = entries_since(snapshot, args.hours)

    message = format_message(snapshot, entries, args.mode)

    if args.dry_run:
        print(message)
        return 0

    if not (os.getenv("SLACK_WEBHOOK_URL") or os.getenv("TELEGRAM_BOT_TOKEN")):
        print("No delivery channel configured. Message that would have been sent:\n")
        print(message)
        return 0

    delivered = deliver(message)
    print(f"Delivered to {delivered} channel(s).")
    return 0 if delivered else 1


if __name__ == "__main__":
    sys.exit(main())
