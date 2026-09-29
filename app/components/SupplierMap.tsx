// Where the watchlist's suppliers are, as a static SVG world map.
//
// A server component: the projection, the land outline and the pin layout are
// all computed at build time with d3-geo, and the browser receives plain SVG
// with no map library and no script. Mount it from a server component (a
// page without 'use client'); a client page can render it by receiving it as
// a prop or as children from a server parent.

import { geoEqualEarth, geoPath } from 'd3-geo';
import { feature } from 'topojson-client';
import type { GeometryCollection, Topology } from 'topojson-specification';
import landTopology from 'world-atlas/land-110m.json';
import type {
  OverlaySeverity,
  SupplierMapCountry,
  SupplierMapOverlay,
  SupplierMapProps,
  SupplierMapSupplier,
} from '../../types/extras-track';
import type { Supplier } from '../../types/intel';

type Pt = [number, number];

// ============================================================================
// Land outline — computed once per server process
// ============================================================================

const WIDTH = 960;
// Douglas–Peucker tolerance in viewBox pixels, and the smallest island kept.
// At 960 wide this halves the Natural Earth 1:110m outline without a visible
// change; coordinates are then rounded to one decimal and written relative.
const SIMPLIFY_PX = 0.9;
const MIN_RING_AREA_PX = 6;

function ringArea(ring: Pt[]): number {
  let sum = 0;
  for (let i = 0; i < ring.length; i++) {
    const [ax, ay] = ring[i];
    const [bx, by] = ring[(i + 1) % ring.length];
    sum += ax * by - bx * ay;
  }
  return sum / 2;
}

function simplify(points: Pt[], tolerance: number): Pt[] {
  if (points.length <= 3) return points;
  const keep = new Uint8Array(points.length);
  keep[0] = 1;
  keep[points.length - 1] = 1;
  const stack: [number, number][] = [[0, points.length - 1]];
  while (stack.length) {
    const [a, b] = stack.pop()!;
    const [ax, ay] = points[a];
    const [bx, by] = points[b];
    const dx = bx - ax;
    const dy = by - ay;
    const length = Math.hypot(dx, dy);
    let worst = 0;
    let worstIdx = -1;
    for (let i = a + 1; i < b; i++) {
      const [px, py] = points[i];
      const distance =
        length === 0
          ? Math.hypot(px - ax, py - ay)
          : Math.abs(dy * px - dx * py + bx * ay - by * ax) / length;
      if (distance > worst) {
        worst = distance;
        worstIdx = i;
      }
    }
    if (worst > tolerance && worstIdx > 0) {
      keep[worstIdx] = 1;
      stack.push([a, worstIdx], [worstIdx, b]);
    }
  }
  return points.filter((_, i) => keep[i] === 1);
}

const round1 = (v: number) => Math.round(v * 10) / 10;

// "M12.3 45.6l1.2-.8 .5 1z": absolute start, then deltas between the rounded
// absolute points, so rounding never accumulates along a coastline.
function ringToPath(ring: Pt[]): string {
  let out = '';
  let prevX = 0;
  let prevY = 0;
  const num = (v: number) => {
    const s = String(round1(v)).replace(/^(-?)0\./, '$1.');
    // A minus sign separates numbers on its own; anything else needs a space.
    out += s.startsWith('-') || /[Mlz]$/.test(out) ? s : ` ${s}`;
  };
  ring.forEach(([x, y], i) => {
    const rx = round1(x);
    const ry = round1(y);
    if (i === 0) {
      out += 'M';
      num(rx);
      num(ry);
    } else {
      if (i === 1) out += 'l';
      num(rx - prevX);
      num(ry - prevY);
    }
    prevX = rx;
    prevY = ry;
  });
  return `${out}z`;
}

function buildLand() {
  const topology = landTopology as unknown as Topology<{ land: GeometryCollection }>;
  const collection = feature(topology, topology.objects.land);
  const polygons: GeoJSON.Position[][][] = [];
  for (const f of collection.features) {
    if (f.geometry.type === 'Polygon') polygons.push(f.geometry.coordinates);
    else if (f.geometry.type === 'MultiPolygon') polygons.push(...f.geometry.coordinates);
  }
  // Antarctica holds no suppliers and would take a fifth of the height.
  const kept = polygons.filter((polygon) => !polygon[0].every(([, lat]) => lat < -55));
  const land: GeoJSON.Feature<GeoJSON.MultiPolygon> = {
    type: 'Feature',
    properties: {},
    geometry: { type: 'MultiPolygon', coordinates: kept },
  };

  const projection = geoEqualEarth().fitWidth(WIDTH, land);
  const [[, top], [, bottom]] = geoPath(projection).bounds(land);
  const height = Math.ceil(bottom - top);

  const rings: Pt[][] = [];
  let current: Pt[] | null = null;
  geoPath(projection, {
    beginPath() {},
    moveTo(x: number, y: number) {
      current = [[x, y]];
      rings.push(current);
    },
    lineTo(x: number, y: number) {
      current?.push([x, y]);
    },
    closePath() {
      current = null;
    },
    arc() {},
  })(land);

  const d = rings
    .map((ring) => simplify(ring, SIMPLIFY_PX))
    .filter((ring) => ring.length >= 3 && Math.abs(ringArea(ring)) >= MIN_RING_AREA_PX)
    .map(ringToPath)
    .join('');

  const project = (lonLat: Pt): Pt | null => {
    const p = projection(lonLat);
    return p ? [p[0], p[1] - top] : null;
  };
  return { d, height, project };
}

const LAND = buildLand();
const HEIGHT = LAND.height;

// ============================================================================
// Where things go
// ============================================================================

// A representative point per country, [longitude, latitude]: the watchlist's
// countries plus a few a supplier is likely to be added in. `short` labels the
// pin; `code` is the fallback when the name will not fit.
const COUNTRY_POINTS: Record<string, { lonLat: Pt; short: string; code: string }> = {
  USA: { lonLat: [-98.5, 39.5], short: 'USA', code: 'US' },
  China: { lonLat: [104.5, 34.5], short: 'China', code: 'CN' },
  Germany: { lonLat: [10.4, 51.1], short: 'Germany', code: 'DE' },
  Switzerland: { lonLat: [8.2, 46.8], short: 'Switzerland', code: 'CH' },
  Austria: { lonLat: [14.6, 47.6], short: 'Austria', code: 'AT' },
  Finland: { lonLat: [26.0, 63.0], short: 'Finland', code: 'FI' },
  Sweden: { lonLat: [15.5, 61.0], short: 'Sweden', code: 'SE' },
  Japan: { lonLat: [138.5, 36.5], short: 'Japan', code: 'JP' },
  'South Korea': { lonLat: [127.9, 36.4], short: 'South Korea', code: 'KR' },
  India: { lonLat: [79.0, 22.5], short: 'India', code: 'IN' },
  'South Africa': { lonLat: [24.5, -29.0], short: 'South Africa', code: 'ZA' },
  Netherlands: { lonLat: [5.3, 52.2], short: 'Netherlands', code: 'NL' },
  'United Kingdom': { lonLat: [-2.0, 53.0], short: 'UK', code: 'GB' },
  France: { lonLat: [2.4, 46.6], short: 'France', code: 'FR' },
  Italy: { lonLat: [12.5, 42.8], short: 'Italy', code: 'IT' },
  Poland: { lonLat: [19.4, 52.0], short: 'Poland', code: 'PL' },
  Taiwan: { lonLat: [121.0, 23.7], short: 'Taiwan', code: 'TW' },
  Vietnam: { lonLat: [106.0, 16.0], short: 'Vietnam', code: 'VN' },
  Indonesia: { lonLat: [113.0, -2.0], short: 'Indonesia', code: 'ID' },
  Brazil: { lonLat: [-51.0, -10.0], short: 'Brazil', code: 'BR' },
  Mexico: { lonLat: [-102.0, 23.5], short: 'Mexico', code: 'MX' },
};

const COUNTRY_ALIASES: Record<string, string> = {
  'United States': 'USA',
  'United States of America': 'USA',
  US: 'USA',
  UK: 'United Kingdom',
  'Great Britain': 'United Kingdom',
  'Republic of Korea': 'South Korea',
  Korea: 'South Korea',
};

/** Overlay ids the map can place, with the name each is known by. */
export const MAP_OVERLAY_POINTS: Record<string, { lonLat: Pt; name: string }> = {
  hormuz: { lonLat: [56.4, 26.5], name: 'Strait of Hormuz' },
  'bab-el-mandeb': { lonLat: [43.4, 12.6], name: 'Bab el-Mandeb' },
  suez: { lonLat: [32.35, 30.6], name: 'Suez Canal' },
  malacca: { lonLat: [100.8, 2.9], name: 'Strait of Malacca' },
  'taiwan-strait': { lonLat: [119.8, 24.3], name: 'Taiwan Strait' },
  panama: { lonLat: [-79.7, 9.1], name: 'Panama Canal' },
  'rhine-kaub': { lonLat: [7.77, 50.08], name: 'Rhine at Kaub' },
};

const OVERLAY_ALIASES: Record<string, string> = {
  'strait-of-hormuz': 'hormuz',
  babelmandeb: 'bab-el-mandeb',
  'red-sea': 'bab-el-mandeb',
  'suez-canal': 'suez',
  'strait-of-malacca': 'malacca',
  taiwan: 'taiwan-strait',
  'panama-canal': 'panama',
  kaub: 'rhine-kaub',
  rhine: 'rhine-kaub',
};

// Lookups keyed by caller-supplied names, so only the table's own entries
// count: "constructor" is not a country.
function own<T>(table: Record<string, T>, key: string): T | null {
  return Object.prototype.hasOwnProperty.call(table, key) ? table[key] : null;
}

function overlayKey(id: string): string {
  const slug = id.trim().toLowerCase().replace(/[\s_]+/g, '-');
  return own(OVERLAY_ALIASES, slug) ?? slug;
}

// ============================================================================
// Colour and size
// ============================================================================

const LEVEL_ORDER = ['LOW', 'MEDIUM', 'HIGH', 'CRITICAL'];

// Fill and the count printed on it. Text colours keep at least 4.5:1 contrast.
const LEVEL_STYLE: Record<string, { fill: string; text: string; label: string }> = {
  CRITICAL: { fill: '#991b1b', text: '#ffffff', label: 'Critical' },
  HIGH: { fill: '#dc2626', text: '#ffffff', label: 'High' },
  MEDIUM: { fill: '#fbbf24', text: '#451a03', label: 'Medium' },
  LOW: { fill: '#15803d', text: '#ffffff', label: 'Low' },
  UNKNOWN: { fill: '#9ca3af', text: '#111827', label: 'No reading' },
};

const SEVERITY_STYLE: Record<OverlaySeverity, { fill: string; label: string }> = {
  quiet: { fill: '#94a3b8', label: 'Quiet' },
  notable: { fill: '#f59e0b', label: 'Notable' },
  severe: { fill: '#dc2626', label: 'Severe' },
};

const severityStyle = (severity: string) => own(SEVERITY_STYLE, severity) ?? SEVERITY_STYLE.quiet;

const RING_COLOUR = '#9ca3af';
// The whole scale, worst first, even when every pin is green: the reader
// should know what red would mean before the day it appears.
const LEGEND_LEVELS = ['CRITICAL', 'HIGH', 'MEDIUM', 'LOW'];

function worstLevel(suppliers: SupplierMapSupplier[]): string {
  let worst = -1;
  for (const s of suppliers) worst = Math.max(worst, LEVEL_ORDER.indexOf((s.level || '').toUpperCase()));
  return worst < 0 ? 'UNKNOWN' : LEVEL_ORDER[worst];
}

// Area grows with the number of suppliers, so the radius grows with its root.
const pinRadius = (count: number) => 3 + 3.2 * Math.sqrt(Math.max(1, count));
const RING_GAP = 3.2;
const MARKER_R = 5;

// ============================================================================
// Layout: keep pins apart, then find room for labels
// ============================================================================

interface Body {
  x: number;
  y: number;
  ax: number;
  ay: number;
  r: number;
  fixed: boolean;
}

// Pins in neighbouring countries (Switzerland, Germany and Austria sit within
// a few pixels of each other) are pushed apart until none overlap, with a weak
// pull back to their true position. Deterministic, so every build lays the map
// out the same way.
function separate(bodies: Body[]) {
  const GAP = 1.5;
  const pass = () => {
    let moved = false;
    for (let i = 0; i < bodies.length; i++) {
      for (let j = i + 1; j < bodies.length; j++) {
        const a = bodies[i];
        const b = bodies[j];
        if (a.fixed && b.fixed) continue;
        let dx = b.x - a.x;
        let dy = b.y - a.y;
        let dist = Math.hypot(dx, dy);
        const min = a.r + b.r + GAP;
        if (dist >= min) continue;
        if (dist < 0.01) {
          dx = 0.6;
          dy = -0.8;
          dist = 1;
        }
        const push = (min - dist) / dist;
        if (a.fixed) {
          b.x += dx * push;
          b.y += dy * push;
        } else if (b.fixed) {
          a.x -= dx * push;
          a.y -= dy * push;
        } else {
          a.x -= (dx * push) / 2;
          a.y -= (dy * push) / 2;
          b.x += (dx * push) / 2;
          b.y += (dy * push) / 2;
        }
        moved = true;
      }
    }
    return moved;
  };
  for (let it = 0; it < 120; it++) {
    const moved = pass();
    for (const body of bodies) {
      if (body.fixed) continue;
      body.x += (body.ax - body.x) * 0.04;
      body.y += (body.ay - body.y) * 0.04;
    }
    if (!moved && it > 10) break;
  }
  for (let it = 0; it < 60 && pass(); it++);
}

interface Box {
  x0: number;
  y0: number;
  x1: number;
  y1: number;
}

interface PlacedLabel {
  x: number;
  y: number;
  anchor: 'start' | 'middle' | 'end';
  text: string;
}

const LABEL_SIZE = { pin: 10, overlay: 9 };

// System UI fonts average a little over half an em per character.
const textWidth = (text: string, size: number) => text.length * size * 0.56 + 2;

function circleHitsBox(cx: number, cy: number, r: number, box: Box): boolean {
  const nx = Math.max(box.x0, Math.min(cx, box.x1));
  const ny = Math.max(box.y0, Math.min(cy, box.y1));
  return (cx - nx) ** 2 + (cy - ny) ** 2 < r * r;
}

const boxesOverlap = (a: Box, b: Box) => a.x0 < b.x1 && b.x0 < a.x1 && a.y0 < b.y1 && b.y0 < a.y1;

function placeLabel(
  x: number,
  y: number,
  r: number,
  texts: string[],
  size: number,
  obstacles: { x: number; y: number; r: number }[],
  taken: Box[]
): PlacedLabel | null {
  const h = size + 1;
  for (const text of texts) {
    const w = textWidth(text, size);
    // Right, left, above, below, then the diagonals; first close to the
    // mark, then a step further out for crowded spots.
    const candidates: { box: Box; label: PlacedLabel }[] = [];
    for (const off of [r + 3, r + 11]) {
      const d = off * 0.7;
      const spots: [number, number, PlacedLabel['anchor']][] = [
        [x + off, y - h / 2, 'start'],
        [x - off - w, y - h / 2, 'end'],
        [x - w / 2, y - off - h, 'middle'],
        [x - w / 2, y + off, 'middle'],
        [x + d, y - d - h, 'start'],
        [x + d, y + d, 'start'],
        [x - d - w, y - d - h, 'end'],
        [x - d - w, y + d, 'end'],
      ];
      for (const [bx, by, anchor] of spots) {
        const box = { x0: bx, y0: by, x1: bx + w, y1: by + h };
        const tx = anchor === 'start' ? box.x0 : anchor === 'end' ? box.x1 : box.x0 + w / 2;
        candidates.push({ box, label: { x: tx, y: box.y0 + h * 0.78, anchor, text } });
      }
    }
    for (const { box, label } of candidates) {
      if (box.x0 < 2 || box.y0 < 2 || box.x1 > WIDTH - 2 || box.y1 > HEIGHT - 2) continue;
      if (obstacles.some((o) => circleHitsBox(o.x, o.y, o.r, box))) continue;
      if (taken.some((t) => boxesOverlap(t, box))) continue;
      taken.push(box);
      return label;
    }
  }
  return null;
}

// ============================================================================
// Data helpers for callers
// ============================================================================

function titleCase(level: string): string {
  return level ? level.charAt(0).toUpperCase() + level.slice(1).toLowerCase() : level;
}

/**
 * The map's `countries` prop from the snapshot's supplier list and
 * data/country_risk.json:
 *
 *   buildMapCountries(intel.suppliers.suppliers, countryRisk.countries)
 *
 * Uses each supplier's event level (what happened to it), not the combined
 * level that folds in where it sits; the standing country floor becomes the
 * grey ring instead.
 */
export function buildMapCountries(
  suppliers: Pick<Supplier, 'name' | 'location' | 'risk_level' | 'event_risk_level' | 'bat_exposure'>[],
  countryRisk: Record<string, { level: string; reason: string }> = {}
): SupplierMapCountry[] {
  const byCountry = new Map<string, SupplierMapCountry>();
  for (const s of suppliers) {
    if (!s.location) continue;
    let entry = byCountry.get(s.location);
    if (!entry) {
      const floor = own(countryRisk, s.location);
      entry = {
        country: s.location,
        suppliers: [],
        ...(floor ? { standingExposure: `${floor.reason} (${titleCase(floor.level)})` } : {}),
      };
      byCountry.set(s.location, entry);
    }
    entry.suppliers.push({ name: s.name, level: s.event_risk_level ?? s.risk_level, tier: s.bat_exposure });
  }
  return Array.from(byCountry.values());
}

// Stable ids for <title>/<desc>, derived from the content so the server
// render never depends on render order.
function contentId(countries: SupplierMapCountry[], overlays: SupplierMapOverlay[]): string {
  const source = countries.map((c) => `${c.country}:${c.suppliers.length}`).join('|') +
    overlays.map((o) => o.id).join('|');
  let hash = 5381;
  for (let i = 0; i < source.length; i++) hash = ((hash << 5) + hash + source.charCodeAt(i)) >>> 0;
  return `supplier-map-${hash.toString(36)}`;
}

const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

interface PinSpec {
  country: SupplierMapCountry;
  point: (typeof COUNTRY_POINTS)[string] | null;
  xy: Pt | null;
  level: string;
  r: number;
}
interface PlacedPin extends PinSpec {
  point: (typeof COUNTRY_POINTS)[string];
  xy: Pt;
}
const isPlacedPin = (p: PinSpec): p is PlacedPin => p.xy !== null && p.point !== null;

interface MarkerSpec {
  overlay: SupplierMapOverlay;
  name: string;
  xy: Pt | null;
}
interface PlacedMarker extends MarkerSpec {
  xy: Pt;
}
const isPlacedMarker = (m: MarkerSpec): m is PlacedMarker => m.xy !== null;

// ============================================================================
// Component
// ============================================================================

export default function SupplierMap({ countries, overlays = [] }: SupplierMapProps) {
  const id = contentId(countries, overlays);
  const totalSuppliers = countries.reduce((n, c) => n + c.suppliers.length, 0);

  // Pins
  const pins: PinSpec[] = countries
    .filter((c) => c.suppliers.length > 0)
    .map((c) => {
      const point = own(COUNTRY_POINTS, c.country) ?? own(COUNTRY_POINTS, own(COUNTRY_ALIASES, c.country) ?? '');
      return {
        country: c,
        point,
        xy: point ? LAND.project(point.lonLat) : null,
        level: worstLevel(c.suppliers),
        r: pinRadius(c.suppliers.length),
      };
    });
  const placedPins = pins.filter(isPlacedPin);
  const unplaced = pins.filter((p) => !isPlacedPin(p)).map((p) => p.country.country);

  // Overlays
  const markers: MarkerSpec[] = overlays.map((o) => {
    const spot = own(MAP_OVERLAY_POINTS, overlayKey(o.id));
    return { overlay: o, name: o.label || spot?.name || o.id, xy: spot ? LAND.project(spot.lonLat) : null };
  });
  const placedMarkers = markers.filter(isPlacedMarker);

  // Separate pins (movable) from each other and from overlay markers (fixed).
  const pinBodies: Body[] = placedPins.map((p) => ({
    x: p.xy[0],
    y: p.xy[1],
    ax: p.xy[0],
    ay: p.xy[1],
    r: p.r + (p.country.standingExposure ? RING_GAP + 1 : 0) + 1,
    fixed: false,
  }));
  const markerBodies: Body[] = placedMarkers.map((m) => ({
    x: m.xy[0],
    y: m.xy[1],
    ax: m.xy[0],
    ay: m.xy[1],
    r: MARKER_R + 1,
    fixed: true,
  }));
  separate([...pinBodies, ...markerBodies]);

  // Labels: countries first, biggest pins first, then overlay names.
  const obstacles = [...pinBodies, ...markerBodies].map((b) => ({ x: b.x, y: b.y, r: b.r }));
  const taken: Box[] = [];
  const pinOrder = placedPins
    .map((p, i) => ({ p, body: pinBodies[i] }))
    .sort((a, b) => b.p.country.suppliers.length - a.p.country.suppliers.length || a.p.country.country.localeCompare(b.p.country.country));
  const pinLabels = pinOrder.map(({ p, body }) =>
    placeLabel(body.x, body.y, body.r - 1, [p.point.short, p.point.code], LABEL_SIZE.pin, obstacles, taken)
  );
  const markerLabels = placedMarkers.map((m, i) =>
    placeLabel(markerBodies[i].x, markerBodies[i].y, MARKER_R, [m.name], LABEL_SIZE.overlay, obstacles, taken)
  );

  const describeCountry = (c: SupplierMapCountry, level: string) => {
    const names = c.suppliers
      .map((s) => `${s.name} (level ${titleCase(s.level || 'unknown')}, ${s.tier} tier)`)
      .join(', ');
    const exposure = c.standingExposure ? ` Standing exposure: ${c.standingExposure}.` : '';
    return `${c.country}: ${plural(c.suppliers.length, 'supplier')}, worst event level ${LEVEL_STYLE[level].label}.${exposure} ${names}.`;
  };

  const hasRing = placedPins.some((p) => p.country.standingExposure);
  const description =
    `${plural(totalSuppliers, 'supplier')} in ${plural(pins.length, 'country', 'countries')}. ` +
    'Pin size shows how many suppliers are in a country; pin colour shows the worst event level among them. ' +
    (hasRing ? 'A grey ring marks standing exposure from the country itself. ' : '') +
    (placedMarkers.length ? 'Diamonds mark shipping chokepoints and river gauges, coloured by severity.' : '');

  return (
    <figure className="rounded-xl border border-gray-200 bg-white shadow-sm overflow-hidden">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 border-b border-gray-200 bg-gray-50 px-4 sm:px-6 py-3">
        <div className="text-base font-bold text-gray-900">Where the suppliers are</div>
        <div className="text-xs text-gray-500">
          {plural(totalSuppliers, 'supplier')} in {plural(pins.length, 'country', 'countries')}
        </div>
      </div>

      {/* On a phone the map keeps a legible minimum width and scrolls
          sideways. The right-to-left container makes it open on its right
          side, Europe to Japan, where most of the watchlist is; the SVG
          itself is set back to left-to-right so its labels anchor normally. */}
      <div className="overflow-x-auto" dir="rtl">
        <svg
          direction="ltr"
          viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
          role="img"
          aria-labelledby={`${id}-title ${id}-desc`}
          className="block w-full min-w-[640px] h-auto"
        >
          <title id={`${id}-title`}>Map of supplier countries on the watchlist</title>
          <desc id={`${id}-desc`}>{description}</desc>
          <rect width={WIDTH} height={HEIGHT} fill="#f8fafc" />
          <path d={LAND.d} fill="#e2e8f0" />

          {/* A pin nudged off its country keeps a thread back to it. */}
          {placedPins.map((p, i) => {
            const body = pinBodies[i];
            const moved = Math.hypot(body.x - body.ax, body.y - body.ay);
            if (moved < 2) return null;
            return (
              <g key={`lead-${p.country.country}`}>
                <line x1={round1(body.ax)} y1={round1(body.ay)} x2={round1(body.x)} y2={round1(body.y)} stroke="#64748b" strokeWidth={0.8} />
                <circle cx={round1(body.ax)} cy={round1(body.ay)} r={1.6} fill="#64748b" />
              </g>
            );
          })}

          {placedMarkers.map((m, i) => {
            const body = markerBodies[i];
            const style = severityStyle(m.overlay.severity);
            const s = MARKER_R;
            return (
              <g key={`marker-${m.overlay.id}`}>
                <title>{`${m.name}: ${style.label.toLowerCase()}`}</title>
                <path
                  d={`M${round1(body.x)} ${round1(body.y - s)}l${s} ${s}-${s} ${s}-${s}-${s}z`}
                  fill={style.fill}
                  stroke="#ffffff"
                  strokeWidth={1.5}
                />
              </g>
            );
          })}

          {placedPins
            .map((p, i) => ({ p, body: pinBodies[i] }))
            .sort((a, b) => b.p.r - a.p.r)
            .map(({ p, body }) => {
              const style = LEVEL_STYLE[p.level];
              const cx = round1(body.x);
              const cy = round1(body.y);
              return (
                <g key={`pin-${p.country.country}`}>
                  <title>{describeCountry(p.country, p.level)}</title>
                  {p.country.standingExposure && (
                    <circle cx={cx} cy={cy} r={round1(p.r + RING_GAP)} fill="none" stroke={RING_COLOUR} strokeWidth={2} />
                  )}
                  <circle cx={cx} cy={cy} r={round1(p.r)} fill={style.fill} stroke="#ffffff" strokeWidth={1.5} />
                  <text
                    x={cx}
                    y={round1(cy + 3.2)}
                    textAnchor="middle"
                    fontSize={9}
                    fontWeight={700}
                    fill={style.text}
                  >
                    {p.country.suppliers.length}
                  </text>
                </g>
              );
            })}

          <g
            fontSize={LABEL_SIZE.pin}
            fontWeight={600}
            fill="#334155"
            stroke="#f8fafc"
            strokeWidth={3}
            strokeLinejoin="round"
            paintOrder="stroke"
          >
            {pinLabels.map((label, i) =>
              label ? (
                <text key={`pl-${pinOrder[i].p.country.country}`} x={round1(label.x)} y={round1(label.y)} textAnchor={label.anchor}>
                  {label.text}
                </text>
              ) : null
            )}
          </g>
          <g
            fontSize={LABEL_SIZE.overlay}
            fill="#475569"
            stroke="#f8fafc"
            strokeWidth={3}
            strokeLinejoin="round"
            paintOrder="stroke"
          >
            {markerLabels.map((label, i) =>
              label ? (
                <text key={`ml-${placedMarkers[i].overlay.id}`} x={round1(label.x)} y={round1(label.y)} textAnchor={label.anchor}>
                  {label.text}
                </text>
              ) : null
            )}
          </g>
        </svg>
      </div>

      <figcaption className="border-t border-gray-200 px-4 sm:px-6 py-3 text-xs text-gray-600">
        <div className="flex flex-wrap items-center gap-x-5 gap-y-2" aria-hidden="true">
          <span className="flex items-center gap-1.5">
            {LEGEND_LEVELS.map((l) => (
              <svg key={l} width="12" height="12" viewBox="0 0 12 12">
                <circle cx="6" cy="6" r="5" fill={LEVEL_STYLE[l].fill} />
              </svg>
            ))}
            <span>Worst event level: {LEGEND_LEVELS.map((l) => LEVEL_STYLE[l].label).join(', ')}</span>
          </span>
          <span className="flex items-center gap-1.5">
            <svg width="26" height="14" viewBox="0 0 26 14">
              <circle cx="5" cy="9" r="3.5" fill="#94a3b8" />
              <circle cx="18" cy="7" r="6.5" fill="#94a3b8" />
            </svg>
            Bigger pin, more suppliers
          </span>
          {hasRing && (
            <span className="flex items-center gap-1.5">
              <svg width="16" height="16" viewBox="0 0 16 16">
                <circle cx="8" cy="8" r="4" fill="#94a3b8" />
                <circle cx="8" cy="8" r="6.8" fill="none" stroke={RING_COLOUR} strokeWidth="1.6" />
              </svg>
              Standing country exposure
            </span>
          )}
          {placedMarkers.length > 0 && (
            <span className="flex items-center gap-1.5">
              {(['quiet', 'notable', 'severe'] as OverlaySeverity[]).map((sev) => (
                <svg key={sev} width="12" height="12" viewBox="0 0 12 12">
                  <path d="M6 1l5 5-5 5-5-5z" fill={SEVERITY_STYLE[sev].fill} />
                </svg>
              ))}
              Chokepoint: quiet, notable, severe
            </span>
          )}
        </div>
        {unplaced.length > 0 && (
          <p className="mt-2 text-gray-500">
            Not on the map (no position on file): {unplaced.join(', ')}.
          </p>
        )}
      </figcaption>

      <div className="sr-only">
        <p>Supplier countries, largest first:</p>
        <ul>
          {pins
            .slice()
            .sort((a, b) => b.country.suppliers.length - a.country.suppliers.length || a.country.country.localeCompare(b.country.country))
            .map((p) => (
              <li key={`sr-${p.country.country}`}>{describeCountry(p.country, p.level)}</li>
            ))}
        </ul>
        {markers.length > 0 && (
          <>
            <p>Chokepoints:</p>
            <ul>
              {markers.map((m) => (
                <li key={`sr-${m.overlay.id}`}>
                  {m.name}: {severityStyle(m.overlay.severity).label.toLowerCase()}
                  {m.xy ? '' : ' (not placed on the map)'}.
                </li>
              ))}
            </ul>
          </>
        )}
      </div>
    </figure>
  );
}
