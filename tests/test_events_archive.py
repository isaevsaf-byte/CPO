"""Tests for the events archive behind /track-record.

The snapshot forgets anything older than 21 days; the archive is the only
place those entries survive. So the properties that matter are the ones that
protect it: an entry is kept once, never rewritten, never lost when the
snapshot moves on, and a run that brings nothing new does not touch the file.
"""

import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ARCHIVER_PATH = Path(__file__).resolve().parent.parent / "scripts" / "backfill_archive.py"


@pytest.fixture(scope="module")
def archiver():
    spec = importlib.util.spec_from_file_location("backfill_archive", ARCHIVER_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["backfill_archive"] = module
    spec.loader.exec_module(module)
    return module


def entry(at="2026-09-14T11:08:30.408433+00:00", kind="price_move", entity="Infineon", **overrides):
    base = {
        "at": at,
        "kind": kind,
        "direction": "info",
        "entity": entity,
        "headline": f"{entity} -7.7% (2.1× its normal daily range), no corroborating signal",
        "detail": "Exposure tier: High. Cause unconfirmed.",
        "href": f"/details/{entity}",
    }
    base.update(overrides)
    return base


NOW = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)


def test_the_same_entry_carried_by_many_snapshots_is_kept_once(archiver, tmp_path):
    path = tmp_path / "archive.json"
    carried = [entry(), entry()]
    assert archiver.update_archive(path, carried, now=NOW) == 1
    assert len(json.loads(path.read_text())["entries"]) == 1


def test_entries_before_the_scoring_rework_are_left_out(archiver, tmp_path):
    path = tmp_path / "archive.json"
    archiver.update_archive(path, [entry(at="2026-09-01T23:59:00+00:00"), entry()], now=NOW)
    entries = json.loads(path.read_text())["entries"]
    assert [e["at"][:10] for e in entries] == ["2026-09-14"]


def test_a_run_that_adds_nothing_does_not_touch_the_file(archiver, tmp_path):
    path = tmp_path / "archive.json"
    archiver.update_archive(path, [entry()], now=NOW)
    before = path.read_bytes()
    later = datetime(2026, 10, 3, tzinfo=timezone.utc)
    assert archiver.update_archive(path, [entry()], now=later) == 0
    assert archiver.update_archive(path, [], now=later) == 0
    assert path.read_bytes() == before


def test_entries_that_aged_out_of_the_snapshot_survive_the_next_merge(archiver, tmp_path):
    path = tmp_path / "archive.json"
    archiver.update_archive(path, [entry(at="2026-09-08T20:28:42+00:00", entity="GPI")], now=NOW)
    # The next harvest's change_log no longer carries the GPI entry.
    added = archiver.update_archive(path, [entry(at="2026-10-01T02:00:00+00:00")], now=NOW)
    entities = [e["entity"] for e in json.loads(path.read_text())["entries"]]
    assert added == 1
    assert entities == ["GPI", "Infineon"]


def test_an_archived_entry_is_never_rewritten_by_a_later_copy(archiver, tmp_path):
    path = tmp_path / "archive.json"
    archiver.update_archive(path, [entry(detail="as first logged")], now=NOW)
    archiver.update_archive(path, [entry(detail="edited later")], now=NOW)
    assert json.loads(path.read_text())["entries"][0]["detail"] == "as first logged"


def test_output_is_sorted_by_time_whatever_order_it_arrives_in(archiver, tmp_path):
    path = tmp_path / "archive.json"
    late = entry(at="2026-09-21T17:22:04+00:00", kind="overall_rag", entity="Overall status",
                 headline="Overall status AMBER → GREEN", href=None)
    early = entry(at="2026-09-08T20:28:42+00:00", entity="GPI")
    archiver.update_archive(path, [late, early], now=NOW)
    assert [e["entity"] for e in json.loads(path.read_text())["entries"]] == ["GPI", "Overall status"]


def test_bare_timestamps_are_read_as_utc(archiver):
    cleaned = archiver.clean_entry(entry(at="2026-09-14T11:08:30"))
    assert cleaned["at"] == "2026-09-14T11:08:30+00:00"


def test_the_buyer_is_never_named(archiver):
    cleaned = archiver.clean_entry(entry(detail="BAT exposure: High. Cause unconfirmed."))
    assert cleaned["detail"] == "Exposure tier: High. Cause unconfirmed."
    assert archiver.scrub_buyer("High exposure to BAT requires review") == (
        "High exposure to the company requires review"
    )
    # Whole words only: a batch is not the buyer.
    assert archiver.scrub_buyer("BATCH recall") == "BATCH recall"


def test_entries_missing_what_the_page_needs_are_dropped(archiver):
    assert archiver.clean_entry({"kind": "price_move", "entity": "GPI", "headline": "x"}) is None
    assert archiver.clean_entry(entry(at="not a date")) is None
    assert archiver.clean_entry("not an entry") is None


def test_an_unreadable_archive_is_refused_not_overwritten(archiver, tmp_path):
    path = tmp_path / "archive.json"
    path.write_text("{ truncated")
    with pytest.raises(ValueError):
        archiver.update_archive(path, [entry()], now=NOW)
    assert path.read_text() == "{ truncated"


def test_dry_run_reports_without_writing(archiver, tmp_path):
    path = tmp_path / "archive.json"
    assert archiver.update_archive(path, [entry()], now=NOW, dry_run=True) == 1
    assert not path.exists()
