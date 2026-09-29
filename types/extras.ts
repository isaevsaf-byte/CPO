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

// ============================================================================
// Check your own list
// ============================================================================

export type CheckTier = Exposure | 'Low';

export type ListDelimiter = 'tab' | 'comma' | 'semicolon' | 'pipe';

/** One supplying site as read from the pasted list. */
export interface CheckRow {
  name: string;
  country: string;
  /** Null when the line had no category; such rows still count per country. */
  category: string | null;
  tier: CheckTier | null;
  /** 1-based line number in the pasted text. */
  line: number;
}

export interface SkippedLine {
  line: number;
  text: string;
  reason: string;
}

export interface ParsedList {
  rows: CheckRow[];
  skipped: SkippedLine[];
  /** Column names when the first line was read as a header row. */
  header: string[] | null;
  delimiter: ListDelimiter;
  mergedDuplicates: number;
  unrecognisedTiers: string[];
  rowsWithoutCategory: number;
}

export interface CategorySummary {
  category: string;
  suppliers: string[];
  /** Distinct supplier + country pairs. */
  sites: number;
  countries: { country: string; sites: number }[];
  singleSource: boolean;
  /** Two or more suppliers, every one of them in the same country. */
  singleCountry: boolean;
  topCountry: string;
  topShare: number;
}

export interface CountrySummary {
  country: string;
  suppliers: string[];
  categories: string[];
  /** Suppliers here with a Critical or High tier. */
  critHigh: number;
  /** This country's share of all Critical/High suppliers; null when the list
   *  carries no Critical/High tiers at all. */
  shareOfCritHigh: number | null;
  standing: CountryRiskEntry | null;
}

export interface ListAnalysis {
  supplierCount: number;
  siteCount: number;
  categories: CategorySummary[];
  countries: CountrySummary[];
  singleSource: CategorySummary[];
  singleCountry: CategorySummary[];
  tiersProvided: boolean;
  critHighTotal: number;
  sitesInFlaggedCountries: number;
}

export interface WhatIfResult {
  country: string;
  affected: CheckRow[];
  /** Categories with no supplying site left outside the country. */
  stranded: { category: string; suppliers: string[] }[];
  /** Categories that lose supply here but keep sites elsewhere. */
  covered: { category: string; lost: string[]; remaining: { name: string; country: string }[] }[];
}

// ============================================================================
// Free scan request
// ============================================================================

export interface ScanSupplierRow {
  id: number;
  name: string;
  country: string;
}

export interface ScanContact {
  name: string;
  company: string;
  email: string;
  note: string;
}

/** What /check hands to /scan inside the same browser tab. */
export interface ScanPrefillRow {
  name: string;
  country: string;
}
