import type { Metadata } from 'next';
import Dashboard from './components/board/Dashboard';
import SupplierMap, { buildMapCountries } from './components/SupplierMap';
import intel from '../data/intel_snapshot.json';
import countryRisk from '../data/country_risk.json';
import type { IntelSnapshot, WorldSignalItem } from '../types/intel';
import type { SupplierMapOverlay } from '../types/extras-track';

const typedIntel = intel as unknown as IntelSnapshot;

export const metadata: Metadata = { alternates: { canonical: '/' } };

// Chokepoints and the Rhine gauge, when the harvest has read them, become map
// markers coloured by how far they are from normal.
function worldOverlays(items: WorldSignalItem[]): SupplierMapOverlay[] {
  return items.map((item) => ({ id: item.id, label: item.label, severity: item.severity }));
}

// Server component: draws the map at build time and hands it to the
// interactive dashboard, which is a client component.
export default function Page() {
  const suppliers = typedIntel.suppliers?.suppliers ?? [];
  const world = typedIntel.world_signals;
  const overlays = world ? worldOverlays([...world.chokepoints, ...world.rivers]) : [];
  return (
    <Dashboard
      mapSlot={
        <SupplierMap
          countries={buildMapCountries(suppliers, (countryRisk as { countries: Record<string, { level: string; reason: string }> }).countries)}
          overlays={overlays}
        />
      }
    />
  );
}
