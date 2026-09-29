'use client';

import { useMemo, useState } from 'react';
import Link from 'next/link';
import type { Supplier } from '../../../types/intel';
import { SCENARIOS, assessScenario, formatMillions } from './economics';

// "What if" in the terms a procurement lead acts on: which suppliers, how much
// supply, and on which day the line runs short — time to survive against time
// to recover, rather than a colour.

const COUNTRY_STOP_DAYS = 30;

type Choice = { kind: 'preset'; id: string } | { kind: 'country'; country: string };

export default function Scenarios({ suppliers }: { suppliers: Supplier[] }) {
  const countries = useMemo(
    () => Array.from(new Set(suppliers.map((s) => s.location))).sort(),
    [suppliers],
  );
  const [choice, setChoice] = useState<Choice>({ kind: 'preset', id: SCENARIOS[0]?.id ?? '' });

  const { title, what, duration, loss } = useMemo(() => {
    if (choice.kind === 'country') {
      const lossMap = new Map<string, number>();
      suppliers.filter((s) => s.location === choice.country).forEach((s) => lossMap.set(s.name, 1));
      return {
        title: `Supply from ${choice.country} stops for ${COUNTRY_STOP_DAYS} days`,
        what: 'Export controls, a border closure or a blockade: nothing ships from any supplying site in that country.',
        duration: COUNTRY_STOP_DAYS,
        loss: lossMap,
      };
    }
    const scenario = SCENARIOS.find((s) => s.id === choice.id) ?? SCENARIOS[0];
    const lossMap = new Map<string, number>();
    scenario?.suppliers.forEach((name) => lossMap.set(name, scenario.supply_loss));
    return {
      title: scenario?.title ?? '',
      what: scenario?.what ?? '',
      duration: scenario?.duration_days ?? 0,
      loss: lossMap,
    };
  }, [choice, suppliers]);

  const impact = useMemo(() => assessScenario(suppliers, loss, duration), [suppliers, loss, duration]);
  const worst = impact.categories[0];

  return (
    <section className="mb-8 rounded-xl border border-gray-200 bg-white shadow-sm overflow-hidden" aria-labelledby="whatif-heading">
      <div className="border-b border-gray-200 bg-gray-50 px-6 py-4">
        <h2 id="whatif-heading" className="text-lg font-bold text-gray-900">What if</h2>
        <p className="mt-1 text-sm text-gray-600">
          Pick a disruption and see which suppliers it touches, how much supply goes with them, and when stock runs out.
        </p>
        <div className="mt-3 flex flex-wrap items-center gap-2" role="group" aria-label="Scenario">
          {SCENARIOS.map((s) => {
            const active = choice.kind === 'preset' && choice.id === s.id;
            return (
              <button
                key={s.id}
                type="button"
                onClick={() => setChoice({ kind: 'preset', id: s.id })}
                aria-pressed={active}
                className={`rounded-full border px-3 py-1 text-xs font-medium transition-colors ${
                  active ? 'border-blue-800 bg-blue-800 text-white' : 'border-gray-300 bg-white text-gray-700 hover:border-gray-400'
                }`}
              >
                {s.title}
              </button>
            );
          })}
          <label className="flex items-center gap-2 text-xs text-gray-600">
            <span>or a country stops:</span>
            <select
              id="whatif-country"
              value={choice.kind === 'country' ? choice.country : ''}
              onChange={(e) => e.target.value && setChoice({ kind: 'country', country: e.target.value })}
              className="rounded border border-gray-300 bg-white px-2 py-1 text-xs text-gray-800"
            >
              <option value="">Choose…</option>
              {countries.map((c) => (
                <option key={c} value={c}>{c}</option>
              ))}
            </select>
          </label>
        </div>
      </div>

      <div className="px-6 py-4">
        <h3 className="text-base font-semibold text-gray-900">{title}</h3>
        <p className="mt-0.5 text-sm text-gray-600">{what}</p>

        {impact.categories.length === 0 ? (
          <p className="mt-3 text-sm text-gray-500">No supplier on the watchlist is affected.</p>
        ) : (
          <>
            <dl className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
              <div>
                <dt className="text-[11px] font-semibold uppercase tracking-wide text-gray-500">Suppliers hit</dt>
                <dd className="font-mono text-xl font-semibold text-gray-900">{impact.affectedCount}</dd>
              </div>
              <div>
                <dt className="text-[11px] font-semibold uppercase tracking-wide text-gray-500">Supply disrupted</dt>
                <dd className="font-mono text-xl font-semibold text-gray-900">{formatMillions(impact.spendAtRiskM)}</dd>
              </div>
              <div>
                <dt className="text-[11px] font-semibold uppercase tracking-wide text-gray-500">First shortfall</dt>
                <dd className={`font-mono text-xl font-semibold ${impact.earliestShortfallDay != null ? 'text-red-700' : 'text-green-700'}`}>
                  {impact.earliestShortfallDay != null ? `day ${impact.earliestShortfallDay}` : 'none'}
                </dd>
              </div>
              <div>
                <dt className="text-[11px] font-semibold uppercase tracking-wide text-gray-500">No alternative</dt>
                <dd className="font-mono text-xl font-semibold text-gray-900">
                  {impact.categories.filter((c) => c.noAlternative).length}
                  <span className="ml-1 font-sans text-xs font-normal text-gray-500">categories</span>
                </dd>
              </div>
            </dl>

            <div className="mt-4 overflow-x-auto">
              <table className="w-full min-w-[640px] text-sm">
                <thead>
                  <tr className="border-b border-gray-200 text-left text-[11px] uppercase tracking-wide text-gray-500">
                    <th className="py-2 pr-3 font-semibold">Category</th>
                    <th className="py-2 pr-3 font-semibold">Suppliers hit</th>
                    <th className="py-2 pr-3 font-semibold">Supply lost</th>
                    <th className="py-2 pr-3 font-semibold">Stock lasts</th>
                    <th className="py-2 pr-3 font-semibold">Short for</th>
                    <th className="py-2 font-semibold">Owner</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {impact.categories.map((c) => {
                    const lasts = c.stockCoverDays != null && c.lostShare > 0 ? Math.round(c.stockCoverDays / c.lostShare) : null;
                    return (
                      <tr key={c.category}>
                        <td className="py-2 pr-3 align-top">
                          <div className="font-medium text-gray-900">{c.category}</div>
                          {c.noAlternative && <div className="text-xs font-semibold text-red-700">No other source on the list</div>}
                        </td>
                        <td className="py-2 pr-3 align-top">
                          {c.affected.map((s, i) => (
                            <span key={s.name}>
                              {i > 0 && ', '}
                              <Link href={`/details/${encodeURIComponent(s.name)}`} className="text-blue-800 hover:underline">{s.name}</Link>
                            </span>
                          ))}
                        </td>
                        <td className="py-2 pr-3 align-top font-mono">{Math.round(c.lostShare * 100)}%</td>
                        <td className="py-2 pr-3 align-top font-mono">{lasts != null ? `${lasts} days` : '—'}</td>
                        <td className={`py-2 pr-3 align-top font-mono ${c.shortfallDays > 0 ? 'font-semibold text-red-700' : 'text-green-700'}`}>
                          {c.shortfallDays > 0 ? `${c.shortfallDays} days` : 'covered'}
                        </td>
                        <td className="py-2 align-top text-xs text-gray-600">{c.owner ?? '—'}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>

            {worst && worst.shortfallDays > 0 && (
              <p className="mt-3 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-900">
                {worst.category} runs short first. Stock covers about {Math.round((worst.stockCoverDays ?? 0) / worst.lostShare)} days
                against a {duration}-day disruption, and qualifying a new source takes about {worst.requalifyDays} days, so the
                decision on buffer stock or a second source has to be made before the event, not during it.
              </p>
            )}
          </>
        )}
        <p className="mt-3 text-[11px] text-gray-500">
          Spend, stock cover and qualification times are illustrative figures set for this demonstration.
        </p>
      </div>
    </section>
  );
}
