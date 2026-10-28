import { useEffect, useMemo, useRef, useState } from "react";
import type { AnalyzerViewMode } from "@/lib/types";
import { useHardwareStore } from "@/store/useHardwareStore";

function tritString(pulses: number[], shortUs: number, longUs: number): string {
  if (!shortUs || !longUs) return "";
  const mid = (shortUs + longUs) / 2;
  const highs = pulses.filter((p) => p > 0);
  const bits: string[] = [];
  for (let i = 0; i + 1 < highs.length; i += 2) {
    const a = highs[i]!;
    const b = highs[i + 1]!;
    const shortFirst = a < mid && b >= mid;
    const longFirst = a >= mid && b < mid;
    if (shortFirst) bits.push("0");
    else if (longFirst) bits.push("1");
    else bits.push("F");
  }
  return bits.join("");
}

function sliceClientFskBits(
  freqDev: Float32Array,
  thresholdHz: number,
  samplesPerBit: number,
): string {
  const spb = Math.max(samplesPerBit, 1);
  // Trace is already downsampled — treat each point as ~1 sample of the viewport
  const nBits = Math.floor(freqDev.length / Math.max(1, Math.round(spb)));
  if (nBits <= 0) {
    // Fallback: one sample per point
    let out = "";
    for (let i = 0; i < freqDev.length; i++) {
      out += freqDev[i]! > thresholdHz ? "1" : "0";
    }
    return out;
  }
  let out = "";
  const step = freqDev.length / nBits;
  for (let k = 0; k < nBits; k++) {
    const mid = Math.min(freqDev.length - 1, Math.floor((k + 0.5) * step));
    out += freqDev[mid]! > thresholdHz ? "1" : "0";
  }
  return out;
}

function findPreambles(bits: string): { pattern: string; bit_offset: number }[] {
  const patterns: [string, string][] = [
    ["0xAAAA", "1010101010101010"],
    ["0x5555", "0101010101010101"],
    ["0x2DD4", "0010110111010100"],
  ];
  const out: { pattern: string; bit_offset: number }[] = [];
  for (const [name, pat] of patterns) {
    let start = 0;
    while (out.length < 8) {
      const idx = bits.indexOf(pat, start);
      if (idx < 0) break;
      out.push({ pattern: name, bit_offset: idx });
      start = idx + 1;
    }
  }
  return out;
}

const MODES: { id: AnalyzerViewMode; label: string }[] = [
  { id: "ook", label: "AM / OOK Envelope" },
  { id: "fsk", label: "FM / FSK Frequency Deviation" },
  { id: "constellation", label: "I/Q Constellation & Eye" },
];

export function PulseAnalyzerCanvas() {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const constRef = useRef<HTMLCanvasElement>(null);
  const viewRef = useRef({ zoom: 1, pan: 0, dragging: false, lastX: 0, thrDrag: false });
  const envRef = useRef<Float32Array | null>(null);
  const freqRef = useRef<Float32Array | null>(null);
  const phaseRef = useRef<Float32Array | null>(null);
  const rafRef = useRef(0);

  const [mode, setMode] = useState<AnalyzerViewMode>("ook");
  const [fskThrHz, setFskThrHz] = useState(0);

  const analyze = useHardwareStore((s) => s.analyze);
  const threshold = useHardwareStore((s) => s.analyzeThreshold);
  const setAnalyzeThreshold = useHardwareStore((s) => s.setAnalyzeThreshold);

  useEffect(() => {
    if (!analyze?.envelope_ds?.length) {
      envRef.current = null;
      freqRef.current = null;
      phaseRef.current = null;
      return;
    }
    envRef.current = Float32Array.from(analyze.envelope_ds);
    freqRef.current = analyze.freq_dev_ds?.length
      ? Float32Array.from(analyze.freq_dev_ds)
      : null;
    phaseRef.current = analyze.phase_ds?.length
      ? Float32Array.from(analyze.phase_ds)
      : null;
    if (analyze.threshold > 0) {
      setAnalyzeThreshold(analyze.threshold);
    }
    setFskThrHz(0);
  }, [analyze, setAnalyzeThreshold]);

  const timingPills = useMemo(() => {
    if (!analyze) return null;
    const t = analyze.timing;
    const decoded = tritString(
      analyze.pulses_us,
      t.short_us ?? 300,
      t.long_us ?? 900,
    );
    return { ...t, decoded };
  }, [analyze]);

  const fskClient = useMemo(() => {
    const fsk = analyze?.fsk;
    const freqArr = analyze?.freq_dev_ds;
    if (!fsk || !freqArr?.length) return null;
    const freq = Float32Array.from(freqArr);
    const ratio =
      analyze.envelope_ds.length > 0
        ? analyze.num_samples / analyze.envelope_ds.length
        : 1;
    const spbTrace = Math.max(1, fsk.samples_per_bit / ratio);
    const bits =
      mode === "fsk" ? sliceClientFskBits(freq, fskThrHz, spbTrace) : fsk.bits;
    const found = findPreambles(bits);
    return {
      bits,
      preambles: found.length ? found : fsk.preambles,
      spbTrace,
    };
  }, [analyze, fskThrHz, mode]);

  useEffect(() => {
    const paint = () => {
      const canvas = canvasRef.current;
      if (!canvas) {
        rafRef.current = requestAnimationFrame(paint);
        return;
      }
      const ctx = canvas.getContext("2d");
      if (!ctx) {
        rafRef.current = requestAnimationFrame(paint);
        return;
      }
      const dpr = window.devicePixelRatio || 1;
      const cssW = canvas.clientWidth;
      const cssH = canvas.clientHeight;
      if (canvas.width !== Math.floor(cssW * dpr) || canvas.height !== Math.floor(cssH * dpr)) {
        canvas.width = Math.floor(cssW * dpr);
        canvas.height = Math.floor(cssH * dpr);
      }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      const w = cssW;
      const h = cssH;
      ctx.fillStyle = "#020617";
      ctx.fillRect(0, 0, w, h);

      if (mode === "constellation") {
        ctx.fillStyle = "#64748b";
        ctx.font = "11px JetBrains Mono";
        ctx.fillText("Constellation → panel below", 12, h / 2);
        rafRef.current = requestAnimationFrame(paint);
        return;
      }

      const series = mode === "fsk" ? freqRef.current : envRef.current;
      if (!series || series.length < 2) {
        ctx.fillStyle = "#64748b";
        ctx.font = "11px JetBrains Mono";
        ctx.fillText(
          mode === "fsk"
            ? "No FSK deviation trace — re-run Analyze on a .cs8"
            : "Load a .cs8 capture to render M=√(I²+Q²)",
          12,
          h / 2,
        );
        rafRef.current = requestAnimationFrame(paint);
        return;
      }

      const { zoom, pan } = viewRef.current;
      const visible = Math.max(8, Math.floor(series.length / zoom));
      const start = Math.max(0, Math.min(series.length - visible, Math.floor(pan)));
      const end = Math.min(series.length, start + visible);

      if (mode === "ook") {
        const peak = analyze?.envelope_peak || 1;
        ctx.beginPath();
        ctx.strokeStyle = "#06b6d4";
        ctx.lineWidth = 1.2;
        for (let i = start; i < end; i++) {
          const x = ((i - start) / (end - start - 1 || 1)) * w;
          const y = h - (series[i]! / peak) * (h - 8) - 4;
          if (i === start) ctx.moveTo(x, y);
          else ctx.lineTo(x, y);
        }
        ctx.stroke();

        const thrY = h - (threshold / peak) * (h - 8) - 4;
        ctx.setLineDash([5, 4]);
        ctx.strokeStyle = "#f59e0b";
        ctx.beginPath();
        ctx.moveTo(0, thrY);
        ctx.lineTo(w, thrY);
        ctx.stroke();
        ctx.setLineDash([]);
        ctx.fillStyle = "#f59e0b";
        ctx.font = "10px JetBrains Mono";
        ctx.fillText(`thr ${threshold.toFixed(3)}`, 6, Math.max(12, thrY - 4));

        ctx.strokeStyle = "#10b981";
        ctx.globalAlpha = 0.55;
        ctx.beginPath();
        let lastHigh = series[start]! >= threshold;
        let segStart = start;
        for (let i = start + 1; i <= end; i++) {
          const high = i < end ? series[i]! >= threshold : !lastHigh;
          if (high !== lastHigh || i === end) {
            const x0 = ((segStart - start) / (end - start - 1 || 1)) * w;
            const x1 = ((i - 1 - start) / (end - start - 1 || 1)) * w;
            const y = lastHigh ? h * 0.15 : h * 0.85;
            ctx.moveTo(x0, y);
            ctx.lineTo(x1, y);
            lastHigh = high;
            segStart = i;
          }
        }
        ctx.stroke();
        ctx.globalAlpha = 1;
      } else {
        // FSK frequency deviation (Hz), centered
        let peak = 1;
        for (let i = start; i < end; i++) {
          peak = Math.max(peak, Math.abs(series[i]!));
        }
        const midY = h / 2;
        ctx.strokeStyle = "#334155";
        ctx.beginPath();
        ctx.moveTo(0, midY);
        ctx.lineTo(w, midY);
        ctx.stroke();

        ctx.beginPath();
        ctx.strokeStyle = "#a78bfa";
        ctx.lineWidth = 1.2;
        for (let i = start; i < end; i++) {
          const x = ((i - start) / (end - start - 1 || 1)) * w;
          const y = midY - (series[i]! / peak) * (h * 0.45);
          if (i === start) ctx.moveTo(x, y);
          else ctx.lineTo(x, y);
        }
        ctx.stroke();

        const thrY = midY - (fskThrHz / peak) * (h * 0.45);
        ctx.setLineDash([5, 4]);
        ctx.strokeStyle = "#f59e0b";
        ctx.beginPath();
        ctx.moveTo(0, thrY);
        ctx.lineTo(w, thrY);
        ctx.stroke();
        ctx.setLineDash([]);
        ctx.fillStyle = "#f59e0b";
        ctx.font = "10px JetBrains Mono";
        ctx.fillText(
          `Δf thr ${fskThrHz.toFixed(0)} Hz · ±${(peak / 1000).toFixed(1)} kHz span`,
          6,
          Math.max(12, thrY - 4),
        );

        // Bit overlay
        ctx.strokeStyle = "#34d399";
        ctx.globalAlpha = 0.5;
        ctx.beginPath();
        let lastHigh = series[start]! > fskThrHz;
        let segStart = start;
        for (let i = start + 1; i <= end; i++) {
          const high = i < end ? series[i]! > fskThrHz : !lastHigh;
          if (high !== lastHigh || i === end) {
            const x0 = ((segStart - start) / (end - start - 1 || 1)) * w;
            const x1 = ((i - 1 - start) / (end - start - 1 || 1)) * w;
            const y = lastHigh ? h * 0.2 : h * 0.8;
            ctx.moveTo(x0, y);
            ctx.lineTo(x1, y);
            lastHigh = high;
            segStart = i;
          }
        }
        ctx.stroke();
        ctx.globalAlpha = 1;
      }

      rafRef.current = requestAnimationFrame(paint);
    };
    rafRef.current = requestAnimationFrame(paint);
    return () => cancelAnimationFrame(rafRef.current);
  }, [analyze, threshold, mode, fskThrHz]);

  useEffect(() => {
    const c = constRef.current;
    if (!c || mode !== "constellation") return;
    const density = analyze?.constellation_density;
    const size = analyze?.constellation_size ?? 200;
    const ctx = c.getContext("2d");
    if (!ctx) return;
    c.width = 200;
    c.height = 200;
    ctx.fillStyle = "#020617";
    ctx.fillRect(0, 0, 200, 200);
    if (!density || density.length < size * size) {
      ctx.fillStyle = "#64748b";
      ctx.font = "10px sans-serif";
      ctx.fillText("No constellation", 40, 100);
      return;
    }
    const img = ctx.createImageData(size, size);
    for (let i = 0; i < size * size; i++) {
      const v = density[i] ?? 0;
      const o = i * 4;
      img.data[o] = Math.min(255, 40 + v);
      img.data[o + 1] = Math.min(255, 80 + v * 0.6);
      img.data[o + 2] = 255;
      img.data[o + 3] = v > 0 ? 200 : 40;
    }
    // Scale 200 density → canvas 200
    const tmp = document.createElement("canvas");
    tmp.width = size;
    tmp.height = size;
    tmp.getContext("2d")!.putImageData(img, 0, 0);
    ctx.imageSmoothingEnabled = false;
    ctx.drawImage(tmp, 0, 0, 200, 200);
    // Axes
    ctx.strokeStyle = "#475569";
    ctx.beginPath();
    ctx.moveTo(100, 0);
    ctx.lineTo(100, 200);
    ctx.moveTo(0, 100);
    ctx.lineTo(200, 100);
    ctx.stroke();
  }, [analyze, mode]);

  const onWheel = (e: React.WheelEvent) => {
    e.preventDefault();
    const v = viewRef.current;
    const factor = e.deltaY < 0 ? 1.15 : 1 / 1.15;
    v.zoom = Math.max(1, Math.min(64, v.zoom * factor));
  };

  const onPointerDown = (e: React.PointerEvent<HTMLCanvasElement>) => {
    const canvas = canvasRef.current;
    if (!canvas || mode === "constellation") return;
    const rect = canvas.getBoundingClientRect();
    const y = e.clientY - rect.top;
    if (mode === "ook") {
      const peak = analyze?.envelope_peak || 1;
      const thrY = rect.height - (threshold / peak) * (rect.height - 8) - 4;
      if (Math.abs(y - thrY) < 8) viewRef.current.thrDrag = true;
      else {
        viewRef.current.dragging = true;
        viewRef.current.lastX = e.clientX;
      }
    } else {
      const series = freqRef.current;
      let peak = 1;
      if (series) {
        for (let i = 0; i < series.length; i++) peak = Math.max(peak, Math.abs(series[i]!));
      }
      const midY = rect.height / 2;
      const thrY = midY - (fskThrHz / peak) * (rect.height * 0.45);
      if (Math.abs(y - thrY) < 8) viewRef.current.thrDrag = true;
      else {
        viewRef.current.dragging = true;
        viewRef.current.lastX = e.clientX;
      }
    }
    canvas.setPointerCapture(e.pointerId);
  };

  const onPointerMove = (e: React.PointerEvent<HTMLCanvasElement>) => {
    const canvas = canvasRef.current;
    const v = viewRef.current;
    if (!canvas) return;
    if (v.thrDrag) {
      const rect = canvas.getBoundingClientRect();
      if (mode === "ook") {
        const peak = analyze?.envelope_peak || 1;
        const t = 1 - (e.clientY - rect.top) / rect.height;
        setAnalyzeThreshold(Math.max(0, Math.min(peak, t * peak)));
      } else {
        const series = freqRef.current;
        let peak = 1;
        if (series) {
          for (let i = 0; i < series.length; i++) peak = Math.max(peak, Math.abs(series[i]!));
        }
        const midY = rect.height / 2;
        const y = e.clientY - rect.top;
        const hz = ((midY - y) / (rect.height * 0.45)) * peak;
        setFskThrHz(hz);
      }
      return;
    }
    const series = mode === "fsk" ? freqRef.current : envRef.current;
    if (v.dragging && series) {
      const dx = e.clientX - v.lastX;
      v.lastX = e.clientX;
      const visible = Math.max(8, Math.floor(series.length / v.zoom));
      v.pan -= (dx / canvas.clientWidth) * visible;
      v.pan = Math.max(0, Math.min(series.length - visible, v.pan));
    }
  };

  const onPointerUp = () => {
    viewRef.current.dragging = false;
    viewRef.current.thrDrag = false;
  };

  const bitsShow = (fskClient?.bits || analyze?.fsk?.bits || "").slice(0, 128);
  const preambles = fskClient?.preambles ?? analyze?.fsk?.preambles ?? [];

  return (
    <div className="flex h-full min-h-0 flex-col gap-1">
      <div className="flex flex-wrap gap-1">
        {MODES.map((m) => (
          <button
            key={m.id}
            type="button"
            className={`rounded border px-2 py-0.5 text-[10px] ${
              mode === m.id
                ? "border-cyan-600 bg-cyan-950/50 text-cyan-200"
                : "border-slate-700 text-slate-400 hover:text-slate-200"
            }`}
            onClick={() => setMode(m.id)}
          >
            {m.label}
          </button>
        ))}
      </div>

      <div className="flex flex-wrap gap-1.5 text-[10px]">
        {mode === "ook" && timingPills && (
          <>
            <Pill label="Ts" value={`${timingPills.short_us?.toFixed(0) ?? "—"} µs`} />
            <Pill label="Tl" value={`${timingPills.long_us?.toFixed(0) ?? "—"} µs`} />
            <Pill label="Tsync" value={`${timingPills.sync_gap_us?.toFixed(0) ?? "—"} µs`} />
            <Pill label="Enc" value={timingPills.encoding} accent="amber" />
            <Pill label="Code" value={timingPills.decoded || "—"} accent="cyan" wide />
          </>
        )}
        {mode === "fsk" && analyze?.fsk && (
          <>
            <Pill
              label="±Δf"
              value={`${analyze.fsk.deviation_khz.toFixed(1)} kHz`}
              accent="amber"
            />
            <Pill label="baud" value={`${analyze.fsk.baud_est.toFixed(0)}`} />
            <Pill
              label="offset"
              value={`${analyze.fsk.carrier_offset_hz.toFixed(0)} Hz`}
            />
            {preambles.slice(0, 3).map((p, i) => (
              <Pill
                key={`${p.pattern}-${p.bit_offset}-${i}`}
                label="sync"
                value={`${p.pattern}@${p.bit_offset}`}
                accent="cyan"
              />
            ))}
            <Pill label="bits" value={bitsShow || "—"} accent="cyan" wide />
          </>
        )}
        {mode === "constellation" && (
          <span className="text-slate-500">
            Density I/Q · {analyze?.constellation_size ?? 200}²
            {analyze?.downsampled ? " · large-file downsample" : ""}
          </span>
        )}
        {!analyze && <span className="text-slate-500">No analyze result</span>}
      </div>

      {mode !== "constellation" ? (
        <canvas
          ref={canvasRef}
          className="min-h-0 flex-1 w-full cursor-crosshair rounded border border-slate-800 bg-slate-950"
          onWheel={onWheel}
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
        />
      ) : (
        <div className="flex min-h-0 flex-1 items-center justify-center gap-4 rounded border border-slate-800 bg-slate-950 p-2">
          <canvas
            ref={constRef}
            width={200}
            height={200}
            className="h-[200px] w-[200px] rounded border border-slate-700"
          />
          <div className="max-w-xs text-[10px] text-slate-400">
            <p className="mb-1 text-slate-300">I/Q constellation (density)</p>
            <p>
              Bright clusters mark symbol states for PSK / FSK. Crosshair is I=0 / Q=0. Re-analyze
              after capturing FSK/GFSK/PSK IQ for clearer clouds.
            </p>
            {analyze?.fsk && (
              <p className="mt-2 text-violet-300/90">{analyze.fsk.notes}</p>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function Pill({
  label,
  value,
  accent = "emerald",
  wide,
}: {
  label: string;
  value: string;
  accent?: "emerald" | "amber" | "cyan";
  wide?: boolean;
}) {
  const colors = {
    emerald: "border-emerald-800 text-emerald-300",
    amber: "border-amber-800 text-amber-300",
    cyan: "border-cyan-800 text-cyan-300",
  };
  return (
    <span
      className={`rounded border bg-slate-900/90 px-1.5 py-0.5 ${colors[accent]} ${
        wide ? "max-w-full truncate font-mono" : ""
      }`}
      title={value}
    >
      <span className="text-slate-500">{label} </span>
      {value}
    </span>
  );
}
