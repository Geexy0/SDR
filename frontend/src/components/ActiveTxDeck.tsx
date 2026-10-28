import { useHardwareStore } from "@/store/useHardwareStore";
import { api } from "@/lib/api";

const STAGES = [
  "1. Armed / Listening",
  "2. Jamming + Capturing Code 1",
  "3. Capturing Code 2 + Replaying Code 1",
  "4. Stored Code 2 Ready",
] as const;

export function ActiveTxDeck() {
  const jammerMode = useHardwareStore((s) => s.jammerMode);
  const chirpBandwidthHz = useHardwareStore((s) => s.chirpBandwidthHz);
  const chirpPeriodUs = useHardwareStore((s) => s.chirpPeriodUs);
  const txVgaDb = useHardwareStore((s) => s.txVgaDb);
  const maxDurationSec = useHardwareStore((s) => s.maxDurationSec);
  const frequencyHz = useHardwareStore((s) => s.frequencyHz);
  const sampleRateMsps = useHardwareStore((s) => s.sampleRateMsps);
  const jamOffsetHz = useHardwareStore((s) => s.jamOffsetHz);
  const rolljamStage = useHardwareStore((s) => s.rolljamStage);
  const lnaDb = useHardwareStore((s) => s.lnaDb);
  const rxVgaDb = useHardwareStore((s) => s.rxVgaDb);
  const setJammerMode = useHardwareStore((s) => s.setJammerMode);
  const setChirpBandwidthHz = useHardwareStore((s) => s.setChirpBandwidthHz);
  const setChirpPeriodUs = useHardwareStore((s) => s.setChirpPeriodUs);
  const setMaxDurationSec = useHardwareStore((s) => s.setMaxDurationSec);
  const setJamOffsetHz = useHardwareStore((s) => s.setJamOffsetHz);
  const setRolljamStage = useHardwareStore((s) => s.setRolljamStage);
  const setFrequencyHz = useHardwareStore((s) => s.setFrequencyHz);
  const setToast = useHardwareStore((s) => s.setToast);
  const setTxVga = useHardwareStore((s) => s.setTxVga);

  const startJammer = async () => {
    try {
      await api.jammer({
        mode: jammerMode,
        frequency_hz: Math.round(frequencyHz),
        sample_rate: sampleRateMsps * 1_000_000,
        tx_vga: txVgaDb,
        amp: 0,
        bandwidth_hz: chirpBandwidthHz,
        sweep_period_s: chirpPeriodUs / 1e6,
        max_duration_sec: maxDurationSec,
      });
      setToast(`Jammer ${jammerMode} armed`);
    } catch (err) {
      setToast(err instanceof Error ? err.message : String(err));
    }
  };

  const runRolljam = async () => {
    try {
      setRolljamStage(2);
      await api.rolljam({
        phase: "CAPTURE_JAM_REPLAY",
        frequency_hz: Math.round(frequencyHz),
        sample_rate: sampleRateMsps * 1_000_000,
        lna: lnaDb,
        vga: rxVgaDb,
        num_samples: sampleRateMsps * 1_000_000 * 2,
        tx_vga: txVgaDb,
        amp: 0,
        jam_frequency_hz: Math.round(frequencyHz + jamOffsetHz),
        jam_duration_sec: Math.min(2, maxDurationSec),
        replay_max_duration_sec: maxDurationSec,
      });
      setRolljamStage(4);
      setToast("Rolljam sequence complete — Code 2 stored");
    } catch (err) {
      setRolljamStage(1);
      setToast(err instanceof Error ? err.message : String(err));
    }
  };

  return (
    <div className="grid h-full min-h-0 gap-2 overflow-auto p-2 lg:grid-cols-2">
      <section className="panel rounded p-3">
        <h3 className="mb-3 text-[10px] uppercase tracking-wider text-slate-400">
          Lab Jammer Control
        </h3>
        <div className="mb-3 flex gap-1 rounded border border-slate-800 p-0.5">
          <button
            type="button"
            className={`flex-1 px-2 py-1.5 text-[10px] ${
              jammerMode === "CW_AWGN" ? "bg-red-950 text-red-300" : "text-slate-400"
            }`}
            onClick={() => setJammerMode("CW_AWGN")}
          >
            Hardware CW / AWGN
          </button>
          <button
            type="button"
            className={`flex-1 px-2 py-1.5 text-[10px] ${
              jammerMode === "CHIRP_BARRAGE" ? "bg-amber-950 text-amber-300" : "text-slate-400"
            }`}
            onClick={() => setJammerMode("CHIRP_BARRAGE")}
          >
            Linear FM Chirp Barrage
          </button>
        </div>

        <label className="mb-2 block text-[10px] text-slate-500">
          Sweep Bandwidth B ({(chirpBandwidthHz / 1e6).toFixed(2)} MHz)
          <input
            type="range"
            min={100_000}
            max={20_000_000}
            step={100_000}
            value={chirpBandwidthHz}
            onChange={(e) => setChirpBandwidthHz(Number(e.target.value))}
            className="mt-1 w-full accent-amber-500"
          />
        </label>
        <label className="mb-2 block text-[10px] text-slate-500">
          Chirp Period T ({chirpPeriodUs} µs)
          <input
            type="range"
            min={10}
            max={10_000}
            step={10}
            value={chirpPeriodUs}
            onChange={(e) => setChirpPeriodUs(Number(e.target.value))}
            className="mt-1 w-full accent-amber-500"
          />
        </label>
        <label className="mb-2 block text-[10px] text-slate-500">
          TX VGA ({txVgaDb} dB)
          <input
            type="range"
            min={0}
            max={47}
            step={1}
            value={txVgaDb}
            onChange={(e) => setTxVga(Number(e.target.value))}
            className="mt-1 w-full accent-red-500"
          />
        </label>
        <label className="mb-3 block text-[10px] text-slate-500">
          Safety Watchdog max_duration_sec
          <input
            type="number"
            min={1}
            max={60}
            step={0.5}
            value={maxDurationSec}
            onChange={(e) => setMaxDurationSec(Number(e.target.value))}
            className="mt-1 w-full rounded border border-slate-700 bg-slate-950 px-2 py-1 text-xs"
          />
        </label>
        <div className="flex gap-2">
          <button
            type="button"
            className="rounded border border-red-700 bg-red-950/60 px-3 py-1.5 text-xs font-semibold text-red-300"
            onClick={() => void startJammer()}
          >
            Arm Jammer
          </button>
          <button
            type="button"
            className="rounded border border-slate-700 px-3 py-1.5 text-xs"
            onClick={() => void api.stop()}
          >
            Stop
          </button>
        </div>
        <p className="mt-2 text-[10px] text-slate-500">
          Chirp IQ path: <code className="text-cyan-400">/dev/shm/chirp.cs8</code> · amp locked 0
          (SCOPE)
        </p>
      </section>

      <section className="panel rounded p-3">
        <h3 className="mb-3 text-[10px] uppercase tracking-wider text-slate-400">
          Rolljam State Machine
        </h3>
        <ol className="mb-4 space-y-2">
          {STAGES.map((label, i) => {
            const stage = (i + 1) as 1 | 2 | 3 | 4;
            const active = rolljamStage === stage;
            const done = rolljamStage > stage;
            return (
              <li
                key={label}
                className={`rounded border px-3 py-2 text-[11px] ${
                  active
                    ? "border-amber-500 bg-amber-950/40 text-amber-200"
                    : done
                      ? "border-emerald-800 bg-emerald-950/30 text-emerald-300"
                      : "border-slate-800 text-slate-500"
                }`}
              >
                {label}
              </li>
            );
          })}
        </ol>
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
          <label className="text-[10px] text-slate-500">
            Target f<sub>c</sub> (Hz)
            <input
              type="number"
              className="mt-0.5 w-full rounded border border-slate-700 bg-slate-950 px-2 py-1 text-xs"
              value={Math.round(frequencyHz)}
              onChange={(e) => setFrequencyHz(Number(e.target.value))}
            />
          </label>
          <label className="text-[10px] text-slate-500">
            Jammer offset f<sub>jam</sub> (Hz)
            <input
              type="number"
              className="mt-0.5 w-full rounded border border-slate-700 bg-slate-950 px-2 py-1 text-xs"
              value={jamOffsetHz}
              onChange={(e) => setJamOffsetHz(Number(e.target.value))}
            />
          </label>
          <label className="text-[10px] text-slate-500">
            RX Capture BW
            <input
              className="mt-0.5 w-full rounded border border-slate-700 bg-slate-950 px-2 py-1 text-xs"
              value={`${sampleRateMsps} MSPS`}
              readOnly
            />
          </label>
        </div>
        <div className="mt-3 flex gap-2">
          <button
            type="button"
            className="rounded border border-amber-700 bg-amber-950/50 px-3 py-1.5 text-xs text-amber-200"
            onClick={() => {
              setRolljamStage(1);
              void runRolljam();
            }}
          >
            Arm Sequencer
          </button>
          <button
            type="button"
            className="rounded border border-slate-700 px-3 py-1.5 text-xs"
            onClick={() => setRolljamStage(1)}
          >
            Reset Stages
          </button>
        </div>
      </section>
    </div>
  );
}
