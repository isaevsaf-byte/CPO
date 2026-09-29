import type {
  CategorySummary,
  CheckRow,
  CheckTier,
  CountryRiskEntry,
  CountrySummary,
  ListAnalysis,
  ScanPrefillRow,
  WhatIfResult,
} from '../../../types/extras';
import { compareText } from './countries';
import { TIER_RANK } from './parseList';

// Units, so the numbers below mean one thing each:
//   supplier = a distinct name (a company with two plants counts once)
//   site     = a distinct supplier + country pair (those two plants count twice)
// A row is one supplier, in one country, for one category.

function nameKey(name: string): string {
  return name.toLowerCase();
}

function siteKey(row: CheckRow): string {
  return `${nameKey(row.name)}|${row.country.toLowerCase()}`;
}

function isCritHigh(tier: CheckTier | null): boolean {
  return tier === 'Critical' || tier === 'High';
}

function tierOrder(tier: CheckTier | null): number {
  return tier ? TIER_RANK[tier] : 9;
}

/** Distinct supplier names, as first written. */
export function distinctNames(rows: CheckRow[]): string[] {
  const seen = new Map<string, string>();
  rows.forEach((row) => {
    const key = nameKey(row.name);
    if (!seen.has(key)) seen.set(key, row.name);
  });
  return Array.from(seen.values());
}

function groupBy<K extends string>(rows: CheckRow[], keyOf: (row: CheckRow) => K | null): Map<K, CheckRow[]> {
  const groups = new Map<K, CheckRow[]>();
  rows.forEach((row) => {
    const key = keyOf(row);
    if (key === null) return;
    const bucket = groups.get(key);
    if (bucket) bucket.push(row);
    else groups.set(key, [row]);
  });
  return groups;
}

function lookupStanding(risk: Record<string, CountryRiskEntry>): (country: string) => CountryRiskEntry | null {
  const byLower = new Map<string, CountryRiskEntry>();
  Object.entries(risk).forEach(([country, entry]) => byLower.set(country.toLowerCase(), entry));
  return (country) => byLower.get(country.toLowerCase()) ?? null;
}

function summariseCategory(category: string, rows: CheckRow[]): CategorySummary {
  const suppliers = distinctNames(rows);
  const siteCountry = new Map<string, string>();
  rows.forEach((row) => siteCountry.set(siteKey(row), row.country));

  const perCountry = new Map<string, number>();
  siteCountry.forEach((country) => perCountry.set(country, (perCountry.get(country) ?? 0) + 1));
  const countries = Array.from(perCountry.entries())
    .map(([country, sites]) => ({ country, sites }))
    .sort((a, b) => b.sites - a.sites || compareText(a.country, b.country));

  const sites = siteCountry.size;
  return {
    category,
    suppliers,
    sites,
    countries,
    singleSource: suppliers.length === 1,
    singleCountry: suppliers.length >= 2 && countries.length === 1,
    topCountry: countries[0].country,
    topShare: countries[0].sites / sites,
  };
}

// Most fragile first: one supplier, then one country, then the heaviest lean
// on a single country.
function fragility(category: CategorySummary): number {
  if (category.singleSource) return 0;
  if (category.singleCountry) return 1;
  return 2;
}

export function analyseList(rows: CheckRow[], risk: Record<string, CountryRiskEntry>): ListAnalysis {
  const standingOf = lookupStanding(risk);

  const categories = Array.from(groupBy(rows, (row) => row.category).entries())
    .map(([category, categoryRows]) => summariseCategory(category, categoryRows))
    .sort(
      (a, b) =>
        fragility(a) - fragility(b) ||
        b.topShare - a.topShare ||
        a.suppliers.length - b.suppliers.length ||
        compareText(a.category, b.category),
    );

  const countryGroups = Array.from(groupBy(rows, (row) => row.country).entries());
  const critHighByCountry = new Map<string, number>();
  countryGroups.forEach(([country, countryRows]) => {
    const names = new Set(countryRows.filter((row) => isCritHigh(row.tier)).map((row) => nameKey(row.name)));
    critHighByCountry.set(country, names.size);
  });
  const critHighTotal = Array.from(critHighByCountry.values()).reduce((sum, n) => sum + n, 0);

  const countries: CountrySummary[] = countryGroups
    .map(([country, countryRows]) => {
      const critHigh = critHighByCountry.get(country) ?? 0;
      const categoryNames = new Set<string>();
      countryRows.forEach((row) => {
        if (row.category) categoryNames.add(row.category);
      });
      return {
        country,
        suppliers: distinctNames(countryRows),
        categories: Array.from(categoryNames).sort(compareText),
        critHigh,
        shareOfCritHigh: critHighTotal > 0 ? critHigh / critHighTotal : null,
        standing: standingOf(country),
      };
    })
    .sort((a, b) => b.suppliers.length - a.suppliers.length || compareText(a.country, b.country));

  const sites = new Set(rows.map(siteKey));
  const flaggedSites = new Set(rows.filter((row) => standingOf(row.country)).map(siteKey));

  return {
    supplierCount: distinctNames(rows).length,
    siteCount: sites.size,
    categories,
    countries,
    singleSource: categories.filter((category) => category.singleSource),
    singleCountry: categories.filter((category) => category.singleCountry),
    tiersProvided: rows.some((row) => row.tier !== null),
    critHighTotal,
    sitesInFlaggedCountries: flaggedSites.size,
  };
}

// "What if this country stops?" — an export ban, a border closure, a war, a
// grid failure. Everything supplied from there is gone; a category survives
// only if some supplier site outside the country still covers it.
export function whatIf(rows: CheckRow[], country: string): WhatIfResult {
  const affected = rows
    .filter((row) => row.country === country)
    .sort((a, b) => tierOrder(a.tier) - tierOrder(b.tier) || compareText(a.name, b.name));

  const affectedCategories = Array.from(
    new Set(affected.map((row) => row.category).filter((category): category is string => category !== null)),
  );

  const stranded: WhatIfResult['stranded'] = [];
  const covered: WhatIfResult['covered'] = [];

  affectedCategories.forEach((category) => {
    const lost = distinctNames(affected.filter((row) => row.category === category));
    const elsewhere = new Map<string, { name: string; country: string }>();
    rows
      .filter((row) => row.category === category && row.country !== country)
      .forEach((row) => {
        if (!elsewhere.has(siteKey(row))) elsewhere.set(siteKey(row), { name: row.name, country: row.country });
      });

    if (elsewhere.size === 0) {
      stranded.push({ category, suppliers: lost });
    } else {
      covered.push({ category, lost, remaining: Array.from(elsewhere.values()) });
    }
  });

  stranded.sort((a, b) => compareText(a.category, b.category));
  covered.sort((a, b) => a.remaining.length - b.remaining.length || compareText(a.category, b.category));

  return { country, affected, stranded, covered };
}

/** The ten sites a scan should look at first: most critical tier first, then
 *  in the order they were pasted. */
export function topForScan(rows: CheckRow[], limit: number): ScanPrefillRow[] {
  const ordered = rows
    .slice()
    .sort((a, b) => tierOrder(a.tier) - tierOrder(b.tier) || a.line - b.line);
  const picked = new Map<string, ScanPrefillRow>();
  for (const row of ordered) {
    if (picked.size >= limit) break;
    if (!picked.has(siteKey(row))) picked.set(siteKey(row), { name: row.name, country: row.country });
  }
  return Array.from(picked.values());
}
