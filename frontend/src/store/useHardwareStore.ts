import { create } from "zustand";
import type {
  AnalyzeResult,
  FreqUnit,
  HardwareState,
  HardwareStatus,
  SubGhzPacket,
  WaterfallLut,
  WorkspaceTab,
} from "@/lib/types";

const LNA_STEPS = [0, 8, 16, 24, 32, 40] as const;
const SAMPLE_RATES = [2, 4, 8, 10, 16, 20] as const;

function snapLna(v: number): number {
  return LNA_STEPS.reduce((best, s) => (Math.abs(s - v) < Math.abs(best - v) ? s : best), 0);
}

function snapRxVga(v: number): number {
  const clamped = Math.max(0, Math.min(62, Math.round(v)));
  return clamped % 2 === 0 ? clamped : clamped - 1;
}

function snapTxVga(v: number): number {
  return Math.max(0, Math.min(47, Math.round(v)));
}

export interface HardwareStore {
  hwState: HardwareState;
  serialPort: string | null;
  subprocessRunning: boolean;
  lastError: string | null;

  frequencyHz: number;
  freqUnit: FreqUnit;
  sampleRateMsps: (typeof SAMPLE_RATES)[number];
  lnaDb: number;
  rxVgaDb: number;
  txVgaDb: number;
  ampOn: boolean;

  workspaceTab: WorkspaceTab;
  waterfallLut: WaterfallLut;
  triggerRssiDb: number;
  sweepStartMhz: number;
  sweepStopMhz: number;
  chirpBandwidthHz: number;
  chirpPeriodUs: number;
  jammerMode: "CW_AWGN" | "CHIRP_BARRAGE";
  maxDurationSec: number;

  bfMode: "framed" | "de_bruijn";
  bfK: 2 | 3;
  bfN: number;
  bfFamily: string;
  bfShortUs: number;
  bfCarrierOffsetHz: number;
  bfProgress: { key: number | null; pct: number; etaS: number };
  bfIsolatedKeys: number[];

  rolljamStage: 1 | 2 | 3 | 4;
  jamOffsetHz: number;

  packets: SubGhzPacket[];
  analyze: AnalyzeResult | null;
  analyzeThreshold: number;
  selectedCapture: string | null;

  pocsagLines: string[];
  mapLat: number;
  mapLon: number;
  mapAlt: number;
  /** false until browser geolocation or map click sets a real RX site */
  mapLocated: boolean;
  spoofPin: { lat: number; lon: number } | null;
  ephemerisPath: string;

  busy: boolean;
  toast: string | null;
  backendOnline: boolean;

  /** Latest /ws/telemetry frame from the single HardwareHeader socket. */
  lastTelemetry: {
    type?: string;
    state?: HardwareState;
    message?: string;
    metrics?: Record<string, unknown>;
  } | null;
  telemetrySeq: number;

  setStatus: (s: HardwareStatus) => void;
  pushTelemetry: (msg: {
    type?: string;
    state?: HardwareState;
    message?: string;
    metrics?: Record<string, unknown>;
  }) => void;
  setBackendOnline: (online: boolean) => void;
  setFrequencyHz: (hz: number) => void;
  setFreqUnit: (u: FreqUnit) => void;
  setSampleRateMsps: (msps: (typeof SAMPLE_RATES)[number]) => void;
  setLna: (v: number) => void;
  setRxVga: (v: number) => void;
  setTxVga: (v: number) => void;
  setAmpOn: (on: boolean) => void;
  setWorkspaceTab: (t: WorkspaceTab) => void;
  setWaterfallLut: (l: WaterfallLut) => void;
  setTriggerRssiDb: (db: number) => void;
  setSweepRange: (startMhz: number, stopMhz: number) => void;
  setChirpBandwidthHz: (hz: number) => void;
  setChirpPeriodUs: (us: number) => void;
  setJammerMode: (m: "CW_AWGN" | "CHIRP_BARRAGE") => void;
  setMaxDurationSec: (s: number) => void;
  setBfMode: (m: "framed" | "de_bruijn") => void;
  setBfK: (k: 2 | 3) => void;
  setBfN: (n: number) => void;
  setBfFamily: (f: string) => void;
  setBfShortUs: (us: number) => void;
  setBfCarrierOffsetHz: (hz: number) => void;
  setBfProgress: (p: { key: number | null; pct: number; etaS: number }) => void;
  setBfIsolatedKeys: (keys: number[]) => void;
  setRolljamStage: (s: 1 | 2 | 3 | 4) => void;
  setJamOffsetHz: (hz: number) => void;
  pushPacket: (p: SubGhzPacket) => void;
  setPackets: (p: SubGhzPacket[]) => void;
  setAnalyze: (a: AnalyzeResult | null) => void;
  setAnalyzeThreshold: (t: number) => void;
  setSelectedCapture: (path: string | null) => void;
  pushPocsag: (line: string) => void;
  setSpoofPin: (pin: { lat: number; lon: number } | null) => void;
  setMapCenter: (lat: number, lon: number, located?: boolean) => void;
  setMapAlt: (alt: number) => void;
  setEphemerisPath: (p: string) => void;
  setBusy: (b: boolean) => void;
  setToast: (t: string | null) => void;
}

export const useHardwareStore = create<HardwareStore>((set) => ({
  hwState: "DISCONNECTED",
  serialPort: null,
  subprocessRunning: false,
  lastError: null,

  frequencyHz: 433_920_000,
  freqUnit: "MHz",
  sampleRateMsps: 10,
  lnaDb: 16,
  rxVgaDb: 20,
  txVgaDb: 20,
  ampOn: false,

  workspaceTab: "subghz",
  waterfallLut: "inferno",
  triggerRssiDb: -40,
  sweepStartMhz: 400,
  sweepStopMhz: 450,
  chirpBandwidthHz: 2_000_000,
  chirpPeriodUs: 1000,
  jammerMode: "CW_AWGN",
  maxDurationSec: 5,

  bfMode: "framed",
  bfK: 2,
  bfN: 12,
  bfFamily: "PT2262",
  bfShortUs: 300,
  bfCarrierOffsetHz: 50_000,
  bfProgress: { key: null, pct: 0, etaS: 0 },
  bfIsolatedKeys: [],

  rolljamStage: 1,
  jamOffsetHz: 200_000,

  packets: [],
  analyze: null,
  analyzeThreshold: 0.15,
  selectedCapture: null,

  pocsagLines: [],
  mapLat: 49.1182,
  mapLon: -1.0972,
  mapAlt: 35,
  mapLocated: false,
  spoofPin: null,
  ephemerisPath: "",

  busy: false,
  toast: null,
  backendOnline: false,

  lastTelemetry: null,
  telemetrySeq: 0,

  setStatus: (s) =>
    set({
      hwState: s.state,
      serialPort: s.serial_port,
      subprocessRunning: s.subprocess_running,
      lastError: s.last_error,
      backendOnline: true,
    }),
  pushTelemetry: (msg) =>
    set((st) => ({
      lastTelemetry: msg,
      telemetrySeq: st.telemetrySeq + 1,
      ...(msg.state ? { hwState: msg.state, backendOnline: true } : {}),
    })),
  setBackendOnline: (online) => set({ backendOnline: online }),
  setFrequencyHz: (hz) => set({ frequencyHz: Math.max(1e6, Math.min(6e9, Math.round(hz))) }),
  setFreqUnit: (u) => set({ freqUnit: u }),
  setSampleRateMsps: (msps) => set({ sampleRateMsps: msps }),
  setLna: (v) => set({ lnaDb: snapLna(v) }),
  setRxVga: (v) => set({ rxVgaDb: snapRxVga(v) }),
  setTxVga: (v) => set({ txVgaDb: snapTxVga(v) }),
  setAmpOn: (on) => set({ ampOn: on }),
  setWorkspaceTab: (t) => set({ workspaceTab: t }),
  setWaterfallLut: (l) => set({ waterfallLut: l }),
  setTriggerRssiDb: (db) => set({ triggerRssiDb: db }),
  setSweepRange: (startMhz, stopMhz) => set({ sweepStartMhz: startMhz, sweepStopMhz: stopMhz }),
  setChirpBandwidthHz: (hz) => set({ chirpBandwidthHz: hz }),
  setChirpPeriodUs: (us) => set({ chirpPeriodUs: us }),
  setJammerMode: (m) => set({ jammerMode: m }),
  setMaxDurationSec: (s) => set({ maxDurationSec: s }),
  setBfMode: (m) => set({ bfMode: m }),
  setBfK: (k) => set({ bfK: k }),
  setBfN: (n) => set({ bfN: Math.max(8, Math.min(24, n)) }),
  setBfFamily: (f) => set({ bfFamily: f }),
  setBfShortUs: (us) => set({ bfShortUs: Math.max(50, Math.min(5000, Math.round(us))) }),
  setBfCarrierOffsetHz: (hz) => set({ bfCarrierOffsetHz: hz }),
  setBfProgress: (p) => set({ bfProgress: p }),
  setBfIsolatedKeys: (keys) => set({ bfIsolatedKeys: keys }),
  setRolljamStage: (s) => set({ rolljamStage: s }),
  setJamOffsetHz: (hz) => set({ jamOffsetHz: hz }),
  pushPacket: (p) =>
    set((st) => ({ packets: [p, ...st.packets].slice(0, 200) })),
  setPackets: (p) => set({ packets: p }),
  setAnalyze: (a) => set({ analyze: a }),
  setAnalyzeThreshold: (t) => set({ analyzeThreshold: t }),
  setSelectedCapture: (path) => set({ selectedCapture: path }),
  pushPocsag: (line) =>
    set((st) => ({ pocsagLines: [...st.pocsagLines.slice(-400), line] })),
  setSpoofPin: (pin) =>
    set({
      spoofPin: pin,
      ...(pin ? { mapLat: pin.lat, mapLon: pin.lon, mapLocated: true } : {}),
    }),
  setMapCenter: (lat, lon, located = true) =>
    set({ mapLat: lat, mapLon: lon, mapLocated: located }),
  setMapAlt: (alt) => set({ mapAlt: alt }),
  setEphemerisPath: (p) => set({ ephemerisPath: p }),
  setBusy: (b) => set({ busy: b }),
  setToast: (t) => set({ toast: t }),
}));

export { LNA_STEPS, SAMPLE_RATES, snapLna, snapRxVga, snapTxVga };
