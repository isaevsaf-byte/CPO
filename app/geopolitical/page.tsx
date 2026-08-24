'use client';

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

export default function GeopoliticalIntelPage() {
  const geo = typedIntel.geopolitical_intel || {};
  const suppliersList: Supplier[] = typedIntel.suppliers?.suppliers || [];

  const suppliersByCountry: { [country: string]: Supplier[] } = {};
  suppliersList.forEach((s) => {
    if (!s.location) return;
    (suppliersByCountry[s.location] ||= []).push(s);
  });

  const countries = Object.entries(geo).sort(
    ([, a], [, b]) => (a.avg_tone ?? 0) - (b.avg_tone ?? 0)
  );

  const byRegion: { [region: string]: typeof countries } = {};
  countries.forEach((entry) => {
    const region = REGION_MAP[entry[0]] || 'Other';
    (byRegion[region] ||= []).push(entry);
  });
  const regionsInOrder = REGION_ORDER.filter((r) => byRegion[r]?.length);

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100">
      <div className="bg-slate-900 border-b border-slate-800">
        <div className="max-w-7xl mx-auto px-6 py-4">
          <div className="flex items-center gap-4">
            <Link href="/" className="text-sky-400 hover:text-sky-300 font-semibold">
              ← Back to Dashboard
            </Link>
            <div className="h-6 w-px bg-slate-700" />
            <div>
              <div className="flex items-center gap-3">
                <span className="text-3xl">🌍</span>
                <h1 className="text-2xl font-bold">Geopolitical Intelligence</h1>
                <span className="px-2 py-0.5 rounded-full text-xs font-semibold bg-sky-950 text-sky-300 border border-sky-800">
                  Experimental — GDELT
                </span>
              </div>
              <p className="text-sm text-slate-400 mt-1 max-w-3xl">
                Early-warning signal, independent of the main risk score: news tone across
                65+ languages for every country where a watchlist supplier is based, updated
                every 15 min. A sharp negative shift here can show up days before it reaches a
                supplier&apos;s stock price or a named headline in the main dashboard.
              </p>
            </div>
          </div>
        </div>
      </div>

      <div className="max-w-7xl mx-auto px-6 py-8 space-y-10">
        {countries.length === 0 ? (
          <div className="bg-slate-900 border border-slate-800 rounded-xl p-8 text-center text-slate-400">
            No GDELT data in the latest snapshot yet — either the harvester hasn&apos;t run
            since this page was added, or GDELT was unreachable during the last run.
          </div>
        ) : (
          regionsInOrder.map((region) => (
            <div key={region}>
              <h2 className="text-xs font-semibold uppercase tracking-wider text-slate-500 mb-3">
                {region}
              </h2>
              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5">
                {byRegion[region].map(([country, data]) => {
                  const countrySuppliers = suppliersByCountry[country] || [];
                  return (
                    <div
                      key={country}
                      className="bg-slate-900 border border-slate-800 rounded-xl p-5 flex flex-col gap-3"
                    >
                      <div className="flex items-start justify-between">
                        <h3 className="text-lg font-bold">{country}</h3>
                        <span
                          className={`px-2 py-0.5 rounded-full text-xs font-bold border ${toneColor(data.avg_tone)}`}
                        >
                          {data.avg_tone !== null ? data.avg_tone.toFixed(1) : 'N/A'} tone
                        </span>
                      </div>

                      {countrySuppliers.length > 0 && (
                        <div className="flex flex-wrap gap-1.5">
                          {countrySuppliers.map((s) => (
                            <span
                              key={s.slug}
                              title={`${s.category} · ${s.bat_exposure} BAT exposure`}
                              className="px-2 py-0.5 rounded text-[11px] bg-slate-800 text-slate-300 border border-slate-700"
                            >
                              {s.name}
                            </span>
                          ))}
                        </div>
                      )}

                      <div className="text-xs text-slate-400">
                        {data.article_count.toLocaleString()} articles · last 3 days
                      </div>
                      <div className="space-y-2 pt-2 border-t border-slate-800">
                        {data.articles.length === 0 ? (
                          <p className="text-xs text-slate-500">No article examples returned.</p>
                        ) : (
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
                                <span className="text-slate-300 group-hover:text-sky-400 group-hover:underline leading-snug">
                                  {a.title}
                                </span>
                              </div>
                            </a>
                          ))
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
  );
}
