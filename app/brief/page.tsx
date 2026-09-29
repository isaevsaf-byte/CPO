import type { Metadata } from 'next';
import Link from 'next/link';
import intel from '../../data/intel_snapshot.json';
import countryRiskData from '../../data/country_risk.json';
import type { ChangeLogEntry, RAGScore } from '../../types/intel';
import { getRAGLabel, getRiskColor } from '../../types/intel';
import type { BriefAction, BriefSnapshot, CountryRiskFile } from '../../types/extras';
import BriefSection from '../components/brief/BriefSection';
import PrintButton from '../components/brief/PrintButton';
import RagStrip from '../components/brief/RagStrip';
import RelativeAge from '../components/brief/RelativeAge';
import WorldSignalsSection from '../components/brief/WorldSignalsSection';
import {
  buildActionItems,
  changesInWindow,
  checksInWindow,
  neutralBuyer,
  readWorldSignals,
  standingExposure,
  statusHeadline,
  statusStreak,
} from '../components/brief/buildBrief';
import { formatDate, formatDateTime, formatDay, formatDuration, parseSnapshotTime } from '../components/brief/time';

export const metadata: Metadata = {
  title: 'This week’s brief — Supply Chain Watchtower',
  description:
    'A printable one-page summary of the demo supplier-risk board: the overall status and how long it has held, what changed in the last seven days, the top actions and standing country exposure. A sample supplier list with live public signals.',
  alternates: { canonical: '/brief' },
};

// Built once, when the site is built, from the same snapshot the board reads.
const snapshot = intel as unknown as BriefSnapshot;
const floors = (countryRiskData as unknown as CountryRiskFile).countries;

const WINDOW_DAYS = 7;
const MAX_CHANGES = 8;
const DAY_MS = 86_400_000;

const STATUS_TONE: Record<RAGScore, { box: string; text: string; emoji: string; word: string }> = {
  RED: { box: 'bg-red-50 border-red-300', text: 'text-red-800', emoji: '🔴', word: 'Red' },
  AMBER: { box: 'bg-amber-50 border-amber-300', text: 'text-amber-800', emoji: '🟡', word: 'Amber' },
  GREEN: { box: 'bg-green-50 border-green-300', text: 'text-green-800', emoji: '🟢', word: 'Green' },
  UNKNOWN: { box: 'bg-gray-50 border-gray-300', text: 'text-gray-800', emoji: '⚪', word: 'Unknown' },
};

const PILLAR_PILL: Record<RAGScore, string> = {
  RED: 'bg-red-100 text-red-800 border-red-300',
  AMBER: 'bg-amber-100 text-amber-800 border-amber-300',
  GREEN: 'bg-green-100 text-green-800 border-green-300',
  UNKNOWN: 'bg-gray-100 text-gray-800 border-gray-300',
};

const ACTION_PILL: Record<BriefAction['kind'], { label: string; tone: string }> = {
  sanctions: { label: 'Sanctions', tone: 'bg-red-100 text-red-900 border-red-400' },
  critical: { label: 'Critical', tone: 'bg-red-100 text-red-800 border-red-300' },
  high: { label: 'High', tone: 'bg-amber-100 text-amber-800 border-amber-300' },
};

function changeMarker(entry: ChangeLogEntry): { glyph: string; tone: string; label: string } {
  if (entry.direction === 'up') return { glyph: '▲', tone: 'text-red-600', label: 'Risk up' };
  if (entry.direction === 'down') return { glyph: '▼', tone: 'text-green-600', label: 'Risk down' };
  return { glyph: '•', tone: 'text-gray-400', label: 'Note' };
}

function countWord(count: number, one: string, many: string): string {
  return `${count} ${count === 1 ? one : many}`;
}

export default function BriefPage() {
  const asOf = parseSnapshotTime(snapshot.last_updated);
  const weekStart = new Date(asOf.getTime() - WINDOW_DAYS * DAY_MS);
  const suppliers = snapshot.suppliers?.suppliers ?? [];
  const peers = snapshot.peer_group ?? [];
  const history = snapshot.rag_history ?? [];

  const score: RAGScore = snapshot.overall_rag?.score ?? history[history.length - 1]?.overall ?? 'UNKNOWN';
  const tone = STATUS_TONE[score] ?? STATUS_TONE.UNKNOWN;
  const actions = buildActionItems(suppliers, peers);
  const topActions = actions.slice(0, 3);

  const streak = statusStreak(history, asOf);
  const streakLine =
    streak && streak.score === score
      ? streak.coversWholeRecord
        ? `${tone.word} at every check in the ${streak.recordDays}-day record kept, so since ${formatDate(parseSnapshotTime(streak.since))} at least.`
        : `${tone.word} since ${formatDateTime(parseSnapshotTime(streak.since))}: ${formatDuration(streak.heldHours)}.`
      : null;

  const checks = checksInWindow(history, asOf, WINDOW_DAYS);
  const checkParts = (['RED', 'AMBER', 'GREEN', 'UNKNOWN'] as RAGScore[])
    .filter((key) => (checks.byScore[key] ?? 0) > 0)
    .map((key) => `${checks.byScore[key]} ${STATUS_TONE[key].word.toLowerCase()}`);

  const changes = changesInWindow(snapshot.change_log, asOf, WINDOW_DAYS);
  const shownChanges = changes.slice(0, MAX_CHANGES);

  const exposure = standingExposure(suppliers, floors);
  const exposedCount = exposure.reduce((sum, group) => sum + group.suppliers.length, 0);

  const world = readWorldSignals(snapshot.world_signals);
  const pillars = snapshot.overall_rag?.pillar_scores;

  return (
    <div className="min-h-screen bg-slate-50 print:bg-white print:min-h-0">
      {/* Rendered with this page only, so the A4 size and print colour rules
          never apply when some other page is printed. */}
      <style>{`
        @page { size: A4; margin: 12mm; }
        @media print {
          .brief-print { zoom: 0.85; }
          .brief-exact { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
        }
      `}</style>

      <div className="brief-print">
        <header className="bg-gradient-to-r from-blue-900 via-blue-800 to-blue-900 text-white shadow-lg overflow-hidden print:bg-none print:text-gray-900 print:shadow-none">
          <div className="max-w-4xl mx-auto px-4 sm:px-6 py-6 print:px-0 print:py-2 flex flex-col sm:flex-row sm:items-end sm:justify-between gap-4">
            <div>
              <div className="text-xs font-semibold uppercase tracking-wider text-blue-200 print:text-gray-500">
                Supply Chain Watchtower · demo board
              </div>
              <h1 className="mt-1 text-2xl sm:text-3xl font-bold">This week&rsquo;s brief</h1>
              <p className="mt-2 text-sm sm:text-base text-blue-100 print:text-gray-600">
                Week to {formatDate(asOf)} · data as of {formatDateTime(asOf)}
              </p>
            </div>
            <PrintButton className="self-start sm:self-auto" />
          </div>
        </header>

        <main className="max-w-4xl mx-auto px-4 sm:px-6 py-8 space-y-6 print:max-w-none print:px-0 print:py-2 print:space-y-3">
          <p className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900 print:bg-transparent print:px-0 print:py-0 print:border-0 print:text-xs">
            <span className="font-semibold">Demo data, for illustration.</span> The watchlist and the
            exposure tier beside each supplier were set up for this demonstration; they are not any
            company&rsquo;s own classification. The signals come from public sources.
          </p>

          {/* 1 · Overall status */}
          <section className={`rounded-xl border-2 p-5 sm:p-6 break-inside-avoid ${tone.box} print:border print:rounded-lg print:p-4`}>
            <div className="flex flex-wrap items-center justify-between gap-4">
              <div className="flex items-center gap-4">
                <span className="text-4xl" aria-hidden="true">
                  {tone.emoji}
                </span>
                <div>
                  <h2 className="text-xs font-semibold uppercase tracking-wider text-gray-500">Overall status</h2>
                  <div className={`text-2xl font-bold ${tone.text}`}>{statusHeadline(score, topActions.length)}</div>
                </div>
              </div>
              {pillars && (
                <div className="flex flex-wrap gap-2">
                  {(
                    [
                      ['Macro', pillars.macro],
                      ['Peers', pillars.peers],
                      ['Suppliers', pillars.suppliers],
                    ] as const
                  ).map(([label, pillar]) => (
                    <span
                      key={label}
                      className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs ${PILLAR_PILL[pillar] ?? PILLAR_PILL.UNKNOWN}`}
                    >
                      <span className="font-medium">{label}</span>
                      <span className="font-bold">{getRAGLabel(pillar)}</span>
                    </span>
                  ))}
                </div>
              )}
            </div>

            <div className="mt-4 space-y-1 text-sm print:mt-2">
              {streakLine && <p className="font-medium text-gray-900">{streakLine}</p>}
              <p className="text-gray-600">
                {checks.total > 0
                  ? `${countWord(checks.total, 'check', 'checks')} in the ${WINDOW_DAYS} days: ${checkParts.join(', ')}.`
                  : `No checks ran in the ${WINDOW_DAYS} days to ${formatDay(asOf)}.`}
              </p>
            </div>

            {history.length > 0 && (
              <div className="mt-4 pt-3 border-t border-black/10 print:mt-2 print:pt-2">
                <RagStrip history={history} />
              </div>
            )}
          </section>

          {/* 2 · What changed */}
          <BriefSection
            title={`What changed in the last ${WINDOW_DAYS} days`}
            aside={`${formatDay(weekStart)} – ${formatDay(asOf)}`}
          >
            {shownChanges.length > 0 ? (
              <>
                <ul className="divide-y divide-gray-100 -my-1">
                  {shownChanges.map((entry, idx) => {
                    const marker = changeMarker(entry);
                    const body = (
                      <>
                        <span className="block text-sm font-medium text-gray-900">{neutralBuyer(entry.headline)}</span>
                        {entry.detail && (
                          <span className="block text-xs text-gray-600 mt-0.5 leading-relaxed">{neutralBuyer(entry.detail)}</span>
                        )}
                      </>
                    );
                    return (
                      <li key={`${entry.at}-${entry.entity}-${idx}`} className="flex items-start gap-3 py-2.5">
                        <span className={`shrink-0 pt-0.5 text-sm ${marker.tone}`} title={marker.label} aria-label={marker.label}>
                          {marker.glyph}
                        </span>
                        <div className="min-w-0 flex-1">
                          {entry.href ? (
                            <Link href={entry.href} className="block hover:underline">
                              {body}
                            </Link>
                          ) : (
                            body
                          )}
                        </div>
                        <span className="shrink-0 pt-0.5 text-xs text-gray-500 whitespace-nowrap text-right">
                          {formatDay(parseSnapshotTime(entry.at))}
                          <RelativeAge iso={entry.at} prefix=" · " className="print:hidden" />
                        </span>
                      </li>
                    );
                  })}
                </ul>
                {changes.length > shownChanges.length && (
                  <p className="mt-2 text-xs text-gray-500">
                    {changes.length - shownChanges.length} more on the{' '}
                    <Link href="/" className="underline hover:text-gray-700">
                      board
                    </Link>
                    .
                  </p>
                )}
              </>
            ) : (
              <p className="text-sm text-gray-600">
                Nothing moved in the {WINDOW_DAYS} days to {formatDay(asOf)}.
                {checks.total > 0 &&
                  ` ${countWord(checks.total, 'check', 'checks')} ran; none changed a supplier’s level, a pillar or the overall status.`}
              </p>
            )}
          </BriefSection>

          {/* 3 · Top actions */}
          <BriefSection title="Top 3 actions" aside="sanctions first, then Critical, then High">
            {topActions.length > 0 ? (
              <>
                <ol className="space-y-2.5">
                  {topActions.map((action, idx) => (
                    <li key={action.href} className="flex items-start gap-3">
                      <span className="brief-exact shrink-0 mt-0.5 flex h-6 w-6 items-center justify-center rounded-full bg-blue-900 text-xs font-bold text-white">
                        {idx + 1}
                      </span>
                      <div className="min-w-0 text-sm text-gray-900">
                        <span
                          className={`mr-2 inline-block rounded border px-1.5 py-0.5 text-[11px] font-bold uppercase ${ACTION_PILL[action.kind].tone}`}
                        >
                          {ACTION_PILL[action.kind].label}
                        </span>
                        <Link href={action.href} className="hover:underline">
                          {action.label}
                        </Link>
                      </div>
                    </li>
                  ))}
                </ol>
                {actions.length > topActions.length && (
                  <p className="mt-3 text-xs text-gray-500">
                    {countWord(actions.length - topActions.length, 'more item', 'more items')} on the{' '}
                    <Link href="/" className="underline hover:text-gray-700">
                      board
                    </Link>
                    .
                  </p>
                )}
              </>
            ) : (
              <p className="text-sm text-gray-600">
                No actions this week. No supplier has a possible sanctions match, and none reached a
                High or Critical event level. Standing country exposure is left out of actions on
                purpose; it is below.
              </p>
            )}
          </BriefSection>

          {/* 4 · Standing exposure */}
          <BriefSection title="Standing exposure" aside="background, the same every week">
            <p className="text-sm text-gray-600">
              {exposedCount > 0
                ? `${exposedCount} of ${suppliers.length} watchlist suppliers sit in ${countWord(exposure.length, 'country', 'countries')} with a standing risk floor. `
                : 'No watchlist supplier sits in a country with a standing risk floor. '}
              It comes from where a supplier operates, not from anything that happened this week, and on
              its own it never turns the board amber or red.
            </p>
            {exposure.length > 0 && (
              <ul className="mt-3 divide-y divide-gray-100 print:mt-2 print:grid print:grid-cols-2 print:gap-x-6 print:divide-y-0">
                {exposure.map((group) => (
                  <li key={group.country} className="py-2.5 break-inside-avoid print:py-1.5">
                    <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                      <span className={`inline-block rounded-full border px-2 py-0.5 text-[11px] font-bold ${getRiskColor(group.level)}`}>
                        {group.level}
                      </span>
                      <span className="text-sm font-semibold text-gray-900">{group.country}</span>
                      <span className="text-xs text-gray-500">{countWord(group.suppliers.length, 'supplier', 'suppliers')}</span>
                    </div>
                    <p className="mt-0.5 text-sm text-gray-700">{group.reason}</p>
                    <p className="mt-0.5 text-xs text-gray-600">
                      {group.suppliers.map((supplier, idx) => (
                        <span key={supplier.name}>
                          {idx > 0 && ' · '}
                          <Link href={`/details/${encodeURIComponent(supplier.name)}`} className="hover:underline">
                            {supplier.name}
                          </Link>{' '}
                          <span className="text-gray-500">
                            ({supplier.category}, {supplier.exposure} tier)
                          </span>
                        </span>
                      ))}
                    </p>
                  </li>
                ))}
              </ul>
            )}
          </BriefSection>

          {/* 5 · World signals: only when the snapshot carries them */}
          {world && <WorldSignalsSection view={world} supplierNames={suppliers.map((supplier) => supplier.name)} />}

          <footer className="pt-2 text-xs text-gray-500 space-y-1 print:pt-1">
            <p>
              Supply Chain Watchtower is a demonstration board: the supplier list is a sample, the signals on it are live.
            </p>
            <p>
              <Link href="/" className="underline hover:text-gray-700 print:no-underline">
                Open the full board
              </Link>
              <span className="hidden print:inline"> at cpo-watchtower.co.uk</span>
            </p>
          </footer>
        </main>
      </div>
    </div>
  );
}
