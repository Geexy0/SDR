/** Client-side haversine / CPA helpers for live map overlays. */

const R = 6_371_000;

export function haversineM(
  lat1: number,
  lon1: number,
  lat2: number,
  lon2: number,
): number {
  const toR = (d: number) => (d * Math.PI) / 180;
  const φ1 = toR(lat1);
  const φ2 = toR(lat2);
  const dφ = toR(lat2 - lat1);
  const dλ = toR(lon2 - lon1);
  const a =
    Math.sin(dφ / 2) ** 2 + Math.cos(φ1) * Math.cos(φ2) * Math.sin(dλ / 2) ** 2;
  return 2 * R * Math.asin(Math.min(1, Math.sqrt(a)));
}

export function formatRange(m: number): string {
  if (m >= 1000) return `${(m / 1000).toFixed(1)} km`;
  return `${Math.round(m)} m`;
}

export type CpaLocal = {
  time_s: number;
  range_m: number;
};

/** Constant-velocity CPA in local ENU (same model as backend). */
export function closestPointOfApproach(
  own: { lat: number; lon: number; speed_mps?: number; course_deg?: number },
  tgt: { lat: number; lon: number; speed_mps?: number; course_deg?: number },
  horizon_s = 3600,
): CpaLocal {
  const φ0 = (own.lat * Math.PI) / 180;
  const toXY = (lat: number, lon: number) => {
    const x = ((lon - own.lon) * Math.PI) / 180 * Math.cos(φ0) * R;
    const y = ((lat - own.lat) * Math.PI) / 180 * R;
    return { x, y };
  };
  const vel = (speed: number, course: number) => {
    const θ = (course * Math.PI) / 180;
    return { vx: speed * Math.sin(θ), vy: speed * Math.cos(θ) };
  };
  const t = toXY(tgt.lat, tgt.lon);
  const ov = vel(own.speed_mps ?? 0, own.course_deg ?? 0);
  const tv = vel(tgt.speed_mps ?? 0, tgt.course_deg ?? 0);
  const rx = t.x;
  const ry = t.y;
  const vx = tv.vx - ov.vx;
  const vy = tv.vy - ov.vy;
  const vv = vx * vx + vy * vy;
  let tStar = 0;
  if (vv >= 1e-9) {
    tStar = -((rx * vx + ry * vy) / vv);
    tStar = Math.max(0, Math.min(horizon_s, tStar));
  }
  const ox2 = ov.vx * tStar;
  const oy2 = ov.vy * tStar;
  const tx2 = rx + tv.vx * tStar;
  const ty2 = ry + tv.vy * tStar;
  return {
    time_s: tStar,
    range_m: Math.hypot(tx2 - ox2, ty2 - oy2),
  };
}
