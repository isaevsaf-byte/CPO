// Custom events for Vercel Web Analytics. Loaded lazily so the board still
// renders if the package or the analytics endpoint is unavailable, and every
// call is best-effort: a lost event must never break a click.
type Props = Record<string, string | number | boolean | null>;

export function trackEvent(name: string, props?: Props): void {
  if (typeof window === 'undefined') return;
  import('@vercel/analytics')
    .then((mod) => mod.track(name, props))
    .catch(() => undefined);
}
