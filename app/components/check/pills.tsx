import type { RiskLevel } from '../../../types/intel';
import { getExposureColor, getRiskColor } from '../../../types/intel';
import type { CheckTier } from '../../../types/extras';

// Same colours as the board: exposure tiers use the watchlist's tier pill,
// standing country levels use its risk-level pill.

export function TierPill({ tier }: { tier: CheckTier | null }) {
  if (!tier) return <span className="text-xs text-gray-400">no tier</span>;
  const tone = tier === 'Low' ? 'bg-gray-100 text-gray-700 border-gray-300' : getExposureColor(tier);
  return (
    <span className={`inline-block px-2 py-0.5 rounded-full text-[11px] font-bold border whitespace-nowrap ${tone}`}>
      {tier}
    </span>
  );
}

export function LevelPill({ level }: { level: RiskLevel }) {
  return (
    <span className={`inline-block px-2 py-0.5 rounded-full text-[11px] font-bold border whitespace-nowrap ${getRiskColor(level)}`}>
      {level}
    </span>
  );
}

export function percent(share: number): string {
  return `${Math.round(share * 100)}%`;
}
