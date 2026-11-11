import { useEffect, useState } from "react";
import { Crosshair, Play, Square } from "lucide-react";
import { api, wsUrl } from "@/lib/api";
import type { RfFingerprintBadge, SubGhzPacket } from "@/lib/types";
import { useHardwareStore } from "@/store/useHardwareStore";
import { PulseAnalyzerCanvas } from "./PulseAnalyzerCanvas";

const FAMILIES = [
  "PT2262",
  "EV1527",
  "CAME",
  "NICE_FLO",
  "LINEAR_MEGACODE",
  "CHAMBERLAIN",
  "GENERIC_PWM",
] as const;

export function SubGhzDeck() {
  const packets = useHardwareStore((s) => s.packets);
  const pushPacket = useHardwareStore((s) => s.pushPacket);
  const setPackets = useHardwareStore((s) => s.setPackets);
  const frequencyHz = useHardwareStore((s) => s.frequencyHz);
  const txVgaDb = useHardwareStore((s) => s.txVgaDb);
  const maxDurationSec = useHardwareStore((s) => s.maxDurationSec);
  const selectedCapture = useHardwareStore((s) => s.selectedCapture);
  const setSelectedCapture = useHardwareStore((s) => s.setSelectedCapture);
  const setAnalyze = useHardwareStore((s) => s.setAnalyze);
  const setToast = useHardwareStore((s) => s.setToast);
  const bfMode = useHardwareStore((s) => s.bfMode);
  const bfK = useHardwareStore((s) => s.bfK);
  const bfN = useHardwareStore((s) => s.bfN);
  const bfFamily = useHardwareStore((s) => s.bfFamily);
  const bfShortUs = useHardwareStore((s) => s.bfShortUs);
  const bfCarrierOffsetHz = useHardwareStore((s) => s.bfCarrierOffsetHz);
  const bfProgress = useHardwareStore((s) => s.bfProgress);
  const bfIsolatedKeys = useHardwareStore((s) => s.bfIsolatedKeys);
  const setBfMode = useHardwareStore((s) => s.setBfMode);
  const setBfK = useHardwareStore((s) => s.setBfK);
  const setBfN = useHardwareStore((s) => s.setBfN);
  const setBfFamily = useHardwareStore((s) => s.setBfFamily);
  const setBfShortUs = useHardwareStore((s) => s.setBfShortUs);
  const setBfCarrierOffsetHz = useHardwareStore((s) => s.setBfCarrierOffsetHz);
  const setBfIsolatedKeys = useHardwareStore((s) => s.setBfIsolatedKeys);

  const [capturePath, setCapturePath] = useState("captures/sample.cs8");
  const [fingerprint, setFingerprint] = useState<RfFingerprintBadge | null>(null);

  useEffect(() => {
    void api
      .packets()
      .then((r) => setPackets(normalizePackets(r.packets as Record<string, unknown>[])))
      .catch(() => undefined);
    const ws = new WebSocket(wsUrl("/ws/subghz"));
    ws.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data as string) as {
          type: string;
          id?: number;
          data?: Record<string, unknown>;
          recent?: Record<string, unknown>[];
        };
        if (msg.type === "status" && Array.isArray(msg.recent)) {
          setPackets(normalizePackets(msg.recent));
        }
        if (msg.type === "packet" && msg.data && msg.id != null) {
          pushPacket(packetFromWs(msg.id, msg.data));
        }
      } catch {
        /* ignore */
      }
    };
    return () => ws.close();
  }, [pushPacket, setPackets]);

  const startSniff = async () => {
    try {
      await api.startSniff({
        frequency_hz: Math.round(frequencyHz),
        sample_rate: 2_000_000,
        max_duration_sec: maxDurationSec,
      });
      setToast("rtl_433 sniff started");
    } catch (err) {
      setToast(err instanceof Error ? err.message : String(err));
    }
  };

  const runAnalyze = async () => {
    const path = selectedCapture || capturePath;
    try {
      const result = await api.analyze({
        filepath: path,
        sample_rate: 2_000_000,
        threshold_method: "otsu",
      });
      setAnalyze(result);
      setSelectedCapture(path);
      const fsk = result.fsk;
      const fskNote = fsk
        ? ` · FSK ±${fsk.deviation_khz.toFixed(1)} kHz @ ${fsk.baud_est.toFixed(0)} baud`
        : "";
      try {
        const fp = await api.fingerprintAnalyze({
          filepath: path,
          sample_rate: 2_000_000,
          nominal_freq_hz: Math.round(frequencyHz) || 433_920_000,
        });
        setFingerprint({
          physical_emitter_id: fp.physical_emitter_id,
          cfo_ppm: fp.fingerprint.cfo_ppm,
          rise_time_us: fp.fingerprint.rise_time_us,
          clone_alert: fp.clone_alert,
          alert: fp.alert,
        });
        setToast(
          fp.clone_alert
            ? `CLONE ALERT · ${fp.physical_emitter_id}`
            : `Analyzed ${result.pulse_count} pulses · ${fp.physical_emitter_id}${fskNote}`,
        );
      } catch {
        setFingerprint(null);
        setToast(
          `Analyzed ${result.pulse_count} pulses · ${result.timing.encoding}${fskNote}`,
        );
      }
    } catch (err) {
      setToast(err instanceof Error ? err.message : String(err));
    }
  };

  const startBf = async () => {
    try {
      await api.bruteforce({
        mode: bfMode,
        frequency_hz: Math.round(frequencyHz),
        sample_rate: 2_000_000,
        tx_vga: txVgaDb,
        amp: 0,
        max_duration_sec: maxDurationSec,
        family: bfFamily,
        nbits: bfN,
        key_start: 0,
        key_end: Math.min(1 << Math.min(bfN, 16), 256),
        k: bfK,
        n: bfN,
        short_us: bfShortUs,
        long_us: bfShortUs * 3,
        loop_tx: bfMode === "de_bruijn",
      });
      setToast(`Bruteforce ${bfMode} armed @ Δf=${bfCarrierOffsetHz} Hz`);
    } catch (err) {
      setToast(err instanceof Error ? err.message : String(err));
    }
  };

  const isolateKey = async () => {
    try {
      const r = await api.bruteforceStop();
      setBfIsolatedKeys(r.candidate_keys);
      setToast(r.note);
    } catch (err) {
      setToast(err instanceof Error ? err.message : String(err));
    }
  };

  return (
    <div className="grid h-full min-h-0 grid-rows-[minmax(0,1fr)_minmax(0,1.1fr)_auto] gap-2 p-2">
      <section className="panel flex min-h-0 flex-col overflow-hidden rounded">
        <div className="flex items-center justify-between border-b border-slate-800 px-2 py-1">
          <h3 className="text-[10px] uppercase tracking-wider text-slate-400">
            rtl_433 Live Sniffer
          </h3>
          <div className="flex gap-1">
            <button
              type="button"
              className="flex items-center gap-1 rounded border border-emerald-800 px-2 py-0.5 text-[10px] text-emerald-300 hover:bg-emerald-950"
              onClick={() => void startSniff()}
            >
              <Play className="h-3 w-3" /> Sniff
            </button>
            <button
              type="button"
              className="flex items-center gap-1 rounded border border-slate-700 px-2 py-0.5 text-[10px] hover:bg-slate-800"
              onClick={() => void api.stopSniff()}
            >
              <Square className="h-3 w-3" /> Stop
            </button>
            <button
              type="button"
              className="rounded border border-amber-800 px-2 py-0.5 text-[10px] text-amber-300"
              title="Bookmark sniff frequency to FREQMAN"
              onClick={() =>
                void api
                  .freqmanBookmark({
                    frequency_hz: Math.round(frequencyHz),
                    description: `SubGHz ${(frequencyHz / 1e6).toFixed(3)}`,
                    category: "RECON",
                    source: "subghz",
                    modulation: "NFM",
                    bandwidth: "12k5",
                  })
                  .then((r) => setToast(`FREQMAN ★ ${r.category} (${r.count})`))
                  .catch((e: Error) => setToast(e.message))
              }
            >
              ★ FREQMAN
            </button>
          </div>
        </div>
        <div className="min-h-0 flex-1 overflow-auto">
          <table className="w-full text-left text-[10px]">
            <thead className="sticky top-0 bg-slate-900 text-slate-500">
              <tr>
                <th className="px-2 py-1">TS</th>
                <th className="px-2 py-1">Protocol</th>
                <th className="px-2 py-1">Freq</th>
                <th className="px-2 py-1">Mod</th>
                <th className="px-2 py-1">RSSI</th>
                <th className="px-2 py-1">Hex</th>
                <th className="px-2 py-1" />
              </tr>
            </thead>
            <tbody>
              {packets.map((p) => (
                <tr key={p.id} className="border-t border-slate-800/80 hover:bg-slate-800/40">
                  <td className="px-2 py-0.5 text-slate-500">{fmtTs(p.ts)}</td>
                  <td className="px-2 py-0.5 text-emerald-300">
                    {p.protocol ?? "?"} / {p.model ?? "—"}
                  </td>
                  <td className="px-2 py-0.5">
                    {p.frequency_hz ? `${(p.frequency_hz / 1e6).toFixed(3)} M` : "—"}
                  </td>
                  <td className="px-2 py-0.5">{String(p.data.mod ?? p.data.modulation ?? "OOK")}</td>
                  <td className="px-2 py-0.5 text-amber-300">
                    {String(p.data.rssi ?? p.data.snr ?? "—")}
                  </td>
                  <td className="max-w-[8rem] truncate px-2 py-0.5 font-mono text-cyan-300">
                    {hexPayload(p)}
                  </td>
                  <td className="px-2 py-0.5">
                    <button
                      type="button"
                      className="rounded border border-red-900 px-1.5 text-red-300 hover:bg-red-950"
                      onClick={() =>
                        void api
                          .replayPacket(p.id)
                          .then(() => setToast(`Replay #${p.id}`))
                          .catch((err: Error) => setToast(err.message))
                      }
                    >
                      Clone / Replay
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="panel flex min-h-0 flex-col overflow-hidden rounded p-2">
        <div className="mb-1 flex flex-wrap items-center gap-2">
          <h3 className="text-[10px] uppercase tracking-wider text-slate-400">
            Signal Inspector · OOK / FSK / PSK
          </h3>
          <input
            className="min-w-[14rem] flex-1 rounded border border-slate-700 bg-slate-950 px-2 py-0.5 text-[10px]"
            value={capturePath}
            onChange={(e) => setCapturePath(e.target.value)}
            placeholder="captures/file.cs8"
          />
          <button
            type="button"
            className="rounded border border-cyan-800 px-2 py-0.5 text-[10px] text-cyan-300"
            onClick={() => void runAnalyze()}
          >
            Analyze
          </button>
        </div>
        {fingerprint && (
          <div
            className={`mb-1 flex flex-wrap items-center gap-2 rounded border px-2 py-0.5 font-mono text-[10px] ${
              fingerprint.clone_alert
                ? "border-red-600 bg-red-950/40 text-red-200"
                : "border-amber-800 bg-amber-950/30 text-amber-200"
            }`}
          >
            <span className="font-bold">{fingerprint.physical_emitter_id}</span>
            <span>CFO {fingerprint.cfo_ppm.toFixed(2)} ppm</span>
            <span>Rise {fingerprint.rise_time_us.toFixed(1)} µs</span>
            {fingerprint.clone_alert && (
              <span className="rounded bg-red-700 px-1 font-bold uppercase text-white">
                CLONE ALERT
              </span>
            )}
          </div>
        )}
        <PulseAnalyzerCanvas />
      </section>

      <section className="panel rounded p-2">
        <div className="mb-2 flex items-center justify-between">
          <h3 className="text-[10px] uppercase tracking-wider text-slate-400">
            De Bruijn / Framed Bruteforce
          </h3>
          <button
            type="button"
            className="flex items-center gap-1 rounded border border-red-600 bg-red-950/70 px-2 py-1 text-[10px] font-bold text-red-300"
            onClick={() => void isolateKey()}
          >
            <Crosshair className="h-3 w-3" />
            TARGET TRIGGERED — ISOLATE KEY
          </button>
        </div>
        <div className="grid grid-cols-2 gap-2 md:grid-cols-4 lg:grid-cols-6">
          <Field label="Mode">
            <select
              className="w-full rounded border border-slate-700 bg-slate-950 px-1 py-0.5 text-[10px]"
              value={bfMode}
              onChange={(e) => setBfMode(e.target.value as "framed" | "de_bruijn")}
            >
              <option value="framed">Framed Packet Stream</option>
              <option value="de_bruijn">De Bruijn Continuous</option>
            </select>
          </Field>
          <Field label="Protocol">
            <select
              className="w-full rounded border border-slate-700 bg-slate-950 px-1 py-0.5 text-[10px]"
              value={bfFamily}
              onChange={(e) => setBfFamily(e.target.value)}
            >
              {FAMILIES.map((f) => (
                <option key={f} value={f}>
                  {f}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Alphabet k">
            <select
              className="w-full rounded border border-slate-700 bg-slate-950 px-1 py-0.5 text-[10px]"
              value={bfK}
              onChange={(e) => setBfK(Number(e.target.value) as 2 | 3)}
            >
              <option value={2}>k=2 binary</option>
              <option value={3}>k=3 tri-state</option>
            </select>
          </Field>
          <Field label="Bit length n">
            <input
              type="number"
              min={8}
              max={24}
              className="w-full rounded border border-slate-700 bg-slate-950 px-1 py-0.5 text-[10px]"
              value={bfN}
              onChange={(e) => setBfN(Number(e.target.value))}
            />
          </Field>
          <Field label="Ts µs">
            <input
              type="number"
              className="w-full rounded border border-slate-700 bg-slate-950 px-1 py-0.5 text-[10px]"
              value={bfShortUs}
              onChange={(e) => setBfShortUs(Number(e.target.value))}
            />
          </Field>
          <Field label="Δf Hz">
            <input
              type="number"
              className="w-full rounded border border-slate-700 bg-slate-950 px-1 py-0.5 text-[10px]"
              value={bfCarrierOffsetHz}
              onChange={(e) => setBfCarrierOffsetHz(Number(e.target.value))}
            />
          </Field>
        </div>
        <div className="mt-2 flex items-center gap-3">
          <button
            type="button"
            className="rounded border border-red-800 bg-red-950/50 px-3 py-1 text-[10px] text-red-300"
            onClick={() => void startBf()}
          >
            Arm TX Stream
          </button>
          <div className="min-w-0 flex-1">
            <div className="mb-0.5 flex justify-between text-[10px] text-slate-400">
              <span>
                key {bfProgress.key ?? "—"} · {bfProgress.pct.toFixed(1)}%
              </span>
              <span>ETA {bfProgress.etaS.toFixed(1)}s</span>
            </div>
            <div className="h-1.5 overflow-hidden rounded bg-slate-800">
              <div
                className="h-full bg-amber-500 transition-all"
                style={{ width: `${Math.min(100, bfProgress.pct)}%` }}
              />
            </div>
          </div>
        </div>
        {bfIsolatedKeys.length > 0 && (
          <p className="mt-1 text-[10px] text-amber-300">
            Isolated window (±2s): [{bfIsolatedKeys.slice(0, 32).join(", ")}
            {bfIsolatedKeys.length > 32 ? "…" : ""}]
          </p>
        )}
      </section>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block text-[10px] text-slate-500">
      <span className="mb-0.5 block">{label}</span>
      {children}
    </label>
  );
}

function fmtTs(ts: string): string {
  try {
    return new Date(ts).toLocaleTimeString();
  } catch {
    return ts;
  }
}

function hexPayload(p: SubGhzPacket): string {
  const d = p.data;
  for (const k of ["data", "code", "id", "hex", "rows"]) {
    if (d[k] != null) return String(d[k]);
  }
  return JSON.stringify(d).slice(0, 48);
}

function normalizePackets(rows: Record<string, unknown>[]): SubGhzPacket[] {
  return rows.map((r, i) => ({
    id: Number(r.id ?? i),
    ts: String(r.ts ?? new Date().toISOString()),
    frequency_hz: r.frequency_hz == null ? null : Number(r.frequency_hz),
    protocol: r.protocol == null ? null : String(r.protocol),
    model: r.model == null ? null : String(r.model),
    data: (r.data as Record<string, unknown>) ?? r,
    raw_pulses_us: (r.raw_pulses_us as number[] | null) ?? null,
  }));
}

function packetFromWs(id: number, data: Record<string, unknown>): SubGhzPacket {
  return {
    id,
    ts: String(data.time ?? data.ts ?? new Date().toISOString()),
    frequency_hz: data.freq != null ? Number(data.freq) : data.frequency_hz != null ? Number(data.frequency_hz) : null,
    protocol: data.protocol != null ? String(data.protocol) : null,
    model: data.model != null ? String(data.model) : null,
    data,
    raw_pulses_us: null,
  };
}
