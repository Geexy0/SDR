import { useCallback, useEffect, useRef, useState } from "react";
import L from "leaflet";
import { api, type GeolocOperator, type HeatCell, type LobRow, type VoiceRecordingRow } from "@/lib/api";
import { closestPointOfApproach, formatRange, haversineM } from "@/lib/geoMath";
import { useHardwareStore } from "@/store/useHardwareStore";
import { LocationIntelPanel } from "./LocationIntelPanel";
import { MissionControlDrawer } from "./MissionControlDrawer";
import { CtcssPill, WebAudioPlayer } from "./WebAudioPlayer";

type TrackMode = "idle" | "adsb" | "ais";

export function ReconSpoofDeck() {
  const mapHostRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<L.Map | null>(null);
  const pinRef = useRef<L.Marker | null>(null);
  const aircraftLayer = useRef<L.LayerGroup | null>(null);
  const vesselLayer = useRef<L.LayerGroup | null>(null);
  const rangeLayer = useRef<L.Polygon | null>(null);
  const heatLayer = useRef<L.LayerGroup | null>(null);
  const lobLayer = useRef<L.LayerGroup | null>(null);
  const ringLayer = useRef<L.LayerGroup | null>(null);
  const operatorLayer = useRef<L.LayerGroup | null>(null);
  const aircraftMarkers = useRef<Map<string, L.CircleMarker>>(new Map());
  const vesselMarkers = useRef<Map<string, L.CircleMarker>>(new Map());
  const aircraftSource = useRef<Map<string, string>>(new Map());
  const operatorRef = useRef<GeolocOperator | null>(null);

  const pocsagLines = useHardwareStore((s) => s.pocsagLines);
  const pushPocsag = useHardwareStore((s) => s.pushPocsag);
  const mapLat = useHardwareStore((s) => s.mapLat);
  const mapLon = useHardwareStore((s) => s.mapLon);
  const mapAlt = useHardwareStore((s) => s.mapAlt);
  const spoofPin = useHardwareStore((s) => s.spoofPin);
  const ephemerisPath = useHardwareStore((s) => s.ephemerisPath);
  const frequencyHz = useHardwareStore((s) => s.frequencyHz);
  const txVgaDb = useHardwareStore((s) => s.txVgaDb);
  const maxDurationSec = useHardwareStore((s) => s.maxDurationSec);
  const setSpoofPin = useHardwareStore((s) => s.setSpoofPin);
  const setMapAlt = useHardwareStore((s) => s.setMapAlt);
  const setEphemerisPath = useHardwareStore((s) => s.setEphemerisPath);
  const setToast = useHardwareStore((s) => s.setToast);

  const [pagerId, setPagerId] = useState(7);
  const [ttPin, setTtPin] = useState("1234");
  const [rdsPs, setRdsPs] = useState("LAB-FM");
  const [rdsRt, setRdsRt] = useState("PP-LAB");
  const [fmHz, setFmHz] = useState(100_000_000);
  const [trackMode, setTrackMode] = useState<TrackMode>("idle");
  const [trackBusy, setTrackBusy] = useState(false);
  const [aircraftCount, setAircraftCount] = useState(0);
  const [vesselCount, setVesselCount] = useState(0);
  const [voiceBusy, setVoiceBusy] = useState(false);
  const [voiceRunning, setVoiceRunning] = useState(false);
  const [voicePhase, setVoicePhase] = useState("idle");
  const [voiceBand, setVoiceBand] = useState<string | null>(null);
  const [voiceFreq, setVoiceFreq] = useState<number | null>(null);
  const [voiceRssi, setVoiceRssi] = useState<number | null>(null);
  const [voiceGain, setVoiceGain] = useState({ lna: 24, vga: 20 });
  const [voiceLog, setVoiceLog] = useState<string[]>([]);
  const [voiceBands, setVoiceBands] = useState<
    { id: string; name: string; note?: string; default_enabled?: boolean }[]
  >([]);
  const [enabledBands, setEnabledBands] = useState<Record<string, boolean>>({});
  const [localAplay, setLocalAplay] = useState(false);
  const [voiceCtcss, setVoiceCtcss] = useState<number | null>(null);
  const [recordings, setRecordings] = useState<VoiceRecordingRow[]>([]);
  const [recsOpen, setRecsOpen] = useState(true);
  const terminalRef = useRef<HTMLPreElement>(null);

  const refreshRecordings = async () => {
    try {
      const r = await api.voiceRecordings(40);
      setRecordings(r.recordings ?? []);
    } catch {
      /* ignore */
    }
  };

  const trackColor = (source: string | undefined, fallback: string) => {
    const s = (source || "").toUpperCase();
    if (s === "LOCAL_RF" || s === "HYBRID") return { color: "#34d399", fill: "#22d3ee", opacity: 0.95, radius: 7 };
    if (s === "OPENSKY_NET") return { color: "#64748b", fill: "#475569", opacity: 0.35, radius: 4 };
    return { color: fallback, fill: fallback, opacity: 0.85, radius: 5 };
  };

  const upsertTrack = (
    layer: L.LayerGroup | null,
    markers: Map<string, L.CircleMarker>,
    id: string,
    lat: number,
    lon: number,
    label: string,
    color: string,
    onCount: (n: number) => void,
    source?: string,
    speedMps?: number,
    courseDeg?: number,
  ) => {
    if (!layer || !Number.isFinite(lat) || !Number.isFinite(lon)) return;
    const style = trackColor(source, color);
    const op = operatorRef.current;
    let tip = source ? `${label} · ${source}` : label;
    if (op) {
      const rng = haversineM(op.lat, op.lon, lat, lon);
      tip += ` · ${formatRange(rng)}`;
      if (speedMps != null && speedMps > 0) {
        const cpa = closestPointOfApproach(
          { lat: op.lat, lon: op.lon, speed_mps: 0, course_deg: 0 },
          { lat, lon, speed_mps: speedMps, course_deg: courseDeg ?? 0 },
        );
        tip += ` · CPA ${formatRange(cpa.range_m)} @ ${Math.round(cpa.time_s)}s`;
      }
    }
    const existing = markers.get(id);
    if (existing) {
      existing.setLatLng([lat, lon]);
      existing.setTooltipContent(tip);
      existing.setStyle({
        color: style.color,
        fillColor: style.fill,
        fillOpacity: style.opacity,
        radius: style.radius,
        weight: source === "OPENSKY_NET" ? 1 : 2,
      });
      if (source) aircraftSource.current.set(id, source);
      return;
    }
    const marker = L.circleMarker([lat, lon], {
      radius: style.radius,
      color: style.color,
      weight: source === "OPENSKY_NET" ? 1 : 2,
      fillColor: style.fill,
      fillOpacity: style.opacity,
    })
      .bindTooltip(tip, { permanent: false, direction: "top" })
      .addTo(layer);
    markers.set(id, marker);
    if (source) aircraftSource.current.set(id, source);
    onCount(markers.size);
  };

  const applyLocationLayers = useCallback(
    (layers: {
      heatmap: HeatCell[];
      lobs: LobRow[];
      rings: { radius_m: number; polygon: [number, number][] }[];
      intersection: { lat: number; lon: number } | null;
      operator: GeolocOperator | null;
      showHeat: boolean;
      showRings: boolean;
      showLobs: boolean;
      showWifiCell: boolean;
    }) => {
      operatorRef.current = layers.operator;
      const map = mapRef.current;
      if (!map) return;
      if (!heatLayer.current) heatLayer.current = L.layerGroup().addTo(map);
      if (!lobLayer.current) lobLayer.current = L.layerGroup().addTo(map);
      if (!ringLayer.current) ringLayer.current = L.layerGroup().addTo(map);
      if (!operatorLayer.current) operatorLayer.current = L.layerGroup().addTo(map);

      heatLayer.current.clearLayers();
      if (layers.showHeat) {
        for (const c of layers.heatmap) {
          const r = Math.min(18, 4 + Math.sqrt(c.hit_count) * 3);
          L.circleMarker([c.cell_lat, c.cell_lon], {
            radius: r,
            color: "#f97316",
            fillColor: "#ea580c",
            fillOpacity: Math.min(0.55, 0.15 + c.hit_count * 0.08),
            weight: 1,
          })
            .bindTooltip(
              `hits ${c.hit_count}` +
                (c.peak_rssi_db != null ? ` · peak ${c.peak_rssi_db.toFixed(1)} dB` : ""),
            )
            .addTo(heatLayer.current);
        }
      }

      lobLayer.current.clearLayers();
      if (layers.showLobs) {
        for (const lob of layers.lobs) {
          const latlngs = (lob.ray ?? []).map(([la, lo]) => L.latLng(la, lo));
          if (latlngs.length >= 2) {
            L.polyline(latlngs, {
              color: "#22d3ee",
              weight: 2,
              dashArray: "4 6",
              opacity: 0.85,
            })
              .bindTooltip(lob.label || `${lob.bearing_deg.toFixed(0)}°`)
              .addTo(lobLayer.current);
          }
          L.circleMarker([lob.lat, lob.lon], {
            radius: 4,
            color: "#67e8f9",
            fillColor: "#0891b2",
            fillOpacity: 0.9,
          }).addTo(lobLayer.current);
        }
        if (layers.intersection) {
          L.circleMarker([layers.intersection.lat, layers.intersection.lon], {
            radius: 8,
            color: "#f472b6",
            fillColor: "#db2777",
            fillOpacity: 0.9,
            weight: 2,
          })
            .bindTooltip("LOB intersection", { permanent: true, direction: "top" })
            .addTo(lobLayer.current);
        }
      }

      ringLayer.current.clearLayers();
      if (layers.showRings) {
        for (const ring of layers.rings) {
          const latlngs = ring.polygon.map(([la, lo]) => L.latLng(la, lo));
          L.polygon(latlngs, {
            color: "#fbbf24",
            weight: 1,
            dashArray: "2 8",
            fillOpacity: 0.03,
          })
            .bindTooltip(`${(ring.radius_m / 1000).toFixed(1)} km`)
            .addTo(ringLayer.current);
        }
      }

      operatorLayer.current.clearLayers();
      if (layers.operator) {
        L.circleMarker([layers.operator.lat, layers.operator.lon], {
          radius: 7,
          color: "#fde68a",
          fillColor: "#f59e0b",
          fillOpacity: 0.95,
          weight: 2,
        })
          .bindTooltip("Operator", { permanent: false })
          .addTo(operatorLayer.current);
        if (layers.showWifiCell && layers.operator.accuracy_m != null) {
          L.circle([layers.operator.lat, layers.operator.lon], {
            radius: Math.max(25, layers.operator.accuracy_m),
            color: "#a78bfa",
            weight: 1,
            dashArray: "3 5",
            fillColor: "#7c3aed",
            fillOpacity: 0.08,
          })
            .bindTooltip("Browser Wi‑Fi/cell accuracy")
            .addTo(operatorLayer.current);
        }
      }
    },
    [],
  );

  useEffect(() => {
    const host = mapHostRef.current;
    if (!host || mapRef.current) return;

    const map = L.map(host, {
      zoomControl: true,
      attributionControl: false,
    });
    map.setView([useHardwareStore.getState().mapLat, useHardwareStore.getState().mapLon], 11);
    // Key-free dark basemap (Carto CDN often blocked / rate-limited in some envs).
    L.tileLayer(
      "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}",
      { maxZoom: 16, attribution: "Esri" },
    ).addTo(map);
    L.tileLayer(
      "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Reference/MapServer/tile/{z}/{y}/{x}",
      { maxZoom: 16, opacity: 0.85 },
    ).addTo(map);
    aircraftLayer.current = L.layerGroup().addTo(map);
    vesselLayer.current = L.layerGroup().addTo(map);
    heatLayer.current = L.layerGroup().addTo(map);
    lobLayer.current = L.layerGroup().addTo(map);
    ringLayer.current = L.layerGroup().addTo(map);
    operatorLayer.current = L.layerGroup().addTo(map);
    // Legacy lab pin (operator GPS supersedes when available)
    L.circleMarker([49.1182, -1.0972], {
      radius: 5,
      color: "#64748b",
      fillColor: "#475569",
      fillOpacity: 0.7,
      weight: 1,
    })
      .bindTooltip("Lab default", { permanent: false })
      .addTo(map);
    map.on("click", (e: L.LeafletMouseEvent) => {
      useHardwareStore.getState().setSpoofPin({ lat: e.latlng.lat, lon: e.latlng.lng });
    });
    mapRef.current = map;

    const invalidate = () => map.invalidateSize({ animate: false });
    const t0 = window.setTimeout(invalidate, 50);
    const t1 = window.setTimeout(invalidate, 250);
    const ro = new ResizeObserver(invalidate);
    ro.observe(host);

    return () => {
      window.clearTimeout(t0);
      window.clearTimeout(t1);
      ro.disconnect();
      map.remove();
      mapRef.current = null;
      aircraftLayer.current = null;
      vesselLayer.current = null;
      pinRef.current = null;
    };
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    if (!spoofPin) {
      pinRef.current?.remove();
      pinRef.current = null;
      return;
    }
    const icon = L.divIcon({
      className: "",
      html: `<div style="width:12px;height:12px;border:2px solid #ef4444;background:#ef4444aa;border-radius:50%;box-shadow:0 0 0 2px #020617"></div>`,
      iconSize: [12, 12],
      iconAnchor: [6, 6],
    });
    if (!pinRef.current) {
      pinRef.current = L.marker([spoofPin.lat, spoofPin.lon], {
        title: "GPS Spoof Target",
        icon,
      }).addTo(map);
    } else {
      pinRef.current.setLatLng([spoofPin.lat, spoofPin.lon]);
      pinRef.current.setIcon(icon);
    }
  }, [spoofPin]);

  const telemetrySeq = useHardwareStore((s) => s.telemetrySeq);
  const lastTelemetry = useHardwareStore((s) => s.lastTelemetry);

  useEffect(() => {
    // Consume shared HardwareHeader /ws/telemetry via Zustand (no second socket).
    const msg = lastTelemetry;
    if (!msg) return;
    try {
      if (msg.message === "pocsag" || msg.message === "decoder_line") {
        const line = String(
          msg.metrics?.raw ?? msg.metrics?.line ?? msg.metrics?.text ?? "",
        );
        if (line) pushPocsag(line);
      }
      if (msg.message?.startsWith("decoder_started:")) {
        const kind = msg.message.split(":")[1];
        if (kind === "adsb" || kind === "ais") setTrackMode(kind);
      }
      if (msg.message === "decoder_stopped") {
        setTrackMode("idle");
      }
      if (msg.message === "voice_recon" && msg.metrics) {
        const m = msg.metrics;
        const phase = String(m.phase ?? "");
        if (phase) setVoicePhase(phase);
        setVoiceRunning(Boolean(m.running));
        setVoiceBand(m.band != null ? String(m.band) : null);
        setVoiceFreq(
          typeof m.listen_hz === "number"
            ? m.listen_hz
            : typeof m.frequency_hz === "number"
              ? m.frequency_hz
              : null,
        );
        if (typeof m.listen_rssi_db === "number") setVoiceRssi(m.listen_rssi_db);
        else if (typeof m.rssi_db === "number") setVoiceRssi(m.rssi_db);
        if (typeof m.lna === "number" && typeof m.vga === "number") {
          setVoiceGain({ lna: m.lna, vga: m.vga });
        }
        if ("ctcss_hz" in m) {
          setVoiceCtcss(typeof m.ctcss_hz === "number" ? m.ctcss_hz : null);
        }
        const ev = String(m.event ?? "");
        if (ev && ev !== "dwell_tick") {
          const mhz =
            typeof m.frequency_hz === "number"
              ? ` ${(m.frequency_hz / 1e6).toFixed(4)} MHz`
              : "";
          const pl =
            typeof m.ctcss_hz === "number" ? ` · PL ${m.ctcss_hz.toFixed(1)}` : "";
          const line = `${new Date().toLocaleTimeString()} · ${ev}${mhz}${pl}`;
          setVoiceLog((prev) => [...prev.slice(-80), line]);
        }
        if (ev === "vox_saved") {
          void refreshRecordings();
        }
        if (ev === "stopped") {
          setVoiceRunning(false);
          setVoicePhase("idle");
          setVoiceCtcss(null);
        }
      }
      if (msg.message === "adsb_meta" && msg.metrics) {
        const poly = msg.metrics.range_polygon as number[][] | undefined;
        const map = mapRef.current;
        if (map && Array.isArray(poly) && poly.length >= 3) {
          const latlngs = poly.map((p) => L.latLng(Number(p[0]), Number(p[1])));
          if (rangeLayer.current) {
            rangeLayer.current.setLatLngs(latlngs);
          } else {
            rangeLayer.current = L.polygon(latlngs, {
              color: "#34d399",
              weight: 1.5,
              dashArray: "6 4",
              fillColor: "#22d3ee",
              fillOpacity: 0.08,
            })
              .bindTooltip("Max LOCAL_RF reception range", { sticky: true })
              .addTo(map);
          }
        }
      }
      if (msg.message === "adsb") {
        const lat = Number(msg.metrics?.lat);
        const lon = Number(msg.metrics?.lon);
        const icao = String(msg.metrics?.icao ?? "").toUpperCase();
        const call = String(msg.metrics?.callsign ?? (icao || "AC")).trim();
        const source = String(msg.metrics?.source ?? "");
        const id = icao || `${lat.toFixed(3)},${lon.toFixed(3)}`;
        const speed =
          typeof msg.metrics?.speed_mps === "number"
            ? msg.metrics.speed_mps
            : typeof msg.metrics?.gs === "number"
              ? Number(msg.metrics.gs) * 0.514444
              : undefined;
        const course =
          typeof msg.metrics?.track === "number"
            ? Number(msg.metrics.track)
            : typeof msg.metrics?.course_deg === "number"
              ? Number(msg.metrics.course_deg)
              : undefined;
        upsertTrack(
          aircraftLayer.current,
          aircraftMarkers.current,
          id,
          lat,
          lon,
          call,
          "#22d3ee",
          setAircraftCount,
          source,
          speed,
          course,
        );
      }
      if (msg.message === "ais") {
        const lat = Number(msg.metrics?.lat);
        const lon = Number(msg.metrics?.lon);
        const mmsi = String(msg.metrics?.mmsi ?? "");
        const name = String(msg.metrics?.name ?? (mmsi || "VES"));
        const id = mmsi || `${lat.toFixed(3)},${lon.toFixed(3)}`;
        const speed =
          typeof msg.metrics?.speed_mps === "number"
            ? msg.metrics.speed_mps
            : typeof msg.metrics?.sog === "number"
              ? Number(msg.metrics.sog) * 0.514444
              : undefined;
        const course =
          typeof msg.metrics?.cog === "number"
            ? Number(msg.metrics.cog)
            : typeof msg.metrics?.course_deg === "number"
              ? Number(msg.metrics.course_deg)
              : undefined;
        upsertTrack(
          vesselLayer.current,
          vesselMarkers.current,
          id,
          lat,
          lon,
          name,
          "#34d399",
          setVesselCount,
          undefined,
          speed,
          course,
        );
      }
    } catch {
      /* ignore */
    }
  }, [telemetrySeq, lastTelemetry, pushPocsag]);

  const startTracks = async (kind: "adsb" | "ais") => {
    setTrackBusy(true);
    try {
      const lat = spoofPin?.lat ?? mapLat;
      const lon = spoofPin?.lon ?? mapLon;
      const res = (await api.startDecoder({
        kind,
        frequency_hz: kind === "adsb" ? 1_090_000_000 : 161_975_000,
        lat,
        lon,
      })) as { cmd?: string };
      setTrackMode(kind);
      const cmd = String(res.cmd ?? "");
      const hybrid = cmd.includes("opensky") && (cmd.includes("soft_modes") || cmd.includes("hackrf"));
      setToast(
        kind === "adsb"
          ? hybrid
            ? "Aircraft tracking (hybrid LOCAL_RF + OpenSky)"
            : cmd.includes("opensky")
              ? "Aircraft tracking (OpenSky network)"
              : "Aircraft tracking (ADS-B 1090 MHz LOCAL_RF)"
          : "Boat tracking (AIS via HackRF)",
      );
    } catch (err) {
      setTrackMode("idle");
      setToast(err instanceof Error ? err.message : String(err));
    } finally {
      setTrackBusy(false);
    }
  };

  const stopTracks = async () => {
    setTrackBusy(true);
    try {
      await api.stopDecoder();
      setTrackMode("idle");
      setToast("Tracking stopped");
    } catch (err) {
      setToast(err instanceof Error ? err.message : String(err));
    } finally {
      setTrackBusy(false);
    }
  };

  const clearTracks = () => {
    aircraftLayer.current?.clearLayers();
    vesselLayer.current?.clearLayers();
    rangeLayer.current?.remove();
    rangeLayer.current = null;
    aircraftMarkers.current.clear();
    vesselMarkers.current.clear();
    aircraftSource.current.clear();
    setAircraftCount(0);
    setVesselCount(0);
  };

  const bookmarkFreq = async (hz: number, description: string, source: string) => {
    try {
      const r = await api.freqmanBookmark({
        frequency_hz: Math.round(hz),
        description,
        category: "RECON",
        source,
      });
      setToast(`FREQMAN ${r.category}: ${description || hz} (${r.count} entries)`);
    } catch (err) {
      setToast(err instanceof Error ? err.message : String(err));
    }
  };

  useEffect(() => {
    void refreshRecordings();
    void api
      .reconStatus()
      .then((st) => {
        const bands = (st.voice_bands as typeof voiceBands) ?? [];
        setVoiceBands(bands);
        const init: Record<string, boolean> = {};
        for (const b of bands) {
          init[b.id] = b.default_enabled !== false;
        }
        setEnabledBands(init);
        const vr = st.voice_recon as { running?: boolean; phase?: string } | undefined;
        if (vr?.running) {
          setVoiceRunning(true);
          setVoicePhase(String(vr.phase ?? "scanning"));
        }
      })
      .catch(() => {
        /* ignore */
      });
  }, []);

  const startVoice = async () => {
    setVoiceBusy(true);
    try {
      const bands = Object.entries(enabledBands)
        .filter(([, on]) => on)
        .map(([id]) => id);
      await api.startVoiceRecon({
        bands,
        local_aplay: localAplay,
        audio_enabled: true,
        start_lna: 24,
        start_vga: 20,
        squelch_db: -50,
        vox_open_hold_sec: 0.5,
      });
      setVoiceRunning(true);
      setVoicePhase("scanning");
      setToast("Auto Voice Recon started — browser audio + VOX logger");
      void refreshRecordings();
    } catch (err) {
      setToast(err instanceof Error ? err.message : String(err));
    } finally {
      setVoiceBusy(false);
    }
  };

  const stopVoice = async () => {
    setVoiceBusy(true);
    try {
      await api.stopVoiceRecon();
      setVoiceRunning(false);
      setVoicePhase("idle");
      setVoiceFreq(null);
      setToast("Auto Voice Recon stopped");
    } catch (err) {
      setToast(err instanceof Error ? err.message : String(err));
    } finally {
      setVoiceBusy(false);
    }
  };

  useEffect(() => {
    terminalRef.current?.scrollTo({ top: terminalRef.current.scrollHeight });
  }, [pocsagLines]);

  const synth = async (body: Record<string, unknown>) => {
    try {
      await api.synthesize(body);
      setToast(`TX synthesize ${String(body.kind)}`);
    } catch (err) {
      setToast(err instanceof Error ? err.message : String(err));
    }
  };

  return (
    <div className="grid h-full min-h-0 grid-rows-[minmax(280px,1fr)_auto] gap-2 overflow-auto p-2 lg:grid-cols-2 lg:grid-rows-[minmax(280px,1fr)_auto]">
      <section className="panel flex min-h-[240px] flex-col overflow-hidden rounded lg:min-h-0">
        <div className="flex items-center justify-between border-b border-slate-800 px-2 py-1">
          <h3 className="text-[10px] uppercase tracking-wider text-slate-400">
            POCSAG / FLEX Terminal
          </h3>
          <div className="flex gap-1">
            <button
              type="button"
              className="rounded border border-cyan-800 px-2 py-0.5 text-[10px] text-cyan-300"
              onClick={() =>
                void api
                  .startDecoder({ kind: "pocsag", frequency_hz: Math.round(frequencyHz) })
                  .then(() => setToast("POCSAG decoder"))
                  .catch((e: Error) => setToast(e.message))
              }
            >
              Start 1200
            </button>
            <button
              type="button"
              className="rounded border border-slate-700 px-2 py-0.5 text-[10px]"
              onClick={() => void api.stopDecoder()}
            >
              Stop
            </button>
          </div>
        </div>
        <pre
          ref={terminalRef}
          className="min-h-0 flex-1 overflow-auto bg-black/60 p-2 text-[10px] leading-relaxed text-emerald-400"
        >
          {pocsagLines.length === 0
            ? "waiting for pager traffic…\ncapcode · baud · function · ascii"
            : pocsagLines.join("\n")}
        </pre>
      </section>

      <section className="panel flex min-h-[280px] flex-col overflow-hidden rounded lg:min-h-0">
        <div className="border-b border-slate-800 px-2 pt-2">
          <LocationIntelPanel onLayersChange={applyLocationLayers} />
        </div>
        <div className="flex shrink-0 flex-wrap items-center justify-between gap-1 border-b border-slate-800 px-2 py-1">
          <div className="flex items-center gap-2">
            <h3 className="text-[10px] uppercase tracking-wider text-slate-400">
              Live Map · Aircraft + Boats · DF
            </h3>
            <span className="text-[9px] text-cyan-400/90">AC {aircraftCount}</span>
            <span className="text-[9px] text-emerald-400/90">VES {vesselCount}</span>
            <span className="text-[9px] text-emerald-300/90" title="Local RF / hybrid">
              ● LOCAL
            </span>
            <span className="text-[9px] text-slate-500" title="OpenSky reference">
              ● NET
            </span>
            <span className="text-[9px] text-slate-500">
              {trackMode === "adsb"
                ? "hybrid 1090"
                : trackMode === "ais"
                  ? "RX AIS"
                  : "idle"}
            </span>
          </div>
          <div className="flex flex-wrap gap-1">
            <button
              type="button"
              disabled={trackBusy}
              className="rounded border border-cyan-700 bg-cyan-950/40 px-2 py-0.5 text-[10px] text-cyan-200 disabled:opacity-40"
              onClick={() => void startTracks("adsb")}
            >
              Start Aircraft
            </button>
            <button
              type="button"
              disabled={trackBusy}
              className="rounded border border-emerald-700 bg-emerald-950/40 px-2 py-0.5 text-[10px] text-emerald-200 disabled:opacity-40"
              onClick={() => void startTracks("ais")}
            >
              Start Boats
            </button>
            <button
              type="button"
              disabled={trackBusy || trackMode === "idle"}
              className="rounded border border-slate-600 px-2 py-0.5 text-[10px] disabled:opacity-40"
              onClick={() => void stopTracks()}
            >
              Stop Tracking
            </button>
            <button
              type="button"
              className="rounded border border-slate-700 px-2 py-0.5 text-[10px] text-slate-400"
              onClick={clearTracks}
            >
              Clear
            </button>
          </div>
        </div>
        <div
          ref={mapHostRef}
          className="relative w-full flex-1"
          style={{ minHeight: 260, height: "100%" }}
        />
        <div className="grid grid-cols-2 gap-2 border-t border-slate-800 p-2 sm:grid-cols-4">
          <Num label="Lat" value={spoofPin?.lat ?? mapLat} onChange={(v) => setSpoofPin({ lat: v, lon: spoofPin?.lon ?? mapLon })} />
          <Num label="Lon" value={spoofPin?.lon ?? mapLon} onChange={(v) => setSpoofPin({ lat: spoofPin?.lat ?? mapLat, lon: v })} />
          <Num label="Alt m" value={mapAlt} onChange={setMapAlt} />
          <label className="text-[10px] text-slate-500">
            brdc ephemeris
            <input
              className="mt-0.5 w-full rounded border border-slate-700 bg-slate-950 px-1 py-0.5 text-[10px]"
              value={ephemerisPath}
              onChange={(e) => setEphemerisPath(e.target.value)}
              placeholder="captures/brdc.n"
            />
          </label>
        </div>
        <div className="flex flex-wrap gap-2 border-t border-slate-800 p-2">
          <button
            type="button"
            className="rounded border border-red-700 bg-red-950/50 px-2 py-1 text-[10px] text-red-300"
            onClick={() =>
              void synth({
                kind: "gps_l1",
                lat: spoofPin?.lat ?? mapLat,
                lon: spoofPin?.lon ?? mapLon,
                alt: mapAlt,
                ephemeris_path: ephemerisPath || null,
                tx_vga: txVgaDb,
                amp: 0,
                max_duration_sec: maxDurationSec,
              })
            }
          >
            Synthesize & Transmit L1 (1575.42 MHz)
          </button>
          <span className="self-center text-[9px] text-slate-500">
            One RF decoder at a time (HackRF). Aircraft + boat markers share this map.
          </span>
        </div>
      </section>

      <MissionControlDrawer />

      <section className="panel col-span-full rounded p-3">
        <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
          <div>
            <h3 className="text-[10px] uppercase tracking-wider text-slate-400">
              Auto Voice Recon
            </h3>
            <p className="text-[9px] text-slate-500">
              Scan public speakable bands (PMR446, CB, marine, airband, ham…). Browser WebAudio
              stream + CTCSS + VOX WAV logger. Optional local aplay. AGC raises LNA/VGA (AMP off).
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2 text-[10px]">
            <span className="rounded border border-slate-700 px-2 py-0.5 text-slate-300">
              {voiceRunning ? voicePhase : "idle"}
              {voiceBand ? ` · ${voiceBand}` : ""}
            </span>
            {voiceFreq != null && (
              <span className="inline-flex items-center gap-1 rounded border border-amber-800 px-2 py-0.5 text-amber-300">
                {(voiceFreq / 1e6).toFixed(4)} MHz
                {voiceRssi != null ? ` · ${voiceRssi.toFixed(1)} dBFS` : ""}
                <CtcssPill hz={voiceCtcss} />
                <button
                  type="button"
                  className="ml-1 rounded border border-amber-700 px-1 text-[9px] text-amber-200 hover:bg-amber-950"
                  title="Bookmark to FREQMAN RECON.TXT"
                  onClick={() =>
                    void bookmarkFreq(
                      voiceFreq,
                      voiceBand ? `Voice ${voiceBand}` : "Voice dwell",
                      "voice_recon",
                    )
                  }
                >
                  ★ FREQMAN
                </button>
              </span>
            )}
            <span className="text-slate-500">
              LNA {voiceGain.lna} / VGA {voiceGain.vga}
            </span>
            <label className="flex items-center gap-1 text-slate-400">
              <input
                type="checkbox"
                checked={localAplay}
                disabled={voiceRunning}
                onChange={(e) => setLocalAplay(e.target.checked)}
              />
              local aplay
            </label>
            <button
              type="button"
              disabled={voiceBusy || voiceRunning}
              className="rounded border border-amber-700 bg-amber-950/40 px-2 py-1 text-amber-200 disabled:opacity-40"
              onClick={() => void startVoice()}
            >
              Start Auto Recon
            </button>
            <button
              type="button"
              disabled={voiceBusy || !voiceRunning}
              className="rounded border border-slate-600 px-2 py-1 disabled:opacity-40"
              onClick={() => void stopVoice()}
            >
              Stop
            </button>
          </div>
        </div>
        <div className="mb-2 flex flex-wrap gap-2">
          {voiceBands.map((b) => (
            <label
              key={b.id}
              className="flex items-center gap-1 rounded border border-slate-800 px-2 py-1 text-[10px] text-slate-300"
              title={b.note}
            >
              <input
                type="checkbox"
                checked={enabledBands[b.id] ?? false}
                disabled={voiceRunning}
                onChange={(e) =>
                  setEnabledBands((prev) => ({ ...prev, [b.id]: e.target.checked }))
                }
              />
              {b.name}
            </label>
          ))}
        </div>
        <WebAudioPlayer enabled={voiceRunning} className="mb-2" />
        <pre className="max-h-28 overflow-auto rounded bg-black/50 p-2 text-[10px] leading-relaxed text-amber-200/90">
          {voiceLog.length === 0
            ? "waiting — Start Auto Recon to hunt voice activity…"
            : voiceLog.join("\n")}
        </pre>
        <div className="mt-2 border-t border-slate-800 pt-2">
          <button
            type="button"
            className="mb-1 text-[10px] uppercase tracking-wider text-slate-400 hover:text-slate-200"
            onClick={() => {
              setRecsOpen((o) => !o);
              void refreshRecordings();
            }}
          >
            Recent Voice Captures {recsOpen ? "▾" : "▸"} ({recordings.length})
          </button>
          {recsOpen && (
            <ul className="max-h-40 space-y-2 overflow-auto">
              {recordings.length === 0 && (
                <li className="text-[10px] text-slate-600">
                  No VOX captures yet — transmissions above squelch for ≥500 ms are saved.
                </li>
              )}
              {recordings.map((r) => (
                <li
                  key={r.id}
                  className="rounded border border-slate-800 px-2 py-1 text-[10px] text-slate-300"
                >
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-amber-200">
                      {r.band_name} · {(r.freq_hz / 1e6).toFixed(4)} MHz
                    </span>
                    <span className="text-slate-500">
                      {r.duration_sec.toFixed(1)}s
                      {r.peak_rssi_db != null ? ` · peak ${r.peak_rssi_db.toFixed(1)} dB` : ""}
                    </span>
                    {r.ctcss_hz != null && <CtcssPill hz={r.ctcss_hz} />}
                    <span className="text-slate-600">{r.timestamp.slice(11, 19)}</span>
                  </div>
                  <audio
                    className="mt-1 h-7 w-full"
                    controls
                    preload="none"
                    src={api.voiceRecordingAudioUrl(r.id)}
                  />
                </li>
              ))}
            </ul>
          )}
        </div>
      </section>

      <section className="panel col-span-full rounded p-3">
        <h3 className="mb-2 text-[10px] uppercase tracking-wider text-slate-400">
          Quick Signal Injectors
        </h3>
        <div className="grid gap-3 md:grid-cols-3">
          <div className="rounded border border-slate-800 p-2">
            <div className="mb-1 text-[11px] text-amber-300">LRS Restaurant Pager</div>
            <label className="text-[10px] text-slate-500">
              Pager ID
              <input
                type="number"
                className="mt-0.5 w-full rounded border border-slate-700 bg-slate-950 px-2 py-1 text-xs"
                value={pagerId}
                onChange={(e) => setPagerId(Number(e.target.value))}
              />
            </label>
            <button
              type="button"
              className="mt-2 rounded border border-amber-800 px-2 py-1 text-[10px] text-amber-300"
              onClick={() =>
                void synth({
                  kind: "lrs_pager",
                  pager_id: pagerId,
                  frequency_hz: Math.round(frequencyHz),
                  tx_vga: txVgaDb,
                  amp: 0,
                  max_duration_sec: maxDurationSec,
                })
              }
            >
              Alert Pager
            </button>
          </div>
          <div className="rounded border border-slate-800 p-2">
            <div className="mb-1 text-[11px] text-cyan-300">TouchTunes Jukebox</div>
            <label className="text-[10px] text-slate-500">
              PIN
              <input
                className="mt-0.5 w-full rounded border border-slate-700 bg-slate-950 px-2 py-1 text-xs"
                value={ttPin}
                onChange={(e) => setTtPin(e.target.value)}
              />
            </label>
            <button
              type="button"
              className="mt-2 rounded border border-cyan-800 px-2 py-1 text-[10px] text-cyan-300"
              onClick={() =>
                void synth({
                  kind: "touchtunes",
                  pin: ttPin,
                  frequency_hz: Math.round(frequencyHz),
                  tx_vga: txVgaDb,
                  amp: 0,
                  max_duration_sec: maxDurationSec,
                })
              }
            >
              Inject Command
            </button>
          </div>
          <div className="rounded border border-slate-800 p-2">
            <div className="mb-1 text-[11px] text-emerald-300">FM RDS RadioText</div>
            <div className="grid grid-cols-2 gap-1">
              <label className="text-[10px] text-slate-500">
                PS
                <input
                  className="mt-0.5 w-full rounded border border-slate-700 bg-slate-950 px-1 py-0.5 text-[10px]"
                  value={rdsPs}
                  maxLength={8}
                  onChange={(e) => setRdsPs(e.target.value)}
                />
              </label>
              <label className="text-[10px] text-slate-500">
                f (Hz)
                <input
                  type="number"
                  className="mt-0.5 w-full rounded border border-slate-700 bg-slate-950 px-1 py-0.5 text-[10px]"
                  value={fmHz}
                  onChange={(e) => setFmHz(Number(e.target.value))}
                />
              </label>
            </div>
            <label className="mt-1 block text-[10px] text-slate-500">
              RT
              <input
                className="mt-0.5 w-full rounded border border-slate-700 bg-slate-950 px-1 py-0.5 text-[10px]"
                value={rdsRt}
                onChange={(e) => setRdsRt(e.target.value)}
              />
            </label>
            <button
              type="button"
              className="mt-2 rounded border border-emerald-800 px-2 py-1 text-[10px] text-emerald-300"
              onClick={() =>
                void synth({
                  kind: "fm_rds",
                  fm_frequency_hz: fmHz,
                  ps: rdsPs,
                  rt: rdsRt,
                  tx_vga: txVgaDb,
                  amp: 0,
                  max_duration_sec: maxDurationSec,
                })
              }
            >
              Transmit PS/RT
            </button>
          </div>
        </div>
      </section>
    </div>
  );
}

function Num({
  label,
  value,
  onChange,
}: {
  label: string;
  value: number;
  onChange: (v: number) => void;
}) {
  return (
    <label className="text-[10px] text-slate-500">
      {label}
      <input
        type="number"
        step="any"
        className="mt-0.5 w-full rounded border border-slate-700 bg-slate-950 px-1 py-0.5 text-[10px]"
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
      />
    </label>
  );
}
