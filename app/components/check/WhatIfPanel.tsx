'use client';

import { useMemo } from 'react';
import type { CheckRow, CountrySummary } from '../../../types/extras';
import { whatIf } from './analyseList';
import { LevelPill, TierPill } from './pills';

interface WhatIfPanelProps {
  rows: CheckRow[];
  countries: CountrySummary[];
  selected: string;
  onSelect: (country: string) => void;
}

// A pasted list can run to thousands of lines; the page stays readable.
const MAX_AFFECTED_SHOWN = 50;

function whoIsHere(count: number): string {
  if (count === 1) return 'its only supplier is here';
  if (count === 2) return 'both suppliers are here';
  return `all ${count} suppliers are here`;
}

export default function WhatIfPanel({ rows, countries, selected, onSelect }: WhatIfPanelProps) {
  const result = useMemo(() => whatIf(rows, selected), [rows, selected]);
  const standing = countries.find((country) => country.country === selected)?.standing ?? null;
  const affectedSuppliers = new Set(result.affected.map((row) => row.name.toLowerCase())).size;
  const shown = result.affected.slice(0, MAX_AFFECTED_SHOWN);

  return (
    <section className="bg-white rounded-xl border border-gray-200 shadow-sm overflow-hidden">
      <div className="px-4 sm:px-6 py-4 bg-gray-50 border-b border-gray-200 flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-lg font-bold text-gray-900 flex flex-wrap items-center gap-2">
          <label htmlFor="what-if-country">What if</label>
          <select
            id="what-if-country"
            value={selected}
            onChange={(event) => onSelect(event.target.value)}
            className="max-w-[14rem] rounded-lg border border-gray-300 bg-white px-2.5 py-1 text-base font-semibold text-blue-900 focus:outline-none focus:ring-2 focus:ring-blue-800"
          >
            {countries.map((country) => (
              <option key={country.country} value={country.country}>
                {country.country} ({country.suppliers.length})
              </option>
            ))}
          </select>
          <span>stops?</span>
        </h2>
        {standing && (
          <span className="flex items-center gap-2 text-xs text-gray-500">
            Standing floor <LevelPill level={standing.level} />
          </span>
        )}
      </div>

      <div className="px-4 sm:px-6 py-5 space-y-5">
        <p className="text-sm text-gray-600 max-w-3xl">
          An export ban, a border closure, a war or a grid failure: everything supplied from{' '}
          {selected} stops. A category keeps going only if a supplier outside {selected} already
          covers it.
        </p>

        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
          <div className="rounded-lg border border-gray-200 p-4">
            <div className="text-2xl font-bold text-gray-900">{affectedSuppliers}</div>
            <div className="text-xs text-gray-500">supplier{affectedSuppliers === 1 ? '' : 's'} affected</div>
          </div>
          <div className={`rounded-lg border p-4 ${result.stranded.length > 0 ? 'border-red-300 bg-red-50' : 'border-gray-200'}`}>
            <div className={`text-2xl font-bold ${result.stranded.length > 0 ? 'text-red-800' : 'text-gray-900'}`}>
              {result.stranded.length}
            </div>
            <div className="text-xs text-gray-500">
              categor{result.stranded.length === 1 ? 'y' : 'ies'} left with no alternative
            </div>
          </div>
          <div className="rounded-lg border border-gray-200 p-4">
            <div className="text-2xl font-bold text-gray-900">{result.covered.length}</div>
            <div className="text-xs text-gray-500">
              categor{result.covered.length === 1 ? 'y' : 'ies'} still covered elsewhere
            </div>
          </div>
        </div>

        {result.stranded.length > 0 ? (
          <div className="rounded-lg border border-red-300 bg-red-50 p-4">
            <h3 className="font-semibold text-red-900">Left with no alternative</h3>
            <ul className="mt-2 space-y-1.5 text-sm text-red-900">
              {result.stranded.map((item) => (
                <li key={item.category}>
                  <span className="font-semibold">{item.category}</span>
                  <span className="text-red-800"> — {whoIsHere(item.suppliers.length)}: {item.suppliers.join(', ')}</span>
                </li>
              ))}
            </ul>
          </div>
        ) : (
          result.covered.length > 0 && (
            <div className="rounded-lg border border-green-300 bg-green-50 p-4 text-sm text-green-900">
              Every category supplied from {selected} also has a supplier somewhere else.
            </div>
          )
        )}

        {result.covered.length > 0 && (
          <div>
            <h3 className="text-sm font-semibold text-gray-900">Still covered elsewhere</h3>
            <ul className="mt-2 divide-y divide-gray-100 text-sm">
              {result.covered.map((item) => (
                <li key={item.category} className="py-2 flex flex-col sm:flex-row sm:items-baseline sm:gap-2">
                  <span className="font-semibold text-gray-900 sm:w-56 shrink-0">{item.category}</span>
                  <span className="text-gray-600">
                    loses {item.lost.join(', ')}; keeps{' '}
                    {item.remaining.map((site) => `${site.name} (${site.country})`).join(', ')}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        )}

        <div>
          <h3 className="text-sm font-semibold text-gray-900">Affected suppliers</h3>
          <ul className="mt-2 divide-y divide-gray-100 text-sm">
            {shown.map((row) => (
              <li key={`${row.name}|${row.category ?? ''}|${row.line}`} className="py-2 flex items-center justify-between gap-3">
                <span className="min-w-0">
                  <span className="font-medium text-gray-900">{row.name}</span>
                  <span className="text-gray-500"> · {row.category ?? 'no category'}</span>
                </span>
                <TierPill tier={row.tier} />
              </li>
            ))}
          </ul>
          {result.affected.length > shown.length && (
            <p className="mt-2 text-xs text-gray-500">
              and {result.affected.length - shown.length} more lines from {selected}.
            </p>
          )}
        </div>
      </div>
    </section>
  );
}
