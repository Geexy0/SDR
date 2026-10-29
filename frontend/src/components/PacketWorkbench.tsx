import { useMemo, useState } from "react";
import { Binary, Crosshair, FlaskConical } from "lucide-react";
import { api } from "@/lib/api";
import type {
  WorkbenchColumnClass,
  WorkbenchDemodResult,
  WorkbenchLineCode,
  WorkbenchModulation,
} from "@/lib/types";
import { useHardwareStore } from "@/store/useHardwareStore";

const MODS: WorkbenchModulation[] = ["AUTO", "OOK", "2FSK", "GFSK", "BPSK"];
const CODES: WorkbenchLineCode[] = [
  "NRZ",
  "NRZ_INV",
  "MANCHESTER_IEEE",
  "MANCHESTER_THOMAS",
  "DIFF_MANCHESTER",
  "PWM",
];

const COL_STYLE: Record<WorkbenchColumnClass, string> = {
  static: "bg-emerald-950/80 text-emerald-300 border-emerald-800",
  incrementing: "bg-amber-950/80 text-amber-300 border-amber-800",
  high_entropy: "bg-violet-950/80 text-violet-300 border-violet-800",
  mixed: "bg-slate-900 text-slate-400 border-slate-700",
};

const COL_LABEL: Record<WorkbenchColumnClass, string> = {
  static: "Static / Address",
  incrementing: "Counter / Seq",
  high_entropy: "CRC / Checksum",
  mixed: "Mixed",
};

function byteHex(b: number): string {
  return b.toString(16).toUpperCase().padStart(2, "0");
}

function byteBin(b: number): string {
  return b.toString(2).padStart(8, "0");
}

export function PacketWorkbench() {
  const selectedCapture = useHardwareStore((s) => s.selectedCapture);
  const setSelectedCapture = useHardwareStore((s) => s.setSelectedCapture);
  const setToast = useHardwareStore((s) => s.setToast);
  const sampleRateMsps = useHardwareStore((s) => s.sampleRateMsps);

  const [capturePath, setCapturePath] = useState(
    selectedCapture || "captures/sample.cs8",
  );
  const [modulation, setModulation] = useState<WorkbenchModulation>("AUTO");
  const [lineCode, setLineCode] = useState<WorkbenchLineCode>("NRZ");
  const [baudOverride, setBaudOverride] = useState("");
  const [sampleRate, setSampleRate] = useState(String(sampleRateMsps * 1_000_000));
  const [viewMode, setViewMode] = useState<"hex" | "bin">("hex");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<WorkbenchDemodResult | null>(null);

  const matrix = useMemo(() => {
    if (!result?.packets?.length) return null;
    const rows = result.packets
      .map((p) => p.payload_bytes ?? [])
      .filter((r) => r.length > 0);
    if (!rows.length) return null;
    const width = Math.min(...rows.map((r) => r.length));
    const aligned = rows.map((r) => r.slice(0, width));
    const classes = result.column_classes?.length
      ? result.column_classes.slice(0, width)
      : Array.from({ length: width }, () => "mixed" as WorkbenchColumnClass);
    while (classes.length < width) classes.push("mixed");
    return { aligned, classes, width };
  }, [result]);

  const crcBadges = useMemo(() => {
    const ok = (result?.crc_matches ?? []).filter((m) => m.ok);
    const seen = new Set<string>();
    return ok.filter((m) => {
      if (seen.has(m.name)) return false;
      seen.add(m.name);
      return true;
    });
  }, [result]);

  const runDemod = async () => {
    const path = capturePath.trim();
    setBusy(true);
    try {
      const body: Record<string, unknown> = {
        filepath: path,
        sample_rate: Math.round(Number(sampleRate) || 2_000_000),
        modulation,
        line_code: lineCode,
      };
      const baud = Number(baudOverride);
      if (baudOverride.trim() && Number.isFinite(baud) && baud > 0) {
        body.baud_override = baud;
      }
      const r = await api.workbenchDemod(body);
      setResult(r);
      setSelectedCapture(path);
      setToast(
        `Workbench · ${r.modulation} @ ${r.baud_est.toFixed(0)} baud · ${r.packets.length} pkt`,
      );
    } catch (err) {
      setToast(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex h-full min-h-0 flex-col gap-2 p-2">
      <section className="panel shrink-0 rounded border border-slate-800 bg-slate-950/60 p-2">
        <div className="mb-2 flex items-center gap-2 text-[10px] uppercase tracking-wider text-slate-400">
          <Binary className="h-3.5 w-3.5 text-cyan-400" />
          Packet Workbench · Blind Demod & Bit-Slicer
        </div>
        <div className="grid grid-cols-[1fr_auto_auto_auto_auto_auto] items-end gap-2">
          <label className="flex flex-col gap-0.5 text-[10px] text-slate-500">
            Capture (.cs8)
            <input
              className="rounded border border-slate-700 bg-slate-900 px-2 py-1 font-mono text-[11px] text-slate-200"
              value={capturePath}
              onChange={(e) => setCapturePath(e.target.value)}
            />
          </label>
          <label className="flex flex-col gap-0.5 text-[10px] text-slate-500">
            fs
            <input
              className="w-24 rounded border border-slate-700 bg-slate-900 px-2 py-1 font-mono text-[11px]"
              value={sampleRate}
              onChange={(e) => setSampleRate(e.target.value)}
            />
          </label>
          <label className="flex flex-col gap-0.5 text-[10px] text-slate-500">
            Mod
            <select
              className="rounded border border-slate-700 bg-slate-900 px-2 py-1 text-[11px]"
              value={modulation}
              onChange={(e) => setModulation(e.target.value as WorkbenchModulation)}
            >
              {MODS.map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-0.5 text-[10px] text-slate-500">
            Line code
            <select
              className="rounded border border-slate-700 bg-slate-900 px-2 py-1 text-[11px]"
              value={lineCode}
              onChange={(e) => setLineCode(e.target.value as WorkbenchLineCode)}
            >
              {CODES.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-0.5 text-[10px] text-slate-500">
            Baud (opt)
            <input
              className="w-20 rounded border border-slate-700 bg-slate-900 px-2 py-1 font-mono text-[11px]"
              placeholder="auto"
              value={baudOverride}
              onChange={(e) => setBaudOverride(e.target.value)}
            />
          </label>
          <button
            type="button"
            disabled={busy}
            className="flex items-center gap-1 rounded border border-cyan-800 bg-cyan-950/40 px-3 py-1.5 text-[11px] text-cyan-200 hover:bg-cyan-950 disabled:opacity-50"
            onClick={() => void runDemod()}
          >
            <Crosshair className="h-3.5 w-3.5" />
            {busy ? "Demod…" : "Demod"}
          </button>
        </div>
        {result && (
          <div className="mt-2 flex flex-wrap items-center gap-2 text-[10px] text-slate-400">
            <span className="rounded border border-slate-700 px-1.5 py-0.5 font-mono text-slate-300">
              {result.modulation} · {result.baud_est.toFixed(1)} baud · spb{" "}
              {result.samples_per_symbol.toFixed(2)}
            </span>
            {result.sync_word && (
              <span className="rounded border border-emerald-800 px-1.5 py-0.5 font-mono text-emerald-300">
                Sync {result.sync_word}
              </span>
            )}
            {crcBadges.map((m) => (
              <span
                key={m.name}
                className="inline-flex items-center gap-1 rounded border border-violet-700 bg-violet-950/50 px-1.5 py-0.5 font-mono text-violet-200"
              >
                <FlaskConical className="h-3 w-3" />
                {m.name} ✓
              </span>
            ))}
            <span className="truncate text-slate-500">{result.notes}</span>
          </div>
        )}
      </section>

      <section className="panel min-h-0 flex-1 overflow-hidden rounded border border-slate-800 bg-slate-950/40">
        <div className="flex items-center justify-between border-b border-slate-800 px-2 py-1">
          <h3 className="text-[10px] uppercase tracking-wider text-slate-400">
            Differential Packet Matrix
          </h3>
          <div className="flex items-center gap-2">
            <div className="flex gap-1 text-[9px]">
              {(Object.keys(COL_LABEL) as WorkbenchColumnClass[]).map((k) => (
                <span
                  key={k}
                  className={`rounded border px-1 py-0.5 ${COL_STYLE[k]}`}
                >
                  {COL_LABEL[k]}
                </span>
              ))}
            </div>
            <div className="flex gap-0.5">
              <button
                type="button"
                className={`rounded px-1.5 py-0.5 text-[10px] ${
                  viewMode === "hex"
                    ? "bg-slate-800 text-cyan-300"
                    : "text-slate-500"
                }`}
                onClick={() => setViewMode("hex")}
              >
                HEX
              </button>
              <button
                type="button"
                className={`rounded px-1.5 py-0.5 text-[10px] ${
                  viewMode === "bin"
                    ? "bg-slate-800 text-cyan-300"
                    : "text-slate-500"
                }`}
                onClick={() => setViewMode("bin")}
              >
                BIN
              </button>
            </div>
          </div>
        </div>

        {!matrix ? (
          <div className="flex h-40 items-center justify-center text-[11px] text-slate-600">
            Demod a capture to populate aligned packet rows
          </div>
        ) : (
          <div className="h-full overflow-auto p-2">
            <table className="w-max border-collapse font-mono text-[10px]">
              <thead>
                <tr>
                  <th className="sticky left-0 z-10 bg-slate-950 px-1 py-0.5 text-left text-slate-500">
                    #
                  </th>
                  {matrix.classes.map((cls, i) => (
                    <th
                      key={i}
                      title={COL_LABEL[cls]}
                      className={`border px-1 py-0.5 text-center ${COL_STYLE[cls]}`}
                    >
                      {i.toString(16).toUpperCase().padStart(2, "0")}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {matrix.aligned.map((row, ri) => (
                  <tr key={ri} className="hover:bg-slate-900/80">
                    <td className="sticky left-0 z-10 bg-slate-950 px-1 py-0.5 text-slate-500">
                      {ri}
                      {result?.packets[ri]?.sync_hex
                        ? ` · ${result.packets[ri].sync_hex}`
                        : ""}
                    </td>
                    {row.map((b, ci) => (
                      <td
                        key={ci}
                        className={`border px-1 py-0.5 text-center tabular-nums ${COL_STYLE[matrix.classes[ci] ?? "mixed"]}`}
                      >
                        {viewMode === "hex" ? byteHex(b) : byteBin(b)}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
