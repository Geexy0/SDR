import { useWebAudioPlayer } from "@/hooks/useWebAudioPlayer";

type Props = {
  enabled: boolean;
  className?: string;
};

export function WebAudioPlayer({ enabled, className = "" }: Props) {
  const {
    muted,
    setMuted,
    squelchDb,
    setSquelchDb,
    levelDb,
    spectrum,
    meta,
    connected,
    unlock,
  } = useWebAudioPlayer(enabled);

  const minBar = -80;
  const maxBar = 0;

  return (
    <div className={`rounded border border-slate-800 bg-slate-950/80 p-2 ${className}`}>
      <div className="mb-1 flex flex-wrap items-center gap-2 text-[10px] text-slate-400">
        <span className="uppercase tracking-wider">Browser audio</span>
        <span className={connected ? "text-emerald-400" : "text-slate-600"}>
          {connected ? "WS live" : "WS idle"}
        </span>
        {meta.ctcss_hz != null && (
          <span className="rounded border border-violet-700 bg-violet-950/50 px-1.5 py-0.5 text-violet-200">
            CTCSS {Number(meta.ctcss_hz).toFixed(1)} Hz
          </span>
        )}
        <span className="font-mono text-cyan-300/90">{levelDb.toFixed(1)} dBFS</span>
        <button
          type="button"
          className="rounded border border-slate-600 px-1.5 py-0.5 text-slate-200 hover:bg-slate-800"
          onClick={() => void unlock().then(() => setMuted((m) => !m))}
        >
          {muted ? "Unmute" : "Mute"}
        </button>
      </div>
      <div className="mb-1 flex h-8 items-end gap-px">
        {spectrum.map((db, i) => {
          const pct = Math.max(0, Math.min(1, (db - minBar) / (maxBar - minBar)));
          return (
            <div
              key={i}
              className="flex-1 rounded-sm bg-cyan-500/80"
              style={{ height: `${Math.round(pct * 100)}%` }}
            />
          );
        })}
      </div>
      <label className="flex items-center gap-2 text-[10px] text-slate-500">
        Squelch {squelchDb.toFixed(0)} dBFS
        <input
          type="range"
          min={-90}
          max={-10}
          step={1}
          value={squelchDb}
          className="flex-1"
          onChange={(e) => setSquelchDb(Number(e.target.value))}
        />
      </label>
    </div>
  );
}

export function CtcssPill({ hz }: { hz: number | null | undefined }) {
  if (hz == null || !Number.isFinite(hz)) return null;
  return (
    <span className="rounded border border-violet-700 bg-violet-950/60 px-1.5 py-0.5 text-[10px] font-semibold text-violet-200">
      PL {hz.toFixed(1)}
    </span>
  );
}
