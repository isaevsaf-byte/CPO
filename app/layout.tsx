import type { Metadata } from 'next'
import './globals.css'

const SITE_URL = 'https://cpo-watchtower.co.uk'

export const metadata: Metadata = {
  metadataBase: new URL(SITE_URL),
  title: 'Supply Chain Watchtower — demo risk board',
  description:
    'A demonstration board that reads macro conditions, competitor signals and a supplier watchlist as one picture, refreshed every six hours from public data.',
  alternates: { canonical: '/' },
  openGraph: {
    type: 'website',
    url: SITE_URL,
    siteName: 'Supply Chain Watchtower',
    title: 'Supply Chain Watchtower — demo risk board',
    description:
      'A demonstration board that reads macro conditions, competitor signals and a supplier watchlist as one picture, refreshed every six hours from public data.',
  },
  twitter: {
    card: 'summary',
    title: 'Supply Chain Watchtower — demo risk board',
    description:
      'A demonstration board that reads macro conditions, competitor signals and a supplier watchlist as one picture.',
  },
}

// Sits in the layout rather than on the dashboard page so it appears on every
// route — supplier pages, region pages and the geopolitical page are separate
// URLs and are exactly what gets sent as a link. Rendered on the server, so it
// is in the HTML a visitor (or a crawler) sees before any JavaScript runs.
function SampleDataBanner() {
  return (
    <div className="bg-amber-50 border-b border-amber-200">
      <div className="max-w-[100rem] mx-auto px-4 sm:px-6 py-2 flex flex-wrap items-center gap-x-3 gap-y-1">
        <span className="shrink-0 px-2 py-0.5 rounded bg-amber-200 text-amber-900 text-[11px] font-bold uppercase tracking-wider">
          Sample data
        </span>
        <span className="text-xs text-amber-900/80 leading-snug">
          Demonstration dataset. This board is a working template, not a live client&apos;s supply base.
        </span>
      </div>
    </div>
  )
}

export default function RootLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <html lang="en">
      <body>
        <SampleDataBanner />
        {children}
      </body>
    </html>
  )
}
