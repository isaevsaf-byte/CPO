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
import html
import json
import os
import re
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
# A sanctions match is a legal blocker rather than a graded risk, so it pages
# even when the supplier was already elevated for another reason and its level
# did not move. The change log then records a supplier_signal whose headline
# names the signal, not a level, so ALERT_LEVELS never matched it. The label is
# the one SUPPLIER_SIGNAL_LABELS uses in update_intel.py.
ALWAYS_ALERT_SIGNALS = ("OFAC sanctions match",)

MAX_MESSAGE_CHARS = 3900
TRUNCATION_NOTE = "\n…truncated. Full picture on the board."


# ---------------------------------------------------------------------------
# Markup per channel
# ---------------------------------------------------------------------------
# Headlines and the executive summary carry third-party text. Slack reads &,
# < and > as markup, so an unescaped "<!channel>" pinged everyone and
# "<https://example.com|text>" rendered as a link with any label. Telegram
# gets HTML, whose only special characters are the same three; it used to be
# sent as plain text with every * and _ deleted, content included.

def slack_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def telegram_escape(text: str) -> str:
    return html.escape(text, quote=False)


class Markup:
    def __init__(self, escape, bold: str, italic: str):
        self.escape = escape
        self._bold, self._italic = bold, italic

    def bold(self, text: str) -> str:
        return self._bold.format(self.escape(text))

    def italic(self, text: str) -> str:
        return self._italic.format(self.escape(text))


PLAIN = Markup(lambda text: text, "*{}*", "_{}_")
SLACK = Markup(slack_escape, "*{}*", "_{}_")
TELEGRAM = Markup(telegram_escape, "<b>{}</b>", "<i>{}</i>")


class Brief(str):
    """The brief as plain text, carrying each channel's own rendering.

    A str, so it prints, measures and compares as the plain text does, which
    is what --dry-run shows; deliver() sends .slack and .telegram.
    """

    def __new__(cls, plain: str, slack: str, telegram: str):
        brief = super().__new__(cls, plain)
        brief.slack = slack
        brief.telegram = telegram
        return brief


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


# compute_changes stamps every entry of a cycle with one timestamp taken
# moments before last_updated, so "this cycle" is a short window ending at the
# snapshot's own clock — not at the moment this script happens to run. Anchoring
# to wall-clock time instead would silently drop the whole alert if the workflow
# step were delayed, which is exactly when an alert matters.
CYCLE_WINDOW_MINUTES = 15


def entries_this_cycle(snapshot: dict) -> list:
    """Changes stamped with the harvest that just ran."""
    latest = snapshot.get("last_updated")
    if not latest:
        return []
    try:
        cutoff = parse_time(latest) - timedelta(minutes=CYCLE_WINDOW_MINUTES)
    except ValueError:
        return []

    kept = []
    for entry in snapshot.get("change_log", []):
        try:
            if parse_time(entry["at"]) >= cutoff:
                kept.append(entry)
        except (KeyError, ValueError):
            continue
    return kept


def is_escalation(entry: dict) -> bool:
    if entry.get("kind") not in ALERT_KINDS:
        return False
    if entry.get("direction") != "up":
        return False
    headline = entry.get("headline", "")
    if entry.get("kind") == "supplier_signal" and any(signal in headline for signal in ALWAYS_ALERT_SIGNALS):
        return True
    return any(level in headline for level in ALERT_LEVELS)


def _fit(lines: list, limit: int) -> str:
    """Join (text, clippable) lines, cutting at whole lines to stay within
    limit. Only an entry line, which holds no tags, may be clipped part-way,
    and never inside an HTML entity, so the result still parses."""
    message = "\n".join(text for text, _ in lines)
    if len(message) <= limit:
        return message
    budget = limit - len(TRUNCATION_NOTE)
    kept, used = [], 0
    for text, clippable in lines:
        extra = len(text) + (1 if kept else 0)
        if used + extra <= budget:
            kept.append(text)
            used += extra
            continue
        room = budget - used - (1 if kept else 0)
        if clippable and room > 20:
            kept.append(re.sub(r"&[#a-zA-Z0-9]*$", "", text[:room - 1]).rstrip() + "…")
        break
    return "\n".join(kept).rstrip() + TRUNCATION_NOTE


def format_message(snapshot: dict, entries: list, mode: str, markup: Markup = PLAIN) -> str:
    """The brief in one channel's markup; plain text by default."""
    esc = markup.escape
    rag = (snapshot.get("overall_rag") or {}).get("score", "UNKNOWN")
    light = {"RED": "🔴", "AMBER": "🟡", "GREEN": "🟢"}.get(rag, "⚪")
    summary = snapshot.get("executive_summary") or {}

    kind = "alert" if mode == "alert" else "brief"
    lines = [(f"{light} {markup.bold(f'Supply chain {kind} — status {rag}')}", False), ("", False)]

    if entries:
        lines.append((markup.bold(f"{len(entries)} change{'s' if len(entries) > 1 else ''}:"), False))
        for entry in entries[:10]:
            marker = "▲" if entry.get("direction") == "up" else "▼" if entry.get("direction") == "down" else "•"
            lines.append((f"{marker} {esc(entry.get('headline', ''))}", True))
        if len(entries) > 10:
            lines.append((f"…and {len(entries) - 10} more", False))
    else:
        lines.append(("Nothing moved since yesterday. Standing position unchanged.", False))

    if summary.get("headline"):
        lines += [("", False), (markup.italic(summary["headline"]), False)]
    if summary.get("next_step"):
        lines.append((f"{markup.bold('Next step:')} {esc(summary['next_step'])}", False))

    if DASHBOARD_URL:
        lines += [("", False), (esc(DASHBOARD_URL), False)]

    # Telegram rejects a message over 4096 characters outright, so a long
    # backlog would fail to send rather than send short.
    return _fit(lines, MAX_MESSAGE_CHARS)


def render_brief(snapshot: dict, entries: list, mode: str) -> Brief:
    return Brief(
        format_message(snapshot, entries, mode),
        format_message(snapshot, entries, mode, SLACK),
        format_message(snapshot, entries, mode, TELEGRAM),
    )


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
    """Send to every configured channel. Returns the number that accepted it.

    message is normally a Brief carrying each channel's escaped rendering. A
    bare string is escaped whole, so it is still safe, just without bold.
    """
    delivered = 0
    slack_text = getattr(message, "slack", None) or slack_escape(str(message))
    telegram_text = getattr(message, "telegram", None) or telegram_escape(str(message))

    slack_url = os.getenv("SLACK_WEBHOOK_URL")
    if slack_url and post_json(slack_url, {"text": slack_text}):
        delivered += 1

    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if token and chat_id:
        if post_json(
            f"https://api.telegram.org/bot{token}/sendMessage",
            {"chat_id": chat_id, "text": telegram_text, "parse_mode": "HTML",
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

    message = render_brief(snapshot, entries, args.mode)

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
