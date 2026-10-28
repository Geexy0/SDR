/** Waterfall color LUTs — RGB triplets 0..1 */
export type LutName = "inferno" | "viridis" | "phosphor" | "gqrx";

function lerp(a: number, b: number, t: number): number {
  return a + (b - a) * t;
}

function sampleStops(stops: [number, number, number][], t: number): [number, number, number] {
  const x = Math.max(0, Math.min(1, t));
  const n = stops.length - 1;
  const i = Math.min(n - 1, Math.floor(x * n));
  const f = x * n - i;
  const a = stops[i];
  const b = stops[i + 1];
  return [lerp(a[0], b[0], f), lerp(a[1], b[1], f), lerp(a[2], b[2], f)];
}

const INFERNO: [number, number, number][] = [
  [0.0, 0.0, 0.02],
  [0.26, 0.05, 0.33],
  [0.6, 0.15, 0.25],
  [0.95, 0.4, 0.1],
  [0.99, 0.9, 0.4],
];
const VIRIDIS: [number, number, number][] = [
  [0.27, 0.0, 0.33],
  [0.18, 0.35, 0.55],
  [0.13, 0.57, 0.55],
  [0.37, 0.78, 0.38],
  [0.99, 0.91, 0.14],
];
const PHOSPHOR: [number, number, number][] = [
  [0.0, 0.02, 0.0],
  [0.0, 0.25, 0.05],
  [0.05, 0.7, 0.15],
  [0.4, 1.0, 0.4],
  [0.85, 1.0, 0.85],
];
const GQRX: [number, number, number][] = [
  [0.0, 0.0, 0.15],
  [0.0, 0.2, 0.7],
  [0.0, 0.8, 0.4],
  [0.9, 0.9, 0.0],
  [1.0, 0.2, 0.0],
];

const TABLES: Record<LutName, [number, number, number][]> = {
  inferno: INFERNO,
  viridis: VIRIDIS,
  phosphor: PHOSPHOR,
  gqrx: GQRX,
};

export function lutRgb(name: LutName, t: number): [number, number, number] {
  return sampleStops(TABLES[name], t);
}

export function buildLutTexture(name: LutName, size = 256): Uint8Array {
  const data = new Uint8Array(size * 4);
  for (let i = 0; i < size; i++) {
    const [r, g, b] = lutRgb(name, i / (size - 1));
    data[i * 4] = Math.round(r * 255);
    data[i * 4 + 1] = Math.round(g * 255);
    data[i * 4 + 2] = Math.round(b * 255);
    data[i * 4 + 3] = 255;
  }
  return data;
}
