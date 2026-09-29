import Link from 'next/link';
import type { WorldSignalItem } from '../../../types/extras';
import BriefSection from './BriefSection';
import { neutralBuyer, severityTone } from './buildBrief';
import type { WorldSignalsView } from './buildBrief';
import { formatDay } from './time';

const PILL: Record<ReturnType<typeof severityTone>, string> = {
  red: 'bg-red-100 text-red-800 border-red-300',
  amber: 'bg-amber-100 text-amber-800 border-amber-300',
  green: 'bg-green-100 text-green-800 border-green-300',
  neutral: 'bg-gray-100 text-gray-700 border-gray-300',
};

const DOT: Record<ReturnType<typeof severityTone>, string> = {
  red: 'bg-red-500',
  amber: 'bg-amber-400',
  green: 'bg-green-500',
  neutral: 'bg-gray-300',
};

// Plain digit grouping, no locale: the same output on any machine. Counts
// stay whole ("31 ships/day"); prices get the precision their size needs.
function formatNumber(value: number): string {
  const abs = Math.abs(value);
  const digits = Number.isInteger(value) || abs >= 1000 ? 0 : abs >= 100 ? 1 : 2;
  const [whole, fraction] = value.toFixed(digits).split('.');
  const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  return fraction ? `${grouped}.${fraction}` : grouped;
}

function formatValue(item: WorldSignalItem): string | null {
  if (item.value === null || item.value === undefined || item.value === '') return null;
  const value = typeof item.value === 'number' ? formatNumber(item.value) : item.value;
  return item.unit ? `${value} ${item.unit}` : value;
}

// "2026-09-28" and full ISO times both read; anything else is shown as sent.
function formatAsOf(value: string): string {
  const iso = /^\d{4}-\d{2}-\d{2}$/.test(value) ? `${value}T00:00:00Z` : value;
  const date = new Date(/(?:Z|[+-]\d{2}:?\d{2})$/.test(iso) ? iso : `${iso}Z`);
  return Number.isNaN(date.getTime()) ? value : formatDay(date);
}

interface WorldSignalsSectionProps {
  view: WorldSignalsView;
  /** Watchlist names, so an affected supplier can link to its page. */
  supplierNames: string[];
}

export default function WorldSignalsSection({ view, supplierNames }: WorldSignalsSectionProps) {
  const known = new Set(supplierNames);
  const levelTone = severityTone(view.level);

  return (
    <BriefSection
      title="World signals"
      aside={
        view.level ? (
          <span className={`inline-block px-2 py-0.5 rounded-full text-[11px] font-bold uppercase border ${PILL[levelTone]}`}>
            {view.level}
          </span>
        ) : undefined
      }
    >
      <p className="text-sm text-gray-600">
        Conditions outside any single supplier: commodity prices, shipping chokepoints, river
        levels and natural hazards, with the watchlist suppliers each one touches.
      </p>
      {view.groups.length === 0 ? (
        <p className="mt-3 text-sm text-gray-500">No individual readings in this snapshot.</p>
      ) : (
        <div className="mt-3 space-y-4">
          {view.groups.map((group) => (
            <div key={group.key} className="break-inside-avoid">
              <h3 className="text-xs font-semibold uppercase tracking-wider text-gray-500">{group.title}</h3>
              <ul className="mt-1 divide-y divide-gray-100">
                {group.items.map((item, idx) => {
                  const itemTone = severityTone(item.severity);
                  const value = formatValue(item);
                  const affected = item.affected_suppliers ?? [];
                  return (
                    <li key={`${group.key}-${item.label}-${idx}`} className="py-2">
                      <div className="flex items-baseline justify-between gap-3">
                        <span className="flex items-baseline gap-2 min-w-0">
                          <span className={`brief-exact inline-block h-2 w-2 shrink-0 rounded-full ${DOT[itemTone]}`} aria-hidden="true" />
                          <span className="text-sm font-semibold text-gray-900">{item.label}</span>
                          {item.severity && <span className="sr-only">severity {item.severity}</span>}
                        </span>
                        <span className="shrink-0 text-sm font-mono text-gray-900 text-right">
                          {value}
                          {item.change_pct !== null && item.change_pct !== undefined && (
                            <span className="ml-2 text-xs text-gray-500">
                              {item.change_pct > 0 ? '+' : ''}
                              {item.change_pct.toFixed(1)}%
                            </span>
                          )}
                        </span>
                      </div>
                      {item.headline && <p className="mt-0.5 pl-4 text-xs text-gray-600">{neutralBuyer(item.headline)}</p>}
                      {(affected.length > 0 || item.as_of) && (
                        <p className="mt-0.5 pl-4 text-xs text-gray-500">
                          {affected.length > 0 && (
                            <>
                              Touches{' '}
                              {affected.map((name, nameIdx) => (
                                <span key={`${name}-${nameIdx}`}>
                                  {nameIdx > 0 && ', '}
                                  {known.has(name) ? (
                                    <Link href={`/details/${encodeURIComponent(name)}`} className="underline decoration-dotted hover:text-gray-800">
                                      {name}
                                    </Link>
                                  ) : (
                                    name
                                  )}
                                </span>
                              ))}
                            </>
                          )}
                          {affected.length > 0 && item.as_of && ' · '}
                          {item.as_of && `as of ${formatAsOf(item.as_of)}`}
                        </p>
                      )}
                    </li>
                  );
                })}
              </ul>
            </div>
          ))}
        </div>
      )}
    </BriefSection>
  );
}
