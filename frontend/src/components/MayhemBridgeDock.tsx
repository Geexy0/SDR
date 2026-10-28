import { useEffect, useRef, useState } from "react";
import {
  ArrowDown,
  ArrowLeft,
  ArrowRight,
  ArrowUp,
  Circle,
  RotateCcw,
  RotateCw,
} from "lucide-react";
import { api, wsUrl } from "@/lib/api";
import { useHardwareStore } from "@/store/useHardwareStore";

const FEATURED = [
  "jammer",
  "bletx",
  "rx_subghz",
  "replay",
  "pocsag",
  "looking_glass",
] as const;

const BUTTON = {
  Right: 1,
  Left: 2,
  Down: 3,
  Up: 4,
  Select: 5,
  DFU: 6,
  EncoderLeft: 7,
  EncoderRight: 8,
} as const;

export function MayhemBridgeDock() {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const frameRef = useRef<{ w: number; h: number; rgba: Uint8ClampedArray } | null>(null);
  const rafRef = useRef(0);
  const setToast = useHardwareStore((s) => s.setToast);
  const hwState = useHardwareStore((s) => s.hwState);
  const [hostFiles, setHostFiles] = useState<string[]>([
    "captures/",
    "captures/*.cs8",
    "captures/*.sub",
    "captures/*.C16",
  ]);
  const [ppFiles, setPpFiles] = useState<string[]>([
    "/SDCARD/*.C16",
    "/SDCARD/*.SUB",
    "/SDCARD/CAPTURES/",
  ]);
  const [selectedHost, setSelectedHost] = useState<string | null>(null);
  const [selectedPp, setSelectedPp] = useState<string | null>(null);
  const [syncBusy, setSyncBusy] = useState(false);

  useEffect(() => {
    if (hwState !== "MAYHEM_SERIAL") return;
    const ws = new WebSocket(wsUrl("/ws/mayhem-screen"));
    ws.binaryType = "arraybuffer";
    ws.onmessage = (ev) => {
      if (typeof ev.data === "string") return;
      const buf = ev.data as ArrayBuffer;
      if (buf.byteLength < 8) return;
      const v = new DataView(buf);
      const magic = String.fromCharCode(
        v.getUint8(0),
        v.getUint8(1),
        v.getUint8(2),
        v.getUint8(3),
      );
      if (magic !== "MHSC") return;
      const w = v.getUint16(4);
      const h = v.getUint16(6);
      if (w <= 0 || h <= 0 || buf.byteLength < 8 + w * h * 4) return;
      const rgba = new Uint8ClampedArray(buf, 8, w * h * 4);
      frameRef.current = { w, h, rgba };
    };
    return () => ws.close();
  }, [hwState]);

  useEffect(() => {
    const paint = () => {
      const canvas = canvasRef.current;
      const frame = frameRef.current;
      if (canvas) {
        const ctx = canvas.getContext("2d");
        if (ctx) {
          ctx.fillStyle = "#020617";
          ctx.fillRect(0, 0, canvas.width, canvas.height);
          if (frame && frame.rgba.length >= frame.w * frame.h * 4) {
            if (canvas.width !== frame.w || canvas.height !== frame.h) {
              canvas.width = frame.w;
              canvas.height = frame.h;
            }
            const copy = new Uint8ClampedArray(frame.rgba);
            const img = new ImageData(copy, frame.w, frame.h);
            ctx.putImageData(img, 0, 0);
          } else {
            ctx.fillStyle = "#475569";
            ctx.font = "10px JetBrains Mono";
            ctx.fillText(
              hwState === "MAYHEM_SERIAL" ? "waiting screenframeshort…" : "switch to MAYHEM_SERIAL",
              8,
              20,
            );
          }
        }
      }
      rafRef.current = requestAnimationFrame(paint);
    };
    rafRef.current = requestAnimationFrame(paint);
    return () => cancelAnimationFrame(rafRef.current);
  }, [hwState]);

  const sendButton = (button: number) => {
    void api
      .mayhemInput({ kind: "button", button })
      .catch((err: Error) => setToast(err.message));
  };

  const onCanvasClick = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const canvas = canvasRef.current;
    const frame = frameRef.current;
    if (!canvas || !frame) return;
    const rect = canvas.getBoundingClientRect();
    const x = Math.floor(((e.clientX - rect.left) / rect.width) * frame.w);
    const y = Math.floor(((e.clientY - rect.top) / rect.height) * frame.h);
    void api
      .mayhemInput({ kind: "touch", x, y })
      .catch((err: Error) => setToast(err.message));
  };

  const pullToHost = () => {
    if (!selectedPp) return;
    const name = selectedPp.split("/").pop() ?? "pull.bin";
    const dest = `captures/${name}`;
    setHostFiles((f) => (f.includes(dest) ? f : [dest, ...f]));
    setToast(`Queued pull ${selectedPp} → ${dest} (Mayhem SD → host)`);
  };

  const pushToPp = () => {
    if (!selectedHost) return;
    const name = selectedHost.split("/").pop() ?? "push.bin";
    const dest = `/SDCARD/${name}`;
    setPpFiles((f) => (f.includes(dest) ? f : [dest, ...f]));
    setToast(`Queued push ${selectedHost} → ${dest}`);
  };

  return (
    <aside className="flex h-full min-h-0 w-[280px] shrink-0 flex-col border-l border-slate-800 bg-slate-950">
      <div className="border-b border-slate-800 px-2 py-1.5 text-[10px] uppercase tracking-wider text-amber-400">
        PortaPack H4M · Mayhem Bridge
      </div>

      <div className="flex justify-center border-b border-slate-800 bg-black/40 p-2">
        <canvas
          ref={canvasRef}
          width={240}
          height={320}
          className="cursor-crosshair border border-slate-700"
          style={{ width: 240, height: 320, imageRendering: "pixelated" }}
          onClick={onCanvasClick}
        />
      </div>

      <div className="border-b border-slate-800 p-2">
        <div className="mb-2 grid grid-cols-3 place-items-center gap-1">
          <span />
          <IconBtn onClick={() => sendButton(BUTTON.Up)} title="Up">
            <ArrowUp className="h-4 w-4" />
          </IconBtn>
          <span />
          <IconBtn onClick={() => sendButton(BUTTON.Left)} title="Left">
            <ArrowLeft className="h-4 w-4" />
          </IconBtn>
          <IconBtn onClick={() => sendButton(BUTTON.Select)} title="Select" accent>
            <Circle className="h-3.5 w-3.5 fill-current" />
          </IconBtn>
          <IconBtn onClick={() => sendButton(BUTTON.Right)} title="Right">
            <ArrowRight className="h-4 w-4" />
          </IconBtn>
          <span />
          <IconBtn onClick={() => sendButton(BUTTON.Down)} title="Down">
            <ArrowDown className="h-4 w-4" />
          </IconBtn>
          <span />
        </div>
        <div className="flex justify-center gap-2">
          <IconBtn onClick={() => sendButton(BUTTON.EncoderLeft)} title="Encoder CCW">
            <RotateCcw className="h-3.5 w-3.5" />
            <span className="text-[9px]">CCW</span>
          </IconBtn>
          <IconBtn onClick={() => sendButton(BUTTON.EncoderRight)} title="Encoder CW">
            <RotateCw className="h-3.5 w-3.5" />
            <span className="text-[9px]">CW</span>
          </IconBtn>
          <button
            type="button"
            className="rounded border border-amber-700 px-2 py-1 text-[9px] text-amber-300"
            onClick={() => sendButton(BUTTON.DFU)}
          >
            DFU / HackRF
          </button>
        </div>
      </div>

      <div className="border-b border-slate-800 p-2">
        <div className="mb-1 text-[10px] uppercase tracking-wider text-slate-500">
          Quick App Launcher
        </div>
        <div className="grid grid-cols-2 gap-1">
          {FEATURED.map((app) => (
            <button
              key={app}
              type="button"
              className="rounded border border-slate-700 bg-slate-900/80 px-1.5 py-1 text-[10px] text-slate-200 hover:border-amber-700 hover:text-amber-300"
              onClick={() =>
                void api
                  .mayhemAppStart(app)
                  .then(() => setToast(`app ${app}`))
                  .catch((err: Error) => setToast(err.message))
              }
            >
              {app}
            </button>
          ))}
        </div>
      </div>

      <div className="border-b border-slate-800 p-2">
        <button
          type="button"
          disabled={hwState !== "MAYHEM_SERIAL" || syncBusy}
          className="w-full rounded border border-amber-600 bg-amber-950/50 px-2 py-1.5 text-[10px] font-semibold text-amber-200 disabled:opacity-40"
          title="Upload RECON.TXT / NORMANDY.TXT to /FREQMAN and pull .C16/.SUB into captures/"
          onClick={() => {
            setSyncBusy(true);
            void api
              .freqmanSync({ categories: ["RECON", "NORMANDY"], pull_captures: true })
              .then((r) => {
                const up = r.uploaded?.length ?? 0;
                const pull = r.pulled?.length ?? 0;
                for (const p of r.pulled ?? []) {
                  if (p.local) {
                    setHostFiles((f) => (f.includes(p.local!) ? f : [p.local!, ...f]));
                  }
                }
                setToast(`FREQMAN sync: ${up} uploaded, ${pull} pulled`);
              })
              .catch((err: Error) => setToast(err.message))
              .finally(() => setSyncBusy(false));
          }}
        >
          {syncBusy ? "Syncing…" : "Sync Bookmarks to PortaPack SD"}
        </button>
        <p className="mt-1 text-[9px] text-slate-500">
          Requires MAYHEM_SERIAL. Pushes /FREQMAN/*.TXT · pulls new .C16/.SUB → captures/
        </p>
      </div>

      <div className="flex min-h-0 flex-1 flex-col p-2">
        <div className="mb-1 text-[10px] uppercase tracking-wider text-slate-500">
          SD Manager · .C16 / .SUB
        </div>
        <div className="grid min-h-0 flex-1 grid-cols-2 gap-1">
          <FilePane
            title="PortaPack SD"
            files={ppFiles}
            selected={selectedPp}
            onSelect={setSelectedPp}
          />
          <FilePane
            title="Host captures"
            files={hostFiles}
            selected={selectedHost}
            onSelect={setSelectedHost}
          />
        </div>
        <div className="mt-1 flex gap-1">
          <button
            type="button"
            className="flex-1 rounded border border-cyan-800 py-0.5 text-[9px] text-cyan-300"
            onClick={pullToHost}
          >
            SD → Host
          </button>
          <button
            type="button"
            className="flex-1 rounded border border-emerald-800 py-0.5 text-[9px] text-emerald-300"
            onClick={pushToPp}
          >
            Host → SD
          </button>
        </div>
      </div>
    </aside>
  );
}

function IconBtn({
  children,
  onClick,
  title,
  accent,
}: {
  children: React.ReactNode;
  onClick: () => void;
  title: string;
  accent?: boolean;
}) {
  return (
    <button
      type="button"
      title={title}
      onClick={onClick}
      className={`flex items-center justify-center gap-0.5 rounded border px-2 py-1.5 ${
        accent
          ? "border-amber-600 text-amber-300"
          : "border-slate-700 text-slate-300 hover:border-slate-500"
      }`}
    >
      {children}
    </button>
  );
}

function FilePane({
  title,
  files,
  selected,
  onSelect,
}: {
  title: string;
  files: string[];
  selected: string | null;
  onSelect: (f: string) => void;
}) {
  return (
    <div className="flex min-h-0 flex-col overflow-hidden rounded border border-slate-800">
      <div className="border-b border-slate-800 bg-slate-900 px-1 py-0.5 text-[9px] text-slate-500">
        {title}
      </div>
      <ul className="min-h-0 flex-1 overflow-auto text-[9px]">
        {files.map((f) => (
          <li key={f}>
            <button
              type="button"
              className={`block w-full truncate px-1 py-0.5 text-left ${
                selected === f ? "bg-slate-800 text-cyan-300" : "text-slate-400 hover:bg-slate-900"
              }`}
              onClick={() => onSelect(f)}
            >
              {f}
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
