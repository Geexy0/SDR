import { useEffect, useRef, useState } from "react";
import L from "leaflet";
import { api, wsUrl } from "@/lib/api";
import { useHardwareStore } from "@/store/useHardwareStore";

type SensorRow = {
  sensor_id: string;
  last_ts: string;
  hit_count: number;
  last_model: string | null;
  oem_family: string | null;
  last_pressure_kpa: number | null;
  last_temp_c: number | null;
  last_lat: number | null;
  last_lon: number | null;
  vehicle_id: number | null;
};

type VehicleRow = {
  id: number;
  label: string;
  make: string | null;
  model: string | null;
  year: number | null;
  color: string | null;
  notes: string | null;
  sensor_ids: string[];
};

type PassageRow = {
  id: number;
  ts: string;
  cluster_key: string;
  vehicle_id: number | null;
  label: string | null;
  make: string | null;
  model_hint: string | null;
  oem_family: string | null;
  sensor_ids: string[];
  sensor_count: number;
  hit_count: number;
  lat: number | null;
  lon: number | null;
};

type Band = "eu433" | "us315" | "hop_both";

const BAND_LABEL: Record<Band, string> = {
  eu433: "EU 433.92",
  us315: "US 315",
  hop_both: "Hop 433+315",
};

export function TpmsFleetDeck() {
  const mapHostRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<L.Map | null>(null);
  const layerRef = useRef<L.LayerGroup | null>(null);
  const passLayerRef = useRef<L.LayerGroup | null>(null);
  const rxMarkerRef = useRef<L.CircleMarker | null>(null);
  const markersRef = useRef<Map<string, L.CircleMarker>>(new Map());

  const mapLat = useHardwareStore((s) => s.mapLat);
  const mapLon = useHardwareStore((s) => s.mapLon);
  const mapLocated = useHardwareStore((s) => s.mapLocated);
  const setMapCenter = useHardwareStore((s) => s.setMapCenter);
  const setToast = useHardwareStore((s) => s.setToast);

  const [listening, setListening] = useState(false);
  const [busy, setBusy] = useState(false);
  const [autoLink, setAutoLink] = useState(true);
  const [band, setBand] = useState<Band>("eu433");
  const [decodeCount, setDecodeCount] = useState(0);
  const [noiseDb, setNoiseDb] = useState<number | null>(null);
  const [sensors, setSensors] = useState<SensorRow[]>([]);
  const [vehicles, setVehicles] = useState<VehicleRow[]>([]);
  const [passages, setPassages] = useState<PassageRow[]>([]);
  const [openCluster, setOpenCluster] = useState<string>("");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [label, setLabel] = useState("");
  const [make, setMake] = useState("");
  const [model, setModel] = useState("");
  const [year, setYear] = useState("");
  const [color, setColor] = useState("");
  const [notes, setNotes] = useState("");
  const [log, setLog] = useState<string[]>([]);
  const [geoBusy, setGeoBusy] = useState(false);

  const refresh = async () => {
    const [s, v, p, st] = await Promise.all([
      api.tpmsSensors(),
      api.tpmsVehicles(),
      api.tpmsPassages(),
      api.tpmsStatus(),
    ]);
    setSensors((s.sensors as SensorRow[]) ?? []);
    setVehicles((v.vehicles as VehicleRow[]) ?? []);
    setPassages((p.passages as PassageRow[]) ?? []);
    setListening(Boolean(st.running));
    setDecodeCount(Number(st.decode_count ?? 0));
    const n = st.last_noise_db;
    setNoiseDb(typeof n === "number" ? n : n != null ? Number(n) : null);
    const oc = st.open_cluster as { cluster_key?: string; sensor_ids?: string[] } | null;
    setOpenCluster(
      oc?.cluster_key
        ? `${oc.cluster_key} (${(oc.sensor_ids ?? []).length} sensors)`
        : "",
    );
  };

  const locateMe = () => {
    if (!navigator.geolocation) {
      setToast("Browser geolocation unavailable — click the map to set RX site");
      return;
    }
    setGeoBusy(true);
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        setMapCenter(pos.coords.latitude, pos.coords.longitude, true);
        setToast(
          `RX site → ${pos.coords.latitude.toFixed(5)}, ${pos.coords.longitude.toFixed(5)}`,
        );
        setGeoBusy(false);
      },
      (err) => {
        setToast(`Geolocation failed (${err.message}) — click map to set RX site`);
        setGeoBusy(false);
      },
      { enableHighAccuracy: true, timeout: 12_000, maximumAge: 60_000 },
    );
  };

  useEffect(() => {
    if (!mapLocated) locateMe();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const host = mapHostRef.current;
    if (!host || mapRef.current) return;
    const map = L.map(host, { zoomControl: true, attributionControl: false });
    map.setView([mapLat, mapLon], 15);
    L.tileLayer(
      "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}",
      { maxZoom: 16 },
    ).addTo(map);
    rxMarkerRef.current = L.circleMarker([mapLat, mapLon], {
      radius: 8,
      color: "#ef4444",
      fillColor: "#ef4444",
      fillOpacity: 0.5,
      weight: 2,
    })
      .bindTooltip("RX site (click map to move)")
      .addTo(map);
    map.on("click", (e: L.LeafletMouseEvent) => {
      setMapCenter(e.latlng.lat, e.latlng.lng, true);
      setToast(`RX site → ${e.latlng.lat.toFixed(5)}, ${e.latlng.lng.toFixed(5)}`);
    });
    layerRef.current = L.layerGroup().addTo(map);
    passLayerRef.current = L.layerGroup().addTo(map);
    mapRef.current = map;
    const inv = () => map.invalidateSize({ animate: false });
    const t0 = window.setTimeout(inv, 50);
    const t1 = window.setTimeout(inv, 250);
    return () => {
      window.clearTimeout(t0);
      window.clearTimeout(t1);
      map.remove();
      mapRef.current = null;
      layerRef.current = null;
      passLayerRef.current = null;
      rxMarkerRef.current = null;
      markersRef.current.clear();
    };
    // Map host init once; pan follows mapLat/Lon in the effect below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    map.setView([mapLat, mapLon], map.getZoom() || 15);
    rxMarkerRef.current?.setLatLng([mapLat, mapLon]);
  }, [mapLat, mapLon]);

  useEffect(() => {
    void refresh().catch(() => undefined);
  }, []);

  useEffect(() => {
    if (!layerRef.current) return;
    for (const s of sensors) {
      const lat = s.last_lat ?? mapLat;
      const lon = s.last_lon ?? mapLon;
      const vehicle = vehicles.find((v) => v.id === s.vehicle_id);
      const title = vehicle
        ? `${vehicle.label} · ${[vehicle.make, vehicle.model].filter(Boolean).join(" ")} · ${s.sensor_id}`
        : `${s.oem_family ?? s.last_model ?? "TPMS"} · ${s.sensor_id}`;
      const colorHex = vehicle ? "#f59e0b" : "#38bdf8";
      const existing = markersRef.current.get(s.sensor_id);
      if (existing) {
        existing.setLatLng([lat, lon]);
        existing.setStyle({ color: colorHex, fillColor: colorHex });
        existing.setTooltipContent(title);
      } else if (layerRef.current) {
        const m = L.circleMarker([lat, lon], {
          radius: 6,
          color: colorHex,
          fillColor: colorHex,
          fillOpacity: 0.85,
          weight: 1,
        })
          .bindTooltip(title)
          .addTo(layerRef.current);
        m.on("click", () => selectSensor(s));
        markersRef.current.set(s.sensor_id, m);
      }
    }
  }, [sensors, vehicles, mapLat, mapLon]);

  useEffect(() => {
    const layer = passLayerRef.current;
    if (!layer) return;
    layer.clearLayers();
    passages.slice(0, 40).forEach((p, i) => {
      const lat = p.lat ?? mapLat;
      const lon = p.lon ?? mapLon;
      const jlat = lat + (i % 5) * 0.00008;
      const jlon = lon + Math.floor(i / 5) * 0.00008;
      const name = [p.label, p.make, p.model_hint].filter(Boolean).join(" · ");
      L.circleMarker([jlat, jlon], {
        radius: 5,
        color: "#a78bfa",
        fillColor: "#a78bfa",
        fillOpacity: 0.7,
        weight: 1,
      })
        .bindTooltip(`${p.ts.slice(11, 19)} · ${name} · ${p.sensor_count} IDs`)
        .addTo(layer);
    });
  }, [passages, mapLat, mapLon]);

  useEffect(() => {
    const ws = new WebSocket(wsUrl("/ws/telemetry"));
    ws.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data as string) as {
          message?: string;
          metrics?: Record<string, unknown>;
        };
        if (msg.message === "tpms_started") setListening(true);
        if (msg.message === "tpms_stopped") setListening(false);
        if (msg.message === "tpms_rx_health" && msg.metrics) {
          const n = msg.metrics.last_noise_db;
          if (typeof n === "number") setNoiseDb(n);
          const d = msg.metrics.decode_count;
          if (typeof d === "number") setDecodeCount(d);
        }
        if (msg.message === "tpms" && msg.metrics) {
          const sid = String(msg.metrics.sensor_id ?? "");
          const oem = String(msg.metrics.suggested_make ?? msg.metrics.oem_family ?? "");
          const cluster = String(msg.metrics.cluster_key ?? "");
          const line = `${new Date().toLocaleTimeString()} · ID ${sid}${
            oem ? ` · ${oem}` : ""
          }${cluster ? ` · ${cluster}` : ""}`;
          setLog((prev) => [...prev.slice(-120), line]);
          if (typeof msg.metrics.decode_count === "number") {
            setDecodeCount(msg.metrics.decode_count);
          }
          void refresh().catch(() => undefined);
        }
        if (msg.message === "tpms_passage" && msg.metrics) {
          const labelP = String(msg.metrics.label ?? "passage");
          const makeP = String(msg.metrics.make ?? "");
          setLog((prev) => [
            ...prev.slice(-120),
            `${new Date().toLocaleTimeString()} · PASSAGE ${labelP}${makeP ? ` · ${makeP}` : ""}`,
          ]);
          void refresh().catch(() => undefined);
        }
      } catch {
        /* ignore */
      }
    };
    return () => ws.close();
  }, []);

  const selectSensor = (s: SensorRow) => {
    setSelectedId(s.sensor_id);
    const v = vehicles.find((x) => x.id === s.vehicle_id);
    if (v) {
      setLabel(v.label);
      setMake(v.make ?? "");
      setModel(v.model ?? "");
      setYear(v.year != null ? String(v.year) : "");
      setColor(v.color ?? "");
      setNotes(v.notes ?? "");
    } else {
      setLabel(
        s.oem_family
          ? `${s.oem_family.split("/")[0]}-${s.sensor_id.slice(-4)}`
          : `Car-${s.sensor_id.slice(-4)}`,
      );
      setMake(s.oem_family?.split("/")[0] ?? "");
      setModel(s.last_model ?? "");
    }
  };

  const startListen = async () => {
    if (!mapLocated) {
      setToast("Set your location first (Locate me, or click the map)");
      return;
    }
    setBusy(true);
    try {
      await api.tpmsListen({
        frequency_hz: band === "us315" ? 315_000_000 : 433_920_000,
        sample_rate: 2_000_000,
        receiver_lat: mapLat,
        receiver_lon: mapLon,
        auto_link_vehicles: autoLink,
        tpms_only: true,
        band,
        gain: "LNA=32,VGA=40,AMP=0",
      });
      setListening(true);
      setToast(`TPMS mapper LIVE · ${BAND_LABEL[band]} @ your RX site`);
    } catch (err) {
      setToast(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const stopListen = async () => {
    setBusy(true);
    try {
      await api.tpmsListenStop();
      setListening(false);
      setToast("TPMS listen stopped");
      await refresh();
    } catch (err) {
      setToast(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const clearFleet = async () => {
    if (!window.confirm("Clear all TPMS sensors / cars / passages?")) return;
    setBusy(true);
    try {
      if (listening) await api.tpmsListenStop();
      await api.tpmsClear();
      setListening(false);
      setLog([]);
      setToast("TPMS fleet cleared");
      await refresh();
    } catch (err) {
      setToast(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const saveVehicle = async () => {
    if (!label.trim()) {
      setToast("Vehicle label required");
      return;
    }
    setBusy(true);
    try {
      const sensor_ids = selectedId ? [selectedId] : [];
      await api.tpmsUpsertVehicle({
        label: label.trim(),
        make: make || null,
        model: model || null,
        year: year ? Number(year) : null,
        color: color || null,
        notes: notes || null,
        sensor_ids,
      });
      setToast(`Linked ${selectedId ?? "?"} → ${label.trim()}`);
      await refresh();
    } catch (err) {
      setToast(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const selected = sensors.find((s) => s.sensor_id === selectedId) ?? null;
  const rxHealth =
    listening && noiseDb != null
      ? `noise ${noiseDb.toFixed(1)} dB · decodes ${decodeCount}`
      : listening
        ? `waiting for IQ… decodes ${decodeCount}`
        : "idle";

  return (
    <div className="grid h-full min-h-0 grid-rows-[auto_minmax(260px,1fr)_auto] gap-2 overflow-auto p-2 lg:grid-cols-[1.2fr_340px] lg:grid-rows-[auto_1fr]">
      <section className="panel col-span-full rounded p-3 lg:col-span-2">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div>
            <h3 className="text-[10px] uppercase tracking-wider text-slate-400">
              TPMS Car Spy Mapper
            </h3>
            <p className="max-w-3xl text-[9px] text-slate-500">
              Listen for tire-pressure sensors on passing cars, assign stable IDs, auto-cluster
              co-heard tires into a vehicle, and suggest make from public rtl_433 protocol
              families. Refine make/model manually. RX remit — you own privacy compliance.
            </p>
            <p
              className={`mt-1 text-[9px] ${
                mapLocated ? "text-emerald-400/90" : "text-amber-400/90"
              }`}
            >
              RX site {mapLocated ? "set" : "NOT SET (Paris placeholder)"}:{" "}
              {mapLat.toFixed(5)}, {mapLon.toFixed(5)} — click map or Locate me.
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <select
              className="rounded border border-slate-700 bg-slate-950 px-2 py-1 text-[10px] text-slate-200"
              value={band}
              disabled={listening}
              onChange={(e) => setBand(e.target.value as Band)}
            >
              <option value="eu433">EU 433.92 MHz</option>
              <option value="us315">US 315 MHz</option>
              <option value="hop_both">Hop 433.92 + 315</option>
            </select>
            <label className="flex items-center gap-1 text-[10px] text-slate-400">
              <input
                type="checkbox"
                checked={autoLink}
                disabled={listening}
                onChange={(e) => setAutoLink(e.target.checked)}
              />
              auto-link cars
            </label>
            <button
              type="button"
              disabled={busy || geoBusy}
              className="rounded border border-slate-600 px-2 py-1 text-[10px] disabled:opacity-40"
              onClick={() => locateMe()}
            >
              {geoBusy ? "Locating…" : "Locate me"}
            </button>
            <button
              type="button"
              disabled={busy || listening}
              className="rounded border border-cyan-700 bg-cyan-950/40 px-2 py-1 text-[10px] text-cyan-200 disabled:opacity-40"
              onClick={() => void startListen()}
            >
              Start Mapper
            </button>
            <button
              type="button"
              disabled={busy || !listening}
              className="rounded border border-slate-600 px-2 py-1 text-[10px] disabled:opacity-40"
              onClick={() => void stopListen()}
            >
              Stop
            </button>
            <button
              type="button"
              disabled={busy}
              className="rounded border border-rose-900 px-2 py-1 text-[10px] text-rose-300 disabled:opacity-40"
              onClick={() => void clearFleet()}
            >
              Clear fleet
            </button>
            <span className="text-[9px] text-slate-500">
              {listening ? `LIVE ${BAND_LABEL[band]}` : "idle"} · {rxHealth} · IDs{" "}
              {sensors.length} · cars {vehicles.length} · passes {passages.length}
              {openCluster ? ` · open ${openCluster}` : ""}
            </span>
          </div>
        </div>
      </section>

      <section className="panel flex min-h-[260px] flex-col overflow-hidden rounded">
        <div className="border-b border-slate-800 px-2 py-1 text-[10px] uppercase tracking-wider text-slate-400">
          Map · click = RX site · cyan=sensor · amber=linked · violet=passage
        </div>
        <div ref={mapHostRef} className="min-h-[240px] flex-1" />
        <pre className="max-h-28 overflow-auto border-t border-slate-800 bg-black/50 p-2 text-[10px] text-cyan-200/90">
          {log.length === 0
            ? "waiting for TPMS frames from passing cars (sensors TX while rolling)…"
            : log.join("\n")}
        </pre>
      </section>

      <section className="panel flex min-h-0 flex-col gap-2 overflow-auto rounded p-2">
        <h4 className="text-[10px] uppercase tracking-wider text-slate-400">Recent passages</h4>
        <ul className="max-h-28 space-y-1 overflow-auto text-[10px]">
          {passages.length === 0 && (
            <li className="text-slate-600">No passages yet — cars seal after ~18s quiet.</li>
          )}
          {passages.slice(0, 25).map((p) => (
            <li key={p.id} className="rounded border border-slate-800 px-2 py-1 text-slate-300">
              <div className="text-violet-300">
                {p.ts.slice(11, 19)} · {p.label ?? p.cluster_key}
              </div>
              <div className="text-slate-500">
                {[p.make, p.model_hint, p.oem_family].filter(Boolean).join(" · ")} ·{" "}
                {p.sensor_count} sensors · {p.hit_count} hits
              </div>
            </li>
          ))}
        </ul>

        <h4 className="text-[10px] uppercase tracking-wider text-slate-400">Sensor IDs</h4>
        <ul className="max-h-36 space-y-1 overflow-auto text-[10px]">
          {sensors.map((s) => {
            const v = vehicles.find((x) => x.id === s.vehicle_id);
            return (
              <li key={s.sensor_id}>
                <button
                  type="button"
                  className={`w-full rounded border px-2 py-1 text-left ${
                    selectedId === s.sensor_id
                      ? "border-amber-600 bg-amber-950/30 text-amber-100"
                      : "border-slate-800 text-slate-300"
                  }`}
                  onClick={() => selectSensor(s)}
                >
                  <div className="font-mono text-cyan-300">{s.sensor_id}</div>
                  <div className="text-slate-500">
                    {s.oem_family ?? s.last_model ?? "unknown"} · hits {s.hit_count}
                    {s.last_pressure_kpa != null
                      ? ` · ${s.last_pressure_kpa.toFixed(0)} kPa`
                      : ""}
                    {v ? ` · ${v.label}` : ""}
                  </div>
                </button>
              </li>
            );
          })}
        </ul>

        <h4 className="text-[10px] uppercase tracking-wider text-slate-400">
          Refine car tag
        </h4>
        <p className="text-[9px] text-slate-500">
          Selected: {selected?.sensor_id ?? "none"}. Protocol suggests make; edit for accuracy.
        </p>
        <label className="text-[10px] text-slate-500">
          Label
          <input
            className="mt-0.5 w-full rounded border border-slate-700 bg-slate-950 px-2 py-1 text-xs"
            value={label}
            onChange={(e) => setLabel(e.target.value)}
          />
        </label>
        <div className="grid grid-cols-2 gap-1">
          <label className="text-[10px] text-slate-500">
            Make
            <input
              className="mt-0.5 w-full rounded border border-slate-700 bg-slate-950 px-1 py-0.5 text-[10px]"
              value={make}
              onChange={(e) => setMake(e.target.value)}
            />
          </label>
          <label className="text-[10px] text-slate-500">
            Model
            <input
              className="mt-0.5 w-full rounded border border-slate-700 bg-slate-950 px-1 py-0.5 text-[10px]"
              value={model}
              onChange={(e) => setModel(e.target.value)}
            />
          </label>
          <label className="text-[10px] text-slate-500">
            Year
            <input
              className="mt-0.5 w-full rounded border border-slate-700 bg-slate-950 px-1 py-0.5 text-[10px]"
              value={year}
              onChange={(e) => setYear(e.target.value)}
            />
          </label>
          <label className="text-[10px] text-slate-500">
            Color
            <input
              className="mt-0.5 w-full rounded border border-slate-700 bg-slate-950 px-1 py-0.5 text-[10px]"
              value={color}
              onChange={(e) => setColor(e.target.value)}
            />
          </label>
        </div>
        <label className="text-[10px] text-slate-500">
          Notes
          <textarea
            className="mt-0.5 w-full rounded border border-slate-700 bg-slate-950 px-2 py-1 text-[10px]"
            rows={2}
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
          />
        </label>
        <button
          type="button"
          disabled={busy || !selectedId}
          className="rounded border border-amber-700 bg-amber-950/40 px-2 py-1 text-[10px] text-amber-200 disabled:opacity-40"
          onClick={() => void saveVehicle()}
        >
          Save / link selected ID
        </button>

        <h4 className="mt-1 text-[10px] uppercase tracking-wider text-slate-400">Cars</h4>
        <ul className="space-y-1 text-[10px] text-slate-300">
          {vehicles.map((v) => (
            <li key={v.id} className="rounded border border-slate-800 px-2 py-1">
              <div className="text-amber-200">
                {v.label}
                {v.make || v.model
                  ? ` · ${[v.year, v.make, v.model].filter(Boolean).join(" ")}`
                  : ""}
              </div>
              <div className="font-mono text-[9px] text-slate-500">
                {v.sensor_ids.join(", ") || "no sensors"}
              </div>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
