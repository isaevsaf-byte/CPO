/**
 * TypeScript types for the Supply Chain Intelligence Dashboard
 * These types match the JSON structure produced by update_intel.py
 */

// ============================================================================
// Core Types
// ============================================================================

export type RAGScore = 'RED' | 'AMBER' | 'GREEN' | 'UNKNOWN';
export type Status = 'success' | 'error' | 'partial' | 'skipped' | 'fallback';
export type OverallStatus = 'healthy' | 'partial' | 'degraded' | 'fallback';
export type RiskLevel = 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL';
export type Sentiment = 'Positive' | 'Negative' | 'Neutral' | 'N/A';
export type Trend = 'Growing' | 'Declining' | 'Stable' | 'Strengthening' | 'Weakening' | 'Improving' | 'Volatile' | 'N/A';
export type Exposure = 'Critical' | 'High' | 'Medium';

// ============================================================================
// Macro Data Types
// ============================================================================

export interface MacroIndicators {
  fx_rate: number | string | null;
  inflation: string;
  policy: string;
}

export interface MacroRegion {
  status: Status;
  region: string;
  indicators: MacroIndicators;
  summary?: string;
  error?: string;
  last_fetched: string;
}

export interface MacroData {
  status: Status;
  rag_score: RAGScore;
  regions: {
    us: MacroRegion;
    eu: MacroRegion;
    china: MacroRegion;
  };
  volatility_pct: number | null;
  last_fetched: string;
}

export interface MacroEconomyRegion {
  cpi: string;
  rate: string;
  trend: Trend;
  summary: string;
}

export interface MacroEconomy {
  us: MacroEconomyRegion;
  eu: MacroEconomyRegion;
  china: MacroEconomyRegion;
}

// ============================================================================
// Peers Data Types
// ============================================================================

export interface SECFiling {
  title: string;
  summary: string;
  published: string;
}

export interface SECFilingsData {
  status: Status;
  filings: SECFiling[];
  red_signals: number;
  amber_signals: number;
  reason?: string;
  error?: string;
  last_fetched: string;
}

export interface PeerNewsData {
  status: Status;
  recent_news: any[];
  note?: string;
}

export interface Peer {
  name: string;
  full_name: string;
  type: string;
  rag_score: RAGScore;
  summary: string;
  sec_filings: SECFilingsData;
  news: PeerNewsData;
}

export interface PeersData {
  status: Status;
  rag_score: RAGScore;
  total_peers: number;
  total_red_signals: number;
  total_amber_signals: number;
  peers: Peer[];
  last_fetched: string;
}

export interface PeerGroupItem {
  name: string;
  ticker: string;
  region: string;
  sentiment: Sentiment;
  latest_headline: string;
  stock_move: string;
  current_price: number | null;
  daily_change_pct: number | null;
  risk_level: RiskLevel;
  last_signal: string;
  news_risk?: boolean;
  stock_risk?: boolean;
}

// ============================================================================
// Supplier Data Types
// ============================================================================

export interface MatchingVulnerability {
  cveID: string;
  vulnerabilityName: string;
  dateAdded: string;
}

export interface Supplier {
  name: string;
  slug: string;
  category: string;
  cyber_risk: boolean;
  matching_vulnerabilities: MatchingVulnerability[];
  news_risk: boolean;
  news_items: any[];
  stock_risk?: boolean;
  daily_change_pct: number | null;
  current_price: number | null;
  risk_analysis: string;
  risk_level: RiskLevel;
  last_signal: string;
  bat_exposure: Exposure;
  segment: string;
  location: string;
  stock_ticker: string;
  latest_news_summary: string;
}

export interface SuppliersData {
  status: Status;
  rag_score: RAGScore;
  total_suppliers: number;
  suppliers_at_cyber_risk: number;
  suppliers_at_news_risk: number;
  suppliers_at_market_risk?: number;
  suppliers: Supplier[];
  last_fetched: string;
}

// ============================================================================
// Health & Stats Types
// ============================================================================

export interface HarvestError {
  source: string;
  error: string;
  time: string;
}

export interface HarvestWarning {
  source: string;
  warning: string;
  time: string;
}

export interface HarvestStats {
  total_errors: number;
  total_warnings: number;
  total_successes: number;
  errors: HarvestError[];
  warnings: HarvestWarning[];
  duration_seconds: number;
}

export interface HealthStatus {
  pillars: {
    macro: Status;
    peers: Status;
    suppliers: Status;
  };
  errors_count: number;
  warnings_count: number;
  circuit_breaker_state: 'closed' | 'open' | 'half-open';
}

// ============================================================================
// Main Intel Snapshot Type
// ============================================================================

export interface IntelSnapshot {
  last_updated: string;
  version: string;
  status: OverallStatus;
  macro: MacroData;
  peers: PeersData;
  suppliers: SuppliersData;
  macro_economy: MacroEconomy;
  peer_group: PeerGroupItem[];
  harvest_stats?: HarvestStats;
  health?: HealthStatus;
}

// ============================================================================
// Utility Types
// ============================================================================

export interface DataFreshnessInfo {
  lastUpdate: Date;
  hoursSinceUpdate: number;
  isStale: boolean;
  version: string;
  status: OverallStatus;
}

export function getDataFreshness(intel: IntelSnapshot): DataFreshnessInfo {
  const lastUpdate = new Date(intel?.last_updated || new Date());
  const now = new Date();
  const hoursSinceUpdate = (now.getTime() - lastUpdate.getTime()) / (1000 * 60 * 60);

  return {
    lastUpdate,
    hoursSinceUpdate,
    isStale: hoursSinceUpdate > 24,
    version: intel?.version || 'unknown',
    status: intel?.status || 'degraded'
  };
}

// ============================================================================
// RAG Color Utilities
// ============================================================================

export const RAG_COLORS: Record<RAGScore, string> = {
  RED: 'border-red-500 bg-red-50',
  AMBER: 'border-amber-500 bg-amber-50',
  GREEN: 'border-green-500 bg-green-50',
  UNKNOWN: 'border-gray-500 bg-gray-50'
};

export const RAG_LABELS: Record<RAGScore, string> = {
  RED: 'CRITICAL',
  AMBER: 'WARNING',
  GREEN: 'NORMAL',
  UNKNOWN: 'UNKNOWN'
};

export function getRAGColor(score: RAGScore | undefined): string {
  if (!score) return RAG_COLORS.UNKNOWN;
  return RAG_COLORS[score] || RAG_COLORS.UNKNOWN;
}

export function getRAGLabel(score: RAGScore | undefined): string {
  if (!score) return RAG_LABELS.UNKNOWN;
  return RAG_LABELS[score] || RAG_LABELS.UNKNOWN;
}

export function getRiskColor(riskLevel: RiskLevel | undefined): string {
  switch (riskLevel) {
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

export function getExposureColor(exposure: Exposure | undefined): string {
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
