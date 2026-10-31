export type LookAngleData = {
  label: string;
  azimuth_deg: number;
  elevation_deg: number;
  slant_range_m: number;
  ground_range_m?: number;
  fspl_db: number;
  horizon_km: number;
  beyond_horizon?: boolean;
  frequency_hz: number;
};

type Props = {
  data: LookAngleData | null;
  onClose?: () => void;
};

function fmtRange(m: number): string {
  if (m >= 1000) return `${(m / 1000).toFixed(2)} km`;
  return `${m.toFixed(0)} m`;
}

export function LookAngleWidget({ data, onClose }: Props) {
  if (!data) return null;
  return (
    <div className="pointer-events-auto absolute bottom-2 left-2 z-[500] min-w-[200px] rounded border border-amber-800/70 bg-slate-950/95 p-2 shadow-lg">
      <div className="mb-1 flex items-center justify-between gap-2">
        <div className="text-[10px] uppercase tracking-wider text-amber-200/90">
          Target Look-Angle
        </div>
        {onClose && (
          <button
            type="button"
            className="text-[10px] text-slate-500 hover:text-slate-300"
            onClick={onClose}
          >
            ✕
          </button>
        )}
      </div>
      <p className="mb-1 truncate text-[10px] text-slate-300">{data.label}</p>
      <div className="grid grid-cols-2 gap-x-3 gap-y-0.5 font-mono text-[11px] text-amber-100">
        <span className="text-slate-500">Az</span>
        <span>{data.azimuth_deg.toFixed(1)}°</span>
        <span className="text-slate-500">El</span>
        <span>{data.elevation_deg.toFixed(1)}°</span>
        <span className="text-slate-500">Slant</span>
        <span>{fmtRange(data.slant_range_m)}</span>
        <span className="text-slate-500">FSPL</span>
        <span>{data.fspl_db.toFixed(1)} dB</span>
        <span className="text-slate-500">Horizon</span>
        <span>
          {data.horizon_km.toFixed(1)} km
          {data.beyond_horizon ? " · beyond" : ""}
        </span>
      </div>
      <p className="mt-1 text-[9px] text-slate-500">
        {(data.frequency_hz / 1e6).toFixed(3)} MHz · point Yagi to Az/El
      </p>
    </div>
  );
}
