'use client';

import { useParams, useRouter } from 'next/navigation';
import Link from 'next/link';
import intel from '../../../data/intel_snapshot.json';

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

  // Search in peer_group
  const peerGroup = intel?.peer_group || [];
  const peer = peerGroup.find((p: any) => 
    p.name.toLowerCase() === decodeURIComponent(id).toLowerCase()
  );

  // Search in suppliers
  const suppliers = intel?.suppliers?.suppliers || [];
  const supplier = suppliers.find((s: any) => 
    s.name.toLowerCase() === decodeURIComponent(id).toLowerCase() ||
    s.slug === decodeURIComponent(id).toLowerCase()
  );

  const company = peer || supplier;
  const isPeer = !!peer;
  const isSupplier = !!supplier;

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
  const companyName = company.name;
  const ticker = company.ticker || company.stock_ticker;
  const googleNewsUrl = `https://www.google.com/search?q=${encodeURIComponent(companyName)}+supply+chain+news&tbm=nws`;
  const yahooFinanceUrl = ticker ? `https://finance.yahoo.com/quote/${ticker}` : null;
  const secFilingsUrl = `https://www.sec.gov/edgar/search/#/q=${encodeURIComponent(companyName)}`;

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
                <h1 className="text-3xl font-bold text-gray-900">{companyName}</h1>
                <div className="flex items-center gap-3 mt-2">
                  {ticker && (
                    <span className="text-sm text-gray-600 font-mono">{ticker}</span>
                  )}
                  {isPeer && company.risk_level && (
                    <span className={`px-3 py-1 rounded-full text-xs font-bold border ${getRiskColor(company.risk_level)}`}>
                      Risk: {company.risk_level}
                    </span>
                  )}
                  {isSupplier && company.category && (
                    <span className="px-3 py-1 rounded-full text-xs font-semibold bg-blue-100 text-blue-800 border border-blue-300">
                      {company.category}
                    </span>
                  )}
                  {isSupplier && company.bat_exposure && (
                    <span className={`px-3 py-1 rounded-full text-xs font-bold border ${getExposureColor(company.bat_exposure)}`}>
                      {company.bat_exposure} Exposure
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
                  {company.current_price && (
                    <div>
                      <div className="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-1">
                        Stock Price
                      </div>
                      <div className="text-2xl font-bold text-gray-900">
                        ${typeof company.current_price === 'number' 
                          ? company.current_price.toFixed(2) 
                          : company.current_price}
                      </div>
                    </div>
                  )}
                  {company.stock_move && (
                    <div>
                      <div className="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-1">
                        Daily Change
                      </div>
                      <div className={`text-2xl font-bold ${
                        company.stock_move.startsWith('+') ? 'text-green-600' :
                        company.stock_move.startsWith('-') ? 'text-red-600' :
                        'text-gray-600'
                      }`}>
                        {company.stock_move}
                      </div>
                    </div>
                  )}
                  {company.sentiment && (
                    <div>
                      <div className="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-1">
                        Sentiment
                      </div>
                      <div className={`text-lg font-semibold ${
                        company.sentiment === 'Positive' ? 'text-green-600' :
                        company.sentiment === 'Negative' ? 'text-red-600' :
                        'text-gray-600'
                      }`}>
                        {company.sentiment}
                      </div>
                    </div>
                  )}
                  {company.risk_level && (
                    <div>
                      <div className="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-1">
                        Risk Level
                      </div>
                      <div className={`text-lg font-semibold ${
                        company.risk_level === 'CRITICAL' ? 'text-red-600' :
                        company.risk_level === 'HIGH' ? 'text-amber-600' :
                        company.risk_level === 'MEDIUM' ? 'text-yellow-600' :
                        'text-green-600'
                      }`}>
                        {company.risk_level}
                      </div>
                    </div>
                  )}
                </div>
              )}
              {isSupplier && (
                <div className="grid grid-cols-2 gap-4">
                  {company.category && (
                    <div>
                      <div className="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-1">
                        Category
                      </div>
                      <div className="text-lg font-semibold text-gray-900">{company.category}</div>
                    </div>
                  )}
                  {company.bat_exposure && (
                    <div>
                      <div className="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-1">
                        BAT Exposure
                      </div>
                      <div className={`text-lg font-semibold ${
                        company.bat_exposure === 'Critical' ? 'text-red-600' :
                        company.bat_exposure === 'High' ? 'text-amber-600' :
                        'text-green-600'
                      }`}>
                        {company.bat_exposure}
                      </div>
                    </div>
                  )}
                  {company.location && (
                    <div>
                      <div className="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-1">
                        Location
                      </div>
                      <div className="text-lg font-semibold text-gray-900">{company.location}</div>
                    </div>
                  )}
                  {company.segment && (
                    <div>
                      <div className="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-1">
                        Segment
                      </div>
                      <div className="text-lg font-semibold text-gray-900">{company.segment}</div>
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
                {isPeer && company.latest_headline && (
                  <div className="mb-4">
                    <div className="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-2">
                      Latest Headline
                    </div>
                    <p className="text-base text-gray-700 leading-relaxed bg-gray-50 p-4 rounded border border-gray-200">
                      {company.latest_headline}
                    </p>
                  </div>
                )}
                {isSupplier && company.latest_news_summary && (
                  <div className="mb-4">
                    <div className="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-2">
                      Latest News Summary
                    </div>
                    <p className="text-base text-gray-700 leading-relaxed bg-gray-50 p-4 rounded border border-gray-200">
                      {company.latest_news_summary}
                    </p>
                  </div>
                )}
                {isSupplier && company.risk_analysis && (
                  <div>
                    <div className="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-2">
                      Risk Analysis
                    </div>
                    <p className="text-base text-gray-700 leading-relaxed bg-gray-50 p-4 rounded border border-gray-200">
                      {company.risk_analysis}
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
                {isPeer && company.risk_level && (
                  <div>
                    <div className="text-gray-500">Risk Level</div>
                    <div className={`inline-block px-2 py-1 rounded text-xs font-semibold ${getRiskColor(company.risk_level)}`}>
                      {company.risk_level}
                    </div>
                  </div>
                )}
                {isSupplier && company.bat_exposure && (
                  <div>
                    <div className="text-gray-500">BAT Exposure</div>
                    <div className={`inline-block px-2 py-1 rounded text-xs font-semibold ${getExposureColor(company.bat_exposure)}`}>
                      {company.bat_exposure}
                    </div>
                  </div>
                )}
                {isSupplier && company.cyber_risk && (
                  <div>
                    <div className="text-gray-500">Cyber Risk</div>
                    <div className="text-red-600 font-semibold">⚠️ Active</div>
                  </div>
                )}
                {isSupplier && company.news_risk && (
                  <div>
                    <div className="text-gray-500">News Risk</div>
                    <div className="text-amber-600 font-semibold">⚠️ Active</div>
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

