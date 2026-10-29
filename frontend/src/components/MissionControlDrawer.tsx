import { useEffect, useMemo, useState } from "react";
import { api, type MissionFinding, type MissionStatus, type MissionTemplate } from "@/lib/api";
import { useHardwareStore } from "@/store/useHardwareStore";

export function MissionControlDrawer() {
  const setToast = useHardwareStore((s) => s.setToast);
  const lastTelemetry = useHardwareStore((s) => s.lastTelemetry);
  const telemetrySeq = useHardwareStore((s) => s.telemetrySeq);

  const [open, setOpen] = useState(true);
  const [templates, setTemplates] = useState<MissionTemplate[]>([]);
  const [status, setStatus] = useState<MissionStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [syncSd, setSyncSd] = useState(false);
  const [log, setLog] = useState<string[]>([]);

  const refresh = async () => {
    try {
      const st = await api.missionStatus();
      setStatus(st);
    } catch {
      /* ignore */
    }
  };

  useEffect(() => {
    void api
      .missionTemplates()
      .then((r) => setTemplates(r.templates ?? []))
      .catch(() => setTemplates([]));
    void refresh();
  }, []);

  useEffect(() => {
    const m = lastTelemetry?.metrics;
    if (!m || m.mission !== true) return;
    const step = String(m.step_type ?? "");
    const detail = (m.detail as Record<string, unknown> | undefined) ?? {};
    const phase = detail.phase != null ? ` · ${String(detail.phase)}` : "";
    const line = `[${new Date().toISOString().slice(11, 19)}] ${step}${phase}`;
    setLog((prev) => [...prev.slice(-80), line]);
    void refresh();
  }, [telemetrySeq]); // eslint-disable-line react-hooks/exhaustive-deps

  const running = Boolean(status?.running);
  const report = status?.report;
  const findings: MissionFinding[] = report?.findings ?? [];

  const progressPct = useMemo(() => {
    if (!status || status.step_total <= 0) return 0;
    if (status.state === "completed") return 100;
    const idx = Math.max(0, status.step_index + 1);
    return Math.min(100, Math.round((idx / status.step_total) * 100));
  }, [status]);

  const startTemplate = async (tpl: MissionTemplate) => {
    setBusy(true);
    try {
      await api.startMission({
        template_id: tpl.id,
        name: tpl.name,
        sync_freqman_sd: syncSd,
      });
      setLog([`started · ${tpl.name}`]);
      setToast(`Mission started: ${tpl.name}`);
      await refresh();
    } catch (err) {
      setToast(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const stopMission = async () => {
    setBusy(true);
    try {
      await api.stopMission();
      setToast("Mission aborted");
      await refresh();
    } catch (err) {
      setToast(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="panel col-span-full rounded p-3">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <button
          type="button"
          className="text-left"
          onClick={() => setOpen((o) => !o)}
        >
          <h3 className="text-[10px] uppercase tracking-wider text-slate-400">
            Mission Scheduler {open ? "▾" : "▸"}
          </h3>
          <p className="text-[9px] text-slate-500">
            Autonomous RX pipelines — sweep → rank → burst capture → analyze → FREQMAN.
          </p>
        </button>
        <div className="flex flex-wrap items-center gap-2 text-[10px]">
          <span className="rounded border border-slate-700 px-2 py-0.5 text-slate-300">
            {status?.state ?? "idle"}
            {status?.step_type ? ` · ${status.step_type}` : ""}
          </span>
          {status && status.step_total > 0 && (
            <span className="text-slate-500">
              step {Math.max(0, status.step_index + 1)}/{status.step_total} · {progressPct}%
            </span>
          )}
          <label className="flex items-center gap-1 text-slate-400">
            <input
              type="checkbox"
              checked={syncSd}
              disabled={running}
              onChange={(e) => setSyncSd(e.target.checked)}
            />
            sync SD @ end
          </label>
          <button
            type="button"
            disabled={busy || !running}
            className="rounded border border-slate-600 px-2 py-1 disabled:opacity-40"
            onClick={() => void stopMission()}
          >
            Abort
          </button>
        </div>
      </div>

      {open && (
        <>
          <div className="mb-2 h-1.5 overflow-hidden rounded bg-slate-900">
            <div
              className="h-full bg-cyan-600/80 transition-[width] duration-300"
              style={{ width: `${progressPct}%` }}
            />
          </div>

          <div className="mb-3 grid gap-2 md:grid-cols-3">
            {templates.map((tpl) => (
              <div
                key={tpl.id}
                className="rounded border border-slate-800 bg-slate-950/40 p-2"
              >
                <div className="text-[11px] text-cyan-300">{tpl.name}</div>
                <p className="mt-0.5 min-h-[2.5rem] text-[9px] leading-snug text-slate-500">
                  {tpl.description}
                </p>
                <button
                  type="button"
                  disabled={busy || running}
                  className="mt-2 rounded border border-cyan-800 bg-cyan-950/40 px-2 py-1 text-[10px] text-cyan-200 disabled:opacity-40"
                  onClick={() => void startTemplate(tpl)}
                >
                  Launch
                </button>
              </div>
            ))}
            {templates.length === 0 && (
              <p className="text-[10px] text-slate-600 md:col-span-3">
                Loading templates…
              </p>
            )}
          </div>

          <pre className="mb-3 max-h-28 overflow-auto rounded bg-black/50 p-2 text-[10px] leading-relaxed text-cyan-200/90">
            {log.length === 0
              ? "telemetry — launch a template; progress streams on /ws/telemetry"
              : log.join("\n")}
          </pre>

          <div className="border-t border-slate-800 pt-2">
            <h4 className="mb-1 text-[10px] uppercase tracking-wider text-slate-400">
              Mission Summary Report
              {report?.finding_count != null ? ` · ${report.finding_count} channels` : ""}
            </h4>
            {findings.length === 0 ? (
              <p className="text-[10px] text-slate-600">
                No findings yet — completed missions list frequency, RSSI, modulation, and captures here.
              </p>
            ) : (
              <div className="max-h-48 overflow-auto">
                <table className="w-full text-left text-[10px] text-slate-300">
                  <thead className="sticky top-0 bg-slate-950 text-slate-500">
                    <tr>
                      <th className="px-1 py-0.5">Freq</th>
                      <th className="px-1 py-0.5">RSSI</th>
                      <th className="px-1 py-0.5">Mod</th>
                      <th className="px-1 py-0.5">Baud</th>
                      <th className="px-1 py-0.5">.cs8 / .wav</th>
                    </tr>
                  </thead>
                  <tbody>
                    {findings.map((f) => (
                      <tr key={f.id} className="border-t border-slate-900">
                        <td className="px-1 py-0.5 text-amber-200">
                          {(f.frequency_hz / 1e6).toFixed(4)} MHz
                        </td>
                        <td className="px-1 py-0.5">
                          {f.peak_rssi_db != null ? `${f.peak_rssi_db.toFixed(1)} dB` : "—"}
                        </td>
                        <td className="px-1 py-0.5 text-cyan-300">{f.modulation ?? "—"}</td>
                        <td className="px-1 py-0.5">
                          {f.baud_est != null && f.baud_est > 0 ? f.baud_est.toFixed(0) : "—"}
                        </td>
                        <td className="max-w-[14rem] truncate px-1 py-0.5 font-mono text-slate-500" title={f.cs8_path ?? ""}>
                          {f.cs8_path ? f.cs8_path.split("/").pop() : "—"}
                          {f.wav_path ? ` · ${f.wav_path.split("/").pop()}` : ""}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </>
      )}
    </section>
  );
}
