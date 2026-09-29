import type { RAGScore, RagHistoryEntry } from '../../../types/intel';
import { formatDay, parseSnapshotTime } from './time';

// The overall status at every check in the record, oldest on the left: shows
// whether this week's colour is a blip or the tail of a run. Labels come from
// the data, never from the clock, so this renders identically on the server,
// in the browser and on paper.
function tone(score: RAGScore): string {
  if (score === 'RED') return 'bg-red-500 h-6';
  if (score === 'AMBER') return 'bg-amber-400 h-4';
  if (score === 'GREEN') return 'bg-green-500 h-2';
  return 'bg-gray-300 h-2';
}

export default function RagStrip({ history }: { history: RagHistoryEntry[] }) {
  const recent = history
    .slice()
    .sort((a, b) => parseSnapshotTime(a.timestamp).getTime() - parseSnapshotTime(b.timestamp).getTime())
    .slice(-60);
  if (recent.length < 4) return null;

  const first = parseSnapshotTime(recent[0].timestamp);
  const last = parseSnapshotTime(recent[recent.length - 1].timestamp);
  const stamp = (iso: string) => parseSnapshotTime(iso).toISOString().slice(0, 16).replace('T', ' ');

  return (
    <div className="brief-exact">
      <div className="flex items-end gap-[2px] h-6" role="img" aria-label={`Overall status at each of the last ${recent.length} checks`}>
        {recent.map((entry, idx) => (
          <div
            key={`${entry.timestamp}-${idx}`}
            title={`${stamp(entry.timestamp)} UTC: ${entry.overall}`}
            className={`flex-1 rounded-sm ${tone(entry.overall)}`}
          />
        ))}
      </div>
      <div className="mt-1 flex justify-between text-[11px] text-gray-500">
        <span>{formatDay(first)}</span>
        <span>{recent.length} checks</span>
        <span>{formatDay(last)}</span>
      </div>
    </div>
  );
}
