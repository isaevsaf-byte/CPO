import economics from '../../../data/demo_economics.json';
import type { Supplier } from '../../../types/intel';

// Illustrative category economics for the demonstration (see the file's own
// _comment). A scenario is only useful to a procurement lead in money and
// days: how much annual spend runs through what it touches, how long stock
// lasts, and how long a new source takes to qualify.

export type CategoryEconomics = {
  annual_spend_m: number;
  stock_cover_days: number;
  requalify_days: number;
  owner: string;
};

export type Scenario = {
  id: string;
  title: string;
  what: string;
  suppliers: string[];
  supply_loss: number;
  duration_days: number;
};

const CATEGORIES = economics.categories as Record<string, CategoryEconomics>;
const TIER_WEIGHTS = economics.tier_weights as Record<string, number>;

export const SCENARIOS = economics.scenarios as Scenario[];

export function categoryEconomics(category: string): CategoryEconomics | undefined {
  return CATEGORIES[category];
}

/** Each supplier's share of its category's spend, weighted by exposure tier:
 *  a Critical source carries more of the volume than a Medium one. */
export function supplierShares(suppliers: Supplier[]): Map<string, number> {
  const byCategory = new Map<string, Supplier[]>();
  for (const s of suppliers) {
    const list = byCategory.get(s.category) ?? [];
    list.push(s);
    byCategory.set(s.category, list);
  }
  const shares = new Map<string, number>();
  byCategory.forEach((list) => {
    const total = list.reduce((sum, s) => sum + (TIER_WEIGHTS[s.bat_exposure] ?? 1), 0);
    for (const s of list) {
      shares.set(s.name, (TIER_WEIGHTS[s.bat_exposure] ?? 1) / total);
    }
  });
  return shares;
}

export type CategoryImpact = {
  category: string;
  affected: Supplier[];
  /** Share of the category's supply lost while the scenario lasts (0–1). */
  lostShare: number;
  /** True when every supplier the watchlist holds for the category is hit. */
  noAlternative: boolean;
  spendAtRiskM: number;
  stockCoverDays: number | null;
  requalifyDays: number | null;
  /** Days the line runs short once stock is gone, if the scenario outlasts it. */
  shortfallDays: number;
  owner: string | null;
};

export type ScenarioImpact = {
  categories: CategoryImpact[];
  spendAtRiskM: number;
  affectedCount: number;
  earliestShortfallDay: number | null;
};

/** What a scenario does to the watchlist. `lossBySupplier` maps a supplier to
 *  the share of its output lost (1 = stops entirely). */
export function assessScenario(
  suppliers: Supplier[],
  lossBySupplier: Map<string, number>,
  durationDays: number,
): ScenarioImpact {
  const shares = supplierShares(suppliers);
  const byCategory = new Map<string, Supplier[]>();
  for (const s of suppliers) {
    const list = byCategory.get(s.category) ?? [];
    list.push(s);
    byCategory.set(s.category, list);
  }

  const categories: CategoryImpact[] = [];
  byCategory.forEach((list, category) => {
    const affected = list.filter((s) => (lossBySupplier.get(s.name) ?? 0) > 0);
    if (affected.length === 0) return;
    const lostShare = affected.reduce(
      (sum, s) => sum + (shares.get(s.name) ?? 0) * (lossBySupplier.get(s.name) ?? 0),
      0,
    );
    const econ = CATEGORIES[category];
    const stock = econ?.stock_cover_days ?? null;
    // Stock covers the lost share for stock/lostShare days: a 40% loss eats
    // into a 21-day buffer at 40% of the normal rate.
    const lastsDays = stock != null && lostShare > 0 ? stock / lostShare : null;
    const shortfall = lastsDays != null ? Math.max(0, Math.round(durationDays - lastsDays)) : 0;
    categories.push({
      category,
      affected,
      lostShare,
      noAlternative: affected.length === list.length && lostShare >= 0.999,
      spendAtRiskM: econ ? (econ.annual_spend_m * lostShare * durationDays) / 365 : 0,
      stockCoverDays: stock,
      requalifyDays: econ?.requalify_days ?? null,
      shortfallDays: shortfall,
      owner: econ?.owner ?? null,
    });
  });

  categories.sort((a, b) => b.shortfallDays - a.shortfallDays || b.spendAtRiskM - a.spendAtRiskM);
  const shortfalls = categories
    .filter((c) => c.stockCoverDays != null && c.lostShare > 0)
    .map((c) => Math.round((c.stockCoverDays as number) / c.lostShare))
    .filter((d) => d < durationDays);

  return {
    categories,
    spendAtRiskM: categories.reduce((sum, c) => sum + c.spendAtRiskM, 0),
    affectedCount: new Set(categories.flatMap((c) => c.affected.map((s) => s.name))).size,
    earliestShortfallDay: shortfalls.length ? Math.min(...shortfalls) : null,
  };
}

export function formatMillions(m: number): string {
  if (m >= 10) return `£${Math.round(m)}m`;
  if (m >= 1) return `£${m.toFixed(1)}m`;
  return `£${Math.round(m * 1000)}k`;
}
