import type { Supplier } from '../../../types/intel';
import { categoryEconomics } from './economics';

// The board reports what happened to a supplier. The first thing a procurement
// lead asks is a different question: where do I have no second source? On the
// sample watchlist the answer was sitting in the data unasked — both EMS
// suppliers, the whole vape-device line, are in China, and three categories
// run on a single supplier — while the banner read "All clear".

type Thinness = 'one-country' | 'single-source' | 'spread';

type CategoryRow = {
  category: string;
  suppliers: Supplier[];
  countries: string[];
  thinness: Thinness;
  severe: boolean;
};

const TIER_ORDER: Record<string, number> = { Critical: 0, High: 1, Medium: 2 };

function classify(suppliers: Supplier[]): CategoryRow[] {
  const byCategory = new Map<string, Supplier[]>();
  for (const s of suppliers) {
    const list = byCategory.get(s.category) ?? [];
    list.push(s);
    byCategory.set(s.category, list);
  }
  const rows: CategoryRow[] = [];
  byCategory.forEach((list, category) => {
    const countries = Array.from(new Set(list.map((s) => s.location)));
    const thinness: Thinness =
      list.length === 1 ? 'single-source' : countries.length === 1 ? 'one-country' : 'spread';
    const severe = thinness !== 'spread' && list.some((s) => s.bat_exposure === 'Critical' || s.bat_exposure === 'High');
    rows.push({
      category,
      suppliers: [...list].sort((a, b) => (TIER_ORDER[a.bat_exposure] ?? 3) - (TIER_ORDER[b.bat_exposure] ?? 3)),
      countries,
      thinness,
      severe,
    });
  });
  const rank = (r: CategoryRow) => (r.thinness === 'spread' ? 2 : r.severe ? 0 : 1);
  return rows.sort((a, b) => rank(a) - rank(b) || b.suppliers.length - a.suppliers.length);
}

function tierChip(s: Supplier) {
  const tone =
    s.bat_exposure === 'Critical' ? 'border-red-300 bg-red-50 text-red-800' :
    s.bat_exposure === 'High' ? 'border-amber-300 bg-amber-50 text-amber-800' :
    'border-gray-200 bg-gray-50 text-gray-700';
  return (
    <span key={s.name} className={`inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[11px] font-medium ${tone}`}>
      {s.name}
      <span className="font-normal opacity-70">· {s.location} · {s.bat_exposure}</span>
    </span>
  );
}

export default function Concentration({ suppliers }: { suppliers: Supplier[] }) {
  if (suppliers.length === 0) return null;
  const rows = classify(suppliers);
  const thin = rows.filter((r) => r.thinness !== 'spread');
  const spread = rows.filter((r) => r.thinness === 'spread');

  const criticalByCountry = new Map<string, number>();
  for (const s of suppliers) {
    if (s.bat_exposure !== 'Critical') continue;
    criticalByCountry.set(s.location, (criticalByCountry.get(s.location) ?? 0) + 1);
  }
  const criticalTotal = Array.from(criticalByCountry.values()).reduce((a, b) => a + b, 0);
  const criticalSummary = Array.from(criticalByCountry.entries())
    .sort((a, b) => b[1] - a[1])
    .map(([country, n]) => `${country} ${n}`)
    .join(', ');

  return (
    <section className="mb-8 rounded-xl border border-gray-200 bg-white shadow-sm overflow-hidden" aria-labelledby="thin-heading">
      <div className="border-b border-gray-200 bg-gray-50 px-6 py-4">
        <h2 id="thin-heading" className="text-lg font-bold text-gray-900">Where the supply base is thin</h2>
        <p className="mt-1 text-sm text-gray-600">
          Standing structure, not today&apos;s news: categories with no second source, or with every source in one
          country. {criticalTotal > 0 && (
            <>The {criticalTotal} Critical-tier suppliers sit in {criticalByCountry.size} {criticalByCountry.size === 1 ? 'country' : 'countries'}: {criticalSummary}.</>
          )}
        </p>
      </div>
      <ul className="divide-y divide-gray-100">
        {thin.map((row) => {
          const econ = categoryEconomics(row.category);
          return (
            <li key={row.category} className="grid gap-2 px-6 py-3 md:grid-cols-[minmax(0,14rem)_1fr_minmax(0,16rem)] md:items-center">
              <div className="min-w-0">
                <div className="text-sm font-semibold text-gray-900">{row.category}</div>
                <div className="text-xs text-gray-500">
                  {row.suppliers.length} supplier{row.suppliers.length > 1 ? 's' : ''}, {row.countries.length} {row.countries.length === 1 ? 'country' : 'countries'}
                </div>
              </div>
              <div className="flex min-w-0 flex-wrap gap-1.5">{row.suppliers.map(tierChip)}</div>
              <div className="min-w-0 text-xs">
                <span className={`inline-block rounded-full px-2 py-0.5 font-semibold ${row.severe ? 'bg-red-100 text-red-800' : 'bg-amber-100 text-amber-800'}`}>
                  {row.thinness === 'single-source' ? 'Single source' : `All in ${row.countries[0]}`}
                </span>
                {econ && (
                  <div className="mt-1 text-gray-500">
                    Stock lasts ~{econ.stock_cover_days} days; a new source takes ~{econ.requalify_days} to qualify.
                  </div>
                )}
              </div>
            </li>
          );
        })}
      </ul>
      {spread.length > 0 && (
        <div className="border-t border-gray-100 px-6 py-3 text-xs text-gray-500">
          Spread across countries: {spread.map((r) => `${r.category} (${r.suppliers.length} in ${r.countries.length})`).join(' · ')}.
        </div>
      )}
      <div className="border-t border-gray-100 bg-gray-50 px-6 py-2 text-[11px] text-gray-500">
        Stock cover and qualification times are illustrative figures set for this demonstration.
      </div>
    </section>
  );
}
