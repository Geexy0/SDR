import { beforeEach, describe, expect, it } from "vitest";
import {
  snapLna,
  snapRxVga,
  snapTxVga,
  useHardwareStore,
} from "@/store/useHardwareStore";

describe("gain step locks", () => {
  it("snaps LNA to 8 dB steps", () => {
    expect(snapLna(0)).toBe(0);
    expect(snapLna(7)).toBe(8);
    expect(snapLna(20)).toBe(16);
    expect(snapLna(40)).toBe(40);
  });

  it("snaps RX VGA to even 2 dB steps", () => {
    expect(snapRxVga(0)).toBe(0);
    expect(snapRxVga(21)).toBe(20);
    expect(snapRxVga(62)).toBe(62);
    expect(snapRxVga(63)).toBe(62);
  });

  it("clamps TX VGA to 0..47", () => {
    expect(snapTxVga(-1)).toBe(0);
    expect(snapTxVga(20.4)).toBe(20);
    expect(snapTxVga(99)).toBe(47);
  });
});

describe("useHardwareStore", () => {
  beforeEach(() => {
    useHardwareStore.setState({
      lnaDb: 24,
      rxVgaDb: 20,
      txVgaDb: 20,
      telemetrySeq: 0,
      lastTelemetry: null,
      hwState: "DISCONNECTED",
    });
  });

  it("applies step-locked gains via setters", () => {
    useHardwareStore.getState().setLna(19);
    useHardwareStore.getState().setRxVga(21);
    useHardwareStore.getState().setTxVga(50);
    const s = useHardwareStore.getState();
    expect(s.lnaDb).toBe(16);
    expect(s.rxVgaDb).toBe(20);
    expect(s.txVgaDb).toBe(47);
  });

  it("pushTelemetry bumps seq and stores single frame", () => {
    useHardwareStore.getState().pushTelemetry({
      type: "status",
      state: "HACKRF_NATIVE",
      message: "ok",
      metrics: { foo: 1 },
    });
    const s = useHardwareStore.getState();
    expect(s.telemetrySeq).toBe(1);
    expect(s.lastTelemetry?.state).toBe("HACKRF_NATIVE");
    expect(s.lastTelemetry?.metrics?.foo).toBe(1);
    useHardwareStore.getState().pushTelemetry({ type: "metric", message: "adsb" });
    expect(useHardwareStore.getState().telemetrySeq).toBe(2);
  });
});
