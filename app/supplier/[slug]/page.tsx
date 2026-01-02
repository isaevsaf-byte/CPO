'use client';

import { useParams, useRouter } from 'next/navigation';
import intel from '../../../data/intel_snapshot.json';

interface Supplier {
  name: string;
  slug: string;
  category: string;
  bat_exposure: string;
  segment: string;
  location: string;
  stock_ticker: string;
  latest_news_summary: string;
  risk_analysis: string;
  cyber_risk: boolean;
  matching_vulnerabilities: any[];
  news_risk: boolean;
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

function getAffectedBrands(segment: string, category: string): string {
  if (segment.includes('New Categories')) {
    return 'Vuse, Glo';
  } else if (category.includes('Packaging') || category.includes('Paper')) {
    return 'Combustibles Portfolio';
  } else {
    return 'Vuse, Glo, Combustibles';
  }
}

function getSpendTier(exposure: string): string {
  switch (exposure) {
    case 'Critical':
      return 'Tier 1 Strategic Partner';
    case 'High':
      return 'Tier 2 Key Supplier';
    case 'Medium':
      return 'Tier 3 Standard Supplier';
    default:
      return 'Tier 3 Standard Supplier';
  }
}

export default function SupplierDetailPage() {
  const params = useParams();
  const router = useRouter();
  const slug = params?.slug as string;

  // Find supplier by slug
  const suppliers = intel?.suppliers?.suppliers || [];
  const supplier = suppliers.find((s: Supplier) => s.slug === slug) as Supplier | undefined;

  if (!supplier) {
    return (
      <div className="min-h-screen bg-slate-50 p-8">
        <div className="max-w-4xl mx-auto">
          <div className="bg-white rounded-lg shadow p-8 text-center">
            <h1 className="text-2xl font-bold text-gray-900 mb-4">Supplier Not Found</h1>
            <p className="text-gray-600 mb-6">The requested supplier could not be found.</p>
            <button
              onClick={() => router.push('/')}
              className="bg-blue-900 text-white px-6 py-2 rounded-lg hover:bg-blue-800 transition"
            >
              Back to Dashboard
            </button>
          </div>
        </div>
      </div>
    );
  }

  const googleSearchUrl = `https://www.google.com/search?q=${encodeURIComponent(supplier.name)}+news+supply+chain`;

  return (
    <div className="min-h-screen bg-slate-50">
      {/* Header */}
      <div className="bg-white border-b border-gray-200">
        <div className="max-w-7xl mx-auto px-6 py-4">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-4">
              <button
                onClick={() => router.push('/')}
                className="text-blue-900 hover:text-blue-700 font-semibold"
              >
                ← Back to Dashboard
              </button>
              <div className="h-6 w-px bg-gray-300"></div>
              <div>
                <h1 className="text-2xl font-bold text-gray-900">{supplier.name}</h1>
                <div className="flex items-center gap-3 mt-1">
                  {supplier.stock_ticker !== 'N/A' && (
                    <span className="text-sm text-gray-600 font-mono">{supplier.stock_ticker}</span>
                  )}
                  <span className={`px-3 py-1 rounded-full text-xs font-bold border ${getExposureColor(supplier.bat_exposure)}`}>
                    {supplier.bat_exposure} Exposure
                  </span>
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
            {/* BAT Context Section */}
            <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-6">
              <h2 className="text-lg font-bold text-gray-900 mb-4 border-b border-gray-200 pb-2">
                BAT Context
              </h2>
              <div className="space-y-4">
                <div>
                  <div className="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-1">
                    Affected Brands
                  </div>
                  <div className="text-base text-gray-900 font-medium">
                    Used in: {getAffectedBrands(supplier.segment, supplier.category)}
                  </div>
                </div>
                <div>
                  <div className="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-1">
                    Spend Tier
                  </div>
                  <div className="text-base text-gray-900 font-medium">
                    {getSpendTier(supplier.bat_exposure)}
                  </div>
                </div>
                <div>
                  <div className="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-1">
                    Category
                  </div>
                  <div className="text-base text-gray-900">{supplier.category}</div>
                </div>
                <div>
                  <div className="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-1">
                    Segment
                  </div>
                  <div className="text-base text-gray-900">{supplier.segment}</div>
                </div>
              </div>
            </div>

            {/* Live Intelligence Section */}
            <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-6">
              <h2 className="text-lg font-bold text-gray-900 mb-4 border-b border-gray-200 pb-2">
                Live Intelligence
              </h2>
              <div className="space-y-4">
                <div>
                  <a
                    href={googleSearchUrl}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="inline-flex items-center gap-2 bg-blue-900 text-white px-4 py-2 rounded-lg hover:bg-blue-800 transition font-medium"
                  >
                    <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
                    </svg>
                    Google Search: {supplier.name} News
                  </a>
                </div>
                <div>
                  <div className="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-2">
                    Latest News Summary
                  </div>
                  <div className="text-base text-gray-700 leading-relaxed bg-gray-50 p-4 rounded border border-gray-200">
                    {supplier.latest_news_summary}
                  </div>
                </div>
                <div>
                  <div className="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-2">
                    Risk Analysis
                  </div>
                  <div className="text-base text-gray-700 leading-relaxed bg-gray-50 p-4 rounded border border-gray-200">
                    {supplier.risk_analysis}
                  </div>
                </div>
              </div>
            </div>

            {/* Cyber Risk Section */}
            {supplier.cyber_risk && supplier.matching_vulnerabilities.length > 0 && (
              <div className="bg-red-50 rounded-lg shadow-sm border border-red-200 p-6">
                <h2 className="text-lg font-bold text-red-900 mb-4 border-b border-red-200 pb-2">
                  Cyber Risk Alert
                </h2>
                <div className="space-y-3">
                  {supplier.matching_vulnerabilities.map((vuln: any, idx: number) => (
                    <div key={idx} className="bg-white p-3 rounded border border-red-200">
                      <div className="font-mono text-sm font-semibold text-red-900">{vuln.cveID}</div>
                      <div className="text-sm text-gray-700 mt-1">{vuln.vulnerabilityName}</div>
                      <div className="text-xs text-gray-500 mt-1">Added: {vuln.dateAdded}</div>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>

          {/* Sidebar */}
          <div className="space-y-6">
            {/* Quick Facts */}
            <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-6">
              <h3 className="text-base font-bold text-gray-900 mb-4">Quick Facts</h3>
              <div className="space-y-3 text-sm">
                <div>
                  <div className="text-gray-500">Location</div>
                  <div className="text-gray-900 font-medium">{supplier.location}</div>
                </div>
                {supplier.stock_ticker !== 'N/A' && (
                  <div>
                    <div className="text-gray-500">Stock Ticker</div>
                    <div className="text-gray-900 font-medium font-mono">{supplier.stock_ticker}</div>
                  </div>
                )}
                <div>
                  <div className="text-gray-500">Risk Status</div>
                  <div className="flex items-center gap-2 mt-1">
                    {supplier.cyber_risk && (
                      <span className="px-2 py-1 bg-red-100 text-red-800 rounded text-xs font-semibold">
                        Cyber Risk
                      </span>
                    )}
                    {supplier.news_risk && (
                      <span className="px-2 py-1 bg-amber-100 text-amber-800 rounded text-xs font-semibold">
                        News Risk
                      </span>
                    )}
                    {!supplier.cyber_risk && !supplier.news_risk && (
                      <span className="px-2 py-1 bg-green-100 text-green-800 rounded text-xs font-semibold">
                        Low Risk
                      </span>
                    )}
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

