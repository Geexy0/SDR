import { useEffect, useRef, useState } from "react";
import { api, wsUrl } from "@/lib/api";
import { parseWf32Frame as parseWf32 } from "@/lib/binaryFrames";
import { buildLutTexture, type LutName } from "@/lib/luts";
import { useHardwareStore } from "@/store/useHardwareStore";

const RING_ROWS = 512;

interface RingBuffer {
  width: number;
  height: number;
  writeRow: number;
  data: Uint8Array;
  startHz: number;
  stopHz: number;
  binHz: number;
  dbMin: number;
  dbMax: number;
  latest: Float32Array;
  peak: Float32Array;
  avg: Float32Array;
  avgCount: number;
}

function createRing(width: number): RingBuffer {
  return {
    width,
    height: RING_ROWS,
    writeRow: 0,
    data: new Uint8Array(width * RING_ROWS),
    startHz: 0,
    stopHz: 0,
    binHz: 0,
    dbMin: -110,
    dbMax: -20,
    latest: new Float32Array(width),
    peak: new Float32Array(width).fill(-200),
    avg: new Float32Array(width),
    avgCount: 0,
  };
}

/** WF32 header is 36 bytes: magic + u32 n + u32 bin_hz + 2×f32 + 2×u64 */
function u8ToDb(u: number, dbMin: number, dbMax: number): number {
  return dbMin + (u / 255) * (dbMax - dbMin);
}

const VERT = `#version 300 es
in vec2 a_pos;
out vec2 v_uv;
void main(){
  v_uv = a_pos * 0.5 + 0.5;
  gl_Position = vec4(a_pos, 0.0, 1.0);
}`;

const FRAG = `#version 300 es
precision highp float;
in vec2 v_uv;
uniform sampler2D u_hist;
uniform sampler2D u_lut;
uniform float u_write;
uniform float u_rows;
out vec4 outColor;
void main(){
  float row = mod(v_uv.y * u_rows + u_write, u_rows) / u_rows;
  float p = texture(u_hist, vec2(v_uv.x, row)).r;
  outColor = texture(u_lut, vec2(p, 0.5));
}`;

export function SpectrumWaterfall() {
  const spectrumRef = useRef<HTMLCanvasElement>(null);
  const waterRef = useRef<HTMLCanvasElement>(null);
  const ringRef = useRef<RingBuffer | null>(null);
  const dragRef = useRef<{ x0: number; x1: number; shift: boolean } | null>(null);
  const rafRef = useRef(0);
  const wsRef = useRef<WebSocket | null>(null);
  const rowsRef = useRef(0);
  const glRef = useRef<{
    gl: WebGL2RenderingContext;
    prog: WebGLProgram;
    histTex: WebGLTexture;
    lutTex: WebGLTexture;
    loc: Record<string, WebGLUniformLocation | null>;
  } | null>(null);

  const [statusLine, setStatusLine] = useState("WS idle — click Start Sweep");
  const [busy, setBusy] = useState(false);

  const lut = useHardwareStore((s) => s.waterfallLut);
  const triggerRssiDb = useHardwareStore((s) => s.triggerRssiDb);
  const setTriggerRssiDb = useHardwareStore((s) => s.setTriggerRssiDb);
  const setFrequencyHz = useHardwareStore((s) => s.setFrequencyHz);
  const setChirpBandwidthHz = useHardwareStore((s) => s.setChirpBandwidthHz);
  const setSweepRange = useHardwareStore((s) => s.setSweepRange);
  const frequencyHz = useHardwareStore((s) => s.frequencyHz);

  useEffect(() => {
    const ws = new WebSocket(wsUrl("/ws/waterfall"));
    ws.binaryType = "arraybuffer";
    wsRef.current = ws;
    ws.onopen = () => {
      ws.send("attach");
      setStatusLine("WS connected — Start Sweep when ready");
    };
    ws.onclose = () => setStatusLine("WS closed");
    ws.onerror = () => setStatusLine("WS error");
    ws.onmessage = (ev) => {
      if (typeof ev.data === "string") {
        try {
          const msg = JSON.parse(ev.data) as { message?: string; waterfall_running?: boolean };
          if (msg.message) setStatusLine(`WS: ${msg.message}`);
        } catch {
          /* ignore */
        }
        return;
      }
      const parsed = parseWf32(ev.data as ArrayBuffer);
      if (!parsed) return;
      let ring = ringRef.current;
      if (!ring || ring.width !== parsed.n) {
        ring = createRing(parsed.n);
        ringRef.current = ring;
      }
      ring.startHz = parsed.startHz;
      ring.stopHz = parsed.stopHz;
      ring.binHz = parsed.binHz;
      ring.dbMin = parsed.dbMin;
      ring.dbMax = parsed.dbMax;
      const row = ring.writeRow % ring.height;
      ring.data.set(parsed.u8, row * ring.width);
      ring.writeRow = (ring.writeRow + 1) % ring.height;
      ring.avgCount += 1;
      const c = Math.min(ring.avgCount, 64);
      for (let i = 0; i < parsed.n; i++) {
        const db = u8ToDb(parsed.u8[i], parsed.dbMin, parsed.dbMax);
        ring.latest[i] = db;
        ring.peak[i] = Math.max(ring.peak[i], db);
        ring.avg[i] = ring.avg[i] + (db - ring.avg[i]) / c;
      }
      rowsRef.current += 1;
      if (rowsRef.current % 15 === 0) {
        setStatusLine(
          `live ${(parsed.startHz / 1e6).toFixed(1)}–${(parsed.stopHz / 1e6).toFixed(1)} MHz · ${parsed.n} bins · row ${rowsRef.current}`,
        );
      }
    };
    return () => {
      wsRef.current = null;
      ws.close();
    };
  }, []);

  useEffect(() => {
    const canvas = waterRef.current;
    if (!canvas) return;
    const gl = canvas.getContext("webgl2", { antialias: false, alpha: false });
    if (!gl) {
      useHardwareStore.getState().setToast("WebGL2 unavailable — waterfall disabled");
      return;
    }

    const compile = (type: number, src: string) => {
      const sh = gl.createShader(type)!;
      gl.shaderSource(sh, src);
      gl.compileShader(sh);
      if (!gl.getShaderParameter(sh, gl.COMPILE_STATUS)) {
        useHardwareStore.getState().setToast(`WebGL shader: ${gl.getShaderInfoLog(sh)}`);
      }
      return sh;
    };
    const prog = gl.createProgram()!;
    gl.attachShader(prog, compile(gl.VERTEX_SHADER, VERT));
    gl.attachShader(prog, compile(gl.FRAGMENT_SHADER, FRAG));
    gl.linkProgram(prog);
    if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) {
      useHardwareStore.getState().setToast(`WebGL link: ${gl.getProgramInfoLog(prog)}`);
      return;
    }
    gl.useProgram(prog);
    const buf = gl.createBuffer()!;
    gl.bindBuffer(gl.ARRAY_BUFFER, buf);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW);
    const aPos = gl.getAttribLocation(prog, "a_pos");
    gl.enableVertexAttribArray(aPos);
    gl.vertexAttribPointer(aPos, 2, gl.FLOAT, false, 0, 0);

    const histTex = gl.createTexture()!;
    gl.bindTexture(gl.TEXTURE_2D, histTex);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);

    const lutTex = gl.createTexture()!;
    gl.activeTexture(gl.TEXTURE1);
    gl.bindTexture(gl.TEXTURE_2D, lutTex);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    gl.texImage2D(
      gl.TEXTURE_2D,
      0,
      gl.RGBA,
      256,
      1,
      0,
      gl.RGBA,
      gl.UNSIGNED_BYTE,
      buildLutTexture(lut as LutName),
    );

    glRef.current = {
      gl,
      prog,
      histTex,
      lutTex,
      loc: {
        u_hist: gl.getUniformLocation(prog, "u_hist"),
        u_lut: gl.getUniformLocation(prog, "u_lut"),
        u_write: gl.getUniformLocation(prog, "u_write"),
        u_rows: gl.getUniformLocation(prog, "u_rows"),
      },
    };

    return () => {
      gl.deleteProgram(prog);
      gl.deleteTexture(histTex);
      gl.deleteTexture(lutTex);
      glRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- GL init once; LUT updates below
  }, []);

  useEffect(() => {
    const g = glRef.current;
    if (!g) return;
    const { gl, lutTex } = g;
    gl.activeTexture(gl.TEXTURE1);
    gl.bindTexture(gl.TEXTURE_2D, lutTex);
    gl.texImage2D(
      gl.TEXTURE_2D,
      0,
      gl.RGBA,
      256,
      1,
      0,
      gl.RGBA,
      gl.UNSIGNED_BYTE,
      buildLutTexture(lut as LutName),
    );
  }, [lut]);

  useEffect(() => {
    const syncSize = (canvas: HTMLCanvasElement) => {
      const w = Math.max(1, Math.floor(canvas.clientWidth));
      const h = Math.max(1, Math.floor(canvas.clientHeight));
      if (canvas.width !== w || canvas.height !== h) {
        canvas.width = w;
        canvas.height = h;
        return true;
      }
      return false;
    };

    const paint = () => {
      const ring = ringRef.current;
      const spec = spectrumRef.current;
      const water = waterRef.current;

      if (spec) {
        syncSize(spec);
        const ctx = spec.getContext("2d");
        if (ctx) {
          const w = spec.width;
          const h = spec.height;
          ctx.fillStyle = "#020617";
          ctx.fillRect(0, 0, w, h);
          if (!ring) {
            ctx.fillStyle = "#64748b";
            ctx.font = "12px JetBrains Mono";
            ctx.fillText("No FFT rows yet — Start Sweep (needs HACKRF_NATIVE)", 12, h / 2);
          } else {
            const mapY = (db: number) => {
              const t = (db - ring.dbMin) / (ring.dbMax - ring.dbMin);
              return h - Math.max(0, Math.min(1, t)) * h;
            };
            const drawTrace = (arr: Float32Array, color: string, width = 1.5) => {
              ctx.beginPath();
              ctx.strokeStyle = color;
              ctx.lineWidth = width;
              for (let i = 0; i < ring.width; i++) {
                const x = (i / Math.max(1, ring.width - 1)) * w;
                const y = mapY(arr[i]);
                if (i === 0) ctx.moveTo(x, y);
                else ctx.lineTo(x, y);
              }
              ctx.stroke();
            };
            drawTrace(ring.avg, "#10b981", 1.2);
            drawTrace(ring.peak, "#f59e0b", 1.2);
            drawTrace(ring.latest, "#06b6d4", 1.6);

            const ty = mapY(triggerRssiDb);
            ctx.setLineDash([6, 4]);
            ctx.strokeStyle = "#ef4444";
            ctx.beginPath();
            ctx.moveTo(0, ty);
            ctx.lineTo(w, ty);
            ctx.stroke();
            ctx.setLineDash([]);

            if (ring.startHz && ring.stopHz) {
              const fcX =
                ((frequencyHz - ring.startHz) / Math.max(1, ring.stopHz - ring.startHz)) * w;
              ctx.strokeStyle = "#e2e8f0";
              ctx.beginPath();
              ctx.moveTo(fcX, 0);
              ctx.lineTo(fcX, h);
              ctx.stroke();
            }

            const drag = dragRef.current;
            if (drag && drag.shift) {
              const x0 = Math.min(drag.x0, drag.x1);
              const x1 = Math.max(drag.x0, drag.x1);
              ctx.fillStyle = "rgba(6,182,212,0.15)";
              ctx.fillRect(x0, 0, x1 - x0, h);
            }
          }
        }
      }

      const g = glRef.current;
      if (water) {
        syncSize(water);
        if (g) {
          const { gl, histTex, lutTex, loc } = g;
          gl.viewport(0, 0, water.width, water.height);
          if (ring) {
            gl.activeTexture(gl.TEXTURE0);
            gl.bindTexture(gl.TEXTURE_2D, histTex);
            gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1);
            gl.texImage2D(
              gl.TEXTURE_2D,
              0,
              gl.R8,
              ring.width,
              ring.height,
              0,
              gl.RED,
              gl.UNSIGNED_BYTE,
              ring.data,
            );
            gl.activeTexture(gl.TEXTURE1);
            gl.bindTexture(gl.TEXTURE_2D, lutTex);
            gl.uniform1i(loc.u_hist, 0);
            gl.uniform1i(loc.u_lut, 1);
            gl.uniform1f(loc.u_write, ring.writeRow);
            gl.uniform1f(loc.u_rows, ring.height);
            gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
          } else {
            gl.clearColor(0, 0, 0, 1);
            gl.clear(gl.COLOR_BUFFER_BIT);
          }
        }
      }
      rafRef.current = requestAnimationFrame(paint);
    };
    rafRef.current = requestAnimationFrame(paint);
    return () => cancelAnimationFrame(rafRef.current);
  }, [frequencyHz, triggerRssiDb]);

  const hzAt = (clientX: number, canvas: HTMLCanvasElement) => {
    const ring = ringRef.current;
    if (!ring || !ring.startHz) return null;
    const rect = canvas.getBoundingClientRect();
    const t = (clientX - rect.left) / rect.width;
    return ring.startHz + t * (ring.stopHz - ring.startHz);
  };

  const onPointerDown = (e: React.PointerEvent<HTMLCanvasElement>) => {
    const canvas = spectrumRef.current;
    if (!canvas) return;
    const rect = canvas.getBoundingClientRect();
    const x = e.clientX - rect.left;
    dragRef.current = { x0: x, x1: x, shift: e.shiftKey };
    canvas.setPointerCapture(e.pointerId);
  };

  const onPointerMove = (e: React.PointerEvent<HTMLCanvasElement>) => {
    const canvas = spectrumRef.current;
    const drag = dragRef.current;
    if (!canvas || !drag) return;
    const rect = canvas.getBoundingClientRect();
    drag.x1 = e.clientX - rect.left;
    if (!drag.shift) {
      const y = e.clientY - rect.top;
      const ring = ringRef.current;
      if (ring) {
        const t = 1 - y / rect.height;
        const db = ring.dbMin + t * (ring.dbMax - ring.dbMin);
        setTriggerRssiDb(Math.round(db));
      }
    }
  };

  const onPointerUp = (e: React.PointerEvent<HTMLCanvasElement>) => {
    const canvas = spectrumRef.current;
    const drag = dragRef.current;
    const ring = ringRef.current;
    dragRef.current = null;
    if (!canvas || !drag || !ring) return;
    if (drag.shift) {
      const rect = canvas.getBoundingClientRect();
      const t0 = Math.min(drag.x0, drag.x1) / rect.width;
      const t1 = Math.max(drag.x0, drag.x1) / rect.width;
      const f0 = ring.startHz + t0 * (ring.stopHz - ring.startHz);
      const f1 = ring.startHz + t1 * (ring.stopHz - ring.startHz);
      const bw = Math.max(100_000, f1 - f0);
      setChirpBandwidthHz(Math.min(20_000_000, bw));
      setFrequencyHz((f0 + f1) / 2);
      setSweepRange(f0 / 1e6, f1 / 1e6);
    } else if (Math.abs(drag.x1 - drag.x0) < 4) {
      const hz = hzAt(e.clientX, canvas);
      if (hz) setFrequencyHz(hz);
    }
  };

  const startSweep = async () => {
    setBusy(true);
    const s = useHardwareStore.getState();
    try {
      // Clear orphan BUSY before sweep.
      await api.stop().catch(() => undefined);
      await api.stopSweep().catch(() => undefined);
      await api.setMode("HACKRF_NATIVE");
      await api.startSweep({
        start_mhz: s.sweepStartMhz,
        stop_mhz: s.sweepStopMhz,
        bin_width_hz: 100000,
        lna: s.lnaDb,
        vga: s.rxVgaDb,
        amp: 0,
        max_duration_sec: 120,
        target_fps: 30,
        trigger_enabled: true,
        trigger_f_lo_hz: s.frequencyHz - s.chirpBandwidthHz / 2,
        trigger_f_hi_hz: s.frequencyHz + s.chirpBandwidthHz / 2,
        trigger_rssi_db: s.triggerRssiDb,
        trigger_action: "log",
      });
      rowsRef.current = 0;
      const ws = wsRef.current;
      if (ws && ws.readyState === WebSocket.OPEN) ws.send("attach");
      setStatusLine("sweep armed — waiting for rows…");
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setStatusLine(msg);
      useHardwareStore.getState().setToast(msg);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex h-full min-h-0 flex-col border-b border-slate-800">
      <div className="flex shrink-0 items-center justify-between gap-2 border-b border-slate-800 bg-slate-950/80 px-2 py-1 text-[10px] uppercase tracking-wider text-slate-400">
        <span className="truncate normal-case tracking-normal text-cyan-400/90">{statusLine}</span>
        <div className="flex shrink-0 gap-2">
          <button
            type="button"
            disabled={busy}
            className="rounded border border-slate-700 px-2 py-0.5 text-cyan-400 hover:bg-slate-800 disabled:opacity-50"
            onClick={() => void startSweep()}
          >
            Start Sweep
          </button>
          <button
            type="button"
            className="rounded border border-slate-700 px-2 py-0.5 hover:bg-slate-800"
            onClick={() => {
              void api.stopSweep().then(() => setStatusLine("sweep stopped"));
            }}
          >
            Stop
          </button>
        </div>
      </div>
      <canvas
        ref={spectrumRef}
        className="min-h-0 w-full flex-[0.35] cursor-crosshair bg-slate-950"
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
      />
      <canvas ref={waterRef} className="min-h-0 w-full flex-[0.65] bg-black" />
    </div>
  );
}
