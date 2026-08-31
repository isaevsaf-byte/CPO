'use client';

import { useParams } from 'next/navigation';
import Link from 'next/link';
import intelData from '../../../data/intel_snapshot.json';
import type { IntelSnapshot } from '../../../types/intel';

// The JSON import is typed structurally from whatever the checked-in snapshot
// happens to contain, so new harvester fields read as errors until a fresh
// snapshot lands. The declared type is the contract; the file is one sample.
const intel = intelData as unknown as IntelSnapshot;

function getTrendColor(trend: string): string {
  switch (trend?.toLowerCase()) {
    case 'stable':
    case 'improving':
    case 'strengthening':
    case 'growing':
      return 'bg-green-100 text-green-800 border-green-300';
    case 'volatile':
    case 'declining':
    case 'weakening':
      return 'bg-red-100 text-red-800 border-red-300';
    default:
      return 'bg-gray-100 text-gray-800 border-gray-300';
  }
}

function getTrendIcon(trend: string): string {
  switch (trend?.toLowerCase()) {
    case 'stable':
      return '→';
    case 'improving':
    case 'strengthening':
    case 'growing':
      return '↗';
    case 'volatile':
    case 'declining':
    case 'weakening':
      return '↕';
    default:
      return '—';
  }
}

export default function MacroDetailPage() {
  const params = useParams();
  const regionParam = (params?.region as string)?.toLowerCase();

  // Map region param to data key
  const regionMap: { [key: string]: string } = {
    'us': 'us',
    'eu': 'eu',
    'china': 'china',
    'cn': 'china'
  };

  const regionKey = regionMap[regionParam || ''] || regionParam;
  const macroEconomy = intel?.macro_economy || {};
  const regionData = macroEconomy[regionKey as keyof typeof macroEconomy];

  // The ECB reference rate, and only where one actually exists. The US and
  // China entries in macro.regions carry placeholder *strings*
  // ("Placeholder - USD/EUR"), which are truthy — so this used to resolve to a
  // string, fail the isNumber check downstream, and print the region's trend
  // word ("Stable") inside a card labelled "FX Rate".
  const macroRegions = intel?.macro?.regions || {};
  const rawFx = (macroRegions as Record<string, { indicators?: { fx_rate?: unknown } }>)[regionKey]
    ?.indicators?.fx_rate;
  const fxRate = typeof rawFx === 'number' ? rawFx : null;

  if (!regionData) {
    return (
      <div className="min-h-screen bg-slate-50 p-8">
        <div className="max-w-4xl mx-auto">
          <div className="bg-white rounded-lg shadow p-8 text-center">
            <h1 className="text-2xl font-bold text-gray-900 mb-4">Region Not Found</h1>
            <p className="text-gray-600 mb-6">The requested economic region could not be found.</p>
            <Link
              href="/"
              className="bg-blue-900 text-white px-6 py-2 rounded-lg hover:bg-blue-800 transition inline-block"
            >
              Back to Dashboard
            </Link>
          </div>
        </div>
      </div>
    );
  }

  // Region metadata
  const regionMetadata: { [key: string]: { name: string; flag: string; fullName: string } } = {
    us: { name: 'US', flag: '🇺🇸', fullName: 'United States' },
    eu: { name: 'EU', flag: '🇪🇺', fullName: 'European Union' },
    china: { name: 'China', flag: '🇨🇳', fullName: 'People\'s Republic of China' }
  };

  const metadata = regionMetadata[regionKey] || { name: regionKey, flag: '🌍', fullName: regionKey };

  // Same guard as the dashboard cards: a snapshot written before this release
  // has no market reading and carries the old hardcoded CPI/rate strings, which
  // must not be rendered as if they were this month's observation.
  const isCurrentShape = regionData.market_label != null;

  // External links based on region
  const externalLinks: { [key: string]: Array<{ label: string; icon: string; url: string }> } = {
    us: [
      { label: 'Federal Reserve', icon: '🏛️', url: 'https://www.federalreserve.gov' },
      { label: 'BLS Stats', icon: '📊', url: 'https://www.bls.gov' },
      { label: 'Google News: US Economy', icon: '🔎', url: 'https://www.google.com/search?q=US+economy+news&tbm=nws' }
    ],
    eu: [
      { label: 'ECB Policy', icon: '€', url: 'https://www.ecb.europa.eu' },
      { label: 'Eurostat', icon: '📉', url: 'https://ec.europa.eu/eurostat' },
      { label: 'Google News: Eurozone Economy', icon: '🔎', url: 'https://www.google.com/search?q=Eurozone+economy+news&tbm=nws' }
    ],
    china: [
      { label: 'PBoC', icon: '¥', url: 'https://www.pbc.gov.cn/en' },
      { label: 'NBS Data', icon: '📈', url: 'https://www.stats.gov.cn/english' },
      { label: 'Google News: China Economy', icon: '🔎', url: 'https://www.google.com/search?q=China+economy+news&tbm=nws' }
    ]
  };

  const links = externalLinks[regionKey] || [];


  return (
    <div className="min-h-screen bg-slate-50">
      {/* Header */}
      <div className="bg-white border-b border-gray-200">
        <div className="max-w-7xl mx-auto px-6 py-4">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-4">
              <Link
                href="/"
                className="text-blue-900 hover:text-blue-700 font-semibold"
              >
                ← Back to Dashboard
              </Link>
              <div className="h-6 w-px bg-gray-300"></div>
              <div>
                <div className="flex items-center gap-3">
                  <span className="text-4xl">{metadata.flag}</span>
                  <h1 className="text-3xl font-bold text-gray-900">{metadata.fullName}</h1>
                </div>
                <div className="flex items-center gap-3 mt-2">
                  {regionData.trend && regionData.trend !== 'N/A' && (
                    <span className={`px-3 py-1 rounded-full text-xs font-bold border ${getTrendColor(regionData.trend)}`}>
                      {getTrendIcon(regionData.trend)} {regionData.trend}
                    </span>
                  )}
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>

      <div className="max-w-7xl mx-auto px-6 py-8">
        {/* Key Indicators Grid */}
        <div className="mb-8">
          <h2 className="text-xl font-bold text-gray-900 mb-4">Key Economic Indicators</h2>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
            {/* CPI Card */}
            <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-6">
              <div className="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-2">
                CPI (Inflation)
              </div>
              <div className="text-3xl font-bold text-gray-900 mb-1">
                {isCurrentShape && regionData.cpi
                  ? regionData.cpi
                  : <span className="text-xl text-gray-400">Not connected</span>}
              </div>
              <div className="text-xs text-gray-500">
                {!isCurrentShape
                  ? 'Waiting for the next harvest'
                  : regionData.cpi
                    ? `Year on year${regionData.cpi_as_of ? `, ${regionData.cpi_as_of}` : ''} · FRED`
                    : 'No free feed for this region still updates'}
              </div>
            </div>

            {/* Interest Rate Card */}
            <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-6">
              <div className="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-2">
                Interest Rate
              </div>
              <div className="text-3xl font-bold text-gray-900 mb-1">
                {isCurrentShape && regionData.rate
                  ? regionData.rate
                  : <span className="text-xl text-gray-400">Not connected</span>}
              </div>
              <div className="text-xs text-gray-500">
                {!isCurrentShape
                  ? 'Waiting for the next harvest'
                  : `${regionData.rate_label || 'Policy rate'}${regionData.rate && regionData.rate_as_of ? ` · ${regionData.rate_as_of}` : ''}`}
              </div>
            </div>

            {/* Market move card — the one genuinely live reading each region has */}
            <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-6">
              <div className="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-2">
                {regionData.market_label || 'Market'}
              </div>
              <div className={`text-3xl font-bold mb-1 ${
                regionData.market_severity === 'severe' ? 'text-red-700' :
                regionData.market_severity === 'notable' ? 'text-amber-700' :
                'text-gray-900'
              }`}>
                {regionData.market_change_pct != null
                  ? `${regionData.market_change_pct > 0 ? '+' : ''}${regionData.market_change_pct.toFixed(2)}%`
                  : <span className="text-xl text-gray-400">No reading</span>}
              </div>
              <div className="text-xs text-gray-500">
                {regionData.market_sigma_pct != null
                  ? `Today · normal daily range ±${regionData.market_sigma_pct.toFixed(1)}%`
                  : 'Today'}
              </div>
            </div>

            {/* FX Rate Card — ECB daily reference rate, EU only */}
            <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-6">
              <div className="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-2">
                ECB Reference Rate
              </div>
              <div className="text-3xl font-bold text-gray-900 mb-1">
                {fxRate != null
                  ? `€1 = $${fxRate.toFixed(4)}`
                  : <span className="text-xl text-gray-400">Not published</span>}
              </div>
              <div className="text-xs text-gray-500">
                {fxRate != null
                  ? 'EUR/USD · European Central Bank, daily fixing'
                  : 'The ECB publishes euro reference rates only'}
              </div>
            </div>
          </div>
        </div>
        {/* What the numbers say — measured facts only. This section used to be
            headed "Analyst Summary" and filled with template prose about Fed
            policy and industrial output that nothing had fetched. */}
        <div className="mb-8">
          <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-6">
            <h2 className="text-lg font-bold text-gray-900 mb-4 border-b border-gray-200 pb-2">
              What the numbers say
            </h2>
            <div className="prose prose-sm max-w-none">
              <p className="text-base text-gray-700 leading-relaxed">
                {regionData.summary || 'No reading available for this region.'}
              </p>
              {regionData.sources?.length > 0 && (
                <p className="text-xs text-gray-500 mt-3">
                  Sources: {regionData.sources.join(', ')}. This dashboard reports what these
                  feeds publish; it does not add commentary or forecasts.
                </p>
              )}
            </div>
          </div>
        </div>

        {/* External Intelligence Hub */}
        <div className="mb-8">
          <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-6">
            <h2 className="text-lg font-bold text-gray-900 mb-4 border-b border-gray-200 pb-2">
              External Intelligence Hub
            </h2>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              {links.map((link, idx) => (
                <a
                  key={idx}
                  href={link.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="flex items-center gap-3 bg-gray-50 hover:bg-gray-100 border border-gray-200 rounded-lg p-4 transition-colors"
                >
                  <span className="text-2xl">{link.icon}</span>
                  <span className="font-semibold text-gray-900">{link.label}</span>
                  <span className="ml-auto text-gray-400">→</span>
                </a>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

