export type HardwareState =
  | "DISCONNECTED"
  | "MAYHEM_SERIAL"
  | "HACKRF_NATIVE"
  | "BUSY_TX"
  | "BUSY_RX"
  | "MULTIPLEX_RX"
  | "SWITCHING";

export type WorkspaceTab = "subghz" | "workbench" | "tx" | "recon" | "tpms";

export type WorkbenchModulation = "AUTO" | "OOK" | "2FSK" | "GFSK" | "BPSK";
export type WorkbenchLineCode =
  | "NRZ"
  | "NRZ_INV"
  | "MANCHESTER_IEEE"
  | "MANCHESTER_THOMAS"
  | "DIFF_MANCHESTER"
  | "PWM";
export type WorkbenchColumnClass =
  | "static"
  | "incrementing"
  | "high_entropy"
  | "mixed";

export interface WorkbenchPacketRow {
  bit_offset: number;
  sync_bits: string;
  sync_hex: string;
  payload_bits: string;
  payload_hex: string;
  payload_bytes: number[];
  hamming: number;
}

export interface WorkbenchCrcMatch {
  name: string;
  ok: boolean;
  payload_len: number;
  expect: string;
  got: string;
}

export interface WorkbenchDemodResult {
  modulation: WorkbenchModulation;
  line_code: WorkbenchLineCode;
  sample_rate: number;
  baud_est: number;
  samples_per_symbol: number;
  bits: string;
  bits_inv: string;
  sync_word: string | null;
  packets: WorkbenchPacketRow[];
  column_classes: WorkbenchColumnClass[];
  crc_matches: WorkbenchCrcMatch[];
  notes: string;
}

export type WaterfallLut = "inferno" | "viridis" | "phosphor" | "gqrx";

export type FreqUnit = "MHz" | "GHz";

export interface HardwareStatus {
  state: HardwareState;
  serial_port: string | null;
  hackrf_native: boolean;
  mayhem_serial: boolean;
  subprocess_running: boolean;
  subprocess_argv: string[] | null;
  last_error: string | null;
}

export interface SubGhzPacket {
  id: number;
  ts: string;
  frequency_hz: number | null;
  protocol: string | null;
  model: string | null;
  data: Record<string, unknown>;
  raw_pulses_us: number[] | null;
}

export interface RfFingerprintBadge {
  physical_emitter_id: string;
  cfo_ppm: number;
  rise_time_us: number;
  clone_alert: boolean;
  alert?: string | null;
}

export interface AnalyzeTiming {
  short_us: number | null;
  long_us: number | null;
  sync_gap_us: number | null;
  bit_us: number | null;
  encoding: string;
  confidence: number;
  notes: string;
}

export interface AnalyzeFsk {
  carrier_offset_hz: number;
  deviation_hz: number;
  deviation_khz: number;
  baud_est: number;
  samples_per_bit: number;
  bits: string;
  preambles: { pattern: string; bit_offset: number; pattern_bits?: string }[];
  notes: string;
}

export interface AnalyzeResult {
  num_samples: number;
  envelope_peak: number;
  threshold: number;
  threshold_method: string;
  sample_rate: number;
  pulses_us: number[];
  pulse_count: number;
  envelope_ds: number[];
  freq_dev_ds?: number[];
  phase_ds?: number[];
  constellation_size?: number;
  constellation_density?: number[];
  file_bytes?: number;
  downsampled?: boolean;
  timing: AnalyzeTiming;
  fsk?: AnalyzeFsk | null;
  packet_id?: number;
}

export type AnalyzerViewMode = "ook" | "fsk" | "constellation";
