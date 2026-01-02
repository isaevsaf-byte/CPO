'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import Link from 'next/link';
import intel from '../data/intel_snapshot.json';

interface RAGScore {
  RED: string;
  AMBER: string;
  GREEN: string;
  UNKNOWN: string;
}

const RAG_COLORS: RAGScore = {
  RED: 'border-red-500 bg-red-50',
  AMBER: 'border-amber-500 bg-amber-50',
  GREEN: 'border-green-500 bg-green-50',
  UNKNOWN: 'border-gray-500 bg-gray-50'
};

const RAG_LABELS: RAGScore = {
  RED: 'CRITICAL',
  AMBER: 'WARNING',
  GREEN: 'NORMAL',
  UNKNOWN: 'UNKNOWN'
};

function getRAGColor(score: string | undefined): string {
  if (!score) return RAG_COLORS.UNKNOWN;
  return RAG_COLORS[score as keyof RAGScore] || RAG_COLORS.UNKNOWN;
}

function getRAGLabel(score: string | undefined): string {
  if (!score) return RAG_LABELS.UNKNOWN;
  return RAG_LABELS[score as keyof RAGScore] || RAG_LABELS.UNKNOWN;
}

function formatTimestamp(isoString: string | undefined): string {
  if (!isoString) return 'Unknown';
  try {
    const date = new Date(isoString);
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

export default function MorningCoffeeDashboard() {
  const router = useRouter();
  const [isModalOpen, setIsModalOpen] = useState(false);
  const lastUpdate = new Date(intel?.last_updated || new Date());
  const now = new Date();
  const hoursSinceUpdate = (now.getTime() - lastUpdate.getTime()) / (1000 * 60 * 60);
  const isStale = hoursSinceUpdate > 24;

  const macro = intel?.macro || {};
  const peers = intel?.peers || {};
  const suppliers = intel?.suppliers || {};
  const macroEconomy = intel?.macro_economy || {};
  const peerGroup = intel?.peer_group || [];

  // Group suppliers by category
  const suppliersByCategory: { [key: string]: any[] } = {};
  const suppliersList = suppliers?.suppliers || [];
  
  suppliersList.forEach((supplier: any) => {
    const category = supplier.category || 'Other';
    if (!suppliersByCategory[category]) {
      suppliersByCategory[category] = [];
    }
    suppliersByCategory[category].push(supplier);
  });

  // Group suppliers by risk level
  const suppliersByRisk: { [key: string]: any[] } = {
    'High Risk': [],
    'Medium Risk': [],
    'Low Risk': []
  };

  suppliersList.forEach((supplier: any) => {
    if (supplier.cyber_risk || supplier.news_risk) {
      suppliersByRisk['High Risk'].push(supplier);
    } else if (supplier.bat_exposure === 'High' || supplier.bat_exposure === 'Critical') {
      suppliersByRisk['Medium Risk'].push(supplier);
    } else {
      suppliersByRisk['Low Risk'].push(supplier);
    }
  });

  return (
    <div className="min-h-screen bg-slate-50">
      {/* BAT Header */}
      <header className="bg-gradient-to-r from-blue-900 via-blue-800 to-blue-900 text-white shadow-lg">
        <div className="max-w-7xl mx-auto px-6 py-6">
          <div className="flex justify-between items-center">
            <div>
              <h1 className="text-3xl font-bold">BAT Global Supply Watchtower</h1>
              <p className="text-blue-100 mt-2">Intelligence Dashboard • Three Core Pillars</p>
            </div>
            <div className="text-right flex items-center gap-4">
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
              <div>
                <div className="text-sm text-blue-100">
                  Last Updated: {formatTimestamp(intel?.last_updated)}
                </div>
                {isStale && (
                  <span className="inline-block mt-2 bg-amber-500 text-white px-3 py-1 rounded-full text-sm font-semibold">
                    ⚠️ Data Stale ({Math.round(hoursSinceUpdate)}h old)
                  </span>
                )}
              </div>
            </div>
          </div>
        </div>
      </header>

      <div className="max-w-7xl mx-auto px-6 py-8">
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
                {suppliers?.suppliers_at_cyber_risk > 0 && (
                  <div className="text-red-600 font-semibold text-xs">🔴 {suppliers.suppliers_at_cyber_risk} Cyber Risk</div>
                )}
                {suppliers?.suppliers_at_news_risk > 0 && (
                  <div className="text-amber-600 font-semibold text-xs">⚠️ {suppliers.suppliers_at_news_risk} News Risk</div>
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
            {peerGroup.map((peer: any, idx: number) => {
              const isBAT = peer.name === 'British American Tobacco' || peer.name === 'BAT' || peer.ticker === 'BTI';
              const stockMovePositive = peer.stock_move?.startsWith('+');
              const stockMoveNegative = peer.stock_move?.startsWith('-');
              
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
                    <div className="flex items-center gap-2 mb-2">
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
                    </div>
                  </div>
                  
                  <div className="pt-3 border-t border-gray-200">
                    <p className="text-xs text-gray-700 leading-relaxed">{peer.latest_headline || 'No headline'}</p>
                  </div>
                </Link>
              );
            })}
          </div>
        </div>

        {/* Supplier Watchlist Table */}
        <div className="bg-white rounded-xl shadow-sm border border-gray-200 overflow-hidden">
          <div className="px-6 py-4 bg-gray-50 border-b border-gray-200">
            <h2 className="text-xl font-bold text-gray-900">Supplier Watchlist</h2>
            <p className="text-sm text-gray-600 mt-1">Click any supplier for detailed intelligence</p>
          </div>
          
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead className="bg-gray-50 border-b border-gray-200">
                <tr>
                  <th className="px-6 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">Supplier</th>
                  <th className="px-6 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">Category</th>
                  <th className="px-6 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">BAT Exposure</th>
                  <th className="px-6 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">Segment</th>
                  <th className="px-6 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">Location</th>
                  <th className="px-6 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">Risk Status</th>
                </tr>
              </thead>
              <tbody className="bg-white divide-y divide-gray-200">
                {suppliersList.map((supplier: any, idx: number) => (
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
                    <td className="px-6 py-4 whitespace-nowrap">
                      <Link href={`/details/${encodeURIComponent(supplier.name)}`} className="block">
                        <div className="flex items-center gap-2">
                          {supplier.cyber_risk && (
                            <span className="px-2 py-1 bg-red-100 text-red-800 rounded text-xs font-semibold">
                              Cyber
                            </span>
                          )}
                          {supplier.news_risk && (
                            <span className="px-2 py-1 bg-amber-100 text-amber-800 rounded text-xs font-semibold">
                              News
                            </span>
                          )}
                          {!supplier.cyber_risk && !supplier.news_risk && (
                            <span className="px-2 py-1 bg-green-100 text-green-800 rounded text-xs font-semibold">
                              Low
                            </span>
                          )}
                        </div>
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        {/* Peers Section */}
        {peers?.peers && peers.peers.length > 0 && (
          <div className="mt-8 bg-white rounded-xl shadow-sm border border-gray-200 overflow-hidden">
            <div className="px-6 py-4 bg-gray-50 border-b border-gray-200">
              <h2 className="text-xl font-bold text-gray-900">Peers & Competitors</h2>
            </div>
            <div className="p-6">
              <div className="space-y-4">
                {peers.peers.map((peer: any, idx: number) => (
                  <Link
                    key={idx}
                    href={`/details/${encodeURIComponent(peer.name)}`}
                    className="block border-b border-gray-200 pb-4 last:border-0 last:pb-0 hover:bg-slate-50 cursor-pointer transition-colors p-2 rounded -m-2"
                  >
                    <div className="flex items-start justify-between">
                      <div className="flex-1">
                        <div className="flex items-center gap-3">
                          <h3 className="text-lg font-semibold text-gray-900 hover:text-blue-900">{peer.name}</h3>
                          <span className={`px-2 py-1 rounded text-xs font-bold ${
                            peer.rag_score === 'RED' ? 'bg-red-100 text-red-800' :
                            peer.rag_score === 'AMBER' ? 'bg-amber-100 text-amber-800' :
                            'bg-green-100 text-green-800'
                          }`}>
                            {peer.rag_score}
                          </span>
                        </div>
                        <p className="text-sm text-gray-600 mt-1">{peer.full_name} • {peer.type}</p>
                        <p className="text-sm text-gray-700 mt-2 bg-gray-50 p-3 rounded border border-gray-200">
                          {peer.summary}
                        </p>
                      </div>
                    </div>
                  </Link>
                ))}
              </div>
            </div>
          </div>
        )}
      </div>

      <footer className="mt-12 bg-gray-900 text-gray-300 py-6">
        <div className="max-w-7xl mx-auto px-6 text-center text-sm">
          <p>BAT Global Supply Watchtower • Built with the "Flat Data" pattern</p>
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
              </p>

              <div className="mb-6">
                <h3 className="text-base font-bold text-gray-900 mb-3">Data Frequency</h3>
                <p className="text-gray-700 leading-relaxed">
                  Updates daily at 07:00 UTC via automated fetching engine.
                </p>
              </div>

              <div className="mb-6">
                <h3 className="text-base font-bold text-gray-900 mb-3">Sources</h3>
                <ul className="list-disc list-inside space-y-2 text-gray-700">
                  <li><strong>Macro:</strong> Live feeds from Federal Reserve, ECB, and PBoC.</li>
                  <li><strong>Peers:</strong> Real-time market data (LSE/NYSE) and automated news sentiment analysis via Yahoo Finance.</li>
                  <li><strong>Cyber Risk:</strong> Direct sync with CISA Known Exploited Vulnerabilities catalog.</li>
                  <li><strong>Suppliers:</strong> Watchlist of 24 strategic partners monitored for risk signals.</li>
                </ul>
              </div>

              <div className="mb-6">
                <h3 className="text-base font-bold text-gray-900 mb-3">How to use</h3>
                <ul className="list-disc list-inside space-y-2 text-gray-700">
                  <li><strong>Traffic Light System:</strong> Red cards indicate critical risks (Strikes, Bans, Crashes).</li>
                  <li><strong>Deep Dive:</strong> Click any card (Region, Peer, or Supplier) to access the Intelligence Dossier and external verification links.</li>
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
