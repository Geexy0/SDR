import { useEffect, useMemo, useRef, useState } from "react";
import {
  api,
  type FoxhuntResult,
  type GnssL1AnalyzeResult,
  type LobSolveResult,
  type PathLossPoint,
  type RssiGridSolveResult,
} from "@/lib/api";
import { GeigerVariometer } from "@/lib/geigerVariometer";
import { useHardwareStore } from "@/store/useHardwareStore";
import { LocationIntelPanel } from "./LocationIntelPanel";
import { PolarBearingRose } from "./PolarBearingRose";

export type SolverOverlay = {
  cep95: [number, number][] | null;
  mlPin: { lat: number; lon: number; label: string } | null;
  contour80: [number, number][] | null;
  contour95: [number, number][] | null;
};

export type FoxhuntOverlay = {
  lat: number;
  lon: number;
  east_m: number;
  north_m: number;
  status: string;
  bearing_deg: number;
  label: string;
} | null;

export type GnssAlertMarker = {
  lat: number;
  lon: number;
  classification: string;
  ts: number;
};

type Props = {
  onLayersChange: Parameters<typeof LocationIntelPanel>[0]["onLayersChange"];
  onSolverOverlay: (overlay: SolverOverlay) => void;
  onFoxhuntOverlay: (overlay: FoxhuntOverlay) => void;
  onGnssAlert: (marker: GnssAlertMarker) => void;
};

function PathLossCard({ curve, gamma }: { curve: PathLossPoint[]; gamma: number }) {
  const geom = useMemo(() => {
    const pad = 18;
    const w = 220;
    const h = 100;
    if (curve.length === 0) return null;
    const xs = curve.map((p) => p.log10_distance);
    const ysMeas = curve.map((p) => p.rssi_db);
    const ysPred = curve.map((p) => p.predicted_db);
    const xmin = Math.min(...xs);
    const xmax = Math.max(...xs);
    const ymin = Math.min(...ysMeas, ...ysPred);
    const ymax = Math.max(...ysMeas, ...ysPred);
    const sx = (x: number) =>
      pad + ((x - xmin) / Math.max(xmax - xmin, 1e-6)) * (w - 2 * pad);
    const sy = (y: number) =>
      h - pad - ((y - ymin) / Math.max(ymax - ymin, 1e-6)) * (h - 2 * pad);
    const sorted = [...curve].sort((a, b) => a.log10_distance - b.log10_distance);
    const line = sorted
      .map((p) => `${sx(p.log10_distance).toFixed(1)},${sy(p.predicted_db).toFixed(1)}`)
      .join(" ");
    const dots = curve.map((p) => ({
      cx: sx(p.log10_distance),
      cy: sy(p.rssi_db),
    }));
    const maxRes = Math.max(...curve.map((p) => Math.abs(p.residual_db)));
    return { w, h, pad, line, dots, maxRes };
  }, [curve]);

  if (!geom) {
    return (
      <p className="text-[9px] text-slate-600">
        Run RSSI-grid solve to plot P vs log₁₀(d) residuals.
      </p>
    );
  }

  return (
    <div>
      <div className="mb-1 text-[9px] text-slate-500">
        Path-loss · γ={gamma.toFixed(2)} · residual max {geom.maxRes.toFixed(2)} dB
      </div>
      <svg
        width={geom.w}
        height={geom.h}
        className="rounded border border-slate-800 bg-black/40"
      >
        <polyline
          fill="none"
          stroke="#22d3ee"
          strokeWidth="1"
          strokeDasharray="3 3"
          points={geom.line}
        />
        {geom.dots.map((d, i) => (
          <circle key={i} cx={d.cx} cy={d.cy} r={2.5} fill="#fbbf24" />
        ))}
        <text x={geom.pad} y={12} fill="#64748b" fontSize="8">
          RSSI
        </text>
        <text x={geom.w - 42} y={geom.h - 4} fill="#64748b" fontSize="8">
          log₁₀ d
        </text>
      </svg>
    </div>
  );
}

type FoxSample = { lat: number; lon: number; rssi_db: number; t_mono: number };

export function LocationIntelDeck({
  onLayersChange,
  onSolverOverlay,
  onFoxhuntOverlay,
  onGnssAlert,
}: Props) {
  const setToast = useHardwareStore((s) => s.setToast);
  const mapLat = useHardwareStore((s) => s.mapLat);
  const mapLon = useHardwareStore((s) => s.mapLon);
  const [busy, setBusy] = useState(false);
  const [gamma, setGamma] = useState(2.8);
  const [lobResult, setLobResult] = useState<LobSolveResult | null>(null);
  const [rssiResult, setRssiResult] = useState<RssiGridSolveResult | null>(null);

  const [huntActive, setHuntActive] = useState(false);
  const [autoFusion, setAutoFusion] = useState(false);
  const [fusionNote, setFusionNote] = useState("");
  const lastTelemetry = useHardwareStore((s) => s.lastTelemetry);
  const telemetrySeq = useHardwareStore((s) => s.telemetrySeq);
  const [liveRssi, setLiveRssi] = useState(-55);
  const [squelchDb, setSquelchDb] = useState(-90);
  const [ceilingDb, setCeilingDb] = useState(-30);
  const [geigerOn, setGeigerOn] = useState(false);
  const [homing, setHoming] = useState<FoxhuntResult | null>(null);
  const [l1Note, setL1Note] = useState("");
  const samplesRef = useRef<FoxSample[]>([]);
  const t0Ref = useRef(performance.now() / 1000);
  const huntGpsRef = useRef<{ lat: number; lon: number } | null>(null);
  const liveRssiRef = useRef(liveRssi);
  const onFoxhuntOverlayRef = useRef(onFoxhuntOverlay);
  const geigerRef = useRef<GeigerVariometer | null>(null);

  useEffect(() => {
    liveRssiRef.current = liveRssi;
  }, [liveRssi]);

  useEffect(() => {
    onFoxhuntOverlayRef.current = onFoxhuntOverlay;
  }, [onFoxhuntOverlay]);

  useEffect(() => {
    geigerRef.current = new GeigerVariometer({ squelchDb, ceilingDb });
    return () => {
      geigerRef.current?.stop();
      geigerRef.current = null;
    };
  }, []);

  useEffect(() => {
    geigerRef.current?.setConfig({ squelchDb, ceilingDb });
  }, [squelchDb, ceilingDb]);

  useEffect(() => {
    geigerRef.current?.setRssi(liveRssi);
  }, [liveRssi]);

  useEffect(() => {
    if (!huntActive) return;
    if (!navigator.geolocation) {
      setToast("Browser GPS required for fox-hunt");
      setHuntActive(false);
      return;
    }
    const watchId = navigator.geolocation.watchPosition(
      (pos) => {
        huntGpsRef.current = {
          lat: pos.coords.latitude,
          lon: pos.coords.longitude,
        };
      },
      () => setToast("GPS watch failed"),
      { enableHighAccuracy: true, maximumAge: 1000 },
    );
    const tick = window.setInterval(() => {
      void (async () => {
        const gps = huntGpsRef.current;
        if (!gps) return;
        const t_mono = performance.now() / 1000 - t0Ref.current;
        const rssi_db = liveRssiRef.current;
        samplesRef.current = [
          ...samplesRef.current.slice(-80),
          { lat: gps.lat, lon: gps.lon, rssi_db, t_mono },
        ];
        const windowSamples = samplesRef.current.filter(
          (s) => s.t_mono >= t_mono - 5.5,
        );
        if (windowSamples.length < 2) return;
        try {
          const r = await api.foxhunt({ samples: windowSamples, window_sec: 5 });
          setHoming(r);
          onFoxhuntOverlayRef.current({
            lat: gps.lat,
            lon: gps.lon,
            east_m: r.arrow.east_m,
            north_m: r.arrow.north_m,
            status: r.status,
            bearing_deg: r.travel_bearing_deg,
            label:
              r.status === "warmer"
                ? "Getting Warmer"
                : r.status === "colder"
                  ? "Getting Colder"
                  : "Hold",
          });
          if (autoFusion) {
            void api.geoHuntIngest({
              lat: gps.lat,
              lon: gps.lon,
              rssi_db,
              emitter_id: "FOX-HUNT",
            });
          }
        } catch {
          /* transient */
        }
      })();
    }, 700);
    return () => {
      navigator.geolocation.clearWatch(watchId);
      window.clearInterval(tick);
    };
  }, [huntActive, autoFusion, setToast]);

  const toggleGeiger = async () => {
    const g = geigerRef.current;
    if (!g) return;
    if (geigerOn) {
      g.stop();
      setGeigerOn(false);
      setToast("Geiger homing off");
      return;
    }
    await g.start();
    g.setRssi(liveRssi);
    setGeigerOn(true);
    setToast("Geiger homing on — sweep with Yagi eyes-up");
  };

  const startHunt = () => {
    samplesRef.current = [];
    t0Ref.current = performance.now() / 1000;
    setHoming(null);
    setHuntActive(true);
    setToast("Fox-hunt homing — walk/drive; feed live RSSI");
  };

  const stopHunt = () => {
    setHuntActive(false);
    onFoxhuntOverlay(null);
    setToast("Fox-hunt stopped");
  };

  useEffect(() => {
    if (!autoFusion || !lastTelemetry) return;
    if (lastTelemetry.type !== "geohunt_update") return;
    const m = lastTelemetry.metrics ?? {};
    const pin = m.ml_pin as { lat?: number; lon?: number; emitter_id?: string } | undefined;
    const c80 = m.contour_80 as [number, number][] | undefined;
    const c95 = m.contour_95 as [number, number][] | undefined;
    const cep = m.cep95 as { polygon?: [number, number][] } | undefined;
    if (pin?.lat != null && pin.lon != null) {
      onSolverOverlay({
        mlPin: {
          lat: pin.lat,
          lon: pin.lon,
          label: String(pin.emitter_id ?? m.emitter_id ?? "GEO-HUNT"),
        },
        contour80: c80 ?? null,
        contour95: c95 ?? null,
        cep95: cep?.polygon ?? null,
      });
      setFusionNote(
        `Live fusion · ${String(pin.emitter_id ?? m.emitter_id ?? "emit")} @ ${pin.lat.toFixed(5)}, ${pin.lon.toFixed(5)}`,
      );
    }
  }, [autoFusion, lastTelemetry, telemetrySeq, onSolverOverlay]);

  const toggleAutoFusion = async () => {
    try {
      if (autoFusion) {
        await api.geoHuntStop();
        setAutoFusion(false);
        setFusionNote("");
        setToast("AUTO GEO-HUNT FUSION: OFF");
        return;
      }
      await api.geoHuntStart();
      setAutoFusion(true);
      setToast("AUTO GEO-HUNT FUSION: ON");
    } catch (err) {
      setToast(err instanceof Error ? err.message : String(err));
    }
  };

  const commitPolarLob = async (peak: {
    bearing_deg: number;
    angular_std_deg: number;
    peak_rssi_db: number;
  }) => {
    try {
      const st = await api.geolocStatus();
      const op = st.operator;
      if (!op) {
        setToast("Set operator GPS before committing LOB");
        return;
      }
      await api.addLob({
        lat: op.lat,
        lon: op.lon,
        bearing_deg: peak.bearing_deg,
        label: `Polar peak ${peak.bearing_deg.toFixed(0)}°`,
        length_m: 15000,
      });
      if (autoFusion) {
        await api.geoHuntBearing({
          lat: op.lat,
          lon: op.lon,
          bearing_deg: peak.bearing_deg,
          angular_std_deg: peak.angular_std_deg,
          rssi_db: peak.peak_rssi_db,
          emitter_id: "POLAR-LOB",
        });
      }
      setToast(
        `Committed LOB ${peak.bearing_deg.toFixed(0)}° (σ≈${peak.angular_std_deg.toFixed(1)}°) — Solve LOBs next`,
      );
    } catch (err) {
      setToast(err instanceof Error ? err.message : String(err));
    }
  };

  const runL1Analyze = async () => {
    setBusy(true);
    try {
      const r: GnssL1AnalyzeResult = await api.gnssL1Analyze({
        duration_sec: 0.25,
        lna: 24,
        vga: 20,
        operator_lat: mapLat,
        operator_lon: mapLon,
      });
      setL1Note(
        `${r.classification} · ΔN₀ ${Number(r.delta_n0_db ?? 0).toFixed(1)} dB · PAPR ${Number(r.papr_db ?? 0).toFixed(1)} dB`,
      );
      if (r.geo_marker) onGnssAlert(r.geo_marker);
      setToast(
        r.classification === "NOMINAL"
          ? "L1 integrity NOMINAL"
          : `L1 alert: ${r.classification}`,
      );
    } catch (err) {
      setToast(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const runLobSolve = async () => {
    setBusy(true);
    try {
      const { lobs } = await api.listLobs();
      if ((lobs?.length ?? 0) < 2) {
        setToast("Need ≥2 LOBs for Stansfield WLS");
        return;
      }
      const result = await api.lobSolve({
        observations: lobs.map((l) => ({
          lat: l.lat,
          lon: l.lon,
          bearing_deg: l.bearing_deg,
          angular_std_deg: 3.0,
          rssi_db: -45,
        })),
      });
      setLobResult(result);
      if (result.gdop_rejected) {
        setToast(result.message || "GDOP rejected — bearings nearly parallel");
        onSolverOverlay({
          cep95: null,
          mlPin: null,
          contour80: null,
          contour95: null,
        });
        return;
      }
      if (result.ok && result.lat != null && result.lon != null) {
        onSolverOverlay({
          cep95: (result.cep95?.polygon as [number, number][] | undefined) ?? null,
          mlPin: { lat: result.lat, lon: result.lon, label: "Stansfield ML" },
          contour80: null,
          contour95: null,
        });
        setToast(
          `LOB solve · CEP95 ±${result.cep95?.semi_major_m.toFixed(0) ?? "?"} m`,
        );
      }
    } catch (err) {
      setToast(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const runRssiSolve = async () => {
    setBusy(true);
    try {
      const { breadcrumbs } = await api.geolocBreadcrumbs(500);
      const withRssi = (breadcrumbs ?? []).filter(
        (b) =>
          typeof b.rssi_db === "number" &&
          typeof b.lat === "number" &&
          typeof b.lon === "number",
      );
      const obs: { lat: number; lon: number; rssi_db: number }[] = [];
      for (const b of withRssi) {
        const lat = Number(b.lat);
        const lon = Number(b.lon);
        if (obs.some((o) => Math.hypot(o.lat - lat, o.lon - lon) < 1e-5)) continue;
        obs.push({ lat, lon, rssi_db: Number(b.rssi_db) });
      }
      if (obs.length < 4) {
        setToast("Need ≥4 distinct RSSI breadcrumbs for grid solve");
        return;
      }
      const result = await api.rssiGridSolve({
        observations: obs,
        gamma,
        shadow_std_db: 4.0,
        grid_n: 80,
        pad_m: 250,
      });
      setRssiResult(result);
      if (result.ok && result.lat != null && result.lon != null) {
        onSolverOverlay({
          cep95: null,
          mlPin: { lat: result.lat, lon: result.lon, label: "RSSI ML" },
          contour80: (result.contour_80 as [number, number][] | undefined) ?? null,
          contour95: (result.contour_95 as [number, number][] | undefined) ?? null,
        });
        setToast(`RSSI-grid ML · γ=${gamma}`);
      }
    } catch (err) {
      setToast(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const statusColor =
    homing?.status === "warmer"
      ? "text-emerald-300"
      : homing?.status === "colder"
        ? "text-sky-300"
        : "text-slate-400";

  return (
    <div className="space-y-2">
      <LocationIntelPanel onLayersChange={onLayersChange} />

      <div className="rounded border border-slate-800 bg-slate-950/60 p-2">
        <div className="mb-1 flex flex-wrap items-center justify-between gap-2">
          <div>
            <div className="text-[10px] uppercase tracking-wider text-slate-400">
              Fox-Hunt Homing
            </div>
            <p className="text-[9px] text-slate-500">
              Kalman RSSI gradient × GPS motion · Geiger variometer (220→1760 Hz)
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-1 text-[10px]">
            <label className="flex items-center gap-1 text-slate-400">
              RSSI
              <input
                type="range"
                min={-100}
                max={-20}
                step={1}
                value={liveRssi}
                onChange={(e) => setLiveRssi(Number(e.target.value))}
                className="w-24"
              />
              <span className="w-10 tabular-nums text-cyan-200">{liveRssi}</span>
            </label>
            {!huntActive ? (
              <button
                type="button"
                className="rounded border border-emerald-800 px-2 py-0.5 text-emerald-200"
                onClick={startHunt}
              >
                Start Hunt
              </button>
            ) : (
              <button
                type="button"
                className="rounded border border-rose-800 px-2 py-0.5 text-rose-200"
                onClick={stopHunt}
              >
                Stop Hunt
              </button>
            )}
            <button
              type="button"
              className={`rounded border px-2 py-0.5 ${
                geigerOn
                  ? "border-amber-500 text-amber-200"
                  : "border-slate-600 text-slate-300"
              }`}
              onClick={() => void toggleGeiger()}
            >
              Geiger {geigerOn ? "ON" : "off"}
            </button>
            <button
              type="button"
              className={`rounded border px-2 py-0.5 font-semibold tracking-wide ${
                autoFusion
                  ? "border-cyan-500 bg-cyan-950/50 text-cyan-200"
                  : "border-slate-600 text-slate-400"
              }`}
              onClick={() => void toggleAutoFusion()}
            >
              AUTO GEO-HUNT FUSION: {autoFusion ? "ON" : "OFF"}
            </button>
          </div>
        </div>
        {fusionNote && (
          <p className="mb-1 text-[9px] text-cyan-300/90">{fusionNote}</p>
        )}
        <div className="mb-1 flex flex-wrap gap-2 text-[9px] text-slate-500">
          <label className="flex items-center gap-1">
            sq
            <input
              type="number"
              className="w-12 rounded border border-slate-700 bg-slate-950 px-1"
              value={squelchDb}
              onChange={(e) => setSquelchDb(Number(e.target.value) || -90)}
            />
          </label>
          <label className="flex items-center gap-1">
            ceil
            <input
              type="number"
              className="w-12 rounded border border-slate-700 bg-slate-950 px-1"
              value={ceilingDb}
              onChange={(e) => setCeilingDb(Number(e.target.value) || -30)}
            />
          </label>
          <span className={statusColor}>
            {homing
              ? `${homing.status.toUpperCase()} · rate ${homing.rssi_rate_db_s.toFixed(2)} dB/s · ${homing.travel_bearing_deg.toFixed(0)}°`
              : huntActive
                ? "acquiring GPS + Kalman…"
                : "idle"}
          </span>
        </div>
      </div>

      <PolarBearingRose liveRssiDb={liveRssi} onCommitLob={(p) => void commitPolarLob(p)} />

      <div className="rounded border border-slate-800 bg-slate-950/60 p-2">
        <div className="mb-1 flex flex-wrap items-center justify-between gap-2">
          <div>
            <div className="text-[10px] uppercase tracking-wider text-slate-400">
              GNSS L1 Integrity Sentinel
            </div>
            <p className="text-[9px] text-slate-500">
              1575.42 MHz · ΔN₀ · PAPR/kurtosis · CW/chirp/wideband classify
            </p>
          </div>
          <button
            type="button"
            disabled={busy}
            className="rounded border border-violet-800 px-2 py-0.5 text-[10px] text-violet-200 disabled:opacity-40"
            onClick={() => void runL1Analyze()}
          >
            L1 Analyze
          </button>
        </div>
        <p className="text-[9px] text-violet-200/80">{l1Note || "No analysis yet"}</p>
      </div>

      <div className="rounded border border-slate-800 bg-slate-950/60 p-2">
        <div className="mb-1 flex flex-wrap items-center justify-between gap-2">
          <div>
            <div className="text-[10px] uppercase tracking-wider text-slate-400">
              Probabilistic Emitter Fix
            </div>
            <p className="text-[9px] text-slate-500">
              Stansfield 2-pass WLS (LOB) · RSSI log-distance grid ML · CEP₉₅ / iso-contours
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-1 text-[10px]">
            <label className="flex items-center gap-1 text-slate-400">
              γ
              <input
                type="number"
                min={2}
                max={4.5}
                step={0.1}
                className="w-14 rounded border border-slate-700 bg-slate-950 px-1 py-0.5"
                value={gamma}
                onChange={(e) => setGamma(Number(e.target.value) || 2.8)}
              />
            </label>
            <button
              type="button"
              disabled={busy}
              className="rounded border border-cyan-800 px-2 py-0.5 text-cyan-200 disabled:opacity-40"
              onClick={() => void runLobSolve()}
            >
              Solve LOBs
            </button>
            <button
              type="button"
              disabled={busy}
              className="rounded border border-amber-800 px-2 py-0.5 text-amber-200 disabled:opacity-40"
              onClick={() => void runRssiSolve()}
            >
              Solve RSSI grid
            </button>
          </div>
        </div>
        <div className="grid gap-2 md:grid-cols-2">
          <div className="text-[10px] text-slate-400">
            {lobResult?.ok && !lobResult.gdop_rejected ? (
              <span>
                Stansfield · {lobResult.lat?.toFixed(5)}, {lobResult.lon?.toFixed(5)} · CEP₉₅{" "}
                {lobResult.cep95?.semi_major_m.toFixed(0)}×
                {lobResult.cep95?.semi_minor_m.toFixed(0)} m
              </span>
            ) : lobResult?.gdop_rejected ? (
              <span className="text-rose-300">{lobResult.message}</span>
            ) : (
              <span className="text-slate-600">
                No LOB fix yet — add ≥2 bearings then Solve LOBs.
              </span>
            )}
            {rssiResult?.ok && (
              <div className="mt-1 text-amber-200/90">
                RSSI ML · {rssiResult.lat?.toFixed(5)}, {rssiResult.lon?.toFixed(5)} · P₀{" "}
                {rssiResult.p0_db?.toFixed(1)} dB
              </div>
            )}
          </div>
          <PathLossCard
            curve={rssiResult?.path_loss_curve ?? []}
            gamma={rssiResult?.gamma ?? gamma}
          />
        </div>
      </div>
    </div>
  );
}
