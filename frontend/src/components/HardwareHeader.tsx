import { useCallback, useEffect, useMemo, useState } from "react";
import { Radio, ShieldAlert, Zap } from "lucide-react";
import { api, wsUrl } from "@/lib/api";
import type { HardwareState, WaterfallLut } from "@/lib/types";
import { LNA_STEPS, SAMPLE_RATES, useHardwareStore } from "@/store/useHardwareStore";
import { CtcssPill } from "./WebAudioPlayer";

const STATE_STYLE: Record<HardwareState, string> = {
  DISCONNECTED: "bg-slate-800 text-slate-300 border-slate-600",
  MAYHEM_SERIAL: "bg-amber-950/80 text-amber-300 border-amber-600",
  HACKRF_NATIVE: "bg-emerald-950/80 text-emerald-300 border-emerald-600",
  BUSY_TX: "bg-red-950/90 text-red-300 border-red-500 animate-pulse",
  BUSY_RX: "bg-cyan-950/80 text-cyan-300 border-cyan-600",
  MULTIPLEX_RX: "bg-violet-950/90 text-violet-300 border-violet-500 animate-pulse",
  SWITCHING: "bg-slate-800 text-slate-200 border-slate-500",
};

function formatFreq(hz: number, unit: "MHz" | "GHz"): string {
  if (unit === "GHz") return (hz / 1e9).toFixed(6);
  return (hz / 1e6).toFixed(3);
}

export function HardwareHeader() {
  const hwState = useHardwareStore((s) => s.hwState);
  const frequencyHz = useHardwareStore((s) => s.frequencyHz);
  const freqUnit = useHardwareStore((s) => s.freqUnit);
  const sampleRateMsps = useHardwareStore((s) => s.sampleRateMsps);
  const lnaDb = useHardwareStore((s) => s.lnaDb);
  const rxVgaDb = useHardwareStore((s) => s.rxVgaDb);
  const txVgaDb = useHardwareStore((s) => s.txVgaDb);
  const waterfallLut = useHardwareStore((s) => s.waterfallLut);
  const toast = useHardwareStore((s) => s.toast);
  const backendOnline = useHardwareStore((s) => s.backendOnline);
  const setStatus = useHardwareStore((s) => s.setStatus);
  const setBackendOnline = useHardwareStore((s) => s.setBackendOnline);
  const setFrequencyHz = useHardwareStore((s) => s.setFrequencyHz);
  const setFreqUnit = useHardwareStore((s) => s.setFreqUnit);
  const setSampleRateMsps = useHardwareStore((s) => s.setSampleRateMsps);
  const setLna = useHardwareStore((s) => s.setLna);
  const setRxVga = useHardwareStore((s) => s.setRxVga);
  const setTxVga = useHardwareStore((s) => s.setTxVga);
  const setWaterfallLut = useHardwareStore((s) => s.setWaterfallLut);
  const setBfProgress = useHardwareStore((s) => s.setBfProgress);
  const setToast = useHardwareStore((s) => s.setToast);
  const [headerCtcss, setHeaderCtcss] = useState<number | null>(null);
  const [headerListenMhz, setHeaderListenMhz] = useState<number | null>(null);

  const killTx = useCallback(async () => {
    try {
      const results = await Promise.allSettled([
        api.stop(),
        api.stopSweep(),
        api.stopSniff(),
        api.stopDecoder(),
        api.stopVoiceRecon(),
        api.tpmsListenStop(),
        api.bruteforceStop(),
      ]);
      const st = results[0].status === "fulfilled" ? results[0].value : await api.status();
      setStatus(st);
      setToast("TX KILL — stop + sweep/sniff/decoder/voice/tpms halted");
    } catch (err) {
      setToast(err instanceof Error ? err.message : String(err));
    }
  }, [setStatus, setToast]);

  useEffect(() => {
    const poll = () => {
      void api
        .status()
        .then(setStatus)
        .catch((err: Error) => {
          setBackendOnline(false);
          setToast(err.message);
        });
    };
    poll();
    const id = window.setInterval(poll, 3000);
    return () => window.clearInterval(id);
  }, [setStatus, setToast, setBackendOnline]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.code === "Space" || e.code === "Escape") {
        const t = e.target as HTMLElement | null;
        if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable)) {
          return;
        }
        e.preventDefault();
        void killTx();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [killTx]);

  useEffect(() => {
    const ws = new WebSocket(wsUrl("/ws/telemetry"));
    ws.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data as string) as {
          type?: string;
          state?: HardwareState;
          message?: string;
          metrics?: Record<string, unknown>;
        };
        if (msg.state) {
          useHardwareStore.setState({ hwState: msg.state, backendOnline: true });
        }
        if (msg.message === "bruteforce_progress" && msg.metrics) {
          const key = msg.metrics.current_key;
          const eta = Number(msg.metrics.eta_s ?? 0);
          const bytes = Number(msg.metrics.bytes_total ?? 1);
          const sampleIdx = Number(msg.metrics.sample_idx ?? 0);
          const pct = Math.min(100, (sampleIdx / Math.max(1, bytes / 2)) * 100);
          setBfProgress({
            key: typeof key === "number" ? key : key == null ? null : Number(key),
            pct,
            etaS: eta,
          });
        }
        if (msg.message === "voice_recon" && msg.metrics) {
          const m = msg.metrics;
          if (typeof m.ctcss_hz === "number") setHeaderCtcss(m.ctcss_hz);
          else if (m.event === "stopped" || m.ctcss_hz == null) setHeaderCtcss(null);
          const hz =
            typeof m.listen_hz === "number"
              ? m.listen_hz
              : typeof m.frequency_hz === "number"
                ? m.frequency_hz
                : null;
          setHeaderListenMhz(hz != null ? hz / 1e6 : null);
          if (m.event === "stopped") setHeaderListenMhz(null);
        }
      } catch {
        /* ignore */
      }
    };
    return () => ws.close();
  }, [setBfProgress]);

  useEffect(() => {
    if (!toast) return;
    const t = window.setTimeout(() => setToast(null), 3500);
    return () => window.clearTimeout(t);
  }, [toast, setToast]);

  const freqDisplay = useMemo(
    () => formatFreq(frequencyHz, freqUnit),
    [frequencyHz, freqUnit],
  );

  const onFreqWheel = (e: React.WheelEvent<HTMLInputElement>) => {
    e.preventDefault();
    const raw = e.currentTarget.value.replace(/[^\d.]/g, "");
    const digits = raw.replace(".", "");
    if (!digits) return;
    const caret = e.currentTarget.selectionStart ?? digits.length;
    const dotPos = raw.indexOf(".");
    let digitIndex = caret;
    if (dotPos >= 0 && caret > dotPos) digitIndex = caret - 1;
    digitIndex = Math.max(0, Math.min(digits.length - 1, digitIndex));
    const placeFromRight = digits.length - 1 - digitIndex;
    const unitMul = freqUnit === "GHz" ? 1e9 : 1e6;
    const stepHz = (unitMul / Math.pow(10, placeFromRight)) * (e.deltaY < 0 ? 1 : -1);
    setFrequencyHz(frequencyHz + stepHz);
  };

  const switchMode = async (mode: "MAYHEM_SERIAL" | "HACKRF_NATIVE") => {
    try {
      const st = await api.setMode(mode);
      setStatus(st);
    } catch (err) {
      setToast(err instanceof Error ? err.message : String(err));
    }
  };

  return (
    <header className="flex h-14 shrink-0 flex-col border-b border-slate-800 bg-slate-950">
      {!backendOnline && (
        <div className="bg-red-950 px-3 py-1 text-center text-[11px] font-semibold text-red-300">
          BACKEND OFFLINE — API/WS dead. Run{" "}
          <code className="text-amber-300">pp-dashboard</code> then open{" "}
          <code className="text-amber-300">http://127.0.0.1:8765/</code>
        </div>
      )}
      <div className="flex flex-1 items-center gap-3 px-3 py-1.5">
      <div className="flex items-center gap-2">
        <Radio className="h-4 w-4 text-emerald-400" />
        <span className="text-xs font-semibold tracking-widest text-slate-200">
          PP-H4M · HACKRF CYBER-DECK
        </span>
      </div>

      <span
        className={`rounded border px-2 py-0.5 text-[10px] font-bold tracking-wider ${STATE_STYLE[hwState]}`}
      >
        {hwState}
      </span>
      {headerListenMhz != null && (
        <span className="inline-flex items-center gap-1 rounded border border-amber-900/80 px-1.5 py-0.5 text-[10px] text-amber-200">
          dwell {headerListenMhz.toFixed(3)}
          <CtcssPill hz={headerCtcss} />
        </span>
      )}

      <div className="flex items-center gap-1 rounded border border-slate-800 bg-slate-900/90 p-0.5">
        <button
          type="button"
          className={`px-2 py-1 text-[10px] ${
            hwState === "MAYHEM_SERIAL" ? "bg-amber-900/60 text-amber-300" : "text-slate-400"
          }`}
          onClick={() => void switchMode("MAYHEM_SERIAL")}
        >
          MAYHEM
        </button>
        <button
          type="button"
          className={`px-2 py-1 text-[10px] ${
            hwState === "HACKRF_NATIVE" || hwState.startsWith("BUSY")
              ? "bg-emerald-900/60 text-emerald-300"
              : "text-slate-400"
          }`}
          onClick={() => void switchMode("HACKRF_NATIVE")}
        >
          HACKRF
        </button>
        <button
          type="button"
          className={`px-2 py-1 text-[10px] ${
            hwState === "MULTIPLEX_RX"
              ? "bg-violet-900/70 text-violet-200"
              : "text-slate-400"
          }`}
          title="10 MSPS wideband IQ mux → waterfall + /tmp/pp_subghz.cs8 + voice DDC"
          onClick={() => {
            if (hwState === "MULTIPLEX_RX") {
              void api
                .stopMultiplex()
                .then((st) => setStatus(st))
                .catch((e: Error) => setToast(e.message));
              return;
            }
            void api
              .startMultiplex({
                center_hz: 435_000_000,
                subghz_tune_hz: Math.round(frequencyHz),
                voice_tune_hz: Math.round(frequencyHz),
                lna: lnaDb,
                vga: rxVgaDb,
              })
              .then((st) => {
                setStatus(st);
                setToast("MULTIPLEX_RX · 430–440 MHz @ 10 MSPS");
              })
              .catch((e: Error) => setToast(e.message));
          }}
        >
          MUX
        </button>
      </div>

      <div className="flex items-center gap-1">
        <label className="text-[10px] text-slate-500">f<sub>c</sub></label>
        <input
          className="w-28 rounded border border-slate-700 bg-slate-900 px-2 py-1 text-xs text-cyan-300 outline-none focus:border-cyan-600"
          value={freqDisplay}
          onChange={(e) => {
            const v = parseFloat(e.target.value);
            if (!Number.isFinite(v)) return;
            setFrequencyHz(v * (freqUnit === "GHz" ? 1e9 : 1e6));
          }}
          onWheel={onFreqWheel}
        />
        <select
          className="rounded border border-slate-700 bg-slate-900 px-1 py-1 text-[10px]"
          value={freqUnit}
          onChange={(e) => setFreqUnit(e.target.value as "MHz" | "GHz")}
        >
          <option value="MHz">MHz</option>
          <option value="GHz">GHz</option>
        </select>
        <button
          type="button"
          className="rounded border border-amber-800 px-1.5 py-1 text-[9px] text-amber-300 hover:bg-amber-950"
          title="Bookmark current fc to Mayhem FREQMAN RECON.TXT"
          onClick={() =>
            void api
              .freqmanBookmark({
                frequency_hz: Math.round(frequencyHz),
                description: `WF ${(frequencyHz / 1e6).toFixed(4)}`,
                category: "RECON",
                source: "waterfall",
              })
              .then((r) => setToast(`FREQMAN bookmark → ${r.category} (${r.count})`))
              .catch((e: Error) => setToast(e.message))
          }
        >
          ★ FREQMAN
        </button>
      </div>

      <div className="flex items-center gap-1">
        <label className="text-[10px] text-slate-500">SR</label>
        <select
          className="rounded border border-slate-700 bg-slate-900 px-1 py-1 text-[10px]"
          value={sampleRateMsps}
          onChange={(e) =>
            setSampleRateMsps(Number(e.target.value) as (typeof SAMPLE_RATES)[number])
          }
        >
          {SAMPLE_RATES.map((r) => (
            <option key={r} value={r}>
              {r} MSPS
            </option>
          ))}
        </select>
      </div>

      <GainSlider label="LNA" value={lnaDb} min={0} max={40} step={8} marks={[...LNA_STEPS]} onChange={setLna} unit="dB" />
      <GainSlider label="RX VGA" value={rxVgaDb} min={0} max={62} step={2} onChange={setRxVga} unit="dB" />
      <GainSlider label="TX VGA" value={txVgaDb} min={0} max={47} step={1} onChange={setTxVga} unit="dB" />

      <label
        className="flex items-center gap-1 text-[10px] text-slate-500"
        title="External amp locked off (SCOPE). TX paths always send amp=0."
      >
        <input type="checkbox" checked={false} disabled className="accent-red-500" />
        AMP +14 locked
      </label>

      <select
        className="rounded border border-slate-700 bg-slate-900 px-1 py-1 text-[10px]"
        value={waterfallLut}
        onChange={(e) => setWaterfallLut(e.target.value as WaterfallLut)}
        title="Waterfall LUT"
      >
        <option value="inferno">Inferno</option>
        <option value="viridis">Viridis</option>
        <option value="phosphor">Phosphor</option>
        <option value="gqrx">GQRX</option>
      </select>

      <div className="ml-auto flex items-center gap-2">
        {toast && (
          <span className="max-w-xs truncate text-[10px] text-amber-300">{toast}</span>
        )}
        <button
          type="button"
          onClick={() => void killTx()}
          className="flex items-center gap-1 rounded border border-red-600 bg-red-950/80 px-3 py-1.5 text-xs font-bold tracking-wide text-red-300 hover:bg-red-900"
          title="Space / Esc"
        >
          <ShieldAlert className="h-3.5 w-3.5" />
          TX KILL
          <Zap className="h-3 w-3" />
        </button>
      </div>
      </div>
    </header>
  );
}

function GainSlider({
  label,
  value,
  min,
  max,
  step,
  marks,
  onChange,
  unit,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  marks?: number[];
  onChange: (v: number) => void;
  unit: string;
}) {
  return (
    <label className="flex items-center gap-1 text-[10px] text-slate-400">
      <span className="w-10 shrink-0">{label}</span>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        list={marks ? `${label}-marks` : undefined}
        onChange={(e) => onChange(Number(e.target.value))}
        className="w-16 accent-emerald-500"
      />
      {marks && (
        <datalist id={`${label}-marks`}>
          {marks.map((m) => (
            <option key={m} value={m} />
          ))}
        </datalist>
      )}
      <span className="w-10 text-right text-emerald-400">
        {value}
        {unit}
      </span>
    </label>
  );
}
