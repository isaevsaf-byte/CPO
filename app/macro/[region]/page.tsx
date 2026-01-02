'use client';

import { useParams } from 'next/navigation';
import Link from 'next/link';
import intel from '../../../data/intel_snapshot.json';

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

  // Get FX rate from macro regions if available
  const macroRegions = intel?.macro?.regions || {};
  let fxRate = null;
  if (regionKey === 'us' && macroRegions.us?.indicators?.fx_rate) {
    fxRate = macroRegions.us.indicators.fx_rate;
  } else if (regionKey === 'eu' && macroRegions.eu?.indicators?.fx_rate) {
    fxRate = macroRegions.eu.indicators.fx_rate;
  } else if (regionKey === 'china' && macroRegions.china?.indicators?.fx_rate) {
    fxRate = macroRegions.china.indicators.fx_rate;
  }

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

  // Mock GDP trend (would be fetched from real API in production)
  const gdpTrend = regionData.trend === 'Stable' || regionData.trend === 'Improving' ? 'Moderate Growth' : 'Slowing';

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
                {regionData.cpi || 'N/A'}
              </div>
              <div className="text-xs text-gray-500">Consumer Price Index</div>
            </div>

            {/* Interest Rate Card */}
            <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-6">
              <div className="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-2">
                Interest Rate
              </div>
              <div className="text-3xl font-bold text-gray-900 mb-1">
                {regionData.rate || 'N/A'}
              </div>
              <div className="text-xs text-gray-500">
                {regionKey === 'us' ? 'Fed Funds Rate' : 
                 regionKey === 'eu' ? 'ECB Main Rate' : 
                 'PBOC Policy Rate'}
              </div>
            </div>

            {/* FX Rate Card */}
            <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-6">
              <div className="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-2">
                FX Rate
              </div>
              <div className="text-3xl font-bold text-gray-900 mb-1">
                {fxRate && typeof fxRate === 'number' 
                  ? (regionKey === 'eu' ? `€1 = $${fxRate.toFixed(4)}` : 
                     regionKey === 'china' ? `¥1 = $${fxRate.toFixed(4)}` : 
                     `$${fxRate.toFixed(4)}`)
                  : regionData.trend || 'N/A'}
              </div>
              <div className="text-xs text-gray-500">
                {regionKey === 'eu' ? 'EUR/USD' : 
                 regionKey === 'china' ? 'CNY/USD' : 
                 'USD Index'}
              </div>
            </div>

            {/* GDP Trend Card */}
            <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-6">
              <div className="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-2">
                GDP Trend
              </div>
              <div className="text-3xl font-bold text-gray-900 mb-1">
                {gdpTrend}
              </div>
              <div className="text-xs text-gray-500">Economic Growth Outlook</div>
            </div>
          </div>
        </div>

        {/* Analyst Summary */}
        <div className="mb-8">
          <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-6">
            <h2 className="text-lg font-bold text-gray-900 mb-4 border-b border-gray-200 pb-2">
              Analyst Summary
            </h2>
            <div className="prose prose-sm max-w-none">
              <p className="text-base text-gray-700 leading-relaxed">
                {regionData.summary || 'No summary available.'}
              </p>
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

