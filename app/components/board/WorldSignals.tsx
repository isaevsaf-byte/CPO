import Link from 'next/link';
import type { WorldSignalItem, WorldSignals as WorldSignalsData, WorldSeverity } from '../../../types/intel';
import { safeHref } from './links';

// Conditions every supplier depends on and none of them controls: oil and
// other inputs, shipping chokepoints, the Rhine, natural hazards. On 28 Sep
// 2026 the board read "All clear" while Strait of Hormuz transits were down
// 96% on the year, Brent was near $115 and the Rhine at Kaub stood at 0 cm.
// None of it touched a supplier's own news, and none of it was on the page.

const GROUPS: { key: 'commodities' | 'chokepoints' | 'rivers' | 'hazards'; title: string }[] = [
  { key: 'chokepoints', title: 'Shipping chokepoints' },
  { key: 'rivers', title: 'Rhine' },
  { key: 'commodities', title: 'Inputs and energy' },
  { key: 'hazards', title: 'Natural hazards' },
];

// "quiet" is also what the harvest reports when every source failed, so the
// level only means "within normal range" if at least one source answered.
export function worldSignalsAnswered(data?: WorldSignalsData): boolean {
  if (!data) return false;
  const sources = Object.values(data.sources ?? {});
  return sources.length === 0 || sources.some((src) => src.status !== 'failed');
}

export function severityTone(severity: WorldSeverity | undefined): string {
  return severity === 'severe'
    ? 'border-red-300 bg-red-50 text-red-800'
    : severity === 'notable'
      ? 'border-amber-300 bg-amber-50 text-amber-800'
      : 'border-gray-200 bg-gray-50 text-gray-600';
}

function severityLabel(severity: WorldSeverity | undefined): string {
  return severity === 'severe' ? 'Severe' : severity === 'notable' ? 'Unusual' : 'Normal';
}

function formatValue(item: WorldSignalItem): string {
  if (item.value == null) return '—';
  const v = typeof item.value === 'number'
    ? (Math.abs(item.value) >= 100 ? Math.round(item.value).toLocaleString('en-GB') : item.value.toLocaleString('en-GB', { maximumFractionDigits: 2 }))
    : item.value;
  return item.unit ? `${v} ${item.unit}` : String(v);
}

function Item({ item }: { item: WorldSignalItem }) {
  const href = safeHref(item.source_url);
  return (
    <li className={`rounded-lg border p-3 ${severityTone(item.severity)}`}>
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-sm font-semibold text-gray-900">{item.label}</span>
        <span className="shrink-0 text-[11px] font-semibold uppercase tracking-wide">{severityLabel(item.severity)}</span>
      </div>
      <div className="mt-1 flex items-baseline gap-2">
        <span className="font-mono text-lg font-semibold text-gray-900">{formatValue(item)}</span>
        {item.change_pct != null && (
          <span className="font-mono text-xs text-gray-600">
            {item.change_pct > 0 ? '+' : ''}{item.change_pct.toFixed(0)}%
          </span>
        )}
      </div>
      <p className="mt-1 text-xs leading-relaxed text-gray-700">{item.headline}</p>
      {item.affected_suppliers && item.affected_suppliers.length > 0 && item.severity !== 'quiet' && (
        <div className="mt-2 flex flex-wrap gap-1">
          {item.affected_suppliers.slice(0, 8).map((name) => (
            <Link
              key={name}
              href={`/details/${encodeURIComponent(name)}`}
              className="rounded border border-black/10 bg-white/70 px-1.5 py-0.5 text-[11px] text-gray-800 hover:underline"
            >
              {name}
            </Link>
          ))}
          {item.affected_suppliers.length > 8 && (
            <span className="text-[11px] text-gray-500">+{item.affected_suppliers.length - 8} more</span>
          )}
        </div>
      )}
      <div className="mt-2 text-[11px] text-gray-500">
        {item.as_of && <>Data {item.as_of}</>}
        {item.source && (
          <>
            {item.as_of && ' · '}
            {href ? (
              <a href={href} target="_blank" rel="noopener noreferrer" className="hover:underline">{item.source}</a>
            ) : (
              item.source
            )}
          </>
        )}
      </div>
    </li>
  );
}

export default function WorldSignals({ data }: { data?: WorldSignalsData }) {
  if (!data || !worldSignalsAnswered(data)) return null;
  const groups = GROUPS.map((g) => ({ ...g, items: data[g.key] ?? [] })).filter((g) => g.items.length > 0);
  if (groups.length === 0) return null;

  return (
    <section className="mb-8 rounded-xl border border-gray-200 bg-white shadow-sm overflow-hidden" aria-labelledby="world-heading">
      <div className="flex flex-wrap items-start justify-between gap-3 border-b border-gray-200 bg-gray-50 px-6 py-4">
        <div className="min-w-0">
          <h2 id="world-heading" className="text-lg font-bold text-gray-900">What the world is doing to the supply base</h2>
          <p className="mt-1 text-sm text-gray-600">
            Shipping lanes, rivers, energy and raw-material prices, and natural hazards near supplying sites. None of these
            shows up in a supplier&apos;s own news until it is already late.
          </p>
        </div>
        <span className={`shrink-0 rounded-full border px-3 py-1 text-xs font-bold uppercase tracking-wide ${severityTone(data.level)}`}>
          {severityLabel(data.level)}
        </span>
      </div>
      {data.drivers.length > 0 && (
        <ul className="border-b border-gray-100 px-6 py-3 text-sm text-gray-800 space-y-1">
          {data.drivers.map((d, i) => (
            <li key={i} className="flex gap-2"><span aria-hidden="true" className="text-gray-400">→</span><span>{d}</span></li>
          ))}
        </ul>
      )}
      <div className="grid gap-6 px-6 py-4 lg:grid-cols-2">
        {groups.map((g) => (
          <div key={g.key} className="min-w-0">
            <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider text-gray-500">{g.title}</h3>
            <ul className="grid gap-2 sm:grid-cols-2">
              {g.items.map((item) => <Item key={item.id} item={item} />)}
            </ul>
          </div>
        ))}
      </div>
    </section>
  );
}
