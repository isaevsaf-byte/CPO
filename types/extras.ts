/**
 * Types for the brief, the list check, the scan request and the site nav.
 *
 * Kept apart from types/intel.ts, which mirrors what the harvester writes and
 * changes with it. Nothing here is produced by the harvester except
 * WorldSignals, which a snapshot may or may not carry.
 */

import type { Exposure, IntelSnapshot, RAGScore, RiskLevel } from './intel';

// ============================================================================
// World signals (optional snapshot block)
// ============================================================================

/** One reading: a commodity price, a shipping chokepoint, a river level, a
 *  natural hazard. Every field except the label may be missing. */
export interface WorldSignalItem {
  label: string;
  value?: number | string | null;
  unit?: string | null;
  as_of?: string | null;
  change_pct?: number | null;
  severity?: string | null;
  headline?: string | null;
  affected_suppliers?: string[] | null;
}

export type WorldSignalGroup = 'drivers' | 'commodities' | 'chokepoints' | 'rivers' | 'hazards';

export interface WorldSignals {
  level?: string | null;
  drivers?: WorldSignalItem[] | null;
  commodities?: WorldSignalItem[] | null;
  chokepoints?: WorldSignalItem[] | null;
  rivers?: WorldSignalItem[] | null;
  hazards?: WorldSignalItem[] | null;
}

/** The snapshot as the brief reads it: the harvester's shape, plus blocks a
 *  newer harvest may add. */
export type BriefSnapshot = IntelSnapshot & { world_signals?: WorldSignals | null };

// ============================================================================
// Standing country exposure (data/country_risk.json)
// ============================================================================

export interface CountryRiskEntry {
  level: RiskLevel;
  reason: string;
}

export interface CountryRiskFile {
  _comment?: string;
  countries: Record<string, CountryRiskEntry>;
}

// ============================================================================
// Brief
// ============================================================================

export interface BriefAction {
  kind: 'sanctions' | 'critical' | 'high';
  label: string;
  href: string;
}

/** How long the overall status has held, measured to the snapshot time rather
 *  than to the reader's clock, so the brief reads the same on every screen
 *  and on paper. */
export interface StatusStreak {
  score: RAGScore;
  /** ISO time of the first reading in the current run. */
  since: string;
  heldHours: number;
  /** True when the run reaches back to the oldest reading kept, so the real
   *  start is earlier than `since`. */
  coversWholeRecord: boolean;
  recordDays: number;
}

export interface WeekChecks {
  total: number;
  byScore: Partial<Record<RAGScore, number>>;
}

export interface StandingExposureSupplier {
  name: string;
  category: string;
  exposure: Exposure;
}

export interface StandingExposureGroup {
  country: string;
  level: RiskLevel;
  reason: string;
  suppliers: StandingExposureSupplier[];
}
