'use client';

import { useEffect, useMemo, useState } from 'react';
import { useRouter } from 'next/navigation';
import Link from 'next/link';
import intel from '../data/intel_snapshot.json';
import { useDataFreshness } from '../hooks/useDataFreshness';
import type {
  IntelSnapshot,
  RAGScore as RAGScoreType,
  Supplier,
  PeerGroupItem,
  ChangeLogEntry,
} from '../types/intel';
import {
  getRAGColor,
  getRAGLabel,
  getExposureColor,
  RAG_COLORS,
  RAG_LABELS,
} from '../types/intel';

// Cast intel to proper type
const typedIntel = intel as unknown as IntelSnapshot;

// Snapshots written before timestamps carried an offset are bare UTC
// date-times, and JavaScript resolves those as LOCAL time — which showed an
// 18:22 UTC harvest as "6:22 PM GMT+1" and skewed every age calculation by
// the reader's offset. The harvester now emits +00:00 (see utc_now_iso), and
// this pins the older entries still sitting in rag_history to UTC as well.
function parseSnapshotTime(isoString: string): Date {
  const hasZone = /(?:Z|[+-]\d{2}:?\d{2})$/.test(isoString);
  return new Date(hasZone ? isoString : `${isoString}Z`);
}

function formatTimestamp(isoString: string | undefined): string {
  if (!isoString) return 'Unknown';
  try {
    const date = parseSnapshotTime(isoString);
    return date.toLocaleString('en-US', {
      month: 'short',
      day: 'numeric',
      year: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
      timeZoneName: 'short'
    });
  } catch {
    return 'Unknown';
  }
}

// Health status indicator component
function HealthIndicator({ status }: { status: string }) {
  const color = status === 'success' ? 'bg-green-500' : status === 'error' ? 'bg-red-500' : 'bg-yellow-500';
  return (
    <span className={`inline-block w-2 h-2 rounded-full ${color}`} title={`Status: ${status}`} />
  );
}

// How long the current overall score has been in effect — tells a CPO
// whether today's status is a new blip or something that's been sitting
// there for days, without exposing raw per-cycle check data.
function currentStreakDuration(history: { overall: string; timestamp: string }[] | undefined): string | null {
  if (!history || history.length === 0) return null;
  const current = history[history.length - 1].overall;
  let streakStart = history[history.length - 1].timestamp;
  for (let i = history.length - 1; i >= 0; i--) {
    if (history[i].overall !== current) break;
    streakStart = history[i].timestamp;
  }
  const hours = (Date.now() - parseSnapshotTime(streakStart).getTime()) / (1000 * 60 * 60);
  if (hours < 1) return null;
  if (hours < 48) return `${Math.round(hours)}h`;
  return `${Math.round(hours / 24)}d`;
}

// Keys for the reader's own visit clock. The snapshot is shared and static;
// "since I last looked" is per-person, so it lives in the browser.
const VISIT_ANCHOR_KEY = 'watchtower:visit-anchor';
const LAST_SEEN_KEY = 'watchtower:last-seen';
// Reloading the page mid-session should not wipe the feed the reader is
// still working through, so the anchor only advances after a real gap away.
const SESSION_GAP_MS = 30 * 60 * 1000;
const FIRST_VISIT_WINDOW_DAYS = 7;
const COLLAPSED_ENTRY_COUNT = 6;

function relativeAge(date: Date): string {
  const minutes = (Date.now() - date.getTime()) / (1000 * 60);
  if (minutes < 60) return `${Math.max(1, Math.round(minutes))}m ago`;
  const hours = minutes / 60;
  if (hours < 36) return `${Math.round(hours)}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}

function ChangeEntryRow({ entry }: { entry: ChangeLogEntry }) {
  const marker =
    entry.direction === 'up' ? { glyph: '▲', tone: 'text-red-600' } :
    entry.direction === 'down' ? { glyph: '▼', tone: 'text-green-600' } :
    { glyph: '•', tone: 'text-gray-400' };

  const body = (
    <>
      <div className="text-sm font-medium text-gray-900">{entry.headline}</div>
      {entry.detail && (
        <div className="text-xs text-gray-600 mt-0.5 leading-relaxed">{entry.detail}</div>
      )}
    </>
  );

  return (
    <li className="flex items-start gap-3 py-2.5">
      <span className={`shrink-0 pt-0.5 text-sm ${marker.tone}`} aria-hidden="true">
        {marker.glyph}
      </span>
      <div className="min-w-0 flex-1">
        {entry.href ? (
          <Link href={entry.href} className="block hover:underline">{body}</Link>
        ) : (
          body
        )}
      </div>
      <span className="shrink-0 text-xs text-gray-400 whitespace-nowrap pt-0.5">
        {relativeAge(parseSnapshotTime(entry.at))}
      </span>
    </li>
  );
}

// "What moved since you last looked" — the question a status board cannot
// answer. An all-clear board is identical every morning, which is a poor
// reason to open it again; this section has content on quiet days too.
function ChangeFeed({ entries }: { entries: ChangeLogEntry[] }) {
  const [anchor, setAnchor] = useState<Date | null>(null);
  const [mounted, setMounted] = useState(false);
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    const now = Date.now();
    let resolvedAnchor: string | null = null;
    try {
      const lastSeenRaw = window.localStorage.getItem(LAST_SEEN_KEY);
      const lastSeen = lastSeenRaw ? new Date(lastSeenRaw).getTime() : null;

      if (lastSeen && now - lastSeen > SESSION_GAP_MS) {
        // Returning after a break: diff from the end of the previous visit.
        resolvedAnchor = lastSeenRaw;
      } else if (lastSeen) {
        // Same working session — keep whatever window is already on screen.
        resolvedAnchor = window.localStorage.getItem(VISIT_ANCHOR_KEY);
      }

      if (resolvedAnchor) {
        window.localStorage.setItem(VISIT_ANCHOR_KEY, resolvedAnchor);
      }
      window.localStorage.setItem(LAST_SEEN_KEY, new Date(now).toISOString());
    } catch {
      // Private mode / blocked storage: fall back to the first-visit window.
      resolvedAnchor = null;
    }

    setAnchor(resolvedAnchor ? new Date(resolvedAnchor) : null);
    setMounted(true);
  }, []);

  const sorted = useMemo(
    () => [...entries].sort(
      (a, b) => parseSnapshotTime(b.at).getTime() - parseSnapshotTime(a.at).getTime()
    ),
    [entries]
  );

  const cutoff = anchor
    ? anchor.getTime()
    : Date.now() - FIRST_VISIT_WINDOW_DAYS * 24 * 60 * 60 * 1000;
  const sinceVisit = sorted.filter((entry) => parseSnapshotTime(entry.at).getTime() > cutoff);

  if (entries.length === 0) return null;

  // Rendered only after the visit clock is read, so the server and the first
  // client pass agree on markup.
  if (!mounted) {
    return <div className="mb-8 h-32 rounded-xl border border-gray-200 bg-white" aria-hidden="true" />;
  }

  const hasNew = sinceVisit.length > 0;
  const shown = hasNew ? sinceVisit : sorted.slice(0, 3);
  const visible = expanded ? shown : shown.slice(0, COLLAPSED_ENTRY_COUNT);

  return (
    <div className="mb-8 rounded-xl border border-gray-200 bg-white shadow-sm overflow-hidden">
      <div className="flex flex-wrap items-baseline justify-between gap-2 border-b border-gray-200 bg-gray-50 px-6 py-4">
        <h2 className="text-lg font-bold text-gray-900">
          {hasNew
            ? `${sinceVisit.length} change${sinceVisit.length > 1 ? 's' : ''}${anchor ? ' since your last visit' : ' recently'}`
            : anchor
              ? 'Nothing new since your last visit'
              : 'No changes recorded yet'}
        </h2>
        <span className="text-xs text-gray-500">
          {anchor
            ? `Last visit ${relativeAge(anchor)}`
            : `Showing the last ${FIRST_VISIT_WINDOW_DAYS} days`}
        </span>
      </div>
      <div className="px-6 py-2">
        {!hasNew && (
          <p className="pt-2 text-sm text-gray-500">
            Most recent activity{anchor ? ', from before your last visit' : ''}:
          </p>
        )}
        <ul className="divide-y divide-gray-100">
          {visible.map((entry, idx) => (
            <ChangeEntryRow key={`${entry.at}-${entry.entity}-${idx}`} entry={entry} />
          ))}
        </ul>
        {shown.length > COLLAPSED_ENTRY_COUNT && (
          <button
            onClick={() => setExpanded(!expanded)}
            className="mb-3 mt-1 text-sm font-medium text-blue-800 hover:underline"
          >
            {expanded ? 'Show less' : `Show all ${shown.length}`}
          </button>
        )}
      </div>
    </div>
  );
}

export default function MorningCoffeeDashboard() {
  const router = useRouter();
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [riskFilter, setRiskFilter] = useState<'all' | 'cyber' | 'news' | 'operational' | 'critical' | 'high' | 'medium' | 'geopolitical' | 'sanctions' | 'recall'>('all');

  // Use the data freshness hook
  const {
    isStale,
    hoursSinceUpdate,
    hasNewVersion,
    isChecking,
    refreshData,
    dismissNewVersion,
  } = useDataFreshness({
    currentVersion: typedIntel.version,
    lastUpdated: typedIntel.last_updated,
    checkInterval: 5 * 60 * 1000, // Check every 5 minutes
    staleThresholdHours: 24,
  });

  const macro = typedIntel?.macro || {} as IntelSnapshot['macro'];
  const peers = typedIntel?.peers || {} as IntelSnapshot['peers'];
  const suppliers = typedIntel?.suppliers || {} as IntelSnapshot['suppliers'];
  const macroEconomy = typedIntel?.macro_economy || {} as IntelSnapshot['macro_economy'];
  const peerGroup = typedIntel?.peer_group || [] as PeerGroupItem[];

  // Group suppliers by category
  const suppliersByCategory: { [key: string]: Supplier[] } = {};
  const suppliersList: Supplier[] = suppliers?.suppliers || [];

  suppliersList.forEach((supplier: Supplier) => {
    const category = supplier.category || 'Other';
    if (!suppliersByCategory[category]) {
      suppliersByCategory[category] = [];
    }
    suppliersByCategory[category].push(supplier);
  });

  // Concrete "what to actually look at" list for the Overall Status banner
  // — naming specific companies and reasons instead of just "Suppliers".
  // Sanctions matches first (most urgent), then CRITICAL, then HIGH.
  type ActionItem = { label: string; href: string };
  const actionItems: ActionItem[] = [];
  suppliersList
    .filter((s: any) => s.sanctions_hit)
    .forEach((s) => actionItems.push({
      label: `Verify possible sanctions match: ${s.name}`,
      href: `/details/${encodeURIComponent(s.name)}`,
    }));
  suppliersList
    .filter((s: any) => s.counts_toward_rag !== false && s.risk_level === 'CRITICAL' && !s.sanctions_hit)
    .forEach((s) => actionItems.push({ label: `${s.name}: ${s.last_signal}`, href: `/details/${encodeURIComponent(s.name)}` }));
  peerGroup
    .filter((p) => p.risk_level === 'CRITICAL')
    .forEach((p) => actionItems.push({ label: `${p.name}: ${p.last_signal}`, href: `/details/${encodeURIComponent(p.name)}` }));
  if (actionItems.length < 3) {
    suppliersList
      .filter((s: any) => s.counts_toward_rag !== false && s.risk_level === 'HIGH')
      .forEach((s) => actionItems.push({ label: `${s.name}: ${s.last_signal}`, href: `/details/${encodeURIComponent(s.name)}` }));
  }
  const topActionItems = actionItems.slice(0, 3);

  // Suppliers whose current risk level reflects something that actually
  // happened this cycle, rather than the standing floor applied to every
  // supplier in a flagged country. Same filter process_suppliers applies for
  // the pillar RAG rollup, recomputed here from the list itself so a headline
  // count can never disagree with the rows its filter reveals.
  const isActionable = (supplier: Supplier) => (supplier as any).counts_toward_rag !== false;
  const actionableCritical = suppliersList.filter((s) => isActionable(s) && s.risk_level === 'CRITICAL').length;
  const actionableHigh = suppliersList.filter((s) => isActionable(s) && s.risk_level === 'HIGH').length;
  const actionableMedium = suppliersList.filter((s) => isActionable(s) && s.risk_level === 'MEDIUM').length;

  return (
    <div className="min-h-screen bg-slate-50">
      {/* Header */}
      <header className="bg-gradient-to-r from-blue-900 via-blue-800 to-blue-900 text-white shadow-lg overflow-hidden">
        <div className="max-w-[100rem] mx-auto px-4 sm:px-6 py-6">
          <div className="flex flex-col sm:flex-row sm:justify-between sm:items-center gap-4">
            <div>
              <h1 className="text-2xl sm:text-3xl font-bold">Global Supply Chain Watchtower</h1>
              <p className="text-blue-100 mt-2 text-sm sm:text-base">Intelligence Dashboard • Three Core Pillars</p>
            </div>
            <div className="flex flex-col sm:items-end gap-3">
              <div className="flex flex-wrap items-center gap-3 sm:gap-4">
                {/* Health Status Indicators */}
                <div className="flex items-center gap-2" title="Data Source Health">
                  <span className="text-xs text-blue-200 mr-1">Health:</span>
                  <HealthIndicator status={macro?.status || 'unknown'} />
                  <HealthIndicator status={peers?.status || 'unknown'} />
                  <HealthIndicator status={suppliers?.status || 'unknown'} />
                </div>

                <Link
                  href="/geopolitical"
                  className="text-xs text-blue-200 hover:text-white underline decoration-dotted underline-offset-2"
                  title="Experimental GDELT-based geopolitical signal, not part of the main risk score"
                >
                  🌍 Geopolitical Intel (beta)
                </Link>

                <button
                  onClick={() => setIsModalOpen(true)}
                  className="p-2 rounded-full hover:bg-blue-800 transition-colors"
                  aria-label="About this Tool"
                  title="About this Tool"
                >
                  <svg className="w-6 h-6 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                  </svg>
                </button>
              </div>
              <div className="sm:text-right">
                <div className="text-sm text-blue-100 flex items-center gap-2 flex-wrap">
                  <span>Last Updated: {formatTimestamp(typedIntel?.last_updated)}</span>
                  {isChecking && (
                    <span className="animate-spin text-xs">&#8635;</span>
                  )}
                </div>
                {typedIntel?.version && (
                  <div className="text-xs text-blue-200 font-mono truncate max-w-[220px] sm:max-w-none">
                    v{typedIntel.version}
                  </div>
                )}
                {isStale && (
                  <span className="inline-block mt-2 bg-amber-500 text-white px-3 py-1 rounded-full text-sm font-semibold">
                    &#9888; Data Stale ({Math.round(hoursSinceUpdate)}h old)
                  </span>
                )}
              </div>
            </div>
          </div>
        </div>
      </header>

      {/* New Version Available Banner */}
      {hasNewVersion && (
        <div className="bg-blue-600 text-white px-4 py-3">
          <div className="max-w-[100rem] mx-auto flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-3">
              <span className="text-lg">&#128260;</span>
              <span className="font-medium">New data available! Click refresh to see the latest intelligence.</span>
            </div>
            <div className="flex items-center gap-2">
              <button
                onClick={refreshData}
                className="bg-white text-blue-600 px-4 py-1.5 rounded-lg font-semibold hover:bg-blue-50 transition-colors"
              >
                Refresh Now
              </button>
              <button
                onClick={dismissNewVersion}
                className="text-blue-200 hover:text-white px-2 py-1"
                aria-label="Dismiss"
              >
                &#10005;
              </button>
            </div>
          </div>
        </div>
      )}

      <div className="max-w-[100rem] mx-auto px-6 py-8">
        <ChangeFeed entries={typedIntel?.change_log ?? []} />

        {/* Overall Status Rollup — single "should I worry today" answer,
            derived from the worst of the three pillar RAG scores below */}
        {typedIntel?.overall_rag && (
          <div className={`mb-8 rounded-xl border-2 p-6 flex flex-wrap items-center justify-between gap-4 ${
            typedIntel.overall_rag.score === 'RED' ? 'bg-red-50 border-red-300' :
            typedIntel.overall_rag.score === 'AMBER' ? 'bg-amber-50 border-amber-300' :
            'bg-green-50 border-green-300'
          }`}>
            <div className="flex items-center gap-4">
              <span className="text-4xl" aria-hidden="true">
                {typedIntel.overall_rag.score === 'RED' ? '🔴' : typedIntel.overall_rag.score === 'AMBER' ? '🟡' : '🟢'}
              </span>
              <div>
                <div className="text-xs font-semibold uppercase tracking-wider text-gray-500">Overall Status</div>
                <div className={`text-2xl font-bold ${
                  typedIntel.overall_rag.score === 'RED' ? 'text-red-800' :
                  typedIntel.overall_rag.score === 'AMBER' ? 'text-amber-800' :
                  'text-green-800'
                }`}>
                  {typedIntel.overall_rag.score === 'RED'
                    ? (topActionItems.length > 0
                        ? `${topActionItems.length} item${topActionItems.length > 1 ? 's' : ''} need review`
                        : 'Action needed today')
                    : typedIntel.overall_rag.score === 'AMBER' ? 'Monitor closely' :
                   'All clear'}
                </div>
              </div>
            </div>
            {typedIntel.overall_rag.score !== 'GREEN' && topActionItems.length > 0 && (
              <div className="text-sm text-gray-700 w-full sm:w-auto">
                <ul className="space-y-1">
                  {topActionItems.map((item, idx) => (
                    <li key={idx}>
                      <Link href={item.href} className="hover:underline">
                        <span className="text-gray-500">→</span> {item.label}
                      </Link>
                    </li>
                  ))}
                </ul>
              </div>
            )}
            {typedIntel.overall_rag.score !== 'GREEN' && topActionItems.length === 0 && typedIntel.overall_rag.driven_by?.length > 0 && (
              <div className="text-sm text-gray-700">
                Driven by:{' '}
                <span className="font-semibold capitalize">
                  {typedIntel.overall_rag.driven_by.join(', ')}
                </span>
                <span className="text-gray-500"> — see below for detail</span>
              </div>
            )}
            {typedIntel.executive_summary && (
              <div className="w-full pt-4 mt-1 border-t border-black/10 space-y-3">
                <p className="text-base font-semibold text-gray-900 leading-snug max-w-3xl">
                  {typedIntel.executive_summary.headline}
                </p>
                {typedIntel.executive_summary.context && (
                  <p className="text-sm text-gray-600 leading-relaxed max-w-3xl">
                    {typedIntel.executive_summary.context}
                  </p>
                )}
                {typedIntel.executive_summary.next_step && (
                  <div className="flex items-start gap-2.5 max-w-3xl rounded-lg border border-black/5 bg-white/60 px-3.5 py-2.5">
                    <span className="shrink-0 pt-0.5 text-xs font-semibold uppercase tracking-wider text-gray-500">
                      Next step
                    </span>
                    <span className="text-sm font-medium text-gray-800">
                      {typedIntel.executive_summary.next_step}
                    </span>
                  </div>
                )}
              </div>
            )}
            {typedIntel.rag_history && typedIntel.rag_history.length > 1 && currentStreakDuration(typedIntel.rag_history) && (
              <div className="w-full text-xs text-gray-500 pt-3 mt-1 border-t border-black/10">
                {typedIntel.overall_rag.score === 'GREEN' ? 'Stable' : `Status unchanged`} for {currentStreakDuration(typedIntel.rag_history)}
              </div>
            )}
          </div>
        )}

        {/* Three Core Pillars Overview */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-6 mb-8">
          {/* PILLAR 1: MACRO OVERVIEW */}
          <div className={`bg-white p-6 rounded-xl shadow-sm border-t-4 ${getRAGColor(macro?.rag_score)}`}>
            <div className="flex justify-between items-start mb-4">
              <h2 className="text-gray-500 font-semibold uppercase text-xs tracking-wider">Macro Overview</h2>
              <span className={`px-2 py-1 rounded text-xs font-bold ${
                macro?.rag_score === 'RED' ? 'bg-red-100 text-red-800' :
                macro?.rag_score === 'AMBER' ? 'bg-amber-100 text-amber-800' :
                macro?.rag_score === 'GREEN' ? 'bg-green-100 text-green-800' :
                'bg-gray-100 text-gray-800'
              }`}>
                {getRAGLabel(macro?.rag_score)}
              </span>
            </div>
            {macro?.status === 'success' && (
              <div className="space-y-2 text-sm">
                <div className="flex justify-between">
                  <span className="text-gray-600">US:</span>
                  <span className="font-semibold">{macro?.regions?.us?.status === 'success' ? '✓' : '—'}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-gray-600">EU:</span>
                  <span className="font-semibold">
                    {typeof macro?.regions?.eu?.indicators?.fx_rate === 'number' 
                      ? `€1 = $${macro.regions.eu.indicators.fx_rate.toFixed(4)}` 
                      : '—'}
                  </span>
                </div>
                <div className="flex justify-between">
                  <span className="text-gray-600">China:</span>
                  <span className="font-semibold">{macro?.regions?.china?.status === 'success' ? '✓' : '—'}</span>
                </div>
              </div>
            )}
          </div>

          {/* PILLAR 2: PEERS & COMPETITORS */}
          <div className={`bg-white p-6 rounded-xl shadow-sm border-t-4 ${getRAGColor(peers?.rag_score)}`}>
            <div className="flex justify-between items-start mb-4">
              <h2 className="text-gray-500 font-semibold uppercase text-xs tracking-wider">Peers & Competitors</h2>
              <span className={`px-2 py-1 rounded text-xs font-bold ${
                peers?.rag_score === 'RED' ? 'bg-red-100 text-red-800' :
                peers?.rag_score === 'AMBER' ? 'bg-amber-100 text-amber-800' :
                peers?.rag_score === 'GREEN' ? 'bg-green-100 text-green-800' :
                'bg-gray-100 text-gray-800'
              }`}>
                {getRAGLabel(peers?.rag_score)}
              </span>
            </div>
            {peers?.status === 'success' && (
              <div className="space-y-2 text-sm">
                <div className="text-2xl font-bold text-gray-900">{peers?.total_peers || 0}</div>
                <div className="text-gray-600">Companies tracked</div>
                {peers?.total_red_signals > 0 && (
                  <div className="text-red-600 font-semibold text-xs">🔴 {peers.total_red_signals} Distress</div>
                )}
                {peers?.total_amber_signals > 0 && (
                  <div className="text-amber-600 font-semibold text-xs">⚠️ {peers.total_amber_signals} Warning</div>
                )}
              </div>
            )}
          </div>

          {/* PILLAR 3: SUPPLIER WATCHLIST */}
          <div className={`bg-white p-6 rounded-xl shadow-sm border-t-4 ${getRAGColor(suppliers?.rag_score)}`}>
            <div className="flex justify-between items-start mb-4">
              <h2 className="text-gray-500 font-semibold uppercase text-xs tracking-wider">Supplier Watchlist</h2>
              <span className={`px-2 py-1 rounded text-xs font-bold ${
                suppliers?.rag_score === 'RED' ? 'bg-red-100 text-red-800' :
                suppliers?.rag_score === 'AMBER' ? 'bg-amber-100 text-amber-800' :
                suppliers?.rag_score === 'GREEN' ? 'bg-green-100 text-green-800' :
                'bg-gray-100 text-gray-800'
              }`}>
                {getRAGLabel(suppliers?.rag_score)}
              </span>
            </div>
            {suppliers?.status === 'success' && (
              <div className="space-y-2 text-sm">
                <div className="text-2xl font-bold text-gray-900">{suppliers?.total_suppliers || 0}</div>
                <div className="text-gray-600">Suppliers monitored</div>
                {/* Sanctions match is the single most severe signal — shown first */}
                {(suppliers as any)?.suppliers_at_sanctions_risk > 0 && (
                  <button
                    onClick={() => setRiskFilter(riskFilter === 'sanctions' ? 'all' : 'sanctions')}
                    className={`block text-white bg-red-700 font-bold text-xs px-2 py-1 rounded hover:bg-red-800 cursor-pointer ${riskFilter === 'sanctions' ? 'ring-2 ring-red-900' : ''}`}
                  >
                    🚫 {(suppliers as any).suppliers_at_sanctions_risk} Sanctions Match — verify now
                  </button>
                )}
                {/* Severity counts use the actionable_* figures — the same
                    ones the pillar RAG score is computed from. total_* counts
                    every supplier in the bucket including those sitting there
                    purely on a standing geographic floor, which put "9 Medium
                    Risk" next to an all-clear GREEN badge and made the reader
                    choose which of the two to believe. The unchanged
                    structural exposure is still shown, below and separately. */}
                {actionableCritical > 0 && (
                  <button
                    onClick={() => setRiskFilter(riskFilter === 'critical' ? 'all' : 'critical')}
                    className={`block text-red-700 font-semibold text-xs hover:underline cursor-pointer ${riskFilter === 'critical' ? 'bg-red-100 px-2 py-0.5 rounded' : ''}`}
                  >
                    🚨 {actionableCritical} Critical
                  </button>
                )}
                {actionableHigh > 0 && (
                  <button
                    onClick={() => setRiskFilter(riskFilter === 'high' ? 'all' : 'high')}
                    className={`block text-red-600 font-semibold text-xs hover:underline cursor-pointer ${riskFilter === 'high' ? 'bg-red-100 px-2 py-0.5 rounded' : ''}`}
                  >
                    ⚠️ {actionableHigh} High Risk
                  </button>
                )}
                {actionableMedium > 0 && (
                  <button
                    onClick={() => setRiskFilter(riskFilter === 'medium' ? 'all' : 'medium')}
                    className={`block text-amber-600 font-semibold text-xs hover:underline cursor-pointer ${riskFilter === 'medium' ? 'bg-amber-100 px-2 py-0.5 rounded' : ''}`}
                  >
                    📋 {actionableMedium} Medium Risk
                  </button>
                )}
                {actionableCritical + actionableHigh + actionableMedium === 0 &&
                  !(suppliers as any)?.suppliers_at_sanctions_risk && (
                    <div className="text-xs text-green-700 font-semibold">
                      ✓ No new signals this cycle
                    </div>
                  )}
                {/* Show risk type counts */}
                {suppliers?.suppliers_at_cyber_risk > 0 && (
                  <button
                    onClick={() => setRiskFilter(riskFilter === 'cyber' ? 'all' : 'cyber')}
                    className={`block text-gray-600 font-semibold text-xs hover:underline cursor-pointer ${riskFilter === 'cyber' ? 'bg-gray-100 px-2 py-0.5 rounded' : ''}`}
                  >
                    🔒 {suppliers.suppliers_at_cyber_risk} Cyber
                  </button>
                )}
                {(suppliers as any)?.suppliers_at_recall_risk > 0 && (
                  <button
                    onClick={() => setRiskFilter(riskFilter === 'recall' ? 'all' : 'recall')}
                    className={`block text-amber-700 font-semibold text-xs hover:underline cursor-pointer ${riskFilter === 'recall' ? 'bg-amber-100 px-2 py-0.5 rounded' : ''}`}
                  >
                    ⚠️ {(suppliers as any).suppliers_at_recall_risk} CPSC Recall
                  </button>
                )}
                {/* Standing country exposure, phrased so it does not read as
                    something that happened today. These are the same
                    suppliers the severity counts above used to double-count,
                    and the set barely moves from one cycle to the next. */}
                {(suppliers as any)?.suppliers_at_geopolitical_risk > 0 && (
                  <button
                    onClick={() => setRiskFilter(riskFilter === 'geopolitical' ? 'all' : 'geopolitical')}
                    className={`block text-left text-gray-500 text-xs hover:underline cursor-pointer pt-1 ${riskFilter === 'geopolitical' ? 'bg-orange-50 px-2 py-0.5 rounded' : ''}`}
                    title="Standing exposure from the country a supplier operates in — not a signal that something changed"
                  >
                    🌍 {(suppliers as any).suppliers_at_geopolitical_risk} in flagged regions
                    <span className="block text-gray-400">standing exposure, unchanged</span>
                  </button>
                )}
              </div>
            )}
          </div>
        </div>

        {/* Global Macro Context */}
        <div className="mb-8">
          <h2 className="text-xl font-bold text-gray-900 mb-4">Global Macro Context</h2>
          <p className="text-sm text-gray-600 mb-4">Click any region for detailed economic intelligence</p>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            {/* US Card */}
            <Link
              href="/macro/us"
              className="bg-white rounded-lg shadow-sm border border-gray-200 p-5 block hover:scale-[1.01] transition-transform cursor-pointer"
            >
              <div className="flex items-center justify-between mb-3">
                <div className="flex items-center gap-2">
                  <span className="text-2xl">🇺🇸</span>
                  <span className="font-bold text-gray-900">US</span>
                </div>
                {macroEconomy?.us?.trend === 'Stable' && <span className="text-gray-500">→</span>}
                {macroEconomy?.us?.trend === 'Improving' && <span className="text-green-600">↗</span>}
                {macroEconomy?.us?.trend === 'Volatile' && <span className="text-red-600">↕</span>}
                {macroEconomy?.us?.trend === 'Declining' && <span className="text-red-600">↕</span>}
              </div>
              <div className="space-y-2 text-sm">
                <div className="flex justify-between items-center">
                  <span className="text-gray-600">CPI:</span>
                  <span className="font-mono font-semibold text-gray-900">{macroEconomy?.us?.cpi || 'N/A'}</span>
                </div>
                <div className="flex justify-between items-center">
                  <span className="text-gray-600">Rate:</span>
                  <span className="font-mono font-semibold text-gray-900">{macroEconomy?.us?.rate || 'N/A'}</span>
                </div>
                <div className="pt-2 border-t border-gray-200">
                  <p className="text-xs text-gray-700 leading-relaxed">{macroEconomy?.us?.summary || 'No data'}</p>
                </div>
              </div>
            </Link>

            {/* EU Card */}
            <Link
              href="/macro/eu"
              className="bg-white rounded-lg shadow-sm border border-gray-200 p-5 block hover:scale-[1.01] transition-transform cursor-pointer"
            >
              <div className="flex items-center justify-between mb-3">
                <div className="flex items-center gap-2">
                  <span className="text-2xl">🇪🇺</span>
                  <span className="font-bold text-gray-900">EU</span>
                </div>
                {macroEconomy?.eu?.trend === 'Stable' && <span className="text-gray-500">→</span>}
                {macroEconomy?.eu?.trend === 'Improving' && <span className="text-green-600">↗</span>}
                {macroEconomy?.eu?.trend === 'Strengthening' && <span className="text-green-600">↗</span>}
                {macroEconomy?.eu?.trend === 'Volatile' && <span className="text-red-600">↕</span>}
                {macroEconomy?.eu?.trend === 'Weakening' && <span className="text-red-600">↕</span>}
              </div>
              <div className="space-y-2 text-sm">
                <div className="flex justify-between items-center">
                  <span className="text-gray-600">CPI:</span>
                  <span className="font-mono font-semibold text-gray-900">{macroEconomy?.eu?.cpi || 'N/A'}</span>
                </div>
                <div className="flex justify-between items-center">
                  <span className="text-gray-600">Rate:</span>
                  <span className="font-mono font-semibold text-gray-900">{macroEconomy?.eu?.rate || 'N/A'}</span>
                </div>
                <div className="pt-2 border-t border-gray-200">
                  <p className="text-xs text-gray-700 leading-relaxed">{macroEconomy?.eu?.summary || 'No data'}</p>
                </div>
              </div>
            </Link>

            {/* China Card */}
            <Link
              href="/macro/china"
              className="bg-white rounded-lg shadow-sm border border-gray-200 p-5 block hover:scale-[1.01] transition-transform cursor-pointer"
            >
              <div className="flex items-center justify-between mb-3">
                <div className="flex items-center gap-2">
                  <span className="text-2xl">🇨🇳</span>
                  <span className="font-bold text-gray-900">CN</span>
                </div>
                {macroEconomy?.china?.trend === 'Stable' && <span className="text-gray-500">→</span>}
                {macroEconomy?.china?.trend === 'Improving' && <span className="text-green-600">↗</span>}
                {macroEconomy?.china?.trend === 'Growing' && <span className="text-green-600">↗</span>}
                {macroEconomy?.china?.trend === 'Volatile' && <span className="text-red-600">↕</span>}
                {macroEconomy?.china?.trend === 'Declining' && <span className="text-red-600">↕</span>}
              </div>
              <div className="space-y-2 text-sm">
                <div className="flex justify-between items-center">
                  <span className="text-gray-600">CPI:</span>
                  <span className="font-mono font-semibold text-gray-900">{macroEconomy?.china?.cpi || 'N/A'}</span>
                </div>
                <div className="flex justify-between items-center">
                  <span className="text-gray-600">Rate:</span>
                  <span className="font-mono font-semibold text-gray-900">{macroEconomy?.china?.rate || 'N/A'}</span>
                </div>
                <div className="pt-2 border-t border-gray-200">
                  <p className="text-xs text-gray-700 leading-relaxed">{macroEconomy?.china?.summary || 'No data'}</p>
                </div>
              </div>
            </Link>
          </div>
        </div>

        {/* Peer Intelligence */}
        <div className="mb-8">
          <h2 className="text-xl font-bold text-gray-900 mb-4">Peer Intelligence</h2>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
            {peerGroup.map((peer: PeerGroupItem, idx: number) => {
              const isBAT = peer.name === 'British American Tobacco' || peer.name === 'BAT' || peer.ticker === 'BTI';
              const stockMovePositive = peer.stock_move?.startsWith('+');
              const stockMoveNegative = peer.stock_move?.startsWith('-');
              const hasSecSignal = (peer.sec_red_signals ?? 0) > 0 || (peer.sec_amber_signals ?? 0) > 0;

              return (
                <Link
                  key={idx}
                  href={`/details/${encodeURIComponent(peer.name)}`}
                  className={`bg-white rounded-lg shadow-sm border-2 p-5 block hover:bg-slate-50 cursor-pointer transition-colors ${
                    isBAT
                      ? 'border-blue-600 bg-blue-50'
                      : 'border-gray-200'
                  }`}
                >
                  <div className="flex items-center justify-between mb-3">
                    <div>
                      <div className="flex items-center gap-2">
                        <h3 className="font-bold text-gray-900">{peer.name}</h3>
                        {isBAT && (
                          <span className="px-2 py-0.5 bg-blue-600 text-white text-xs font-semibold rounded">
                            Our View
                          </span>
                        )}
                      </div>
                      <div className="text-xs text-gray-600 font-mono mt-1">{peer.ticker}</div>
                    </div>
                  </div>

                  <div className="mb-3">
                    <div className="flex items-center gap-2 mb-2 flex-wrap">
                      <span className={`px-2 py-1 rounded text-xs font-semibold ${
                        peer.sentiment === 'Positive' ? 'bg-green-100 text-green-800' :
                        peer.sentiment === 'Negative' ? 'bg-red-100 text-red-800' :
                        'bg-gray-100 text-gray-800'
                      }`}>
                        {peer.sentiment}
                      </span>
                      <span className={`font-mono font-semibold text-sm ${
                        stockMovePositive ? 'text-green-600' :
                        stockMoveNegative ? 'text-red-600' :
                        'text-gray-600'
                      }`}>
                        {peer.stock_move || 'N/A'}
                      </span>
                      {hasSecSignal && (
                        <span
                          className="px-1.5 py-0.5 bg-amber-700 text-white rounded text-xs"
                          title={`SEC 8-K filing signal: ${peer.sec_red_signals ?? 0} distress, ${peer.sec_amber_signals ?? 0} management-change`}
                        >
                          📄 SEC
                        </span>
                      )}
                    </div>
                  </div>

                  <div className="pt-3 border-t border-gray-200 space-y-1">
                    <p className="text-xs text-gray-700 leading-relaxed">{peer.latest_headline || 'No headline'}</p>
                    {hasSecSignal && peer.summary && (
                      <p className="text-xs text-amber-800 leading-relaxed font-medium">{peer.summary}</p>
                    )}
                  </div>
                </Link>
              );
            })}
          </div>
        </div>

        {/* Supplier Watchlist Table */}
        <div className="bg-white rounded-xl shadow-sm border border-gray-200 overflow-hidden">
          <div className="px-6 py-4 bg-gray-50 border-b border-gray-200">
            <div className="flex items-center justify-between">
              <div>
                <h2 className="text-xl font-bold text-gray-900">Supplier Watchlist</h2>
                <p className="text-sm text-gray-600 mt-1">Click any supplier for detailed intelligence</p>
              </div>
              {riskFilter !== 'all' && (
                <div className="flex items-center gap-2">
                  <span className={`px-3 py-1.5 rounded-full text-sm font-semibold ${
                    riskFilter === 'sanctions' ? 'bg-red-700 text-white' :
                    riskFilter === 'critical' ? 'bg-red-200 text-red-900' :
                    riskFilter === 'high' ? 'bg-red-100 text-red-800' :
                    riskFilter === 'medium' ? 'bg-amber-100 text-amber-800' :
                    riskFilter === 'cyber' ? 'bg-gray-100 text-gray-800' :
                    riskFilter === 'recall' ? 'bg-amber-100 text-amber-800' :
                    riskFilter === 'news' ? 'bg-amber-100 text-amber-800' :
                    riskFilter === 'geopolitical' ? 'bg-orange-100 text-orange-800' :
                    'bg-gray-100 text-gray-800'
                  }`}>
                    {riskFilter === 'sanctions' && '🚫 Sanctions Match'}
                    {riskFilter === 'critical' && '🚨 Critical Risk'}
                    {riskFilter === 'high' && '⚠️ High Risk'}
                    {riskFilter === 'medium' && '📋 Medium Risk'}
                    {riskFilter === 'cyber' && '🔒 Cyber Risk'}
                    {riskFilter === 'recall' && '⚠️ CPSC Recall'}
                    {riskFilter === 'news' && '📰 News Risk'}
                    {riskFilter === 'operational' && '⚠️ Operational Risk'}
                    {riskFilter === 'geopolitical' && '🌍 Geopolitical Risk'}
                  </span>
                  <button
                    onClick={() => setRiskFilter('all')}
                    className="text-gray-500 hover:text-gray-700 text-sm underline"
                  >
                    Show All
                  </button>
                </div>
              )}
            </div>
          </div>

          {(() => {
            const filteredSuppliers = suppliersList.filter((supplier: Supplier) => {
              if (riskFilter === 'all') return true;
              // Severity filters mirror the actionable counts on the card
              // above; the geopolitical filter below is what surfaces the
              // structural-floor suppliers those counts exclude.
              if (riskFilter === 'critical') return isActionable(supplier) && supplier.risk_level === 'CRITICAL';
              if (riskFilter === 'high') return isActionable(supplier) && supplier.risk_level === 'HIGH';
              if (riskFilter === 'medium') return isActionable(supplier) && supplier.risk_level === 'MEDIUM';
              if (riskFilter === 'sanctions') return (supplier as any).sanctions_hit;
              if (riskFilter === 'cyber') return supplier.cyber_risk;
              if (riskFilter === 'recall') return (supplier as any).recall_risk;
              if (riskFilter === 'news') return supplier.news_risk;
              if (riskFilter === 'operational') return (supplier as any).operational_risk;
              if (riskFilter === 'geopolitical') return supplier.geopolitical_risk != null;
              return true;
            });

            const riskBadges = (supplier: Supplier) => (
              <>
                {supplier.risk_level === 'CRITICAL' && (
                  <span className="px-2 py-1 bg-red-100 text-red-800 rounded text-xs font-semibold">Critical</span>
                )}
                {supplier.risk_level === 'HIGH' && (
                  <span className="px-2 py-1 bg-red-100 text-red-800 rounded text-xs font-semibold">High</span>
                )}
                {supplier.risk_level === 'MEDIUM' && (
                  <span className="px-2 py-1 bg-amber-100 text-amber-800 rounded text-xs font-semibold">Medium</span>
                )}
                {(supplier.risk_level === 'LOW' || !supplier.risk_level) && (
                  <span className="px-2 py-1 bg-green-100 text-green-800 rounded text-xs font-semibold">Low</span>
                )}
                {supplier.sanctions_hit && (
                  <span className="px-1.5 py-0.5 bg-red-800 text-white rounded text-xs" title={`OFAC SDN match — verify: ${supplier.sanctions_matches?.[0] || ''}`}>🚫</span>
                )}
                {supplier.cyber_risk && (
                  <span className="px-1.5 py-0.5 bg-red-600 text-white rounded text-xs" title="CISA cyber vulnerability">🔒</span>
                )}
                {supplier.recall_risk && (
                  <span className="px-1.5 py-0.5 bg-amber-700 text-white rounded text-xs" title={`CPSC recall: ${supplier.matching_recalls?.[0]?.product || ''}`}>⚠️</span>
                )}
                {supplier.news_risk && (
                  <span className="px-1.5 py-0.5 bg-amber-600 text-white rounded text-xs" title="News-based risk">📰</span>
                )}
                {supplier.geopolitical_risk && (
                  <span className="px-1.5 py-0.5 bg-orange-600 text-white rounded text-xs" title={supplier.geopolitical_risk?.reason || 'Geopolitical risk'}>🌍</span>
                )}
              </>
            );

            return (
              <>
                {/* Mobile: stacked cards — a wide table forces the Risk
                    Status column (the whole point of this list) off-screen
                    with no visible hint that there's more to scroll to. */}
                <div className="md:hidden divide-y divide-gray-200">
                  {filteredSuppliers.map((supplier: Supplier, idx: number) => (
                    <Link
                      key={idx}
                      href={`/details/${encodeURIComponent(supplier.name)}`}
                      className="block px-4 py-4 hover:bg-slate-50 active:bg-slate-100 transition-colors"
                    >
                      <div className="flex items-start justify-between gap-3">
                        <div>
                          <div className="text-sm font-semibold text-gray-900">{supplier.name}</div>
                          <div className="text-xs text-gray-500">
                            {supplier.category}
                            {supplier.stock_ticker && supplier.stock_ticker !== 'N/A' && (
                              <span className="font-mono"> • {supplier.stock_ticker}</span>
                            )}
                          </div>
                        </div>
                        <span className={`shrink-0 px-2 py-1 rounded-full text-xs font-bold border ${getExposureColor(supplier.bat_exposure || 'Medium')}`}>
                          {supplier.bat_exposure || 'Medium'}
                        </span>
                      </div>
                      <div className="flex items-center gap-2 flex-wrap mt-2">
                        {riskBadges(supplier)}
                      </div>
                      {supplier.risk_level && supplier.risk_level !== 'LOW' && supplier.last_signal && (
                        <div className="text-xs text-gray-600 mt-1.5">{supplier.last_signal}</div>
                      )}
                      {supplier.geopolitical_risk?.reason && !supplier.geopolitical_risk?.escalated && (
                        <div className="text-xs text-orange-700 mt-1">
                          🌍 Also in a flagged region: {supplier.geopolitical_risk.reason}
                        </div>
                      )}
                      <div className="text-xs text-gray-400 mt-1.5">{supplier.location || 'Unknown'}</div>
                    </Link>
                  ))}
                </div>

                {/* Desktop/tablet: full table */}
                <div className="hidden md:block overflow-x-auto">
                  <table className="w-full">
                    <thead className="bg-gray-50 border-b border-gray-200">
                      <tr>
                        <th className="px-6 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">Supplier</th>
                        <th className="px-6 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">Category</th>
                        <th className="px-6 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">BAT Exposure</th>
                        <th className="px-6 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">Segment</th>
                        <th className="px-6 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">Location</th>
                        <th className="px-6 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider min-w-[320px]">Risk Status</th>
                      </tr>
                    </thead>
                    <tbody className="bg-white divide-y divide-gray-200">
                      {filteredSuppliers.map((supplier: Supplier, idx: number) => (
                  <tr
                    key={idx}
                    className="hover:bg-slate-50 cursor-pointer transition-colors"
                  >
                    <td className="px-6 py-4 whitespace-nowrap">
                      <Link href={`/details/${encodeURIComponent(supplier.name)}`} className="block">
                        <div className="text-sm font-semibold text-gray-900 hover:text-blue-900">{supplier.name}</div>
                        {supplier.stock_ticker && supplier.stock_ticker !== 'N/A' && (
                          <div className="text-xs text-gray-500 font-mono">{supplier.stock_ticker}</div>
                        )}
                      </Link>
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap">
                      <Link href={`/details/${encodeURIComponent(supplier.name)}`} className="block">
                        <div className="text-sm text-gray-900">{supplier.category}</div>
                      </Link>
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap">
                      <Link href={`/details/${encodeURIComponent(supplier.name)}`} className="block">
                        <span className={`px-2 py-1 rounded-full text-xs font-bold border ${getExposureColor(supplier.bat_exposure || 'Medium')}`}>
                          {supplier.bat_exposure || 'Medium'}
                        </span>
                      </Link>
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap">
                      <Link href={`/details/${encodeURIComponent(supplier.name)}`} className="block">
                        <div className="text-sm text-gray-900">{supplier.segment || 'N/A'}</div>
                      </Link>
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap">
                      <Link href={`/details/${encodeURIComponent(supplier.name)}`} className="block">
                        <div className="text-sm text-gray-900">{supplier.location || 'Unknown'}</div>
                      </Link>
                    </td>
                    <td className="px-6 py-4">
                      <Link href={`/details/${encodeURIComponent(supplier.name)}`} className="block">
                        <div className="flex flex-col gap-1">
                          <div className="flex items-center gap-2">
                            {/* Use risk_level as primary indicator */}
                            {supplier.risk_level === 'CRITICAL' && (
                              <span className="px-2 py-1 bg-red-100 text-red-800 rounded text-xs font-semibold">
                                Critical
                              </span>
                            )}
                            {supplier.risk_level === 'HIGH' && (
                              <span className="px-2 py-1 bg-red-100 text-red-800 rounded text-xs font-semibold">
                                High
                              </span>
                            )}
                            {supplier.risk_level === 'MEDIUM' && (
                              <span className="px-2 py-1 bg-amber-100 text-amber-800 rounded text-xs font-semibold">
                                Medium
                              </span>
                            )}
                            {(supplier.risk_level === 'LOW' || !supplier.risk_level) && (
                              <span className="px-2 py-1 bg-green-100 text-green-800 rounded text-xs font-semibold">
                                Low
                              </span>
                            )}
                            {/* Show risk type badges */}
                            {supplier.sanctions_hit && (
                              <span className="px-1.5 py-0.5 bg-red-800 text-white rounded text-xs" title={`OFAC SDN match — verify: ${supplier.sanctions_matches?.[0] || ''}`}>
                                🚫
                              </span>
                            )}
                            {supplier.cyber_risk && (
                              <span className="px-1.5 py-0.5 bg-red-600 text-white rounded text-xs" title="CISA cyber vulnerability">
                                🔒
                              </span>
                            )}
                            {supplier.recall_risk && (
                              <span className="px-1.5 py-0.5 bg-amber-700 text-white rounded text-xs" title={`CPSC recall: ${supplier.matching_recalls?.[0]?.product || ''}`}>
                                ⚠️
                              </span>
                            )}
                            {supplier.news_risk && (
                              <span className="px-1.5 py-0.5 bg-amber-600 text-white rounded text-xs" title="News-based risk">
                                📰
                              </span>
                            )}
                            {supplier.geopolitical_risk && (
                              <span className="px-1.5 py-0.5 bg-orange-600 text-white rounded text-xs" title={supplier.geopolitical_risk?.reason || 'Geopolitical risk'}>
                                🌍
                              </span>
                            )}
                          </div>
                          {/* Show risk reason for non-LOW risks */}
                          {supplier.risk_level && supplier.risk_level !== 'LOW' && supplier.last_signal && (
                            <div className="text-xs text-gray-600 max-w-md" title={supplier.last_signal}>
                              {supplier.last_signal}
                            </div>
                          )}
                          {/* Show geopolitical context as extra info only when it's NOT
                              already the reason shown above — when escalated=true, the
                              line above already says the same thing ("🌍 Geopolitical: X"),
                              so repeating it here was pure duplication. */}
                          {supplier.geopolitical_risk?.reason && !supplier.geopolitical_risk?.escalated && (
                            <div className="text-xs text-orange-700 max-w-md" title={supplier.geopolitical_risk.reason}>
                              🌍 Also in a flagged region: {supplier.geopolitical_risk.reason}
                            </div>
                          )}
                        </div>
                      </Link>
                    </td>
                  </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </>
            );
          })()}
        </div>

      </div>

      <footer className="mt-12 bg-gray-900 text-gray-300 py-6">
        <div className="max-w-[100rem] mx-auto px-6 text-center text-sm">
          <p>Global Supply Chain Watchtower • Built with the "Flat Data" pattern</p>
          <p className="mt-2">Zero infrastructure cost • Unbreakable stability • Official data sources only</p>
        </div>
      </footer>

      {/* About Modal */}
      {isModalOpen && (
        <div 
          className="fixed inset-0 z-50 flex items-center justify-center p-4"
          onClick={() => setIsModalOpen(false)}
        >
          {/* Backdrop */}
          <div className="absolute inset-0 bg-black/50 backdrop-blur-sm" />
          
          {/* Modal Content */}
          <div 
            className="relative bg-white rounded-xl shadow-2xl max-w-2xl w-full max-h-[90vh] overflow-y-auto z-10"
            onClick={(e) => e.stopPropagation()}
          >
            {/* Header */}
            <div className="sticky top-0 bg-gradient-to-r from-blue-900 to-blue-800 text-white px-6 py-4 rounded-t-xl flex justify-between items-center">
              <h2 className="text-xl font-bold">System Status & Methodology</h2>
              <button
                onClick={() => setIsModalOpen(false)}
                className="p-1 rounded-full hover:bg-blue-700 transition-colors"
                aria-label="Close"
              >
                <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>

            {/* Body */}
            <div className="px-6 py-6 prose prose-sm max-w-none">
              <p className="text-gray-700 leading-relaxed mb-6">
                This Intelligence Deck aggregates real-time supply chain signals for British American Tobacco leadership.
                Risk assessment is based on <strong>threats to supply continuity</strong>, not stock price movements.
              </p>

              {/* What Changed — the feed is the first thing on the page now */}
              <div className="mb-6">
                <h3 className="text-base font-bold text-gray-900 mb-3">What Changed Since You Last Looked</h3>
                <p className="text-gray-700 leading-relaxed">
                  The list at the top of the page is everything that moved since your last visit —
                  a supplier&apos;s risk level going up or down, a new signal appearing or clearing,
                  a competitor&apos;s status shifting, an economic outlook turning. Your browser
                  remembers when you were last here, so the list is yours: come back after two days
                  and you see two days&apos; worth.
                </p>
                <p className="text-gray-700 leading-relaxed mt-2">
                  This exists because a board that says &quot;all clear&quot; looks identical every
                  morning, and identical is easy to stop reading. Even on a calm day something has
                  usually moved &mdash; and a large, unexplained share-price move on a supplier that
                  matters shows up here even when nothing has turned the board amber or red.
                </p>
                <p className="text-gray-700 leading-relaxed mt-2">
                  One thing it deliberately leaves out: the standing exposure that comes simply from
                  where a supplier operates. That is real, but it is the same every day, and
                  repeating it would push the things that actually changed off the screen.
                </p>
              </div>

              {/* Overall Status & Trend */}
              <div className="mb-6">
                <h3 className="text-base font-bold text-gray-900 mb-3">The Traffic Light</h3>
                <p className="text-gray-700 leading-relaxed">
                  The banner below that list gives you one answer to &quot;do I need to worry today?&quot;
                  🟢 <strong>green</strong> means everything looks normal, 🟡 <strong>amber</strong> means something
                  is worth keeping an eye on, and 🔴 <strong>red</strong> means something needs attention now.
                  It automatically takes the worst of the three sections below it (Global Economy, Peers &amp;
                  Competitors, Suppliers) — you don&apos;t need to check all three yourself. When it&apos;s
                  amber or red, the banner also lists the specific company (or companies) causing it and why —
                  click any of them to jump straight to the details.
                </p>
                <p className="text-gray-700 leading-relaxed mt-2">
                  Underneath, a line tells you how long the current colour has been in effect, so you
                  can tell at a glance whether today&apos;s status is brand new or has been sitting
                  there for days.
                </p>
                <p className="text-gray-700 leading-relaxed mt-2">
                  One thing worth knowing: some suppliers sit in countries with long-standing, ongoing
                  tension (for example, general trade friction between the US and China) — that&apos;s shown
                  on the supplier&apos;s own card so you have the context, but it no longer by itself turns the
                  whole board red. Only a real, fresh development does that. This keeps the red light meaningful
                  instead of being on all the time for reasons that never change.
                </p>
              </div>

              {/* Risk Level Legend */}
              <div className="mb-6">
                <h3 className="text-base font-bold text-gray-900 mb-3">Risk Level Legend</h3>
                <div className="space-y-3">
                  {/* CRITICAL */}
                  <div className="flex items-start gap-3 p-3 bg-red-50 border border-red-200 rounded-lg">
                    <span className="px-2 py-1 bg-red-100 text-red-800 rounded text-xs font-bold whitespace-nowrap">🚨 Critical</span>
                    <div className="text-sm">
                      <p className="font-semibold text-red-800">Immediate threat to supply</p>
                      <p className="text-red-700 mt-1">
                        <strong>Triggers:</strong> name match on the US sanctions watchlist, 2+ product safety recalls, bankruptcy, factory fire/closure, ransomware attack, labor strike, active war zone
                      </p>
                      <p className="text-red-600 mt-1 text-xs italic">
                        Example: &quot;Supplier X files for Chapter 11 bankruptcy&quot;
                      </p>
                    </div>
                  </div>

                  {/* HIGH */}
                  <div className="flex items-start gap-3 p-3 bg-orange-50 border border-orange-200 rounded-lg">
                    <span className="px-2 py-1 bg-orange-100 text-orange-800 rounded text-xs font-bold whitespace-nowrap">⚠️ High</span>
                    <div className="text-sm">
                      <p className="font-semibold text-orange-800">Serious concern requiring monitoring</p>
                      <p className="text-orange-700 mt-1">
                        <strong>Triggers:</strong> Fraud/SEC investigation, major product recall, stock crash &gt;15%, executive exodus, severe regional tensions/sanctions
                      </p>
                      <p className="text-orange-600 mt-1 text-xs italic">
                        Example: &quot;SEC opens investigation into Supplier Y accounting practices&quot;
                      </p>
                    </div>
                  </div>

                  {/* MEDIUM */}
                  <div className="flex items-start gap-3 p-3 bg-amber-50 border border-amber-200 rounded-lg">
                    <span className="px-2 py-1 bg-amber-100 text-amber-800 rounded text-xs font-bold whitespace-nowrap">📋 Medium</span>
                    <div className="text-sm">
                      <p className="font-semibold text-amber-800">Potential concern, watch closely</p>
                      <p className="text-amber-700 mt-1">
                        <strong>Triggers:</strong> Mass layoffs, supply disruption news, stock &gt;10% drop (Critical/High exposure suppliers), credit downgrade, trade war/instability
                      </p>
                      <p className="text-amber-600 mt-1 text-xs italic">
                        Example: &quot;Supplier Z announces 20% workforce reduction&quot;
                      </p>
                    </div>
                  </div>

                  {/* LOW */}
                  <div className="flex items-start gap-3 p-3 bg-green-50 border border-green-200 rounded-lg">
                    <span className="px-2 py-1 bg-green-100 text-green-800 rounded text-xs font-bold whitespace-nowrap">✓ Low</span>
                    <div className="text-sm">
                      <p className="font-semibold text-green-800">Normal operations</p>
                      <p className="text-green-700 mt-1">
                        <strong>Status:</strong> No negative operational news. Stock fluctuations &lt;10% are considered normal market volatility.
                      </p>
                      <p className="text-green-600 mt-1 text-xs italic">
                        Note: A 2-5% stock drop alone is NOT a supply risk
                      </p>
                    </div>
                  </div>
                </div>
              </div>

              <div className="mb-6">
                <h3 className="text-base font-bold text-gray-900 mb-3">Where the Information Comes From</h3>
                <ul className="list-disc list-inside space-y-2 text-gray-700">
                  <li><strong>Global Economy:</strong> official European Central Bank exchange rates and market data, for the US, EU, and China</li>
                  <li><strong>Competitors:</strong> live stock prices, news headlines, and official regulatory filings for PMI, Imperial, and Japan Tobacco</li>
                  <li><strong>Cyber Security:</strong> the US government&apos;s public list of security flaws currently being exploited by attackers</li>
                  <li><strong>Sanctions:</strong> the US Treasury&apos;s official watchlist of people and companies barred from doing business — a match here is flagged for a compliance team to double-check by hand, since name-matching software can occasionally get it wrong</li>
                  <li><strong>Product Safety Recalls:</strong> the US Consumer Product Safety Commission&apos;s public recall database, checked for the last 90 days</li>
                  <li><strong>News:</strong> financial news headlines plus a broader news search for wider coverage</li>
                  <li><strong>Geopolitical Risk:</strong> a curated list of conflict zones and sanctioned regions, cross-checked against live news so a country isn&apos;t flagged just because it&apos;s mentioned near an unrelated headline</li>
                  <li><strong>Suppliers:</strong> all 24 strategic partners are checked against every category above — stock movement, news, cyber, sanctions, recalls, and geopolitical risk</li>
                </ul>
              </div>

              <div className="mb-6">
                <h3 className="text-base font-bold text-gray-900 mb-3">Update Frequency</h3>
                <p className="text-gray-700">
                  Data refreshes <strong>every 6 hours</strong> via automated pipeline.
                </p>
              </div>

              <div className="mb-6">
                <h3 className="text-base font-bold text-gray-900 mb-3">How to Use</h3>
                <ul className="list-disc list-inside space-y-2 text-gray-700">
                  <li><strong>Start with the change list</strong> at the top — it is the part that is different from yesterday</li>
                  <li><strong>Click risk counts</strong> in the Supplier Watchlist card to filter by severity</li>
                  <li><strong>Click any supplier</strong> row to view detailed intelligence dossier</li>
                  <li><strong>Hover over risk badges</strong> to see the specific trigger reason</li>
                </ul>
              </div>
            </div>

            {/* Footer */}
            <div className="sticky bottom-0 bg-gray-50 border-t border-gray-200 px-6 py-4 rounded-b-xl">
              <p className="text-sm text-gray-600 text-center">
                Version 1.0 | Sovereign Intelligence Architecture
              </p>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
