'use client';

// Regulatory dates that reach the watchlist, with a countdown to each.
//
// A client component so it can sit on any page, including the board itself.
// The list, the dates and the affected suppliers are all in the prerendered
// HTML; only what depends on today's date (the countdown, the "in force"
// state and which entries a limited list shows) is filled in after mount.

import { useEffect, useId, useMemo, useState } from 'react';
import Link from 'next/link';
import calendarData from '../../data/regulatory_calendar.json';
import watchlistData from '../../data/suppliers.json';
import type { RegulatoryCalendarData, RegulatoryEntry } from '../../types/extras-track';

interface WatchlistSupplier {
  name: string;
  category: string;
  tier: string;
}

const calendar = calendarData as unknown as RegulatoryCalendarData;
const WATCHLIST: WatchlistSupplier[] = (
  watchlistData as unknown as { suppliers: { name: string; category: string; bat_exposure: string }[] }
).suppliers.map((s) => ({ name: s.name, category: s.category, tier: s.bat_exposure }));
const ALL_CATEGORIES = Array.from(new Set(WATCHLIST.map((s) => s.category)));

const DAY_MS = 86_400_000;
// A date stays on a limited list this long after it comes into force: the
// month after a rule starts is when questions about it arrive.
const IN_FORCE_WINDOW_DAYS = 60;
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

// Calendar dates compared as whole days. The entry is a legal date, the
// reader's "today" is their own calendar day; both become UTC midnights.
function dayNumber(isoDate: string): number {
  const [y, m, d] = isoDate.split('-').map(Number);
  return Date.UTC(y, m - 1, d) / DAY_MS;
}

function todayNumber(): number {
  const now = new Date();
  return Date.UTC(now.getFullYear(), now.getMonth(), now.getDate()) / DAY_MS;
}

function formatDate(isoDate: string): string {
  const [y, m, d] = isoDate.split('-').map(Number);
  return `${d} ${MONTHS[m - 1]} ${y}`;
}

const withCommas = (n: number) => String(n).replace(/\B(?=(\d{3})+(?!\d))/g, ',');

function hostOf(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, '');
  } catch {
    return 'source';
  }
}

// Which entries a list shows: upcoming dates and recently started ones, in
// date order. Before mount the reference day is the day the calendar was last
// verified, which is fixed data, so the server and the first browser render
// pick the same entries; after mount it is the reader's today.
function select(entries: RegulatoryEntry[], referenceDay: number, limit?: number): RegulatoryEntry[] {
  const sorted = [...entries].sort((a, b) => a.date.localeCompare(b.date));
  const current = sorted.filter((e) => dayNumber(e.date) >= referenceDay - IN_FORCE_WINDOW_DAYS);
  const pool = current.length > 0 ? current : sorted.slice(-Math.max(1, limit ?? sorted.length));
  return limit ? pool.slice(0, limit) : pool;
}

function Countdown({ date, today }: { date: string; today: number | null }) {
  const base = 'shrink-0 inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-semibold whitespace-nowrap';
  if (today === null) {
    // Holds the badge's place so nothing shifts when it fills in.
    return <span className={`${base} border-gray-200 bg-gray-50 text-transparent`} aria-hidden="true">000 days</span>;
  }
  const left = dayNumber(date) - today;
  if (left < 0) return <span className={`${base} border-slate-800 bg-slate-800 text-white`}>In force</span>;
  if (left === 0) return <span className={`${base} border-red-300 bg-red-100 text-red-800`}>Applies today</span>;
  const text = left === 1 ? 'Tomorrow' : `${withCommas(left)} days left`;
  const tone =
    left <= 30 ? 'border-red-300 bg-red-100 text-red-800' :
    left <= 120 ? 'border-amber-300 bg-amber-100 text-amber-800' :
    'border-gray-300 bg-gray-100 text-gray-700';
  return <span className={`${base} ${tone}`}>{text}</span>;
}

const TIER_DOT: Record<string, string> = {
  Critical: 'bg-red-500',
  High: 'bg-amber-500',
  Medium: 'bg-green-500',
};

function SupplierChip({ supplier }: { supplier: WatchlistSupplier }) {
  return (
    <Link
      href={`/details/${encodeURIComponent(supplier.name)}`}
      title={`${supplier.name}: ${supplier.tier} exposure tier`}
      className="inline-flex items-center gap-1 rounded border border-gray-200 bg-gray-50 px-1.5 py-0.5 text-[11px] text-gray-700 hover:border-gray-300 hover:bg-white"
    >
      <span className={`h-1.5 w-1.5 rounded-full ${TIER_DOT[supplier.tier] ?? 'bg-gray-400'}`} aria-hidden="true" />
      {supplier.name}
    </Link>
  );
}

function Affected({ entry, compact }: { entry: RegulatoryEntry; compact: boolean }) {
  const rows = entry.affected_categories.map((category) => ({
    category,
    suppliers: WATCHLIST.filter((s) => s.category === category),
  }));
  const supplierCount = rows.reduce((n, r) => n + r.suppliers.length, 0);
  const coversAll =
    ALL_CATEGORIES.length > 0 && ALL_CATEGORIES.every((c) => entry.affected_categories.includes(c));

  if (compact) {
    return (
      <div className="mt-1 text-xs text-gray-500">
        {coversAll
          ? `Every category on the watchlist · ${supplierCount} suppliers`
          : rows.map((r) => `${r.category} (${r.suppliers.length})`).join(' · ')}
      </div>
    );
  }

  const list = (
    <ul className="mt-1.5 space-y-1.5">
      {rows.map((r) => (
        <li key={r.category} className="flex flex-wrap items-center gap-1.5">
          <span className="text-xs font-semibold text-gray-600">{r.category}</span>
          {r.suppliers.length === 0 ? (
            <span className="text-xs text-gray-400">no supplier on the watchlist</span>
          ) : (
            r.suppliers.map((s) => <SupplierChip key={s.name} supplier={s} />)
          )}
        </li>
      ))}
    </ul>
  );

  if (coversAll) {
    return (
      <details className="mt-2 group">
        <summary className="cursor-pointer text-xs font-semibold text-gray-600 hover:text-gray-900">
          Every category on the watchlist: {supplierCount} suppliers
          <span className="ml-1 font-normal text-gray-400 group-open:hidden">(show)</span>
        </summary>
        {list}
      </details>
    );
  }
  return (
    <div className="mt-2">
      <div className="text-[11px] font-semibold uppercase tracking-wider text-gray-400">
        Affects {supplierCount} supplier{supplierCount === 1 ? '' : 's'}
      </div>
      {list}
    </div>
  );
}

export default function RegulatoryCalendar({ limit, compact = false }: { limit?: number; compact?: boolean }) {
  const titleId = useId();
  const [today, setToday] = useState<number | null>(null);

  useEffect(() => {
    setToday(todayNumber());
    // A page left open overnight moves on to the next day.
    const timer = window.setInterval(() => setToday(todayNumber()), 60 * 60 * 1000);
    return () => window.clearInterval(timer);
  }, []);

  const entries = useMemo(
    () => select(calendar.entries, today ?? dayNumber(calendar.verified_on), limit),
    [today, limit]
  );
  const shownIds = new Set(entries.map((e) => e.id));
  const lastShown = entries[entries.length - 1]?.date ?? '';
  const notShown = calendar.entries.filter((e) => !shownIds.has(e.id));
  const later = notShown.filter((e) => e.date > lastShown).length;
  const earlier = notShown.length - later;

  return (
    <section
      aria-labelledby={titleId}
      className="rounded-xl border border-gray-200 bg-white shadow-sm overflow-hidden"
    >
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 border-b border-gray-200 bg-gray-50 px-4 sm:px-6 py-3">
        <h2 id={titleId} className={`${compact ? 'text-base' : 'text-lg'} font-bold text-gray-900`}>
          Regulatory calendar
        </h2>
        <span className="text-xs text-gray-500">
          Dates checked against official sources, {formatDate(calendar.verified_on)}
        </span>
      </div>

      <ol className="divide-y divide-gray-100">
        {entries.map((entry) => (
          <li key={entry.id} className={`px-4 sm:px-6 ${compact ? 'py-3' : 'py-4'}`}>
            <div className={compact ? '' : 'sm:grid sm:grid-cols-[7.5rem_1fr] sm:gap-4'}>
              {!compact && (
                <div className="mb-1 sm:mb-0 pt-0.5">
                  <time dateTime={entry.date} className="block text-sm font-semibold text-gray-900">
                    {formatDate(entry.date)}
                  </time>
                  <span className="text-xs text-gray-500">{entry.jurisdiction}</span>
                </div>
              )}
              <div className="min-w-0">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    {compact && (
                      <div className="text-xs text-gray-500">
                        <time dateTime={entry.date}>{formatDate(entry.date)}</time> · {entry.jurisdiction}
                      </div>
                    )}
                    <h3 className="text-sm font-semibold text-gray-900 leading-snug" title={compact ? entry.what : undefined}>
                      {entry.title}
                    </h3>
                  </div>
                  <Countdown date={entry.date} today={today} />
                </div>
                {!compact && <p className="mt-1 text-sm text-gray-600 leading-relaxed">{entry.what}</p>}
                <Affected entry={entry} compact={compact} />
                {!compact && (
                  <a
                    href={entry.source_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="mt-2 inline-block text-xs text-blue-800 hover:underline"
                  >
                    Source: {hostOf(entry.source_url)} ↗
                  </a>
                )}
              </div>
            </div>
          </li>
        ))}
      </ol>

      {notShown.length > 0 && (
        <div className="border-t border-gray-100 px-4 sm:px-6 py-2.5 text-xs text-gray-500">
          {[
            later > 0 && `${later} later date${later === 1 ? '' : 's'} not shown`,
            earlier > 0 && `${earlier} already in force for a while`,
          ]
            .filter(Boolean)
            .join(' · ')}
        </div>
      )}
    </section>
  );
}
