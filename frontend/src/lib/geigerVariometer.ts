/**
 * WebAudio "Geiger Homing" variometer — pitch + pulse rate scale with
 * normalized RSSI above squelch (220 Hz → 1760 Hz).
 */

export type GeigerConfig = {
  squelchDb: number;
  ceilingDb: number;
};

const DEFAULT: GeigerConfig = { squelchDb: -90, ceilingDb: -30 };

export class GeigerVariometer {
  private ctx: AudioContext | null = null;
  private timer: number | null = null;
  private enabled = false;
  private lastNorm = 0;
  private config: GeigerConfig;

  constructor(config: Partial<GeigerConfig> = {}) {
    this.config = { ...DEFAULT, ...config };
  }

  setConfig(partial: Partial<GeigerConfig>) {
    this.config = { ...this.config, ...partial };
  }

  setRssi(rssiDb: number | null) {
    if (rssiDb == null) {
      this.lastNorm = 0;
      return;
    }
    const span = Math.max(1e-3, this.config.ceilingDb - this.config.squelchDb);
    this.lastNorm = Math.min(
      1,
      Math.max(0, (rssiDb - this.config.squelchDb) / span),
    );
  }

  async start() {
    if (this.enabled) return;
    const AC =
      window.AudioContext ||
      (window as unknown as { webkitAudioContext: typeof AudioContext })
        .webkitAudioContext;
    this.ctx = new AC();
    this.enabled = true;
    this.schedule();
  }

  stop() {
    this.enabled = false;
    if (this.timer != null) {
      window.clearTimeout(this.timer);
      this.timer = null;
    }
    void this.ctx?.close();
    this.ctx = null;
  }

  get isEnabled() {
    return this.enabled;
  }

  private schedule() {
    if (!this.enabled || !this.ctx) return;
    const n = this.lastNorm;
    // Pulse interval: 800ms (cold) → 80ms (hot)
    const intervalMs = 800 - n * 720;
    // Pitch: 220 → 1760 Hz
    const freq = 220 * Math.pow(8, n);
    if (n > 0.02) this.beep(freq, 0.04 + n * 0.04);
    this.timer = window.setTimeout(() => this.schedule(), Math.max(60, intervalMs));
  }

  private beep(freq: number, durSec: number) {
    const ctx = this.ctx;
    if (!ctx) return;
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = "sine";
    osc.frequency.value = freq;
    gain.gain.value = 0.0001;
    gain.gain.exponentialRampToValueAtTime(0.18, ctx.currentTime + 0.01);
    gain.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + durSec);
    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.start();
    osc.stop(ctx.currentTime + durSec + 0.02);
  }
}
