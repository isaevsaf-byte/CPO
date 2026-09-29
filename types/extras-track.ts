/**
 * Types for the pages and components added alongside the main board:
 * the /track-record page, the regulatory calendar and the supplier map.
 * The snapshot's own types stay in types/intel.ts.
 */

import type { ChangeDirection, ChangeKind } from './intel';

// ============================================================================
// Track record (data/events_archive.json, written by scripts/backfill_archive.py)
// ============================================================================

/** One change the board logged, exactly the fields the archive keeps. */
export interface ArchivedChange {
  at: string;
  kind: ChangeKind;
  entity: string;
  direction: ChangeDirection;
  headline: string;
  detail: string;
  href: string | null;
}

export interface EventsArchive {
  generated_at: string | null;
  /** First day of the record, YYYY-MM-DD. */
  since: string;
  entries: ArchivedChange[];
}

/** An archived change prepared on the server, so both renders show the same text. */
export interface TrackEntry {
  id: string;
  kind: ChangeKind;
  direction: ChangeDirection;
  entity: string;
  headline: string;
  detail: string;
  /** Where the entity links: its /details page, or the entry's own href. */
  entityHref: string | null;
}

/** Everything one harvest logged, under one timestamp. */
export interface TrackHarvest {
  at: string;
  /** e.g. "Mon 14 Sep · 11:08 UTC" */
  label: string;
  entries: TrackEntry[];
}

export interface TrackWeek {
  /** Monday of the week (UTC), YYYY-MM-DD. */
  weekStart: string;
  /** e.g. "14–20 Sep 2026" */
  label: string;
  /** Set on a week the record only partly covers: its first or its latest. */
  note: string | null;
  harvests: TrackHarvest[];
}

export interface TrackKindCount {
  kind: ChangeKind;
  label: string;
  count: number;
}

/** A stretch of time the overall status spent above green. */
export interface TrackSpell {
  level: 'AMBER' | 'RED';
  from: string;
  /** Null while the spell is still open at the end of the record. */
  to: string | null;
  /** e.g. "2d 21h" */
  duration: string;
}

export interface TrackEntityCount {
  entity: string;
  href: string | null;
  count: number;
}

export interface TrackRecordData {
  since: string;
  /** Latest point the record covers: the newest harvest or entry. */
  asOf: string;
  total: number;
  weeks: TrackWeek[];
  kinds: TrackKindCount[];
  spells: TrackSpell[];
  priceFalls: TrackEntityCount[];
}

// ============================================================================
// Regulatory calendar (data/regulatory_calendar.json)
// ============================================================================

export interface RegulatoryEntry {
  id: string;
  /** Date the obligation applies from, YYYY-MM-DD. */
  date: string;
  title: string;
  jurisdiction: string;
  /** Plain-English summary of what changes on that date. */
  what: string;
  /** Watchlist categories, as named in data/suppliers.json. */
  affected_categories: string[];
  /** Primary source the date was checked against. */
  source_url: string;
}

export interface RegulatoryCalendarData {
  /** When the dates were last checked against their sources, YYYY-MM-DD. */
  verified_on: string;
  entries: RegulatoryEntry[];
}

// ============================================================================
// Supplier map (app/components/SupplierMap.tsx)
// ============================================================================

export type OverlaySeverity = 'quiet' | 'notable' | 'severe';

export interface SupplierMapSupplier {
  name: string;
  /** Event risk level: LOW, MEDIUM, HIGH or CRITICAL. */
  level: string;
  /** Exposure tier: Critical, High or Medium. */
  tier: string;
}

export interface SupplierMapCountry {
  country: string;
  suppliers: SupplierMapSupplier[];
  /** Standing country exposure, e.g. "MEDIUM: US-China trade war". Absent when none. */
  standingExposure?: string;
}

export interface SupplierMapOverlay {
  id: string;
  label: string;
  severity: OverlaySeverity;
}

export interface SupplierMapProps {
  countries: SupplierMapCountry[];
  overlays?: SupplierMapOverlay[];
}
