'use client';

import type { RagHistoryEntry, RAGScore } from '../types/intel';
import { getRAGLabel } from '../types/intel';

type PillarKey = 'overall' | 'macro' | 'peers' | 'suppliers';

const STATUS_FILL: Record<RAGScore, string> = {
  RED: 'bg-red-500',
  AMBER: 'bg-amber-400',
  GREEN: 'bg-green-500',
  UNKNOWN: 'bg-gray-300',
};

const ROWS: { key: PillarKey; label: string }[] = [
  { key: 'overall', label: 'Overall' },
  { key: 'macro', label: 'Macro' },
  { key: 'peers', label: 'Peers' },
  { key: 'suppliers', label: 'Suppliers' },
];

interface Segment {
  status: RAGScore;
  startMs: number;
  endMs: number;
}

// Each history entry is a snapshot at one harvest cycle; its "duration" runs
// until the next entry (or until now, for the most recent one). Adjacent
// entries with the same status are merged into one segment so the timeline
// reads as a handful of colored spans, not dozens of slivers — the same
// decluttering the Overall Status card's dot-strip needed earlier.
function buildSegments(history: RagHistoryEntry[], field: PillarKey): Segment[] {
  if (history.length === 0) return [];
  const sorted = [...history].sort(
    (a, b) => new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime()
  );
  const now = Date.now();
  const slices: Segment[] = sorted.map((entry, i) => ({
    status: entry[field],
    startMs: new Date(entry.timestamp).getTime(),
    endMs: i < sorted.length - 1 ? new Date(sorted[i + 1].timestamp).getTime() : now,
  }));

  const merged: Segment[] = [];
  for (const s of slices) {
    const last = merged[merged.length - 1];
    if (last && last.status === s.status) {
      last.endMs = s.endMs;
    } else {
      merged.push({ ...s });
    }
  }
  return merged;
}

function formatTick(ms: number): string {
  return new Date(ms).toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}

function formatTooltipRange(startMs: number, endMs: number, rangeEnd: number): string {
  const start = formatTick(startMs);
  if (endMs >= rangeEnd - 60_000) return `${start} – now`;
  return `${start} – ${formatTick(endMs)}`;
}

export default function RagTrendChart({ history }: { history?: RagHistoryEntry[] }) {
  if (!history || history.length < 2) return null;

  const sortedTs = history
    .map((h) => new Date(h.timestamp).getTime())
    .sort((a, b) => a - b);
  const rangeStart = sortedTs[0];
  const rangeEnd = Date.now();
  const rangeSpan = Math.max(rangeEnd - rangeStart, 1);
  const midMs = rangeStart + rangeSpan / 2;

  return (
    <div className="bg-white rounded-xl shadow-sm border border-gray-200 p-6 mb-8">
      <div className="flex items-center justify-between mb-4 flex-wrap gap-2">
        <h2 className="text-lg font-bold text-gray-900">Status Trend</h2>
        <div className="flex items-center gap-4 text-xs text-gray-600">
          {(['GREEN', 'AMBER', 'RED'] as RAGScore[]).map((s) => (
            <span key={s} className="flex items-center gap-1.5">
              <span className={`w-2.5 h-2.5 rounded-full ${STATUS_FILL[s]}`} />
              {getRAGLabel(s)}
            </span>
          ))}
        </div>
      </div>

      <div className="space-y-3">
        {ROWS.map((row) => {
          const segments = buildSegments(history, row.key);
          return (
            <div key={row.key} className="flex items-center gap-3">
              <div className="w-20 shrink-0 text-xs font-semibold text-gray-500 uppercase tracking-wide">
                {row.label}
              </div>
              {/* No overflow-hidden here — segments get rounded end-caps
                  individually instead, so the hover tooltip (positioned
                  above each segment) isn't clipped by the track. */}
              <div className="flex-1 flex h-6 gap-[2px] bg-gray-100">
                {segments.map((seg, idx) => {
                  const widthPct = ((seg.endMs - seg.startMs) / rangeSpan) * 100;
                  const roundedClass = [
                    idx === 0 ? 'rounded-l-md' : '',
                    idx === segments.length - 1 ? 'rounded-r-md' : '',
                  ].join(' ');
                  return (
                    <div
                      key={idx}
                      tabIndex={0}
                      className={`group relative h-full outline-none ${STATUS_FILL[seg.status]} ${roundedClass} hover:brightness-110 focus:brightness-110 transition`}
                      style={{ width: `${widthPct}%`, minWidth: widthPct > 0 ? '2px' : 0 }}
                    >
                      <div className="pointer-events-none absolute bottom-full left-1/2 -translate-x-1/2 mb-2 hidden group-hover:block group-focus:block z-10 whitespace-nowrap">
                        <div className="bg-gray-900 text-white text-xs rounded-md px-2.5 py-1.5 shadow-lg">
                          <span className="font-bold">{getRAGLabel(seg.status)}</span>
                          <span className="text-gray-300 ml-1.5">
                            {formatTooltipRange(seg.startMs, seg.endMs, rangeEnd)}
                          </span>
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          );
        })}
      </div>

      <div className="flex items-center gap-3 mt-2">
        <div className="w-20 shrink-0" />
        <div className="flex-1 flex justify-between text-[11px] text-gray-400">
          <span>{formatTick(rangeStart)}</span>
          <span>{formatTick(midMs)}</span>
          <span>Now</span>
        </div>
      </div>
    </div>
  );
}
