'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useEffect, useState } from 'react';

interface NavItem {
  href: string;
  label: string;
  /** Other path prefixes that belong to this item, e.g. supplier pages
   *  opened from the board. */
  also?: string[];
}

const NAV_ITEMS: NavItem[] = [
  { href: '/', label: 'Board', also: ['/details', '/macro'] },
  { href: '/brief', label: 'Brief' },
  { href: '/track-record', label: 'Track record' },
  { href: '/geopolitical', label: 'Geopolitical' },
];

function matches(pathname: string, base: string): boolean {
  if (base === '/') return pathname === '/';
  return pathname === base || pathname.startsWith(`${base}/`);
}

function isActive(pathname: string, item: NavItem): boolean {
  return matches(pathname, item.href) || (item.also ?? []).some((prefix) => matches(pathname, prefix));
}

// Mounted once in the root layout. Sticky and one line tall on a wide screen;
// on a phone it collapses to the current section's name and a menu button.
// Hidden when printing, so the brief prints as a clean page.
export default function SiteNav() {
  const pathname = usePathname() ?? '/';
  const [open, setOpen] = useState(false);
  const current = NAV_ITEMS.find((item) => isActive(pathname, item));

  // Close the phone menu whenever the route changes.
  useEffect(() => {
    setOpen(false);
  }, [pathname]);

  const linkClass = (active: boolean) =>
    `block rounded-md px-3 py-1.5 text-sm font-medium transition-colors ${
      active ? 'bg-blue-50 text-blue-900 font-semibold' : 'text-gray-600 hover:bg-gray-50 hover:text-gray-900'
    }`;

  return (
    <nav aria-label="Site" className="sticky top-0 z-40 border-b border-gray-200 bg-white/95 backdrop-blur print:hidden">
      <div className="max-w-[100rem] mx-auto px-4 sm:px-6">
        <div className="flex h-12 items-center justify-between gap-4">
          <Link href="/" className="shrink-0 text-sm font-bold tracking-tight text-blue-900">
            <span className="hidden lg:inline">Supply Chain </span>Watchtower
          </Link>

          <ul className="hidden md:flex items-center gap-0.5 lg:gap-1">
            {NAV_ITEMS.map((item) => {
              const active = isActive(pathname, item);
              return (
                <li key={item.href}>
                  <Link href={item.href} aria-current={active ? 'page' : undefined} className={linkClass(active)}>
                    {item.label}
                  </Link>
                </li>
              );
            })}
          </ul>

          <button
            type="button"
            onClick={() => setOpen((value) => !value)}
            aria-expanded={open}
            aria-controls="site-nav-menu"
            className="md:hidden inline-flex items-center gap-1.5 rounded-md border border-gray-300 px-3 py-1.5 text-sm font-semibold text-gray-800 hover:bg-gray-50"
          >
            {current?.label ?? 'Menu'}
            <svg
              className={`h-4 w-4 transition-transform ${open ? 'rotate-180' : ''}`}
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
              aria-hidden="true"
            >
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
            </svg>
            <span className="sr-only">{open ? 'Close menu' : 'Open menu'}</span>
          </button>
        </div>

        <ul id="site-nav-menu" className={`md:hidden gap-1 pb-3 ${open ? 'grid' : 'hidden'}`}>
          {NAV_ITEMS.map((item) => {
            const active = isActive(pathname, item);
            return (
              <li key={item.href}>
                <Link
                  href={item.href}
                  aria-current={active ? 'page' : undefined}
                  onClick={() => setOpen(false)}
                  className={linkClass(active)}
                >
                  {item.label}
                </Link>
              </li>
            );
          })}
        </ul>
      </div>
    </nav>
  );
}
