import type { AnalyzeResult, HardwareStatus, SubGhzPacket } from "./types";

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, {
      ...init,
      headers: {
        "Content-Type": "application/json",
        ...(init?.headers ?? {}),
      },
    });
  } catch {
    throw new Error(
      `Backend unreachable (${path}). Start: pp-dashboard  →  http://127.0.0.1:8765`,
    );
  }
  const body = await res.json().catch(() => ({ detail: res.statusText }));
  if (!res.ok) {
    const detail = (body as { detail?: unknown }).detail;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail ?? body));
  }
  return body as T;
}

export type VoiceRecordingRow = {
  id: number;
  timestamp: string;
  band_name: string;
  freq_hz: number;
  peak_rssi_db: number | null;
  ctcss_hz: number | null;
  duration_sec: number;
  filepath: string;
};

export const api = {
  status: () => req<HardwareStatus>("/api/hardware/status"),
  setMode: (mode: "MAYHEM_SERIAL" | "HACKRF_NATIVE") =>
    req<HardwareStatus>("/api/hardware/mode", {
      method: "POST",
      body: JSON.stringify({ mode }),
    }),
  stop: () => req<HardwareStatus>("/api/hardware/stop", { method: "POST" }),
  startMultiplex: (body: Record<string, unknown> = {}) =>
    req<HardwareStatus>("/api/hardware/multiplex", {
      method: "POST",
      body: JSON.stringify({
        center_hz: 435_000_000,
        sample_rate: 10_000_000,
        lna: 24,
        vga: 20,
        amp: 0,
        ...body,
      }),
    }),
  stopMultiplex: () =>
    req<HardwareStatus>("/api/hardware/multiplex/stop", { method: "POST" }),

  startSweep: (body: Record<string, unknown>) =>
    req("/api/recon/sweep", { method: "POST", body: JSON.stringify(body) }),
  stopSweep: () => req("/api/recon/sweep/stop", { method: "POST" }),

  startSniff: (body: Record<string, unknown>) =>
    req("/api/subghz/sniff", { method: "POST", body: JSON.stringify(body) }),
  stopSniff: () => req("/api/subghz/sniff/stop", { method: "POST" }),
  packets: () =>
    req<{ packets: Array<Record<string, unknown> | SubGhzPacket> }>(
      "/api/subghz/packets?limit=100",
    ),
  analyze: (body: Record<string, unknown>) =>
    req<AnalyzeResult>("/api/subghz/analyze", { method: "POST", body: JSON.stringify(body) }),
  bruteforce: (body: Record<string, unknown>) =>
    req("/api/subghz/bruteforce", { method: "POST", body: JSON.stringify(body) }),
  bruteforceStop: () =>
    req<{ candidate_keys: number[]; center_sample: number | null; note: string }>(
      "/api/subghz/bruteforce/stop",
      { method: "POST" },
    ),
  replayPacket: (packet_id: number) =>
    req("/api/subghz/replay", {
      method: "POST",
      body: JSON.stringify({ packet_id, tx_vga: 20, amp: 0, max_duration_sec: 5 }),
    }),

  jammer: (body: Record<string, unknown>) =>
    req("/api/tx/jammer", { method: "POST", body: JSON.stringify(body) }),
  rolljam: (body: Record<string, unknown>) =>
    req("/api/rf/rolljam", { method: "POST", body: JSON.stringify(body) }),
  synthesize: (body: Record<string, unknown>) =>
    req("/api/tx/synthesize", { method: "POST", body: JSON.stringify(body) }),

  startDecoder: (body: Record<string, unknown>) =>
    req("/api/recon/decoder", { method: "POST", body: JSON.stringify(body) }),
  stopDecoder: () => req("/api/recon/decoder/stop", { method: "POST" }),

  startVoiceRecon: (body: Record<string, unknown> = {}) =>
    req("/api/recon/voice", { method: "POST", body: JSON.stringify(body) }),
  stopVoiceRecon: () => req("/api/recon/voice/stop", { method: "POST" }),
  reconStatus: () => req<Record<string, unknown>>("/api/recon/status"),
  voiceRecordings: (limit = 40) =>
    req<{ recordings: VoiceRecordingRow[] }>(`/api/recon/recordings?limit=${limit}`),
  voiceRecordingAudioUrl: (id: number) => `/api/recon/recordings/${id}/audio`,

  tpmsStatus: () => req<Record<string, unknown>>("/api/tpms/status"),
  tpmsListen: (body: Record<string, unknown>) =>
    req("/api/tpms/listen", { method: "POST", body: JSON.stringify(body) }),
  tpmsListenStop: () => req("/api/tpms/listen/stop", { method: "POST" }),
  tpmsSensors: () =>
    req<{ sensors: Record<string, unknown>[] }>("/api/tpms/sensors"),
  tpmsPassages: () =>
    req<{ passages: Record<string, unknown>[] }>("/api/tpms/passages?limit=100"),
  tpmsVehicles: () =>
    req<{ vehicles: Record<string, unknown>[] }>("/api/tpms/vehicles"),
  tpmsUpsertVehicle: (body: Record<string, unknown>) =>
    req("/api/tpms/vehicles", { method: "POST", body: JSON.stringify(body) }),
  tpmsLink: (body: Record<string, unknown>) =>
    req("/api/tpms/link", { method: "POST", body: JSON.stringify(body) }),
  tpmsClear: () =>
    req<Record<string, unknown>>("/api/tpms/clear", { method: "POST" }),

  mayhemInput: (body: Record<string, unknown>) =>
    req("/api/mayhem/input", { method: "POST", body: JSON.stringify(body) }),
  mayhemApps: () =>
    req<{ apps: { short_name: string; full_name: string; featured: boolean }[]; featured: string[] }>(
      "/api/mayhem/apps",
    ),
  mayhemAppStart: (app_id: string) =>
    req("/api/mayhem/app", { method: "POST", body: JSON.stringify({ app_id }) }),
  freqmanBookmark: (body: {
    frequency_hz: number;
    description?: string;
    category?: string;
    source?: string;
    modulation?: string;
    bandwidth?: string;
  }) =>
    req<{ ok: boolean; category: string; path: string; count: number }>(
      "/api/mayhem/freqman/bookmark",
      { method: "POST", body: JSON.stringify(body) },
    ),
  freqmanSync: (body: { categories?: string[]; pull_captures?: boolean } = {}) =>
    req<{
      ok: boolean;
      uploaded: Record<string, string>[];
      pulled: Record<string, string>[];
      freqman_listing?: string;
    }>("/api/mayhem/freqman/sync", { method: "POST", body: JSON.stringify(body) }),
};

export function wsUrl(path: string): string {
  const proto = window.location.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${window.location.host}${path}`;
}
