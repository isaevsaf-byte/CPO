'use client';

import { useParams, useRouter } from 'next/navigation';
import Link from 'next/link';
import intel from '../../../data/intel_snapshot.json';

// Type definitions for the page
interface PeerGroupItem {
  name: string;
  ticker: string;
  region: string;
  sentiment: string;
  latest_headline: string;
  stock_move: string;
  current_price: number | null;
  risk_level: string;
  last_signal: string;
}

interface SupplierItem {
  name: string;
  slug: string;
  category: string;
  cyber_risk: boolean;
  news_risk: boolean;
  risk_level: string;
  last_signal: string;
  bat_exposure: string;
  segment: string;
  location: string;
  stock_ticker: string;
  latest_news_summary: string;
  risk_analysis: string;
}

type CompanyData = (PeerGroupItem | SupplierItem) & { [key: string]: unknown };

function getRiskColor(riskLevel: string): string {
  switch (riskLevel?.toUpperCase()) {
    case 'CRITICAL':
      return 'bg-red-100 text-red-800 border-red-300';
    case 'HIGH':
      return 'bg-amber-100 text-amber-800 border-amber-300';
    case 'MEDIUM':
      return 'bg-yellow-100 text-yellow-800 border-yellow-300';
    case 'LOW':
      return 'bg-green-100 text-green-800 border-green-300';
    default:
      return 'bg-gray-100 text-gray-800 border-gray-300';
  }
}

function getExposureColor(exposure: string): string {
  switch (exposure) {
    case 'Critical':
      return 'bg-red-100 text-red-800 border-red-300';
    case 'High':
      return 'bg-amber-100 text-amber-800 border-amber-300';
    case 'Medium':
      return 'bg-green-100 text-green-800 border-green-300';
    default:
      return 'bg-gray-100 text-gray-800 border-gray-300';
  }
}

export default function CompanyDetailPage() {
  const params = useParams();
  const router = useRouter();
  const id = params?.id as string;

  // Helper to normalize strings for comparison
  const normalize = (str: string): string => {
    if (!str) return '';
    return str.toLowerCase().trim().replace(/\s+/g, ' ');
  };

  // Decode and normalize the URL parameter
  const companyName = (() => {
    try {
      return decodeURIComponent(id || '');
    } catch (e) {
      return id || '';
    }
  })();
  const normalizedSearchName = normalize(companyName);

  // Search in peer_group with robust matching
  const peerGroup = intel?.peer_group || [];
  let peer = peerGroup.find((p: any) => {
    if (!p.name) return false;
    const normalizedPeerName = normalize(p.name);
    return normalizedPeerName === normalizedSearchName;
  });

  // Fallback: Try matching with common abbreviations
  if (!peer) {
    const nameVariations: { [key: string]: string[] } = {
      'british american tobacco': ['bat', 'british american tobacco', 'british-american-tobacco'],
      'philip morris int.': ['pmi', 'philip morris international', 'philip morris'],
      'imperial brands': ['imperial', 'imperial brands plc'],
      'japan tobacco': ['jti', 'japan tobacco international']
    };
    
    for (const [fullName, variations] of Object.entries(nameVariations)) {
      if (variations.some(v => normalize(v) === normalizedSearchName)) {
        peer = peerGroup.find((p: any) => normalize(p.name) === normalize(fullName));
        if (peer) break;
      }
    }
  }

  // Search in suppliers with robust matching (check both name and slug)
  const suppliers = intel?.suppliers?.suppliers || [];
  let supplier = suppliers.find((s: any) => {
    if (!s.name && !s.slug) return false;
    const normalizedSupplierName = normalize(s.name || '');
    const normalizedSlug = (s.slug || '').toLowerCase().trim();
    const normalizedSearchSlug = normalizedSearchName.replace(/\s+/g, '-');
    
    return normalizedSupplierName === normalizedSearchName ||
           normalizedSlug === normalizedSearchName ||
           normalizedSlug === normalizedSearchSlug;
  });

  const company = (peer || supplier) as CompanyData | undefined;
  const isPeer = !!peer;
  const isSupplier = !!supplier;

  // Helper to safely get company properties
  const getCompanyProp = <T,>(key: string, defaultValue?: T): T | undefined => {
    if (!company) return defaultValue;
    return (company as Record<string, unknown>)[key] as T | undefined ?? defaultValue;
  };

  if (!company) {
    return (
      <div className="min-h-screen bg-slate-50 p-8">
        <div className="max-w-4xl mx-auto">
          <div className="bg-white rounded-lg shadow p-8 text-center">
            <h1 className="text-2xl font-bold text-gray-900 mb-4">Company Not Found</h1>
            <p className="text-gray-600 mb-6">The requested company could not be found in our intelligence database.</p>
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

  // Prepare external links
  const entityName = company.name;
  const ticker: string | null = (('ticker' in company ? company.ticker : null) || ('stock_ticker' in company ? company.stock_ticker : null)) as string | null;
  const googleNewsUrl = `https://www.google.com/search?q=${encodeURIComponent(entityName)}+supply+chain+news&tbm=nws`;
  const yahooFinanceUrl = ticker ? `https://finance.yahoo.com/quote/${ticker}` : null;
  const secFilingsUrl = `https://www.sec.gov/edgar/search/#/q=${encodeURIComponent(entityName)}`;

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
                <h1 className="text-3xl font-bold text-gray-900">{entityName}</h1>
                <div className="flex items-center gap-3 mt-2">
                  {ticker && (
                    <span className="text-sm text-gray-600 font-mono">{ticker}</span>
                  )}
                  {isPeer && getCompanyProp<string>('risk_level') && (
                    <span className={`px-3 py-1 rounded-full text-xs font-bold border ${getRiskColor(getCompanyProp<string>('risk_level') || '')}`}>
                      Risk: {getCompanyProp<string>('risk_level')}
                    </span>
                  )}
                  {isSupplier && getCompanyProp<string>('category') && (
                    <span className="px-3 py-1 rounded-full text-xs font-semibold bg-blue-100 text-blue-800 border border-blue-300">
                      {getCompanyProp<string>('category')}
                    </span>
                  )}
                  {isSupplier && getCompanyProp<string>('bat_exposure') && (
                    <span className={`px-3 py-1 rounded-full text-xs font-bold border ${getExposureColor(getCompanyProp<string>('bat_exposure') || '')}`}>
                      {getCompanyProp<string>('bat_exposure')} Exposure
                    </span>
                  )}
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>

      <div className="max-w-7xl mx-auto px-6 py-8">
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          {/* Main Content */}
          <div className="lg:col-span-2 space-y-6">
            {/* Primary Stats */}
            <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-6">
              <h2 className="text-lg font-bold text-gray-900 mb-4 border-b border-gray-200 pb-2">
                Primary Stats
              </h2>
              {isPeer && (
                <div className="grid grid-cols-2 gap-4">
                  {getCompanyProp<number>('current_price') && (
                    <div>
                      <div className="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-1">
                        Stock Price
                      </div>
                      <div className="text-2xl font-bold text-gray-900">
                        ${typeof getCompanyProp<number>('current_price') === 'number'
                          ? getCompanyProp<number>('current_price')!.toFixed(2)
                          : getCompanyProp<string>('current_price')}
                      </div>
                    </div>
                  )}
                  {getCompanyProp<string>('stock_move') && (
                    <div>
                      <div className="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-1">
                        Daily Change
                      </div>
                      <div className={`text-2xl font-bold ${
                        getCompanyProp<string>('stock_move')?.startsWith('+') ? 'text-green-600' :
                        getCompanyProp<string>('stock_move')?.startsWith('-') ? 'text-red-600' :
                        'text-gray-600'
                      }`}>
                        {getCompanyProp<string>('stock_move')}
                      </div>
                    </div>
                  )}
                  {getCompanyProp<string>('sentiment') && (
                    <div>
                      <div className="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-1">
                        Sentiment
                      </div>
                      <div className={`text-lg font-semibold ${
                        getCompanyProp<string>('sentiment') === 'Positive' ? 'text-green-600' :
                        getCompanyProp<string>('sentiment') === 'Negative' ? 'text-red-600' :
                        'text-gray-600'
                      }`}>
                        {getCompanyProp<string>('sentiment')}
                      </div>
                    </div>
                  )}
                  {getCompanyProp<string>('risk_level') && (
                    <div>
                      <div className="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-1">
                        Risk Level
                      </div>
                      <div className={`text-lg font-semibold ${
                        getCompanyProp<string>('risk_level') === 'CRITICAL' ? 'text-red-600' :
                        getCompanyProp<string>('risk_level') === 'HIGH' ? 'text-amber-600' :
                        getCompanyProp<string>('risk_level') === 'MEDIUM' ? 'text-yellow-600' :
                        'text-green-600'
                      }`}>
                        {getCompanyProp<string>('risk_level')}
                      </div>
                    </div>
                  )}
                </div>
              )}
              {isSupplier && (
                <div className="grid grid-cols-2 gap-4">
                  {getCompanyProp<string>('category') && (
                    <div>
                      <div className="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-1">
                        Category
                      </div>
                      <div className="text-lg font-semibold text-gray-900">{getCompanyProp<string>('category')}</div>
                    </div>
                  )}
                  {getCompanyProp<string>('bat_exposure') && (
                    <div>
                      <div className="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-1">
                        BAT Exposure
                      </div>
                      <div className={`text-lg font-semibold ${
                        getCompanyProp<string>('bat_exposure') === 'Critical' ? 'text-red-600' :
                        getCompanyProp<string>('bat_exposure') === 'High' ? 'text-amber-600' :
                        'text-green-600'
                      }`}>
                        {getCompanyProp<string>('bat_exposure')}
                      </div>
                    </div>
                  )}
                  {getCompanyProp<string>('location') && (
                    <div>
                      <div className="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-1">
                        Location
                      </div>
                      <div className="text-lg font-semibold text-gray-900">{getCompanyProp<string>('location')}</div>
                    </div>
                  )}
                  {getCompanyProp<string>('segment') && (
                    <div>
                      <div className="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-1">
                        Segment
                      </div>
                      <div className="text-lg font-semibold text-gray-900">{getCompanyProp<string>('segment')}</div>
                    </div>
                  )}
                </div>
              )}
            </div>

            {/* Live Intelligence Section */}
            <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-6">
              <h2 className="text-lg font-bold text-gray-900 mb-4 border-b border-gray-200 pb-2">
                Live Intelligence
              </h2>
              <div className="prose prose-sm max-w-none">
                {isPeer && getCompanyProp<string>('latest_headline') && (
                  <div className="mb-4">
                    <div className="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-2">
                      Latest Headline
                    </div>
                    <p className="text-base text-gray-700 leading-relaxed bg-gray-50 p-4 rounded border border-gray-200">
                      {getCompanyProp<string>('latest_headline')}
                    </p>
                  </div>
                )}
                {isSupplier && getCompanyProp<string>('latest_news_summary') && (
                  <div className="mb-4">
                    <div className="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-2">
                      Latest News Summary
                    </div>
                    <p className="text-base text-gray-700 leading-relaxed bg-gray-50 p-4 rounded border border-gray-200">
                      {getCompanyProp<string>('latest_news_summary')}
                    </p>
                  </div>
                )}
                {isSupplier && getCompanyProp<string>('risk_analysis') && (
                  <div>
                    <div className="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-2">
                      Risk Analysis
                    </div>
                    <p className="text-base text-gray-700 leading-relaxed bg-gray-50 p-4 rounded border border-gray-200">
                      {getCompanyProp<string>('risk_analysis')}
                    </p>
                  </div>
                )}
              </div>
            </div>

            {/* Action Buttons */}
            <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-6">
              <h2 className="text-lg font-bold text-gray-900 mb-4 border-b border-gray-200 pb-2">
                Deep Dive Actions
              </h2>
              <div className="flex flex-wrap gap-3">
                <a
                  href={googleNewsUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-2 bg-blue-900 text-white px-4 py-2 rounded-lg hover:bg-blue-800 transition font-medium"
                >
                  🔎 Search Google News
                </a>
                {yahooFinanceUrl && (
                  <a
                    href={yahooFinanceUrl}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="inline-flex items-center gap-2 bg-green-700 text-white px-4 py-2 rounded-lg hover:bg-green-600 transition font-medium"
                  >
                    📈 Yahoo Finance Page
                  </a>
                )}
                <a
                  href={secFilingsUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-2 bg-gray-700 text-white px-4 py-2 rounded-lg hover:bg-gray-600 transition font-medium"
                >
                  📄 SEC Filings
                </a>
              </div>
            </div>
          </div>

          {/* Sidebar */}
          <div className="space-y-6">
            {/* Quick Facts */}
            <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-6">
              <h3 className="text-base font-bold text-gray-900 mb-4">Quick Facts</h3>
              <div className="space-y-3 text-sm">
                {ticker && (
                  <div>
                    <div className="text-gray-500">Ticker</div>
                    <div className="text-gray-900 font-medium font-mono">{ticker}</div>
                  </div>
                )}
                {isPeer && getCompanyProp<string>('risk_level') && (
                  <div>
                    <div className="text-gray-500">Risk Level</div>
                    <div className={`inline-block px-2 py-1 rounded text-xs font-semibold ${getRiskColor(getCompanyProp<string>('risk_level') || '')}`}>
                      {getCompanyProp<string>('risk_level')}
                    </div>
                  </div>
                )}
                {isSupplier && getCompanyProp<string>('bat_exposure') && (
                  <div>
                    <div className="text-gray-500">BAT Exposure</div>
                    <div className={`inline-block px-2 py-1 rounded text-xs font-semibold ${getExposureColor(getCompanyProp<string>('bat_exposure') || '')}`}>
                      {getCompanyProp<string>('bat_exposure')}
                    </div>
                  </div>
                )}
                {isSupplier && getCompanyProp<boolean>('cyber_risk') && (
                  <div>
                    <div className="text-gray-500">Cyber Risk</div>
                    <div className="text-red-600 font-semibold">&#9888; Active</div>
                  </div>
                )}
                {isSupplier && getCompanyProp<boolean>('news_risk') && (
                  <div>
                    <div className="text-gray-500">News Risk</div>
                    <div className="text-amber-600 font-semibold">&#9888; Active</div>
                  </div>
                )}
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

