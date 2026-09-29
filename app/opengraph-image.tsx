import { ImageResponse } from 'next/og';
import intel from '../data/intel_snapshot.json';
import type { IntelSnapshot, RAGScore } from '../types/intel';

// The card a link to the board unfurls into. Rendered at build time from the
// snapshot the deploy was built with, and every harvest commit redeploys, so
// the status in a freshly shared link is at most one harvest old.

export const alt =
  'Supply Chain Watchtower: the overall status of the demo supplier-risk board, how many suppliers it watches, how many alerts are open, and when it last updated.';
export const size = { width: 1200, height: 630 };
export const contentType = 'image/png';

const snapshot = intel as unknown as IntelSnapshot;

const STATUS: Record<RAGScore, { colour: string; label: string }> = {
  GREEN: { colour: '#22c55e', label: 'All clear' },
  AMBER: { colour: '#f59e0b', label: 'Monitor closely' },
  RED: { colour: '#ef4444', label: 'Action needed' },
  UNKNOWN: { colour: '#94a3b8', label: 'Status unknown' },
};

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

function updatedLabel(iso: string | undefined): string {
  if (!iso) return 'Update time unknown';
  const d = new Date(/(?:Z|[+-]\d{2}:?\d{2})$/.test(iso) ? iso : `${iso}Z`);
  if (Number.isNaN(d.getTime())) return 'Update time unknown';
  const pad = (n: number) => String(n).padStart(2, '0');
  return `Updated ${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]} ${d.getUTCFullYear()}, ${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())} UTC`;
}

// An alert is a supplier with something that happened to it this cycle: an
// event level above LOW, or a sanctions match. The standing country floor is
// not an alert, the same rule the board's own counts follow.
function counts() {
  const suppliers = snapshot.suppliers?.suppliers ?? [];
  const alerts = suppliers.filter(
    (s) => (s.event_risk_level ?? s.risk_level ?? 'LOW') !== 'LOW' || s.sanctions_hit
  ).length;
  return { suppliers: suppliers.length, alerts };
}

// Inter from Google Fonts, subset to the characters on the card. If the fetch
// fails the card still renders, in the default sans.
async function loadFont(weight: number, text: string): Promise<ArrayBuffer | null> {
  try {
    const css = await fetch(
      `https://fonts.googleapis.com/css2?family=Inter:wght@${weight}&text=${encodeURIComponent(text)}`,
      // An older user agent gets TrueType back, which the renderer reads.
      { headers: { 'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_6_8) AppleWebKit/533.21.1 (KHTML, like Gecko) Version/5.0.5 Safari/533.21.1' } }
    ).then((r) => (r.ok ? r.text() : ''));
    const url = css.match(/src: url\((.+?)\) format\('(?:opentype|truetype)'\)/)?.[1];
    if (!url) return null;
    const font = await fetch(url);
    return font.ok ? await font.arrayBuffer() : null;
  } catch {
    return null;
  }
}

function BeaconMark() {
  return (
    <svg width="76" height="76" viewBox="0 0 64 64">
      <rect width="64" height="64" rx="14" fill="#0f172a" fillOpacity="0.35" />
      <path d="M32 17 L7 9 L7 25 Z" fill="#fbbf24" fillOpacity="0.55" />
      <path d="M32 17 L57 9 L57 25 Z" fill="#fbbf24" fillOpacity="0.55" />
      <path d="M26 11 H38 L36.5 23 H27.5 Z" fill="#fbbf24" />
      <path d="M23 10 H41 L32 4 Z" fill="#ffffff" />
      <path d="M27 25 H37 L41 56 H23 Z" fill="#ffffff" />
      <rect x="20" y="55" width="24" height="4" rx="1.5" fill="#ffffff" />
    </svg>
  );
}

export default async function OpengraphImage() {
  const status = STATUS[snapshot.overall_rag?.score ?? 'UNKNOWN'] ?? STATUS.UNKNOWN;
  const { suppliers, alerts } = counts();
  const title = 'Supply Chain Watchtower';
  const subtitle = 'Demo supplier-risk board for procurement leaders';
  const stat = `${suppliers} suppliers · ${alerts} alert${alerts === 1 ? '' : 's'}`;
  const updated = updatedLabel(snapshot.last_updated);
  const site = 'cpo-watchtower.co.uk';

  const glyphs = `${title}${subtitle}OVERALL STATUS${status.label}${stat}${updated}${site}0123456789`;
  const [regular, bold] = await Promise.all([loadFont(400, glyphs), loadFont(700, glyphs)]);
  const fonts =
    regular && bold
      ? [
          { name: 'Inter', data: regular, weight: 400 as const, style: 'normal' as const },
          { name: 'Inter', data: bold, weight: 700 as const, style: 'normal' as const },
        ]
      : undefined;

  return new ImageResponse(
    (
      <div
        style={{
          width: '100%',
          height: '100%',
          display: 'flex',
          flexDirection: 'column',
          justifyContent: 'space-between',
          padding: '60px 72px 52px',
          background: 'linear-gradient(135deg, #172554 0%, #1e3a8a 55%, #1e40af 100%)',
          color: '#ffffff',
          fontFamily: fonts ? 'Inter' : undefined,
          borderTop: `16px solid ${status.colour}`,
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 26 }}>
          <BeaconMark />
          <div style={{ display: 'flex', flexDirection: 'column' }}>
            <div style={{ fontSize: 56, fontWeight: 700, letterSpacing: -1.5 }}>{title}</div>
            <div style={{ fontSize: 27, color: '#bfdbfe', marginTop: 4 }}>{subtitle}</div>
          </div>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: 44 }}>
          <div
            style={{
              display: 'flex',
              width: 150,
              height: 150,
              marginLeft: 34,
              borderRadius: 75,
              background: status.colour,
              boxShadow: `0 0 0 16px ${status.colour}40, 0 0 0 34px ${status.colour}1f`,
            }}
          />
          <div style={{ display: 'flex', flexDirection: 'column' }}>
            <div style={{ fontSize: 24, letterSpacing: 4, color: '#bfdbfe' }}>OVERALL STATUS</div>
            <div style={{ fontSize: 80, fontWeight: 700, letterSpacing: -2, lineHeight: 1.1 }}>{status.label}</div>
            <div style={{ fontSize: 36, color: '#dbeafe', marginTop: 6 }}>{stat}</div>
          </div>
        </div>

        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 24, color: '#bfdbfe' }}>
          <div>{updated}</div>
          <div>{site}</div>
        </div>
      </div>
    ),
    { ...size, fonts }
  );
}
