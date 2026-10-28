import { useCallback, useEffect, useRef, useState } from "react";
import { wsUrl } from "@/lib/api";

export type AudioHello = {
  type?: string;
  sample_rate?: number;
  channels?: number;
  format?: string;
  active?: boolean;
  ctcss_hz?: number | null;
  listen_hz?: number | null;
  band?: string | null;
  rssi_db?: number | null;
};

/**
 * Browser PCM sink for /ws/audio (s16le mono @ 48 kHz).
 * Ring via queued AudioBufferSourceNodes; digital squelch in dBFS on PCM RMS.
 */
export function useWebAudioPlayer(enabled: boolean) {
  const [muted, setMuted] = useState(false);
  const [squelchDb, setSquelchDb] = useState(-55);
  const [levelDb, setLevelDb] = useState(-120);
  const [spectrum, setSpectrum] = useState<number[]>(() => Array(32).fill(0));
  const [meta, setMeta] = useState<AudioHello>({});
  const [connected, setConnected] = useState(false);

  const ctxRef = useRef<AudioContext | null>(null);
  const nextTimeRef = useRef(0);
  const mutedRef = useRef(muted);
  const squelchRef = useRef(squelchDb);
  mutedRef.current = muted;
  squelchRef.current = squelchDb;

  const ensureCtx = useCallback(async () => {
    if (!ctxRef.current) {
      const Ctx = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
      ctxRef.current = new Ctx({ sampleRate: 48_000 });
    }
    if (ctxRef.current.state === "suspended") {
      await ctxRef.current.resume();
    }
    return ctxRef.current;
  }, []);

  const enqueuePcm = useCallback(
    async (pcm: ArrayBuffer) => {
      const ctx = await ensureCtx();
      const samples = new Int16Array(pcm);
      if (samples.length === 0) return;

      let sum = 0;
      for (let i = 0; i < samples.length; i++) {
        const v = samples[i]! / 32768;
        sum += v * v;
      }
      const rms = Math.sqrt(sum / samples.length);
      const db = rms > 1e-9 ? 20 * Math.log10(rms) : -120;
      setLevelDb(db);

      // Mini spectrum (fold bins)
      const bins = 32;
      const step = Math.max(1, Math.floor(samples.length / bins));
      const spec: number[] = [];
      for (let b = 0; b < bins; b++) {
        let s = 0;
        const start = b * step;
        for (let i = start; i < Math.min(start + step, samples.length); i++) {
          const v = samples[i]! / 32768;
          s += v * v;
        }
        const r = Math.sqrt(s / step);
        spec.push(r > 1e-9 ? 20 * Math.log10(r) : -120);
      }
      setSpectrum(spec);

      if (mutedRef.current || db < squelchRef.current) return;

      const buffer = ctx.createBuffer(1, samples.length, ctx.sampleRate || 48_000);
      const ch = buffer.getChannelData(0);
      for (let i = 0; i < samples.length; i++) {
        ch[i] = samples[i]! / 32768;
      }
      const src = ctx.createBufferSource();
      src.buffer = buffer;
      src.connect(ctx.destination);
      const now = ctx.currentTime;
      const startAt = Math.max(now + 0.02, nextTimeRef.current);
      src.start(startAt);
      nextTimeRef.current = startAt + buffer.duration;
    },
    [ensureCtx],
  );

  useEffect(() => {
    if (!enabled) {
      setConnected(false);
      return;
    }
    let ws: WebSocket | null = null;
    let dead = false;
    const open = () => {
      ws = new WebSocket(wsUrl("/ws/audio"));
      ws.binaryType = "arraybuffer";
      ws.onopen = () => setConnected(true);
      ws.onclose = () => {
        setConnected(false);
        if (!dead) window.setTimeout(open, 1500);
      };
      ws.onmessage = (ev) => {
        if (typeof ev.data === "string") {
          try {
            const j = JSON.parse(ev.data) as AudioHello;
            setMeta(j);
          } catch {
            /* ignore */
          }
          return;
        }
        void enqueuePcm(ev.data as ArrayBuffer);
      };
    };
    void ensureCtx().then(open);
    return () => {
      dead = true;
      ws?.close();
      setConnected(false);
    };
  }, [enabled, enqueuePcm, ensureCtx]);

  return {
    muted,
    setMuted,
    squelchDb,
    setSquelchDb,
    levelDb,
    spectrum,
    meta,
    connected,
    unlock: ensureCtx,
  };
}
