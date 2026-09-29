import Link from 'next/link';
import type { Supplier, WorldSignals, WorldSignalItem } from '../../../types/intel';
import { categoryEconomics } from './economics';

// A colour tells a reader something is wrong; it does not tell them what to do
// about it or who should. Paid platforms put an owner and a short playbook
// behind every alert, and that is what a procurement lead acts on. This card
// does the same for whatever is currently flagged on a supplier.

type SignalKind = 'sanctions' | 'cyber' | 'recall' | 'operational' | 'price' | 'geopolitical' | 'world';

const PLAYBOOKS: Record<SignalKind, { title: string; steps: string[] }> = {
  sanctions: {
    title: 'Possible sanctions match',
    steps: [
      'Hold new purchase orders and payments to this supplier until compliance has reviewed the match.',
      'Confirm or rule out the match against the legal entity, registered address and ownership.',
      'If it is confirmed, start the exit plan and move volume to the backup source below.',
    ],
  },
  cyber: {
    title: 'Actively exploited vulnerability',
    steps: [
      "Ask the supplier's security contact whether the affected product is in use and whether it is patched.",
      'Check the connections you share with them: portals, EDI links, remote access.',
      'Record the answer and recheck in a week.',
    ],
  },
  recall: {
    title: 'Product safety recall',
    steps: [
      'Ask for the recall scope and whether any affected lots were shipped to you.',
      'Quarantine any affected lots on hand.',
      'Run a quality review before the next lot is released.',
    ],
  },
  operational: {
    title: 'Operational news',
    steps: [
      'Call the account manager to confirm the impact on your open orders.',
      'Check open purchase orders and stock cover for the category.',
      'Warm up the backup source so volume can move if the answer is bad.',
    ],
  },
  price: {
    title: 'Unexplained share-price fall',
    steps: [
      'Look for a cause: results, guidance, a credit rating change.',
      'If nothing explains it within 48 hours, ask the supplier directly.',
      'Watch payment terms and credit: a sustained fall can come before distress.',
    ],
  },
  geopolitical: {
    title: 'Live escalation in the supplying country',
    steps: [
      "Confirm the site's exposure: its location and the routes your goods take.",
      'Compare stock cover with how long the disruption could last.',
      'Pre-book alternative logistics, or move volume to the backup source.',
    ],
  },
  world: {
    title: 'Route, input or hazard pressure',
    steps: [
      'Ask logistics for current transit times and costs on this route.',
      'Compare stock cover with the expected delay.',
      'Pull orders forward or reroute before the buffer runs down.',
    ],
  },
};

function eventLevel(s: Supplier): string {
  return s.event_risk_level ?? s.risk_level;
}

export function worldSignalsFor(name: string, data?: WorldSignals): WorldSignalItem[] {
  if (!data) return [];
  return [...data.chokepoints, ...data.rivers, ...data.commodities, ...data.hazards].filter(
    (item) => item.severity !== 'quiet' && (item.affected_suppliers ?? []).includes(name),
  );
}

export function signalKinds(s: Supplier, world: WorldSignalItem[]): SignalKind[] {
  const kinds: SignalKind[] = [];
  if (s.sanctions_hit) kinds.push('sanctions');
  if (s.cyber_risk) kinds.push('cyber');
  if (s.recall_risk) kinds.push('recall');
  if ((s.news_risk || s.operational_risk) && eventLevel(s) !== 'LOW') kinds.push('operational');
  if (s.price_move_only) kinds.push('price');
  if (s.geopolitical_risk?.escalated && s.geopolitical_risk.baseline_only === false) kinds.push('geopolitical');
  if (world.length > 0) kinds.push('world');
  return kinds;
}

export default function ActionCard({
  supplier,
  suppliers,
  world,
}: {
  supplier: Supplier;
  suppliers: Supplier[];
  world?: WorldSignals;
}) {
  const worldItems = worldSignalsFor(supplier.name, world);
  const kinds = signalKinds(supplier, worldItems);
  if (kinds.length === 0) return null;

  const econ = categoryEconomics(supplier.category);
  const backups = suppliers.filter((s) => s.category === supplier.category && s.name !== supplier.name);
  const primary = PLAYBOOKS[kinds[0]];

  return (
    <div className="bg-white rounded-lg shadow-sm border-2 border-amber-300 p-6">
      <div className="flex flex-wrap items-baseline justify-between gap-2 border-b border-gray-200 pb-2 mb-4">
        <h2 className="text-lg font-bold text-gray-900">What to do in the next 48 hours</h2>
        <span className="text-xs font-semibold uppercase tracking-wide text-amber-800">{primary.title}</span>
      </div>

      <dl className="grid gap-3 sm:grid-cols-3 mb-4 text-sm">
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">Owner</dt>
          <dd className="text-gray-900">{econ?.owner ?? 'Category manager'}</dd>
        </div>
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">Why it matters</dt>
          <dd className="text-gray-900">{supplier.bat_exposure} tier · {supplier.category}</dd>
        </div>
        {econ && (
          <div>
            <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">Time to survive / recover</dt>
            <dd className="text-gray-900">~{econ.stock_cover_days} days of stock / ~{econ.requalify_days} to qualify a new source</dd>
          </div>
        )}
      </dl>

      {kinds.map((kind) => (
        <div key={kind} className="mb-4 last:mb-0">
          {kinds.length > 1 && <h3 className="text-sm font-semibold text-gray-900 mb-1">{PLAYBOOKS[kind].title}</h3>}
          <ol className="list-decimal pl-5 space-y-1 text-sm text-gray-800">
            {PLAYBOOKS[kind].steps.map((step) => <li key={step}>{step}</li>)}
          </ol>
          {kind === 'world' && (
            <ul className="mt-2 space-y-1 text-xs text-gray-600">
              {worldItems.map((item) => <li key={item.id}>• {item.headline}</li>)}
            </ul>
          )}
        </div>
      ))}

      <div className="mt-4 rounded-lg bg-gray-50 px-3 py-2 text-sm">
        <span className="font-semibold text-gray-900">Backup source: </span>
        {backups.length > 0 ? (
          backups.map((b, i) => (
            <span key={b.name}>
              {i > 0 && ', '}
              <Link href={`/details/${encodeURIComponent(b.name)}`} className="text-blue-800 hover:underline">{b.name}</Link>
              <span className="text-gray-500"> ({b.location})</span>
            </span>
          ))
        ) : (
          <span className="text-red-800">
            none on the watchlist{econ ? ` — qualifying one takes about ${econ.requalify_days} days` : ''}.
          </span>
        )}
      </div>
      <p className="mt-2 text-[11px] text-gray-500">Owners, stock cover and qualification times are illustrative for this demonstration.</p>
    </div>
  );
}
