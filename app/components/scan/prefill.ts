import type { ScanPrefillRow } from '../../../types/extras';
import { MAX_SCAN_ROWS } from './compose';

// The hand-off from /check to /scan. sessionStorage keeps it inside the one
// browser tab, it is written only when the visitor clicks, and /scan deletes
// it as soon as it has been read — so "nothing leaves your browser" on /check
// stays true.
const PREFILL_KEY = 'watchtower:scan-prefill';

export function stashScanPrefill(rows: ScanPrefillRow[]): boolean {
  try {
    window.sessionStorage.setItem(PREFILL_KEY, JSON.stringify(rows.slice(0, MAX_SCAN_ROWS)));
    return true;
  } catch {
    return false;
  }
}

export function takeScanPrefill(): ScanPrefillRow[] | null {
  try {
    const raw = window.sessionStorage.getItem(PREFILL_KEY);
    if (!raw) return null;
    window.sessionStorage.removeItem(PREFILL_KEY);

    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return null;
    const rows = parsed
      .filter(
        (item): item is ScanPrefillRow =>
          !!item &&
          typeof item === 'object' &&
          typeof (item as ScanPrefillRow).name === 'string' &&
          typeof (item as ScanPrefillRow).country === 'string',
      )
      .filter((item) => item.name.trim() !== '')
      .slice(0, MAX_SCAN_ROWS);
    return rows.length > 0 ? rows : null;
  } catch {
    return null;
  }
}
