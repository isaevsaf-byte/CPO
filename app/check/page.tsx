import type { Metadata } from 'next';
import SupplierCheck from '../components/check/SupplierCheck';

export const metadata: Metadata = {
  title: 'Check your own supplier list — Supply Chain Watchtower',
  description:
    'Paste a supplier list and see single-source categories, categories that sit in one country, and what a country outage would take out. Runs entirely in your browser; nothing is uploaded.',
  alternates: { canonical: '/check' },
};

export default function CheckPage() {
  return (
    <div className="min-h-screen bg-slate-50">
      <header className="bg-gradient-to-r from-blue-900 via-blue-800 to-blue-900 text-white shadow-lg overflow-hidden">
        <div className="max-w-6xl mx-auto px-4 sm:px-6 py-6">
          <h1 className="text-2xl sm:text-3xl font-bold">Check your own list</h1>
          <p className="text-blue-100 mt-2 text-sm sm:text-base max-w-3xl">
            Paste your suppliers and see where the supply base is thin: categories with one
            supplier, categories that sit in one country, and what a single country going dark
            would take out.
          </p>
        </div>
      </header>

      <main className="max-w-6xl mx-auto px-4 sm:px-6 py-8">
        <SupplierCheck />
      </main>
    </div>
  );
}
