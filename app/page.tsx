'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import Link from 'next/link';
import intel from '../data/intel_snapshot.json';
import { useDataFreshness } from '../hooks/useDataFreshness';
import type {
  IntelSnapshot,
  RAGScore as RAGScoreType,
  Supplier,
  PeerGroupItem,
  Peer,
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

// Health status indicator component
function HealthIndicator({ status }: { status: string }) {
  const color = status === 'success' ? 'bg-green-500' : status === 'error' ? 'bg-red-500' : 'bg-yellow-500';
  return (
    <span className={`inline-block w-2 h-2 rounded-full ${color}`} title={`Status: ${status}`} />
  );
}

export default function MorningCoffeeDashboard() {
  const router = useRouter();
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [riskFilter, setRiskFilter] = useState<'all' | 'cyber' | 'news' | 'operational' | 'critical' | 'high' | 'medium'>('all');

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

  // Group suppliers by risk level
  const suppliersByRisk: { [key: string]: Supplier[] } = {
    'High Risk': [],
    'Medium Risk': [],
    'Low Risk': []
  };

  suppliersList.forEach((supplier: Supplier) => {
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
              {/* Health Status Indicators */}
              <div className="flex items-center gap-2" title="Data Source Health">
                <span className="text-xs text-blue-200 mr-1">Health:</span>
                <HealthIndicator status={macro?.status || 'unknown'} />
                <HealthIndicator status={peers?.status || 'unknown'} />
                <HealthIndicator status={suppliers?.status || 'unknown'} />
              </div>

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
                <div className="text-sm text-blue-100 flex items-center gap-2">
                  <span>Last Updated: {formatTimestamp(typedIntel?.last_updated)}</span>
                  {isChecking && (
                    <span className="animate-spin text-xs">&#8635;</span>
                  )}
                </div>
                {typedIntel?.version && (
                  <div className="text-xs text-blue-200 font-mono">
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
          <div className="max-w-7xl mx-auto flex items-center justify-between">
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
                {/* Show risk counts by severity */}
                {(suppliers as any)?.total_critical > 0 && (
                  <button
                    onClick={() => setRiskFilter(riskFilter === 'critical' ? 'all' : 'critical')}
                    className={`block text-red-700 font-semibold text-xs hover:underline cursor-pointer ${riskFilter === 'critical' ? 'bg-red-100 px-2 py-0.5 rounded' : ''}`}
                  >
                    🚨 {(suppliers as any).total_critical} Critical
                  </button>
                )}
                {(suppliers as any)?.total_high > 0 && (
                  <button
                    onClick={() => setRiskFilter(riskFilter === 'high' ? 'all' : 'high')}
                    className={`block text-red-600 font-semibold text-xs hover:underline cursor-pointer ${riskFilter === 'high' ? 'bg-red-100 px-2 py-0.5 rounded' : ''}`}
                  >
                    ⚠️ {(suppliers as any).total_high} High Risk
                  </button>
                )}
                {(suppliers as any)?.total_medium > 0 && (
                  <button
                    onClick={() => setRiskFilter(riskFilter === 'medium' ? 'all' : 'medium')}
                    className={`block text-amber-600 font-semibold text-xs hover:underline cursor-pointer ${riskFilter === 'medium' ? 'bg-amber-100 px-2 py-0.5 rounded' : ''}`}
                  >
                    📋 {(suppliers as any).total_medium} Medium Risk
                  </button>
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
            <div className="flex items-center justify-between">
              <div>
                <h2 className="text-xl font-bold text-gray-900">Supplier Watchlist</h2>
                <p className="text-sm text-gray-600 mt-1">Click any supplier for detailed intelligence</p>
              </div>
              {riskFilter !== 'all' && (
                <div className="flex items-center gap-2">
                  <span className={`px-3 py-1.5 rounded-full text-sm font-semibold ${
                    riskFilter === 'critical' ? 'bg-red-200 text-red-900' :
                    riskFilter === 'high' ? 'bg-red-100 text-red-800' :
                    riskFilter === 'medium' ? 'bg-amber-100 text-amber-800' :
                    riskFilter === 'cyber' ? 'bg-gray-100 text-gray-800' :
                    riskFilter === 'news' ? 'bg-amber-100 text-amber-800' :
                    'bg-gray-100 text-gray-800'
                  }`}>
                    {riskFilter === 'critical' && '🚨 Critical Risk'}
                    {riskFilter === 'high' && '⚠️ High Risk'}
                    {riskFilter === 'medium' && '📋 Medium Risk'}
                    {riskFilter === 'cyber' && '🔒 Cyber Risk'}
                    {riskFilter === 'news' && '📰 News Risk'}
                    {riskFilter === 'operational' && '⚠️ Operational Risk'}
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
                {suppliersList
                  .filter((supplier: Supplier) => {
                    if (riskFilter === 'all') return true;
                    if (riskFilter === 'critical') return supplier.risk_level === 'CRITICAL';
                    if (riskFilter === 'high') return supplier.risk_level === 'HIGH';
                    if (riskFilter === 'medium') return supplier.risk_level === 'MEDIUM';
                    if (riskFilter === 'cyber') return supplier.cyber_risk;
                    if (riskFilter === 'news') return supplier.news_risk;
                    if (riskFilter === 'operational') return (supplier as any).operational_risk;
                    return true;
                  })
                  .map((supplier: Supplier, idx: number) => (
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
                            {supplier.cyber_risk && (
                              <span className="px-1.5 py-0.5 bg-red-600 text-white rounded text-xs">
                                🔒
                              </span>
                            )}
                            {supplier.news_risk && (
                              <span className="px-1.5 py-0.5 bg-amber-600 text-white rounded text-xs" title="News-based risk">
                                📰
                              </span>
                            )}
                          </div>
                          {/* Show risk reason for non-LOW risks */}
                          {supplier.risk_level && supplier.risk_level !== 'LOW' && supplier.last_signal && (
                            <div className="text-xs text-gray-600 max-w-xs truncate" title={supplier.last_signal}>
                              {supplier.last_signal}
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
        </div>

        {/* Peers Section */}
        {peers?.peers && peers.peers.length > 0 && (
          <div className="mt-8 bg-white rounded-xl shadow-sm border border-gray-200 overflow-hidden">
            <div className="px-6 py-4 bg-gray-50 border-b border-gray-200">
              <h2 className="text-xl font-bold text-gray-900">Peers & Competitors</h2>
            </div>
            <div className="p-6">
              <div className="space-y-4">
                {peers.peers.map((peer: Peer, idx: number) => (
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
                  Updates every 6 hours via automated GitHub Actions workflow.
                </p>
              </div>

              <div className="mb-6">
                <h3 className="text-base font-bold text-gray-900 mb-3">Sources</h3>
                <ul className="list-disc list-inside space-y-2 text-gray-700">
                  <li><strong>Macro:</strong> ECB for EUR/USD exchange rates. Stock indices (S&P 500, major currencies) via Yahoo Finance.</li>
                  <li><strong>Peers:</strong> Real-time stock prices and news via Yahoo Finance API. SEC 8-K filings for US-listed peers.</li>
                  <li><strong>Cyber Risk:</strong> Direct sync with CISA Known Exploited Vulnerabilities (KEV) catalog.</li>
                  <li><strong>Suppliers:</strong> Watchlist of 24 strategic partners with live stock monitoring, news analysis, and cyber risk detection.</li>
                </ul>
              </div>

              <div className="mb-6">
                <h3 className="text-base font-bold text-gray-900 mb-3">Risk Detection</h3>
                <ul className="list-disc list-inside space-y-2 text-gray-700">
                  <li><strong>🔒 Cyber Risk:</strong> CISA vulnerability matches against supplier names.</li>
                  <li><strong>📰 News Risk:</strong> Keywords like &quot;investigation&quot;, &quot;fraud&quot;, &quot;bankruptcy&quot; detected in headlines.</li>
                  <li><strong>📉 Market Risk:</strong> Stock drops &gt;2% (Medium) or &gt;5% (Critical) trigger alerts.</li>
                </ul>
              </div>

              <div className="mb-6">
                <h3 className="text-base font-bold text-gray-900 mb-3">How to use</h3>
                <ul className="list-disc list-inside space-y-2 text-gray-700">
                  <li><strong>Traffic Light System:</strong> Red = Critical/High risk, Amber = Medium risk, Green = Low risk.</li>
                  <li><strong>Deep Dive:</strong> Click any card (Region, Peer, or Supplier) to access the Intelligence Dossier.</li>
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
