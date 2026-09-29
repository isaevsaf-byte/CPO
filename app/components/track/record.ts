// Builds the /track-record view from the archive and the live snapshot.
// Runs on the server at build time. Every date string is formatted here, from
// the data and in UTC, so nothing on the page depends on the reader's clock or
// locale and the prerendered HTML is exactly what the browser shows.

import type { ChangeDirection, ChangeKind, ChangeLogEntry } from '../../../types/intel';
import type {
  ArchivedChange,
  EventsArchive,
  TrackEntityCount,
  TrackEntry,
  TrackHarvest,
  TrackKindCount,
  TrackRecordData,
  TrackSpell,
  TrackWeek,
} from '../../../types/extras-track';

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const MONTHS_LONG = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
];
const DAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
const DAY_MS = 86_400_000;

// Plain-English names for the filter chips, in the order they are offered.
export const KIND_LABELS: Record<ChangeKind, string> = {
  overall_rag: 'Overall status',
  pillar_rag: 'Pillar status',
  supplier_risk: 'Supplier risk level',
  supplier_signal: 'Supplier signal',
  price_move: 'Unexplained price fall',
  peer_risk: 'Competitor',
  macro_trend: 'Economic outlook',
  supplier_added: 'Supplier added',
  supplier_removed: 'Supplier removed',
};

// Kinds whose entity is a company with its own /details page.
const COMPANY_KINDS: ReadonlySet<string> = new Set([
  'supplier_risk',
  'supplier_signal',
  'supplier_added',
  'price_move',
  'peer_risk',
]);

// The board never names the buyer. The archive is already clean (see
// scripts/backfill_archive.py); entries merged straight from the snapshot are
// not, and neither would be a competitor entry about the buyer itself.
const BUYER_FULL_NAMES = ['British American Tobacco'];
const BUYER_PHRASES: [RegExp, string][] = [
  [/\bBAT exposure\b/g, 'Exposure tier'],
  [/\bexposure to BAT\b/g, 'exposure to the company'],
  [/\bBAT's\b/g, "the company's"],
  [/\bBAT\b/g, 'the company'],
];

export function scrubBuyer(text: string): string {
  let out = text;
  for (const name of BUYER_FULL_NAMES) {
    out = out.replace(new RegExp(`^${name}\\b`), 'The company').split(name).join('the company');
  }
  for (const [pattern, replacement] of BUYER_PHRASES) out = out.replace(pattern, replacement);
  return out;
}

// Snapshots written before timestamps carried an offset are bare UTC.
export function parseUtc(iso: string): Date {
  const hasZone = /(?:Z|[+-]\d{2}:?\d{2})$/.test(iso);
  return new Date(hasZone ? iso : `${iso}Z`);
}

const pad = (n: number) => String(n).padStart(2, '0');

/** "Mon 14 Sep · 11:08 UTC" */
export function formatHarvestTime(iso: string): string {
  const d = parseUtc(iso);
  return `${DAYS[d.getUTCDay()]} ${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]} · ${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())} UTC`;
}

/** "2 September 2026" */
export function formatLongDate(isoDate: string): string {
  const d = parseUtc(isoDate.length === 10 ? `${isoDate}T00:00:00Z` : isoDate);
  return `${d.getUTCDate()} ${MONTHS_LONG[d.getUTCMonth()]} ${d.getUTCFullYear()}`;
}

/** "14 Sep" */
export function formatShortDate(iso: string): string {
  const d = parseUtc(iso);
  return `${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]}`;
}

function mondayOf(d: Date): Date {
  const offset = (d.getUTCDay() + 6) % 7;
  return new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate() - offset));
}

/** "14–20 Sep 2026", or "31 Aug – 6 Sep 2026" across a month end. */
function weekLabel(monday: Date): string {
  const sunday = new Date(monday.getTime() + 6 * DAY_MS);
  const year = sunday.getUTCFullYear();
  if (monday.getUTCMonth() === sunday.getUTCMonth()) {
    return `${monday.getUTCDate()}–${sunday.getUTCDate()} ${MONTHS[sunday.getUTCMonth()]} ${year}`;
  }
  const startYear = monday.getUTCFullYear() !== year ? ` ${monday.getUTCFullYear()}` : '';
  return `${monday.getUTCDate()} ${MONTHS[monday.getUTCMonth()]}${startYear} – ${sunday.getUTCDate()} ${MONTHS[sunday.getUTCMonth()]} ${year}`;
}

/** "15h", or "2d 21h" from two days up. */
export function formatDuration(ms: number): string {
  const hours = Math.max(1, Math.round(ms / 3_600_000));
  if (hours < 48) return `${hours}h`;
  const days = Math.floor(hours / 24);
  const rest = hours % 24;
  return rest ? `${days}d ${rest}h` : `${days}d`;
}

function isDirection(value: unknown): value is ChangeDirection {
  return value === 'up' || value === 'down' || value === 'info';
}

function normalise(raw: Partial<ChangeLogEntry> | Partial<ArchivedChange>): ArchivedChange | null {
  if (!raw.at || !raw.kind || !raw.entity || !raw.headline) return null;
  const stamp = parseUtc(raw.at);
  if (Number.isNaN(stamp.getTime())) return null;
  return {
    at: stamp.toISOString(),
    kind: raw.kind,
    entity: raw.entity,
    direction: isDirection(raw.direction) ? raw.direction : 'info',
    headline: raw.headline,
    detail: raw.detail ?? '',
    href: raw.href ?? null,
  };
}

// Same key the archive dedupes on. Timestamps are compared as instants, since
// the archive and the snapshot spell the same moment with different precision.
function dedupeKey(entry: ArchivedChange): string {
  return [parseUtc(entry.at).getTime(), entry.kind, entry.entity, entry.headline].join('|');
}

/**
 * The archive, topped up with whatever the current snapshot's change_log has
 * that the archive does not yet. Until the harvester appends to the archive on
 * every run, this is what keeps the page current: each harvest redeploys the
 * site, so the snapshot is never more than one run old.
 */
export function mergeRecord(archive: EventsArchive, snapshotLog: ChangeLogEntry[] | undefined): ArchivedChange[] {
  const cutoff = parseUtc(`${archive.since}T00:00:00Z`).getTime();
  const merged = new Map<string, ArchivedChange>();
  for (const raw of [...archive.entries, ...(snapshotLog ?? [])]) {
    const entry = normalise(raw);
    if (!entry || parseUtc(entry.at).getTime() < cutoff) continue;
    const key = dedupeKey(entry);
    if (!merged.has(key)) merged.set(key, entry);
  }
  return Array.from(merged.values()).sort(
    (a, b) => parseUtc(a.at).getTime() - parseUtc(b.at).getTime() || a.kind.localeCompare(b.kind)
  );
}

// Overall status moves are logged as "Overall status GREEN → AMBER".
const RAG_MOVE = /\b(GREEN|AMBER|RED)\s*→\s*(GREEN|AMBER|RED)\b/;

function spellsFrom(entries: ArchivedChange[], asOf: string): TrackSpell[] {
  const spells: TrackSpell[] = [];
  let open: { level: 'AMBER' | 'RED'; from: string } | null = null;

  for (const entry of entries) {
    if (entry.kind !== 'overall_rag') continue;
    const move = RAG_MOVE.exec(entry.headline);
    if (!move) continue;
    const to = move[2];
    if (open && to !== open.level) {
      spells.push({
        level: open.level,
        from: open.from,
        to: entry.at,
        duration: formatDuration(parseUtc(entry.at).getTime() - parseUtc(open.from).getTime()),
      });
      open = null;
    }
    if (!open && (to === 'AMBER' || to === 'RED')) open = { level: to, from: entry.at };
  }
  if (open) {
    spells.push({
      level: open.level,
      from: open.from,
      to: null,
      duration: formatDuration(parseUtc(asOf).getTime() - parseUtc(open.from).getTime()),
    });
  }
  return spells;
}

export function buildTrackRecord({
  archive,
  snapshotLog,
  snapshotUpdated,
  companyNames,
}: {
  archive: EventsArchive;
  snapshotLog?: ChangeLogEntry[];
  /** The snapshot's last_updated: the record runs at least this far. */
  snapshotUpdated?: string;
  /** Suppliers and competitors on the board today, which have a /details page. */
  companyNames: string[];
}): TrackRecordData {
  const entries = mergeRecord(archive, snapshotLog);
  const companies = new Set(companyNames);

  // The archive's generated_at is when the file was written, not when the
  // board last looked, so it does not extend the record.
  const latest = [snapshotUpdated, entries[entries.length - 1]?.at]
    .filter((v): v is string => !!v)
    .map((v) => parseUtc(v))
    .filter((d) => !Number.isNaN(d.getTime()))
    .reduce((max, d) => (d > max ? d : max), parseUtc(`${archive.since}T00:00:00Z`));
  const asOf = latest.toISOString();

  const entityHref = (entry: ArchivedChange): string | null => {
    if (COMPANY_KINDS.has(entry.kind)) {
      return companies.has(entry.entity) ? `/details/${encodeURIComponent(entry.entity)}` : null;
    }
    return entry.href && entry.href.startsWith('/') ? entry.href : null;
  };

  // Harvests, newest first, then grouped into Monday-to-Sunday weeks.
  const byHarvest = new Map<string, TrackHarvest>();
  entries.forEach((entry, idx) => {
    const harvest = byHarvest.get(entry.at) ?? { at: entry.at, label: formatHarvestTime(entry.at), entries: [] };
    const trackEntry: TrackEntry = {
      id: `${entry.at}-${idx}`,
      kind: entry.kind,
      direction: entry.direction,
      entity: scrubBuyer(entry.entity),
      headline: scrubBuyer(entry.headline),
      detail: scrubBuyer(entry.detail),
      entityHref: entityHref(entry),
    };
    harvest.entries.push(trackEntry);
    byHarvest.set(entry.at, harvest);
  });

  const weeksByStart = new Map<string, TrackWeek>();
  const start = parseUtc(`${archive.since}T00:00:00Z`);
  const firstMonday = mondayOf(start);
  const lastMonday = mondayOf(latest);
  // Every week from the start of the record to the latest harvest, quiet ones
  // included: a week in which nothing was logged is part of the record too.
  for (let t = lastMonday.getTime(); t >= firstMonday.getTime(); t -= 7 * DAY_MS) {
    const monday = new Date(t);
    const key = monday.toISOString().slice(0, 10);
    let note: string | null = null;
    if (t === lastMonday.getTime()) note = `Up to ${formatHarvestTime(asOf)}`;
    else if (t < start.getTime()) note = `Record starts ${formatShortDate(start.toISOString())}`;
    weeksByStart.set(key, { weekStart: key, label: weekLabel(monday), note, harvests: [] });
  }
  Array.from(byHarvest.values())
    .sort((a, b) => parseUtc(b.at).getTime() - parseUtc(a.at).getTime())
    .forEach((harvest) => {
      const key = mondayOf(parseUtc(harvest.at)).toISOString().slice(0, 10);
      weeksByStart.get(key)?.harvests.push(harvest);
    });

  const kindCounts = new Map<ChangeKind, number>();
  entries.forEach((e) => kindCounts.set(e.kind, (kindCounts.get(e.kind) ?? 0) + 1));
  const kinds: TrackKindCount[] = (Object.keys(KIND_LABELS) as ChangeKind[])
    .filter((kind) => kindCounts.has(kind))
    .map((kind) => ({ kind, label: KIND_LABELS[kind], count: kindCounts.get(kind) ?? 0 }));
  // Kinds the harvester adds later still get a chip, under their raw name.
  kindCounts.forEach((count, kind) => {
    if (!(kind in KIND_LABELS)) kinds.push({ kind, label: kind.replace(/_/g, ' '), count });
  });

  const falls = new Map<string, number>();
  entries
    .filter((e) => e.kind === 'price_move')
    .forEach((e) => falls.set(e.entity, (falls.get(e.entity) ?? 0) + 1));
  const priceFalls: TrackEntityCount[] = Array.from(falls.entries())
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .map(([entity, count]) => ({
      entity: scrubBuyer(entity),
      href: companies.has(entity) ? `/details/${encodeURIComponent(entity)}` : null,
      count,
    }));

  return {
    since: archive.since,
    asOf,
    total: entries.length,
    weeks: Array.from(weeksByStart.values()),
    kinds,
    spells: spellsFrom(entries, asOf),
    priceFalls,
  };
}
