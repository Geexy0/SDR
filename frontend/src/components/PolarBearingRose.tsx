import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "@/lib/api";
import { useHardwareStore } from "@/store/useHardwareStore";

export type PolarPoint = { bearing_deg: number; rssi_db: number };

type Props = {
  onCommitLob: (peak: {
    bearing_deg: number;
    angular_std_deg: number;
    peak_rssi_db: number;
  }) => void;
  liveRssiDb?: number | null;
  size?: number;
};

export function PolarBearingRose({
  onCommitLob,
  liveRssiDb = null,
  size = 200,
}: Props) {
  const setToast = useHardwareStore((s) => s.setToast);
  const [sweeping, setSweeping] = useState(false);
  const [samples, setSamples] = useState<PolarPoint[]>([]);
  const [peakDeg, setPeakDeg] = useState<number | null>(null);
  const [hpbw, setHpbw] = useState<number | null>(null);
  const [heading, setHeading] = useState(0);
  const hasCompass = useRef(false);
  const sweepRef = useRef<{ t0: number; samples: PolarPoint[] } | null>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);

  // Device orientation / compass when available
  useEffect(() => {
    const onOrient = (e: DeviceOrientationEvent) => {
      const h = e.alpha;
      if (typeof h === "number" && Number.isFinite(h)) {
        hasCompass.current = true;
        setHeading((360 - h) % 360);
      }
    };
    window.addEventListener("deviceorientation", onOrient);
    return () => window.removeEventListener("deviceorientation", onOrient);
  }, []);

  useEffect(() => {
    if (!sweeping) return;
    const id = window.setInterval(() => {
      const st = sweepRef.current;
      if (!st || liveRssiDb == null) return;
      const elapsed = (performance.now() - st.t0) / 1000;
      const bearing = hasCompass.current
        ? heading
        : ((elapsed / 10) * 360) % 360;
      st.samples.push({ bearing_deg: bearing, rssi_db: liveRssiDb });
      setSamples([...st.samples]);
      if (elapsed >= 10) {
        setSweeping(false);
        void finalize(st.samples);
      }
    }, 120);
    return () => window.clearInterval(id);
  }, [sweeping, liveRssiDb, heading]);

  const finalize = async (pts: PolarPoint[]) => {
    try {
      const r = await api.polarPeak({ samples: pts });
      setPeakDeg(r.peak_bearing_deg);
      setHpbw(r.half_power_beamwidth_deg);
      setToast(`Polar peak ${r.peak_bearing_deg.toFixed(0)}° · HPBW ${r.half_power_beamwidth_deg.toFixed(0)}°`);
    } catch (err) {
      setToast(err instanceof Error ? err.message : String(err));
    }
  };

  const startSweep = () => {
    sweepRef.current = { t0: performance.now(), samples: [] };
    setSamples([]);
    setPeakDeg(null);
    setHpbw(null);
    setSweeping(true);
    setToast("Rotate antenna 360° over ~10s…");
  };

  // Draw rose
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    const cx = size / 2;
    const cy = size / 2;
    const rMax = size / 2 - 8;
    ctx.clearRect(0, 0, size, size);
    ctx.fillStyle = "#020617";
    ctx.fillRect(0, 0, size, size);
    ctx.strokeStyle = "#334155";
    ctx.lineWidth = 1;
    for (const rr of [0.33, 0.66, 1]) {
      ctx.beginPath();
      ctx.arc(cx, cy, rMax * rr, 0, Math.PI * 2);
      ctx.stroke();
    }
    for (let d = 0; d < 360; d += 30) {
      const rad = ((d - 90) * Math.PI) / 180;
      ctx.beginPath();
      ctx.moveTo(cx, cy);
      ctx.lineTo(cx + rMax * Math.cos(rad), cy + rMax * Math.sin(rad));
      ctx.stroke();
    }
    if (samples.length > 1) {
      const rssis = samples.map((s) => s.rssi_db);
      const rmin = Math.min(...rssis);
      const rmax = Math.max(...rssis);
      ctx.strokeStyle = "#22d3ee";
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      samples.forEach((s, i) => {
        const norm = (s.rssi_db - rmin) / Math.max(rmax - rmin, 1e-3);
        const rad = ((s.bearing_deg - 90) * Math.PI) / 180;
        const rr = rMax * (0.15 + 0.85 * norm);
        const x = cx + rr * Math.cos(rad);
        const y = cy + rr * Math.sin(rad);
        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      });
      ctx.stroke();
    }
    if (peakDeg != null) {
      const rad = ((peakDeg - 90) * Math.PI) / 180;
      ctx.strokeStyle = "#f472b6";
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.moveTo(cx, cy);
      ctx.lineTo(cx + rMax * Math.cos(rad), cy + rMax * Math.sin(rad));
      ctx.stroke();
    }
    ctx.fillStyle = "#94a3b8";
    ctx.font = "9px monospace";
    ctx.fillText("N", cx - 3, 12);
  }, [samples, peakDeg, size]);

  const commit = () => {
    if (peakDeg == null) {
      setToast("Run a polar sweep first");
      return;
    }
    const peakRssi = samples.length
      ? Math.max(...samples.map((s) => s.rssi_db))
      : -40;
    onCommitLob({
      bearing_deg: peakDeg,
      angular_std_deg: Math.max(1, (hpbw ?? 30) / 2.355),
      peak_rssi_db: peakRssi,
    });
  };

  const tip = useMemo(
    () =>
      peakDeg != null
        ? `peak ${peakDeg.toFixed(0)}° · HPBW ${hpbw?.toFixed(0) ?? "—"}°`
        : sweeping
          ? "sweeping…"
          : "idle",
    [peakDeg, hpbw, sweeping],
  );

  return (
    <div className="rounded border border-slate-800 bg-slate-950/60 p-2">
      <div className="mb-1 flex flex-wrap items-center justify-between gap-1">
        <div>
          <div className="text-[10px] uppercase tracking-wider text-slate-400">
            Polar Antenna Rose
          </div>
          <p className="text-[9px] text-slate-500">{tip}</p>
        </div>
        <div className="flex gap-1 text-[10px]">
          <button
            type="button"
            disabled={sweeping}
            className="rounded border border-cyan-800 px-2 py-0.5 text-cyan-200 disabled:opacity-40"
            onClick={startSweep}
          >
            Start 10s Sweep
          </button>
          <button
            type="button"
            disabled={peakDeg == null}
            className="rounded border border-pink-800 px-2 py-0.5 text-pink-200 disabled:opacity-40"
            onClick={commit}
          >
            Commit Peak LOB to Solver
          </button>
        </div>
      </div>
      <canvas ref={canvasRef} width={size} height={size} className="mx-auto block" />
    </div>
  );
}
