import { useCallback, useEffect, useState } from "react";
import {
  api,
  type GeolocOperator,
  type HeatCell,
  type LobRow,
} from "@/lib/api";
import { useHardwareStore } from "@/store/useHardwareStore";

type Props = {
  onLayersChange: (layers: {
    heatmap: HeatCell[];
    lobs: LobRow[];
    rings: { radius_m: number; polygon: [number, number][] }[];
    intersection: { lat: number; lon: number } | null;
    operator: GeolocOperator | null;
    showHeat: boolean;
    showRings: boolean;
    showLobs: boolean;
    showWifiCell: boolean;
  }) => void;
};

export function LocationIntelPanel({ onLayersChange }: Props) {
  const setToast = useHardwareStore((s) => s.setToast);
  const setMapCenter = useHardwareStore((s) => s.setMapCenter);
  const mapLat = useHardwareStore((s) => s.mapLat);
  const mapLon = useHardwareStore((s) => s.mapLon);

  const [operator, setOperator] = useState<GeolocOperator | null>(null);
  const [gpsFollow, setGpsFollow] = useState(true);
  const [bearing, setBearing] = useState(0);
  const [busy, setBusy] = useState(false);
  const [showHeat, setShowHeat] = useState(true);
  const [showRings, setShowRings] = useState(true);
  const [showLobs, setShowLobs] = useState(true);
  const [showWifiCell, setShowWifiCell] = useState(false);
  const [gnssNote, setGnssNote] = useState<string>("");
  const [crumbCount, setCrumbCount] = useState(0);

  const pushLayers = useCallback(
    async (op: GeolocOperator | null) => {
      try {
        const [heat, lobs, rings] = await Promise.all([
          showHeat ? api.geolocHeatmap(0.002) : Promise.resolve({ cells: [] }),
          showLobs ? api.listLobs() : Promise.resolve({ lobs: [] }),
          showRings && op
            ? api.rangeRings("1000,5000,15000")
            : Promise.resolve({ rings: [] as { radius_m: number; polygon: [number, number][] }[] }),
        ]);
        let intersection: { lat: number; lon: number } | null = null;
        const lobList = lobs.lobs ?? [];
        if (lobList.length >= 2) {
          const a = lobList[lobList.length - 2];
          const b = lobList[lobList.length - 1];
          const hit = await api.intersectLobs({
            a: { lat: a.lat, lon: a.lon, bearing_deg: a.bearing_deg },
            b: { lat: b.lat, lon: b.lon, bearing_deg: b.bearing_deg },
          });
          intersection = hit.intersection;
        }
        onLayersChange({
          heatmap: heat.cells ?? [],
          lobs: lobList,
          rings: rings.rings ?? [],
          intersection,
          operator: op,
          showHeat,
          showRings,
          showLobs,
          showWifiCell,
        });
      } catch {
        onLayersChange({
          heatmap: [],
          lobs: [],
          rings: [],
          intersection: null,
          operator: op,
          showHeat,
          showRings,
          showLobs,
          showWifiCell,
        });
      }
    },
    [onLayersChange, showHeat, showLobs, showRings, showWifiCell],
  );

  const refresh = useCallback(async () => {
    try {
      const st = await api.geolocStatus();
      setOperator(st.operator);
      setCrumbCount(st.breadcrumb_count ?? 0);
      if (st.gnss_latest) {
        const g = st.gnss_latest;
        setGnssNote(
          `${g.jammed ? "⚠ elevated" : "nominal"} · peak ${Number(g.peak_db).toFixed(1)} dB`,
        );
      }
      await pushLayers(st.operator);
    } catch {
      /* ignore */
    }
  }, [pushLayers]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    if (!gpsFollow || !navigator.geolocation) return;
    const id = navigator.geolocation.watchPosition(
      (pos) => {
        const lat = pos.coords.latitude;
        const lon = pos.coords.longitude;
        void api
          .setOperatorGps({
            lat,
            lon,
            accuracy_m: pos.coords.accuracy,
            alt_m: pos.coords.altitude ?? undefined,
            source: "browser",
          })
          .then((r) => {
            setOperator(r.operator);
            setMapCenter(lat, lon, true);
            void pushLayers(r.operator);
          })
          .catch(() => {
            /* offline */
          });
      },
      () => {
        /* permission denied — keep manual */
      },
      { enableHighAccuracy: true, maximumAge: 5_000, timeout: 15_000 },
    );
    return () => navigator.geolocation.clearWatch(id);
  }, [gpsFollow, pushLayers, setMapCenter]);

  const dropFixHere = async () => {
    setBusy(true);
    try {
      const r = await api.setOperatorGps({
        lat: mapLat,
        lon: mapLon,
        source: "map_center",
      });
      setOperator(r.operator);
      setToast("Operator GPS set to map center");
      await pushLayers(r.operator);
    } catch (err) {
      setToast(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const dropBreadcrumb = async () => {
    if (!operator) {
      setToast("Set operator GPS first");
      return;
    }
    setBusy(true);
    try {
      await api.addBreadcrumb({
        kind: "manual",
        lat: operator.lat,
        lon: operator.lon,
        label: "DF walk",
      });
      setToast("Breadcrumb dropped");
      await refresh();
    } catch (err) {
      setToast(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const addBearing = async () => {
    if (!operator) {
      setToast("Set operator GPS first");
      return;
    }
    setBusy(true);
    try {
      await api.addLob({
        lat: operator.lat,
        lon: operator.lon,
        bearing_deg: bearing,
        label: `LOB ${bearing.toFixed(0)}°`,
      });
      setToast(`LOB ${bearing.toFixed(0)}° stored`);
      await refresh();
    } catch (err) {
      setToast(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const clearBearings = async () => {
    setBusy(true);
    try {
      await api.clearLobs();
      setToast("LOBs cleared");
      await refresh();
    } catch (err) {
      setToast(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const sampleGnss = async () => {
    setBusy(true);
    try {
      const r = await api.gnssL1Analyze({
        duration_sec: 0.25,
        lna: 24,
        vga: 20,
        operator_lat: mapLat,
        operator_lon: mapLon,
      });
      setGnssNote(
        `${r.classification} · ΔN₀ ${Number(r.delta_n0_db ?? 0).toFixed(1)} · PAPR ${Number(r.papr_db ?? 0).toFixed(1)}`,
      );
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

  return (
    <div className="mb-2 space-y-2 rounded border border-slate-800 bg-slate-950/50 p-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <div className="text-[10px] uppercase tracking-wider text-slate-400">
            Location Intel
          </div>
          <p className="text-[9px] text-slate-500">
            Operator GPS · RF breadcrumbs / heatmap · LOB DF · range rings · GNSS L1 RX
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2 text-[10px]">
          <span className="rounded border border-amber-800/60 px-2 py-0.5 text-amber-200">
            {operator
              ? `${operator.lat.toFixed(5)}, ${operator.lon.toFixed(5)}`
              : "no operator fix"}
            {operator?.accuracy_m != null ? ` ±${Math.round(operator.accuracy_m)}m` : ""}
          </span>
          <span className="text-slate-500">{crumbCount} crumbs</span>
          {gnssNote && <span className="text-cyan-300/90">{gnssNote}</span>}
        </div>
      </div>

      <div className="flex flex-wrap gap-2 text-[10px]">
        <label className="flex items-center gap-1 text-slate-400">
          <input
            type="checkbox"
            checked={gpsFollow}
            onChange={(e) => setGpsFollow(e.target.checked)}
          />
          follow browser GPS
        </label>
        <label className="flex items-center gap-1 text-slate-400">
          <input type="checkbox" checked={showHeat} onChange={(e) => setShowHeat(e.target.checked)} />
          heatmap
        </label>
        <label className="flex items-center gap-1 text-slate-400">
          <input type="checkbox" checked={showRings} onChange={(e) => setShowRings(e.target.checked)} />
          range rings
        </label>
        <label className="flex items-center gap-1 text-slate-400">
          <input type="checkbox" checked={showLobs} onChange={(e) => setShowLobs(e.target.checked)} />
          LOBs
        </label>
        <label
          className="flex items-center gap-1 text-slate-400"
          title="Shows browser geolocation accuracy circle as Wi‑Fi/cell hybrid context (no RF scan)"
        >
          <input
            type="checkbox"
            checked={showWifiCell}
            onChange={(e) => setShowWifiCell(e.target.checked)}
          />
          Wi‑Fi/cell accuracy
        </label>
      </div>

      <div className="flex flex-wrap gap-1">
        <button
          type="button"
          disabled={busy}
          className="rounded border border-amber-800 px-2 py-0.5 text-[10px] text-amber-200 disabled:opacity-40"
          onClick={() => void dropFixHere()}
        >
          Fix @ map center
        </button>
        <button
          type="button"
          disabled={busy || !operator}
          className="rounded border border-cyan-800 px-2 py-0.5 text-[10px] text-cyan-200 disabled:opacity-40"
          onClick={() => void dropBreadcrumb()}
        >
          Drop breadcrumb
        </button>
        <label className="flex items-center gap-1 text-[10px] text-slate-400">
          bearing°
          <input
            type="number"
            min={0}
            max={359}
            step={1}
            className="w-14 rounded border border-slate-700 bg-slate-950 px-1 py-0.5"
            value={bearing}
            onChange={(e) => setBearing(Number(e.target.value) || 0)}
          />
        </label>
        <button
          type="button"
          disabled={busy || !operator}
          className="rounded border border-emerald-800 px-2 py-0.5 text-[10px] text-emerald-200 disabled:opacity-40"
          onClick={() => void addBearing()}
        >
          Add LOB
        </button>
        <button
          type="button"
          disabled={busy}
          className="rounded border border-slate-600 px-2 py-0.5 text-[10px] disabled:opacity-40"
          onClick={() => void clearBearings()}
        >
          Clear LOBs
        </button>
        <button
          type="button"
          disabled={busy}
          className="rounded border border-violet-800 px-2 py-0.5 text-[10px] text-violet-200 disabled:opacity-40"
          onClick={() => void sampleGnss()}
        >
          GNSS L1 analyze
        </button>
        <button
          type="button"
          disabled={busy}
          className="rounded border border-slate-700 px-2 py-0.5 text-[10px] text-slate-400"
          onClick={() => void refresh()}
        >
          Refresh layers
        </button>
      </div>
    </div>
  );
}
