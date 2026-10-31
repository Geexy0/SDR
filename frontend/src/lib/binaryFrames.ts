/** Shared binary frame parsers for waterfall / PCM WebSocket payloads. */

export type Wf32Frame = {
  n: number;
  binHz: number;
  dbMin: number;
  dbMax: number;
  startHz: number;
  stopHz: number;
  u8: Uint8Array;
};

/** Parse a WF32 waterfall row; returns null for empty/short/corrupt frames. */
export function parseWf32Frame(buf: ArrayBuffer): Wf32Frame | null {
  if (!(buf instanceof ArrayBuffer) || buf.byteLength < 36) return null;
  try {
    const v = new DataView(buf);
    const magic =
      String.fromCharCode(v.getUint8(0), v.getUint8(1), v.getUint8(2), v.getUint8(3));
    if (magic !== "WF32") return null;
    const n = v.getUint32(4, false);
    if (!Number.isFinite(n) || n <= 0 || buf.byteLength < 36 + n) return null;
    const binHz = v.getUint32(8, false);
    const dbMin = v.getFloat32(12, false);
    const dbMax = v.getFloat32(16, false);
    if (!Number.isFinite(dbMin) || !Number.isFinite(dbMax) || Number.isNaN(dbMin)) {
      return null;
    }
    const startHz = Number(v.getBigUint64(20, false));
    const stopHz = Number(v.getBigUint64(28, false));
    if (!Number.isFinite(startHz) || !Number.isFinite(stopHz)) return null;
    const u8 = new Uint8Array(buf, 36, n);
    return { n, binHz, dbMin, dbMax, startHz, stopHz, u8 };
  } catch {
    return null;
  }
}

export type PcmQueue = {
  chunks: Float32Array[];
  samples: number;
};

export function createPcmQueue(): PcmQueue {
  return { chunks: [], samples: 0 };
}

/**
 * Enqueue Int16 LE PCM (mono) into a float queue. Empty / corrupt buffers are
 * ignored without throwing.
 */
export function enqueuePcmInt16(
  q: PcmQueue,
  buf: ArrayBuffer | null | undefined,
  maxSamples = 480_000,
): PcmQueue {
  if (!buf || !(buf instanceof ArrayBuffer) || buf.byteLength < 2) {
    return q;
  }
  try {
    const n = Math.floor(buf.byteLength / 2);
    const src = new Int16Array(buf, 0, n);
    const out = new Float32Array(n);
    for (let i = 0; i < n; i++) {
      const s = src[i]! / 32768;
      out[i] = Number.isFinite(s) ? s : 0;
    }
    const chunks = [...q.chunks, out];
    let samples = q.samples + n;
    while (samples > maxSamples && chunks.length > 1) {
      const dropped = chunks.shift()!;
      samples -= dropped.length;
    }
    return { chunks, samples };
  } catch {
    return q;
  }
}
