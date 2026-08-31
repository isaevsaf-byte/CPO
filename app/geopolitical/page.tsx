'use client';

import { useState } from 'react';
import Link from 'next/link';
import intel from '../../data/intel_snapshot.json';
import type { IntelSnapshot, Supplier } from '../../types/intel';

const typedIntel = intel as unknown as IntelSnapshot;

// Country -> broad region, for grouping the cards below. Only needs to
// cover countries that actually show up as a supplier location — see
// suppliersByCountry — so this list can stay short and hand-maintained.
const REGION_MAP: { [country: string]: string } = {
  USA: 'Americas',
  China: 'Asia-Pacific',
  Japan: 'Asia-Pacific',
  India: 'Asia-Pacific',
  'South Korea': 'Asia-Pacific',
  Germany: 'Europe',
  Austria: 'Europe',
  Finland: 'Europe',
  Netherlands: 'Europe',
  Sweden: 'Europe',
  Switzerland: 'Europe',
  'South Africa': 'Africa',
};
const REGION_ORDER = ['Americas', 'Europe', 'Asia-Pacific', 'Africa', 'Other'];

function toneColor(tone: number | null): string {
  if (tone === null) return 'bg-gray-100 text-gray-700 border-gray-300';
  if (tone < -5) return 'bg-red-100 text-red-800 border-red-300';
  if (tone < 0) return 'bg-amber-100 text-amber-800 border-amber-300';
  return 'bg-green-100 text-green-800 border-green-300';
}

// Countries refresh opportunistically (GDELT rate-limits hard enough that
// only some succeed per harvest), so a card can be showing data from
// several cycles ago — say so plainly rather than implying it's live.
function relativeAttempt(at: string): string {
  const hours = (Date.now() - new Date(at).getTime()) / 3_600_000;
  if (hours < 1) return 'in the last hour';
  if (hours < 24) return `${Math.round(hours)}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}

function freshnessLabel(fetchedAt: string | undefined): string {
  if (!fetchedAt) return '';
  const hours = (Date.now() - new Date(fetchedAt).getTime()) / 3_600_000;
  if (hours < 1) return 'just now';
  if (hours < 24) return `${Math.round(hours)}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}

export default function GeopoliticalIntelPage() {
  // Light by default — matches the main dashboard. Toggle is local to
  // this page (not persisted), since the rest of the site has no dark
  // mode to stay in sync with.
  const [isDark, setIsDark] = useState(false);

  const geo = typedIntel.geopolitical_intel || {};
  const attempts = typedIntel.geopolitical_attempts || {};

  // Why a country has no reading, in the reader's terms. "Awaiting data" on
  // its own gave no way to tell a country that is queued from one that has
  // been failing for days, which is the difference between waiting and
  // needing to look at something.
  const noReadingReason = (country: string): string => {
    const state = attempts[country];
    if (!state?.last_attempt) {
      return 'Queued — GDELT is queried a few countries at a time each cycle, so coverage fills in over a day or so.';
    }
    const when = relativeAttempt(state.last_attempt);
    const streak = state.consecutive_failures ?? 0;
    const repeated = streak >= 3 ? ` Failing for ${streak} attempts running.` : '';
    switch (state.last_status) {
      case 'http_429':
        return `Rate-limited by GDELT ${when}.${repeated} It will be retried on a later cycle.`;
      case 'timeout':
      case 'unreachable':
        return `GDELT did not respond ${when}.${repeated} It will be retried on a later cycle.`;
      case 'empty':
        return `GDELT returned no coverage for this country ${when}.`;
      default:
        return `Last attempt ${when} did not return data.${repeated}`;
    }
  };
  const suppliersList: Supplier[] = typedIntel.suppliers?.suppliers || [];

  const suppliersByCountry: { [country: string]: Supplier[] } = {};
  suppliersList.forEach((s) => {
    if (!s.location) return;
    (suppliersByCountry[s.location] ||= []).push(s);
  });

  // Driven by where the suppliers actually are, not by which countries GDELT
  // happened to answer for. GDELT rate-limits hard enough that most countries
  // return nothing on any given harvest, and listing only the ones that
  // succeeded silently dropped the other nine — including the USA, the joint
  // largest supplier country on the watchlist, which reads as "not monitored"
  // rather than "no reading yet". Countries GDELT has never answered for now
  // appear with their suppliers and an explicit awaiting-data state.
  const countries = Array.from(
    new Set([...Object.keys(suppliersByCountry), ...Object.keys(geo)])
  )
    .map((country) => ({ country, data: geo[country] ?? null }))
    .sort((a, b) => {
      // Most negative first; anything without a reading sorts to the end
      // rather than sitting at 0.0 in the middle of the ranking.
      const toneOf = (d: typeof a.data) =>
        d && d.avg_tone !== null ? d.avg_tone : Number.POSITIVE_INFINITY;
      return toneOf(a.data) - toneOf(b.data);
    });

  const byRegion: { [region: string]: typeof countries } = {};
  countries.forEach((entry) => {
    const region = REGION_MAP[entry.country] || 'Other';
    (byRegion[region] ||= []).push(entry);
  });
  const regionsInOrder = REGION_ORDER.filter((r) => byRegion[r]?.length);

  return (
    <div className={isDark ? 'dark' : ''}>
      <div className="min-h-screen bg-white dark:bg-slate-950 text-gray-900 dark:text-slate-100">
        <div className="bg-white dark:bg-slate-900 border-b border-gray-200 dark:border-slate-800">
          <div className="max-w-7xl mx-auto px-6 py-4">
            <div className="flex items-start justify-between gap-4">
              <div className="flex items-center gap-4">
                <Link
                  href="/"
                  className="text-blue-900 dark:text-sky-400 hover:text-blue-700 dark:hover:text-sky-300 font-semibold"
                >
                  ← Back to Dashboard
                </Link>
                <div className="h-6 w-px bg-gray-300 dark:bg-slate-700" />
                <div>
                  <div className="flex items-center gap-3">
                    <span className="text-3xl">🌍</span>
                    <h1 className="text-2xl font-bold">Geopolitical Intelligence</h1>
                    <span className="px-2 py-0.5 rounded-full text-xs font-semibold bg-sky-50 dark:bg-sky-950 text-sky-700 dark:text-sky-300 border border-sky-200 dark:border-sky-800">
                      Experimental — GDELT
                    </span>
                  </div>
                  <p className="text-sm text-gray-600 dark:text-slate-400 mt-1 max-w-3xl">
                    Early-warning signal, independent of the main risk score: news tone across
                    65+ languages for every country where a watchlist supplier is based, updated
                    every 15 min. A sharp negative shift here can show up days before it reaches a
                    supplier&apos;s stock price or a named headline in the main dashboard.
                  </p>
                </div>
              </div>

              <button
                onClick={() => setIsDark((v) => !v)}
                className="shrink-0 p-2 rounded-full border border-gray-300 dark:border-slate-700 text-gray-600 dark:text-slate-300 hover:bg-gray-100 dark:hover:bg-slate-800 transition-colors"
                aria-label={isDark ? 'Switch to light mode' : 'Switch to dark mode'}
                title={isDark ? 'Switch to light mode' : 'Switch to dark mode'}
              >
                {isDark ? '☀️' : '🌙'}
              </button>
            </div>
          </div>
        </div>

        <div className="max-w-7xl mx-auto px-6 py-8 space-y-10">
          {/* Plain-language explainer — what this is and how to read it,
              for anyone landing here without the backstory. */}
          <div className="bg-gray-50 dark:bg-slate-900 border border-gray-200 dark:border-slate-800 rounded-xl p-5 grid grid-cols-1 sm:grid-cols-2 gap-6">
            <div>
              <h2 className="text-sm font-bold text-gray-900 dark:text-slate-200 mb-2">What is this?</h2>
              <p className="text-sm text-gray-600 dark:text-slate-400 leading-relaxed">
                A smoke detector for the countries your suppliers are based in. It reads global
                news coverage (thousands of outlets, 65+ languages) and scores how negative or
                positive the coverage of each country has been over the last 3 days. It doesn&apos;t
                know anything about supply chains — it just measures the mood of the news. A
                sudden dip is worth a look; it says nothing on its own about whether a specific
                supplier is actually affected. Headlines are listed only where the coverage
                actually touches your supply chain &mdash; a supplier by name, its industry, or
                events like export controls, port disruption or a walkout. Most countries most
                days will show a tone reading and no headlines, which is the honest answer.
              </p>
            </div>
            <div>
              <h2 className="text-sm font-bold text-gray-900 dark:text-slate-200 mb-2">How to read the numbers</h2>
              <ul className="text-sm text-gray-600 dark:text-slate-400 leading-relaxed space-y-1.5">
                <li className="flex items-center gap-2">
                  <span className="px-2 py-0.5 rounded-full text-xs font-bold border bg-green-100 text-green-800 border-green-300">0+</span>
                  Neutral to positive coverage — nothing to flag.
                </li>
                <li className="flex items-center gap-2">
                  <span className="px-2 py-0.5 rounded-full text-xs font-bold border bg-amber-100 text-amber-800 border-amber-300">-5..0</span>
                  Mildly negative — normal background noise for most countries.
                </li>
                <li className="flex items-center gap-2">
                  <span className="px-2 py-0.5 rounded-full text-xs font-bold border bg-red-100 text-red-800 border-red-300">&lt;-5</span>
                  Clearly negative — check whether any relevant headlines came with it.
                </li>
                <li className="pt-1 text-gray-500 dark:text-slate-500">
                  &quot;Articles&quot; is how much the world is talking about that country right
                  now — a busy news cycle, not a risk level by itself.
                </li>
              </ul>
            </div>
          </div>

          {countries.length === 0 ? (
            <div className="bg-gray-50 dark:bg-slate-900 border border-gray-200 dark:border-slate-800 rounded-xl p-8 text-center text-gray-500 dark:text-slate-400">
              No GDELT data in the latest snapshot yet — either the harvester hasn&apos;t run
              since this page was added, or GDELT was unreachable during the last run.
            </div>
          ) : (
            regionsInOrder.map((region) => (
              <div key={region}>
                <h2 className="text-xs font-semibold uppercase tracking-wider text-gray-500 dark:text-slate-500 mb-3">
                  {region}
                </h2>
                <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5">
                  {byRegion[region].map(({ country, data }) => {
                    const countrySuppliers = suppliersByCountry[country] || [];
                    return (
                      <div
                        key={country}
                        className="bg-white dark:bg-slate-900 border border-gray-200 dark:border-slate-800 shadow-sm dark:shadow-none rounded-xl p-5 flex flex-col gap-3"
                      >
                        <div className="flex items-start justify-between">
                          <h3 className="text-lg font-bold">{country}</h3>
                          <span
                            className={`px-2 py-0.5 rounded-full text-xs font-bold border ${
                              data ? toneColor(data.avg_tone) : toneColor(null)
                            }`}
                          >
                            {data && data.avg_tone !== null
                              ? `${data.avg_tone.toFixed(1)} tone`
                              : 'no reading'}
                          </span>
                        </div>

                        {countrySuppliers.length > 0 && (
                          <div className="flex flex-wrap gap-1.5">
                            {countrySuppliers.map((s) => (
                              <span
                                key={s.slug}
                                title={`${s.category} · ${s.bat_exposure} BAT exposure`}
                                className="px-2 py-0.5 rounded text-[11px] bg-gray-100 dark:bg-slate-800 text-gray-700 dark:text-slate-300 border border-gray-200 dark:border-slate-700"
                              >
                                {s.name}
                              </span>
                            ))}
                          </div>
                        )}

                        <div className="text-xs text-gray-500 dark:text-slate-400">
                          {data
                            ? `${data.article_count.toLocaleString()} articles · last ${
                                data.window === '1d' ? '24 hours' : '3 days'
                              }`
                            : 'Awaiting a GDELT reading'}
                          {data?.fetched_at && ` · updated ${freshnessLabel(data.fetched_at)}`}
                        </div>
                        {/* Two different measurements, so say which. Most
                            countries are read from coverage that names them;
                            the USA is read from coverage published there,
                            because a mention query that large never returns. */}
                        {data?.query_mode === 'domestic_press' && (
                          <div
                            className="text-[11px] text-gray-400 dark:text-slate-500 -mt-1"
                            title="GDELT cannot answer a mention-based query for a country this heavily covered, so this reads the tone of coverage published in the country instead."
                          >
                            tone of US-published coverage, not of coverage about the US
                          </div>
                        )}
                        {/* Headlines render only when the harvester confirmed
                            they are supply-chain relevant. Gated on an explicit
                            true so snapshots written before that check — whose
                            stored articles are the old unfiltered top-five —
                            fall through to the honest empty state rather than
                            putting a salmonella outbreak under Germany. */}
                        <div className="space-y-2 pt-2 border-t border-gray-200 dark:border-slate-800">
                          {data?.has_relevant === true && data.articles.length > 0 ? (
                            data.articles.map((a, idx) => (
                              <a
                                key={idx}
                                href={a.url}
                                target="_blank"
                                rel="noopener noreferrer"
                                className="block text-xs group"
                              >
                                <div className="flex items-start gap-2">
                                  <span
                                    className={`shrink-0 mt-0.5 px-1.5 py-0.5 rounded text-[10px] font-bold border ${toneColor(a.tone)}`}
                                  >
                                    {a.tone ?? '—'}
                                  </span>
                                  <span className="text-gray-700 dark:text-slate-300 group-hover:text-blue-700 dark:group-hover:text-sky-400 group-hover:underline leading-snug">
                                    {a.title}
                                  </span>
                                </div>
                              </a>
                            ))
                          ) : data ? (
                            <p className="text-xs text-gray-400 dark:text-slate-500">
                              No supply-chain-relevant coverage surfaced. The tone reading above
                              still stands; there is just nothing here worth reading.
                            </p>
                          ) : (
                            <p className="text-xs text-gray-400 dark:text-slate-500">
                              {noReadingReason(country)}
                            </p>
                          )}
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  );
}
