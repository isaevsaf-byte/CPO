"""Tests for the brief that reaches the reader.

The dashboard is opened when someone chooses to; the digest arrives whether or
not they do, which makes it the part most likely to be trusted blindly and the
part with no one watching it fail. It is also the only module that talks to an
outside service, so everything here runs against a stubbed sender.
"""

import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

DIGEST_PATH = Path(__file__).resolve().parent.parent / "scripts" / "send_digest.py"


@pytest.fixture(scope="module")
def digest():
    spec = importlib.util.spec_from_file_location("send_digest", DIGEST_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["send_digest"] = module
    spec.loader.exec_module(module)
    return module


def at(minutes_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()


def snapshot(entries, rag="GREEN", updated_minutes_ago=1.0, summary=None):
    return {
        "last_updated": at(updated_minutes_ago),
        "overall_rag": {"score": rag},
        "executive_summary": summary,
        "change_log": entries,
    }


def entry(headline, kind="supplier_risk", direction="up", minutes_ago=2.0):
    return {"at": at(minutes_ago), "kind": kind, "direction": direction,
            "entity": "Acme", "headline": headline, "detail": ""}


# ---------------------------------------------------------------------------
# What counts as worth interrupting someone for
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "kind,direction,headline,expected",
    [
        ("supplier_risk", "up", "Acme: MEDIUM → CRITICAL", True),
        ("overall_rag", "up", "Overall status AMBER → RED", True),
        # Recoveries are good news; they belong in the daily brief, not a page.
        ("supplier_risk", "down", "Acme: CRITICAL → LOW", False),
        # A price move is real and is named in the brief, but by design it does
        # not move the RAG colour — paging on it teaches people to ignore pages.
        ("price_move", "info", "Acme -4.2%, no corroborating signal", False),
        ("macro_trend", "up", "EU outlook Stable → Weakening", False),
        # An escalation that stops short of HIGH is not a page either.
        ("supplier_risk", "up", "Acme: LOW → MEDIUM", False),
    ],
)
def test_is_escalation(digest, kind, direction, headline, expected):
    assert digest.is_escalation({"kind": kind, "direction": direction,
                                 "headline": headline}) is expected


# ---------------------------------------------------------------------------
# Which entries belong to the harvest that just ran
# ---------------------------------------------------------------------------

def test_this_cycle_is_anchored_to_the_snapshot_not_the_clock(digest):
    """A delayed workflow step must not silently drop the alert.

    The window ends at the snapshot's own timestamp, so a run that starts late
    still sees the cycle it is reporting on.
    """
    snap = snapshot(
        [entry("Acme: LOW → CRITICAL", minutes_ago=95),
         entry("Old news", minutes_ago=600)],
        updated_minutes_ago=94,
    )
    headlines = [e["headline"] for e in digest.entries_this_cycle(snap)]
    assert headlines == ["Acme: LOW → CRITICAL"]


def test_this_cycle_is_empty_without_a_timestamp(digest):
    assert digest.entries_this_cycle({"change_log": [entry("x")]}) == []


def test_entries_since_ignores_unparseable_rows(digest):
    snap = snapshot([entry("kept", minutes_ago=5), {"headline": "no timestamp"},
                     {"at": "not-a-date", "headline": "bad"}])
    assert [e["headline"] for e in digest.entries_since(snap, hours=24)] == ["kept"]


# ---------------------------------------------------------------------------
# The message itself
# ---------------------------------------------------------------------------

def test_quiet_day_still_says_something(digest):
    """A quiet day gets one short line rather than silence — the habit has to
    survive the days when nothing happened."""
    message = digest.format_message(snapshot([]), [], "daily")
    assert "Nothing moved" in message
    assert "status GREEN" in message


def test_brief_leads_with_what_changed(digest):
    entries = [entry("Acme: LOW → CRITICAL"), entry("Beta: cleared", direction="down")]
    message = digest.format_message(snapshot(entries, rag="RED"), entries, "daily")

    assert "2 changes" in message
    assert "▲ Acme: LOW → CRITICAL" in message
    assert "▼ Beta: cleared" in message


def test_summary_and_next_step_are_carried(digest):
    summary = {"headline": "Concentration risk in one country.",
               "next_step": "Call the two China plants this week."}
    message = digest.format_message(snapshot([], summary=summary), [], "daily")
    assert summary["headline"] in message
    assert summary["next_step"] in message


def test_long_backlog_is_truncated_not_dropped(digest):
    """Telegram rejects anything over 4096 characters outright, so an
    over-long brief would fail to send rather than send short."""
    # Only the first ten entries are listed, so length comes from the headlines
    # themselves — a supplier_risk headline carries the signal text with it.
    entries = [entry("Acme: LOW → CRITICAL. " + "supporting detail " * 60)
               for _ in range(10)]
    message = digest.format_message(snapshot(entries), entries, "daily")

    assert len(message) <= digest.MAX_MESSAGE_CHARS
    assert message.endswith(digest.TRUNCATION_NOTE)


# ---------------------------------------------------------------------------
# Delivery
# ---------------------------------------------------------------------------

def test_alert_mode_stays_silent_without_an_escalation(digest, monkeypatch, tmp_path, capsys):
    """Silence is the feature: an alert only means something if it is rare."""
    sent = []
    monkeypatch.setattr(digest, "deliver", lambda msg: sent.append(msg) or 1)
    snap = tmp_path / "snap.json"
    snap.write_text(__import__("json").dumps(
        snapshot([entry("Acme: MEDIUM → LOW", direction="down")])))
    monkeypatch.setattr(digest, "SNAPSHOT", snap)
    monkeypatch.setattr(sys, "argv", ["send_digest.py", "--mode", "alert"])

    assert digest.main() == 0
    assert sent == []
    assert "staying silent" in capsys.readouterr().out


def test_alert_mode_fires_on_a_real_escalation(digest, monkeypatch, tmp_path):
    sent = []
    monkeypatch.setattr(digest, "deliver", lambda msg: sent.append(msg) or 1)
    monkeypatch.setattr(digest.os, "getenv",
                        lambda k, d=None: "https://hooks.example/x" if k == "SLACK_WEBHOOK_URL" else d)
    snap = tmp_path / "snap.json"
    snap.write_text(__import__("json").dumps(
        snapshot([entry("Acme: MEDIUM → CRITICAL")], rag="RED")))
    monkeypatch.setattr(digest, "SNAPSHOT", snap)
    monkeypatch.setattr(sys, "argv", ["send_digest.py", "--mode", "alert"])

    assert digest.main() == 0
    assert len(sent) == 1
    assert "Acme: MEDIUM → CRITICAL" in sent[0]


def test_missing_snapshot_fails_loudly(digest, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(digest, "SNAPSHOT", tmp_path / "absent.json")
    monkeypatch.setattr(sys, "argv", ["send_digest.py", "--mode", "daily"])

    assert digest.main() == 1
    assert "cannot read snapshot" in capsys.readouterr().err
