import { describe, expect, it } from "vitest";
import {
  createPcmQueue,
  enqueuePcmInt16,
  parseWf32Frame,
} from "@/lib/binaryFrames";

function buildWf32(n: number, dbMin = -80, dbMax = -20): ArrayBuffer {
  const buf = new ArrayBuffer(36 + n);
  const v = new DataView(buf);
  v.setUint8(0, "W".charCodeAt(0));
  v.setUint8(1, "F".charCodeAt(0));
  v.setUint8(2, "3".charCodeAt(0));
  v.setUint8(3, "2".charCodeAt(0));
  v.setUint32(4, n, false);
  v.setUint32(8, 100_000, false);
  v.setFloat32(12, dbMin, false);
  v.setFloat32(16, dbMax, false);
  v.setBigUint64(20, 430_000_000n, false);
  v.setBigUint64(28, 440_000_000n, false);
  const u8 = new Uint8Array(buf, 36, n);
  for (let i = 0; i < n; i++) u8[i] = i % 256;
  return buf;
}

describe("parseWf32Frame", () => {
  it("returns null for empty / short buffers", () => {
    expect(parseWf32Frame(new ArrayBuffer(0))).toBeNull();
    expect(parseWf32Frame(new ArrayBuffer(20))).toBeNull();
  });

  it("parses a normal WF32 row", () => {
    const f = parseWf32Frame(buildWf32(8));
    expect(f).not.toBeNull();
    expect(f!.n).toBe(8);
    expect(f!.u8.length).toBe(8);
    expect(f!.startHz).toBe(430_000_000);
  });

  it("rejects bad magic and NaN db range", () => {
    const bad = buildWf32(4);
    new DataView(bad).setUint8(0, 0);
    expect(parseWf32Frame(bad)).toBeNull();
    const nanBuf = buildWf32(4);
    new DataView(nanBuf).setFloat32(12, Number.NaN, false);
    expect(parseWf32Frame(nanBuf)).toBeNull();
  });
});

describe("enqueuePcmInt16", () => {
  it("ignores empty / null without throwing", () => {
    const q = createPcmQueue();
    expect(() => enqueuePcmInt16(q, null)).not.toThrow();
    expect(enqueuePcmInt16(q, new ArrayBuffer(0)).samples).toBe(0);
  });

  it("queues normal Int16 PCM", () => {
    const buf = new ArrayBuffer(8);
    const i16 = new Int16Array(buf);
    i16[0] = 0;
    i16[1] = 16384;
    i16[2] = -16384;
    i16[3] = 32767;
    const q = enqueuePcmInt16(createPcmQueue(), buf);
    expect(q.samples).toBe(4);
    expect(q.chunks[0]![1]).toBeCloseTo(0.5, 2);
  });
});
