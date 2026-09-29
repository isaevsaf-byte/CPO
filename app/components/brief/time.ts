// Time helpers for the brief. Every formatter here reads UTC fields directly
// instead of going through toLocaleString, so the build server, any browser
// and a printout all show the same text.

// Snapshots written before timestamps carried an offset are bare UTC
// date-times, which JavaScript would otherwise read as local time.
export function parseSnapshotTime(iso: string): Date {
  const hasZone = /(?:Z|[+-]\d{2}:?\d{2})$/.test(iso);
  return new Date(hasZone ? iso : `${iso}Z`);
}

// The dashboard's own wording, so "3d ago" means the same thing on both pages.
// Depends on the clock: only call it after mount.
export function relativeAge(date: Date, now: number = Date.now()): string {
  const minutes = (now - date.getTime()) / (1000 * 60);
  if (minutes < 60) return `${Math.max(1, Math.round(minutes))}m ago`;
  const hours = minutes / 60;
  if (hours < 36) return `${Math.round(hours)}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

function pad(value: number): string {
  return String(value).padStart(2, '0');
}

/** "24 Sep" */
export function formatDay(date: Date): string {
  return `${date.getUTCDate()} ${MONTHS[date.getUTCMonth()]}`;
}

/** "24 Sep 2026" */
export function formatDate(date: Date): string {
  return `${formatDay(date)} ${date.getUTCFullYear()}`;
}

/** "24 Sep 2026, 16:04 UTC" */
export function formatDateTime(date: Date): string {
  return `${formatDate(date)}, ${pad(date.getUTCHours())}:${pad(date.getUTCMinutes())} UTC`;
}

/** "7 days", "30 hours", "less than an hour" */
export function formatDuration(hours: number): string {
  if (hours < 1) return 'less than an hour';
  if (hours < 48) {
    const whole = Math.round(hours);
    return `${whole} hour${whole === 1 ? '' : 's'}`;
  }
  return `${Math.round(hours / 24)} days`;
}
