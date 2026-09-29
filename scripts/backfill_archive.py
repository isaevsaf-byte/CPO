#!/usr/bin/env python3
"""Keep every change the board has logged, not just the last three weeks.

The snapshot's change_log is a rolling window: trim_change_log in
update_intel.py drops anything older than 21 days, so what the board caught
disappears three weeks after it caught it. This file keeps the full record in
data/events_archive.json, which the /track-record page reads.

Two ways in:

  Backfill, once or whenever the archive needs rebuilding. Walks every
  committed snapshot since the scoring rework and collects each change_log
  entry once:

      python scripts/backfill_archive.py
      python scripts/backfill_archive.py --dry-run

  Append, from the harvester after each run (scripts/ is on sys.path when
  update_intel.py runs, so a plain import works):

      from backfill_archive import update_archive
      update_archive(Path("data/events_archive.json"), new_changes)

Both go through update_archive, which is idempotent: the same entries in
produce the same file out, and a run that adds nothing leaves the file (and
its generated_at) untouched, so it never creates an empty commit.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator

REPO_ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT_PATH = "data/intel_snapshot.json"
DEFAULT_ARCHIVE = REPO_ROOT / "data" / "events_archive.json"

# Scoring was reworked on 2 September 2026 (price moves reported once and only
# when they fall; what happened to a supplier split from where it sits).
# Entries logged before then were judged by rules the board no longer uses,
# so the record starts here rather than mixing the two.
ARCHIVE_SINCE = "2026-09-02"

ENTRY_FIELDS = ("at", "kind", "entity", "direction", "headline", "detail", "href")
REQUIRED_FIELDS = ("at", "kind", "entity", "headline")

# The board is a demonstration and never names the buyer. Older harvests wrote
# the buyer's name into a few detail strings ("... exposure: High"); those are
# rewritten into the board's own wording on the way into the archive. Order
# matters: the specific phrasings go before the bare name.
BUYER_PHRASES = (
    (re.compile(r"\bBAT exposure\b"), "Exposure tier"),
    (re.compile(r"\bexposure to BAT\b"), "exposure to the company"),
    (re.compile(r"\bBAT's\b"), "the company's"),
    (re.compile(r"\bBAT\b"), "the company"),
)


def _log(message: str) -> None:
    print(message, file=sys.stderr)


def parse_at(raw: object) -> datetime | None:
    """A change_log timestamp as an aware UTC datetime, or None if unreadable.

    Entries written before the harvester emitted an offset are bare UTC
    date-times; they are pinned to UTC rather than read as local time.
    """
    if not isinstance(raw, str) or not raw:
        return None
    try:
        stamp = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(timezone.utc)


def scrub_buyer(text: str) -> str:
    for pattern, replacement in BUYER_PHRASES:
        text = pattern.sub(replacement, text)
    return text


def clean_entry(raw: object) -> dict | None:
    """One archive entry in canonical form, or None if it cannot be used.

    Keeps exactly the seven fields the page reads, normalises the timestamp to
    ISO 8601 UTC with an explicit offset (so the same entry always produces the
    same dedupe key), and rewrites any mention of the buyer's name.
    """
    if not isinstance(raw, dict):
        return None
    if any(not isinstance(raw.get(field), str) or not raw.get(field) for field in REQUIRED_FIELDS):
        return None
    stamp = parse_at(raw["at"])
    if stamp is None:
        return None

    direction = raw.get("direction")
    href = raw.get("href")
    detail = raw.get("detail")
    return {
        "at": stamp.isoformat(),
        "kind": raw["kind"],
        "entity": raw["entity"],
        "direction": direction if direction in ("up", "down", "info") else "info",
        "headline": scrub_buyer(raw["headline"]),
        "detail": scrub_buyer(detail) if isinstance(detail, str) else "",
        "href": href if isinstance(href, str) and href else None,
    }


def dedupe_key(entry: dict) -> tuple:
    return (entry["at"], entry["kind"], entry["entity"], entry["headline"])


def _sort_key(entry: dict) -> tuple:
    return (parse_at(entry["at"]), entry["kind"], entry["entity"], entry["headline"])


def merge_entries(existing: Iterable, new: Iterable, since: str = ARCHIVE_SINCE) -> list[dict]:
    """Existing plus new, cleaned, from `since` onwards, deduped and sorted.

    Dedupes on (at, kind, entity, headline). The first copy wins, so an entry
    already in the archive is never rewritten by a later snapshot carrying the
    same entry forward.
    """
    cutoff = parse_at(f"{since}T00:00:00+00:00")
    if cutoff is None:
        raise ValueError(f"since must be an ISO date (YYYY-MM-DD), got {since!r}")

    merged: dict[tuple, dict] = {}
    for raw in list(existing) + list(new):
        entry = clean_entry(raw)
        if entry is None or parse_at(entry["at"]) < cutoff:
            continue
        merged.setdefault(dedupe_key(entry), entry)
    return sorted(merged.values(), key=_sort_key)


def load_archive(archive_path: Path) -> dict:
    """The archive on disk, or an empty one if it does not exist yet.

    A file that exists but cannot be read raises rather than being treated as
    empty: overwriting it would throw away the only copy of entries that have
    already aged out of the snapshot.
    """
    archive_path = Path(archive_path)
    if not archive_path.exists():
        return {"generated_at": None, "since": ARCHIVE_SINCE, "entries": []}
    try:
        archive = json.loads(archive_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"{archive_path} is not readable JSON ({exc}); refusing to overwrite it") from exc
    if not isinstance(archive, dict) or not isinstance(archive.get("entries"), list):
        raise ValueError(f"{archive_path} has no entries list; refusing to overwrite it")
    return archive


def _write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        os.replace(tmp_name, path)
    except BaseException:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
        raise


def update_archive(archive_path: Path, new_entries: Iterable, *, since: str | None = None,
                   now: datetime | None = None, dry_run: bool = False) -> int:
    """Merge new change_log entries into the archive. Returns how many were added.

    Safe to call on every harvest with the whole change_log, just this cycle's
    changes, or nothing at all. When nothing new arrives the file is not
    touched, so generated_at only moves when the record does. `since` defaults
    to the archive's own start date; passing one re-cuts the record there.
    """
    archive_path = Path(archive_path)
    archive = load_archive(archive_path)
    since = since or archive.get("since") or ARCHIVE_SINCE
    existing = merge_entries(archive["entries"], [], since)
    merged = merge_entries(existing, new_entries, since)

    known = {dedupe_key(entry) for entry in existing}
    added = sum(1 for entry in merged if dedupe_key(entry) not in known)
    unchanged = (
        merged == archive["entries"]
        and archive.get("since") == since
        and bool(archive.get("generated_at"))
    )
    if unchanged or dry_run:
        return added

    stamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    _write_json_atomic(archive_path, {
        "generated_at": stamp.isoformat(timespec="seconds"),
        "since": since,
        "entries": merged,
    })
    return added


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)


def snapshot_history(repo: Path = REPO_ROOT, since: str = ARCHIVE_SINCE) -> Iterator[tuple[str, dict]]:
    """Every committed snapshot since `since`, newest first, as (sha, data).

    A commit whose snapshot is missing or unparseable is skipped with a note
    on stderr rather than stopping the walk.
    """
    listing = _git(repo, "log", "--format=%H", f"--since={since}", "--", SNAPSHOT_PATH)
    if listing.returncode != 0:
        raise RuntimeError(f"git log failed: {listing.stderr.strip()}")
    for sha in listing.stdout.split():
        shown = _git(repo, "show", f"{sha}:{SNAPSHOT_PATH}")
        if shown.returncode != 0:
            _log(f"skip {sha[:10]}: {shown.stderr.strip() or 'snapshot not in this commit'}")
            continue
        try:
            yield sha, json.loads(shown.stdout)
        except json.JSONDecodeError as exc:
            _log(f"skip {sha[:10]}: snapshot is not valid JSON ({exc})")


def collect_history_entries(repo: Path = REPO_ROOT, since: str = ARCHIVE_SINCE) -> tuple[list, int]:
    """All change_log entries across the snapshot history, and how many commits were read."""
    entries: list = []
    commits = 0
    for _sha, snapshot in snapshot_history(repo, since):
        commits += 1
        log = snapshot.get("change_log") if isinstance(snapshot, dict) else None
        if isinstance(log, list):
            entries.extend(log)
    return entries, commits


def backfill(archive_path: Path = DEFAULT_ARCHIVE, repo: Path = REPO_ROOT,
             since: str = ARCHIVE_SINCE, dry_run: bool = False) -> int:
    entries, commits = collect_history_entries(repo, since)
    added = update_archive(archive_path, entries, since=since, dry_run=dry_run)
    unique = len(merge_entries([], entries, since))
    verb = "would add" if dry_run else "added"
    _log(f"Read {commits} snapshot commits since {since}: {len(entries)} change_log entries, "
         f"{unique} unique. {verb.capitalize()} {added} to {archive_path}.")
    return added


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--archive", type=Path, default=DEFAULT_ARCHIVE,
                        help="archive file to create or merge into (default: data/events_archive.json)")
    parser.add_argument("--since", default=ARCHIVE_SINCE,
                        help=f"first day of the record, YYYY-MM-DD (default: {ARCHIVE_SINCE})")
    parser.add_argument("--repo", type=Path, default=REPO_ROOT, help="repository to read history from")
    parser.add_argument("--dry-run", action="store_true", help="report what would change without writing")
    args = parser.parse_args(argv)
    backfill(args.archive, args.repo, args.since, args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
