import type {
  ChangeLogEntry,
  Exposure,
  PeerGroupItem,
  RAGScore,
  RagHistoryEntry,
  RiskLevel,
  Supplier,
} from '../../../types/intel';
import type {
  BriefAction,
  CountryRiskEntry,
  StandingExposureGroup,
  StatusStreak,
  WeekChecks,
  WorldSignalGroup,
  WorldSignalItem,
  WorldSignals,
} from '../../../types/extras';
import { parseSnapshotTime } from './time';

// Pure functions: the brief is computed once, at build time, from the
// snapshot. Nothing here reads the clock; every window is measured back from
// the snapshot's own last_updated, so the brief describes the week the data
// covers and reads the same wherever it is opened or printed.

const HOUR_MS = 3_600_000;
const DAY_MS = 86_400_000;

// ---------------------------------------------------------------------------
// The buyer is "the company"
// ---------------------------------------------------------------------------

// Harvested text names the buyer, by abbreviation or in full, in phrases like
// "<buyer> exposure: High". The site never does, so every snapshot string the
// brief shows goes through this first.
const BUYER = /\b(?:British American Tobacco|BAT)(['’]s)?(\s+exposure)?\b/g;

export function neutralBuyer(text: string): string {
  return text.replace(
    BUYER,
    (_match: string, possessive: string | undefined, exposure: string | undefined, offset: number, whole: string) => {
      const before = whole.slice(0, offset);
      const startsSentence = before.trim() === '' || /[.!?:]\s*$/.test(before);
      let out = possessive || exposure ? "the company's" : 'the company';
      if (exposure) out += ' exposure';
      return startsSentence ? out.charAt(0).toUpperCase() + out.slice(1) : out;
    },
  );
}

// ---------------------------------------------------------------------------
// Status
// ---------------------------------------------------------------------------

export function statusHeadline(score: RAGScore, actionCount: number): string {
  if (score === 'RED') {
    return actionCount > 0 ? `${actionCount} item${actionCount > 1 ? 's' : ''} need review` : 'Action needed';
  }
  if (score === 'AMBER') return 'Monitor closely';
  if (score === 'GREEN') return 'All clear';
  return 'Status unknown';
}

// How long the current overall colour has held, from rag_history, measured
// to the snapshot time. When the run reaches back to the oldest reading kept,
// the real start is earlier than we can see, and the brief says so.
export function statusStreak(history: RagHistoryEntry[] | undefined, asOf: Date): StatusStreak | null {
  if (!history || history.length === 0) return null;
  const ordered = history
    .slice()
    .sort((a, b) => parseSnapshotTime(a.timestamp).getTime() - parseSnapshotTime(b.timestamp).getTime());

  const latest = ordered[ordered.length - 1];
  let start = ordered.length - 1;
  while (start > 0 && ordered[start - 1].overall === latest.overall) start -= 1;

  const end = Math.max(asOf.getTime(), parseSnapshotTime(latest.timestamp).getTime());
  const since = parseSnapshotTime(ordered[start].timestamp).getTime();
  const oldest = parseSnapshotTime(ordered[0].timestamp).getTime();

  return {
    score: latest.overall,
    since: ordered[start].timestamp,
    heldHours: (end - since) / HOUR_MS,
    coversWholeRecord: start === 0,
    recordDays: Math.max(1, Math.round((end - oldest) / DAY_MS)),
  };
}

export function checksInWindow(history: RagHistoryEntry[] | undefined, asOf: Date, days: number): WeekChecks {
  const start = asOf.getTime() - days * DAY_MS;
  const inWindow = (history ?? []).filter((entry) => parseSnapshotTime(entry.timestamp).getTime() > start);
  const byScore: WeekChecks['byScore'] = {};
  inWindow.forEach((entry) => {
    byScore[entry.overall] = (byScore[entry.overall] ?? 0) + 1;
  });
  return { total: inWindow.length, byScore };
}

// ---------------------------------------------------------------------------
// What changed
// ---------------------------------------------------------------------------

export function changesInWindow(log: ChangeLogEntry[] | undefined, asOf: Date, days: number): ChangeLogEntry[] {
  const start = asOf.getTime() - days * DAY_MS;
  return (log ?? [])
    .filter((entry) => parseSnapshotTime(entry.at).getTime() > start)
    .sort((a, b) => parseSnapshotTime(b.at).getTime() - parseSnapshotTime(a.at).getTime());
}

// ---------------------------------------------------------------------------
// Actions
// ---------------------------------------------------------------------------

function eventLevel(supplier: Supplier): RiskLevel {
  return supplier.event_risk_level ?? supplier.risk_level;
}

function detailsHref(name: string): string {
  return `/details/${encodeURIComponent(name)}`;
}

// The dashboard's action list, in the dashboard's order: sanctions matches,
// then CRITICAL (suppliers by event level, then peers), then HIGH only if
// fewer than three items so far. Event level is event_risk_level, falling
// back to risk_level, so a standing country floor never creates an action.
// One difference: an entity already listed is not listed twice.
export function buildActionItems(suppliers: Supplier[], peers: PeerGroupItem[]): BriefAction[] {
  const items: BriefAction[] = [];
  const add = (item: BriefAction) => {
    if (!items.some((existing) => existing.href === item.href)) items.push(item);
  };

  suppliers
    .filter((supplier) => supplier.sanctions_hit)
    .forEach((supplier) =>
      add({ kind: 'sanctions', label: `Verify possible sanctions match: ${supplier.name}`, href: detailsHref(supplier.name) }),
    );
  suppliers
    .filter((supplier) => eventLevel(supplier) === 'CRITICAL' && !supplier.sanctions_hit)
    .forEach((supplier) =>
      add({ kind: 'critical', label: `${supplier.name}: ${supplier.last_signal}`, href: detailsHref(supplier.name) }),
    );
  peers
    .filter((peer) => peer.risk_level === 'CRITICAL')
    .forEach((peer) => add({ kind: 'critical', label: `${peer.name}: ${peer.last_signal}`, href: detailsHref(peer.name) }));
  if (items.length < 3) {
    suppliers
      .filter((supplier) => eventLevel(supplier) === 'HIGH')
      .forEach((supplier) =>
        add({ kind: 'high', label: `${supplier.name}: ${supplier.last_signal}`, href: detailsHref(supplier.name) }),
      );
  }

  return items.map((item) => ({ ...item, label: neutralBuyer(item.label) }));
}

// ---------------------------------------------------------------------------
// Standing exposure
// ---------------------------------------------------------------------------

const LEVEL_ORDER: Record<RiskLevel, number> = { CRITICAL: 0, HIGH: 1, MEDIUM: 2, LOW: 3 };
const TIER_ORDER: Record<Exposure, number> = { Critical: 0, High: 1, Medium: 2 };

// Where watchlist suppliers sit, read against the same country floors the
// harvester applies. Background, not news: it is identical every week.
export function standingExposure(
  suppliers: Supplier[],
  floors: Record<string, CountryRiskEntry>,
): StandingExposureGroup[] {
  const groups = new Map<string, StandingExposureGroup>();
  suppliers.forEach((supplier) => {
    const floor = supplier.location ? floors[supplier.location] : undefined;
    if (!floor) return;
    const group = groups.get(supplier.location) ?? {
      country: supplier.location,
      level: floor.level,
      reason: floor.reason,
      suppliers: [],
    };
    group.suppliers.push({ name: supplier.name, category: supplier.category, exposure: supplier.bat_exposure });
    groups.set(supplier.location, group);
  });

  const byName = (a: string, b: string) => (a.toLowerCase() < b.toLowerCase() ? -1 : a.toLowerCase() > b.toLowerCase() ? 1 : 0);
  return Array.from(groups.values())
    .map((group) => ({
      ...group,
      suppliers: group.suppliers
        .slice()
        .sort((a, b) => (TIER_ORDER[a.exposure] ?? 9) - (TIER_ORDER[b.exposure] ?? 9) || byName(a.name, b.name)),
    }))
    .sort(
      (a, b) =>
        LEVEL_ORDER[a.level] - LEVEL_ORDER[b.level] ||
        b.suppliers.length - a.suppliers.length ||
        byName(a.country, b.country),
    );
}

// ---------------------------------------------------------------------------
// World signals
// ---------------------------------------------------------------------------

export interface WorldSignalsView {
  level: string | null;
  groups: { key: WorldSignalGroup; title: string; items: WorldSignalItem[] }[];
}

const GROUP_TITLES: Record<WorldSignalGroup, string> = {
  drivers: 'What is driving it',
  commodities: 'Commodities',
  chokepoints: 'Shipping chokepoints',
  rivers: 'Rivers',
  hazards: 'Natural hazards',
};

function readItem(raw: unknown): WorldSignalItem | null {
  // Tolerate a bare string, in case a list carries labels only.
  if (typeof raw === 'string') return raw.trim() ? { label: raw.trim() } : null;
  if (!raw || typeof raw !== 'object') return null;
  const item = raw as Record<string, unknown>;
  const label = typeof item.label === 'string' ? item.label.trim() : '';
  if (!label) return null;
  const text = (value: unknown) => (typeof value === 'string' && value.trim() !== '' ? value.trim() : null);
  return {
    label,
    value: typeof item.value === 'number' && Number.isFinite(item.value) ? item.value : text(item.value),
    unit: text(item.unit),
    as_of: text(item.as_of),
    change_pct: typeof item.change_pct === 'number' && Number.isFinite(item.change_pct) ? item.change_pct : null,
    severity: text(item.severity),
    headline: text(item.headline),
    affected_suppliers: Array.isArray(item.affected_suppliers)
      ? item.affected_suppliers.filter((name): name is string => typeof name === 'string' && name.trim() !== '')
      : [],
  };
}

// Null when the snapshot has no world_signals block, or one with nothing in
// it, so the brief can leave the section out entirely.
export function readWorldSignals(raw: WorldSignals | null | undefined): WorldSignalsView | null {
  if (!raw || typeof raw !== 'object') return null;
  const groups = (Object.keys(GROUP_TITLES) as WorldSignalGroup[])
    .map((key) => {
      const list: unknown = raw[key];
      const items = Array.isArray(list)
        ? list.map(readItem).filter((item): item is WorldSignalItem => item !== null)
        : [];
      return { key, title: GROUP_TITLES[key], items };
    })
    .filter((group) => group.items.length > 0);
  const level = typeof raw.level === 'string' && raw.level.trim() !== '' ? raw.level.trim() : null;
  if (!level && groups.length === 0) return null;
  return { level, groups };
}

/** Maps whatever severity vocabulary the harvester uses onto three tones. */
export function severityTone(severity: string | null | undefined): 'red' | 'amber' | 'green' | 'neutral' {
  const value = (severity ?? '').toLowerCase();
  if (['severe', 'critical', 'high', 'red', 'major', 'extreme', 'alert'].includes(value)) return 'red';
  if (['notable', 'elevated', 'medium', 'amber', 'moderate', 'warning', 'watch'].includes(value)) return 'amber';
  if (['quiet', 'low', 'normal', 'green', 'calm', 'minor'].includes(value)) return 'green';
  return 'neutral';
}
