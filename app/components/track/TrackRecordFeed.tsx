'use client';

import { useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import type { ChangeKind } from '../../../types/intel';
import type { TrackEntry, TrackKindCount, TrackWeek } from '../../../types/extras-track';

type Filter = ChangeKind | 'all';

// Same glyphs and colours as the change feed on the board, so an entry reads
// the same wherever it appears.
const MARKERS = {
  up: { glyph: '▲', tone: 'text-red-600', spoken: 'Escalated' },
  down: { glyph: '▼', tone: 'text-green-600', spoken: 'Eased' },
  info: { glyph: '•', tone: 'text-gray-400', spoken: 'Noted' },
} as const;

function EntryRow({ entry, kindLabel }: { entry: TrackEntry; kindLabel: string }) {
  const marker = MARKERS[entry.direction] ?? MARKERS.info;
  return (
    <li className="flex items-start gap-3">
      <span className={`shrink-0 pt-0.5 text-sm ${marker.tone}`} aria-hidden="true">
        {marker.glyph}
      </span>
      <div className="min-w-0 flex-1">
        <div className="text-sm font-medium text-gray-900">
          <span className="sr-only">{marker.spoken}: </span>
          {entry.entityHref ? (
            <Link
              href={entry.entityHref}
              className="underline decoration-gray-300 underline-offset-2 hover:text-blue-900 hover:decoration-blue-900"
              title={`Open ${entry.entity}`}
            >
              {entry.headline}
            </Link>
          ) : (
            entry.headline
          )}
        </div>
        {entry.detail && (
          <div className="text-xs text-gray-600 mt-0.5 leading-relaxed">{entry.detail}</div>
        )}
        <div className="mt-1 text-[11px] font-semibold uppercase tracking-wider text-gray-400">
          {kindLabel}
        </div>
      </div>
    </li>
  );
}

function Chip({
  label,
  count,
  active,
  onClick,
}: {
  label: string;
  count: number;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={`inline-flex items-center gap-2 rounded-full border px-3 py-1.5 text-sm font-semibold transition-colors ${
        active
          ? 'border-blue-900 bg-blue-900 text-white'
          : 'border-gray-300 bg-white text-gray-700 hover:border-gray-400 hover:bg-gray-50'
      }`}
    >
      {label}
      <span
        className={`rounded-full px-1.5 text-xs font-bold ${
          active ? 'bg-white/20 text-white' : 'bg-gray-100 text-gray-600'
        }`}
      >
        {count}
      </span>
    </button>
  );
}

export default function TrackRecordFeed({
  weeks,
  kinds,
  total,
}: {
  weeks: TrackWeek[];
  kinds: TrackKindCount[];
  total: number;
}) {
  // 'all' on the server and on the first client pass, so the prerendered
  // page carries the whole record.
  const [filter, setFilter] = useState<Filter>('all');

  // A filtered view can be sent as a link: /track-record#price_move
  useEffect(() => {
    const fromHash = decodeURIComponent(window.location.hash.slice(1));
    const match = kinds.find((k) => k.kind === fromHash);
    if (match) setFilter(match.kind);
  }, [kinds]);

  const choose = (next: Filter) => {
    setFilter(next);
    const { pathname, search } = window.location;
    window.history.replaceState(null, '', next === 'all' ? `${pathname}${search}` : `#${next}`);
  };

  const labelOf = useMemo(() => {
    const labels = new Map(kinds.map((k) => [k.kind, k.label]));
    return (kind: ChangeKind) => labels.get(kind) ?? kind;
  }, [kinds]);

  const visibleWeeks = useMemo(() => {
    if (filter === 'all') return weeks;
    return weeks
      .map((week) => ({
        ...week,
        harvests: week.harvests
          .map((harvest) => ({ ...harvest, entries: harvest.entries.filter((e) => e.kind === filter) }))
          .filter((harvest) => harvest.entries.length > 0),
      }))
      .filter((week) => week.harvests.length > 0);
  }, [weeks, filter]);

  const shown = filter === 'all' ? total : kinds.find((k) => k.kind === filter)?.count ?? 0;

  return (
    <div>
      <div className="flex flex-wrap items-center gap-2" role="group" aria-label="Show one kind of change">
        <Chip label="All" count={total} active={filter === 'all'} onClick={() => choose('all')} />
        {kinds.map((k) => (
          <Chip
            key={k.kind}
            label={k.label}
            count={k.count}
            active={filter === k.kind}
            onClick={() => choose(filter === k.kind ? 'all' : k.kind)}
          />
        ))}
      </div>
      <p className="mt-3 text-xs text-gray-500" aria-live="polite">
        {filter === 'all'
          ? `Showing all ${total} change${total === 1 ? '' : 's'}, newest first.`
          : `Showing ${shown} of ${total} changes (${labelOf(filter)}).`}
      </p>

      <div className="mt-5 space-y-6">
        {visibleWeeks.map((week) => {
          const count = week.harvests.reduce((n, h) => n + h.entries.length, 0);
          return (
            <section
              key={week.weekStart}
              aria-labelledby={`week-${week.weekStart}`}
              className="rounded-xl border border-gray-200 bg-white shadow-sm overflow-hidden"
            >
              <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 border-b border-gray-200 bg-gray-50 px-4 sm:px-6 py-3">
                <h2 id={`week-${week.weekStart}`} className="text-base font-bold text-gray-900">
                  {week.label}
                </h2>
                <span className="text-xs text-gray-500">
                  {count > 0 ? `${count} change${count === 1 ? '' : 's'}` : 'Quiet week'}
                  {week.note && <span className="text-gray-400"> · {week.note}</span>}
                </span>
              </div>
              {week.harvests.length === 0 ? (
                <p className="px-4 sm:px-6 py-4 text-sm text-gray-500">
                  Nothing logged: no risk level, signal or status moved enough to record.
                </p>
              ) : (
                <ol className="divide-y divide-gray-100">
                  {week.harvests.map((harvest) => (
                    <li
                      key={harvest.at}
                      className="px-4 sm:px-6 py-3.5 sm:grid sm:grid-cols-[10.5rem_1fr] sm:gap-4"
                    >
                      <time
                        dateTime={harvest.at}
                        className="block pb-2 sm:pb-0 pt-0.5 text-xs font-medium text-gray-500 whitespace-nowrap"
                      >
                        {harvest.label}
                      </time>
                      <ul className="space-y-3">
                        {harvest.entries.map((entry) => (
                          <EntryRow key={entry.id} entry={entry} kindLabel={labelOf(entry.kind)} />
                        ))}
                      </ul>
                    </li>
                  ))}
                </ol>
              )}
            </section>
          );
        })}
      </div>
    </div>
  );
}
