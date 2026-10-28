import { AudioWaveform, CircleDot, RadioTower, SatelliteDish } from "lucide-react";
import type { WorkspaceTab } from "@/lib/types";
import { useHardwareStore } from "@/store/useHardwareStore";
import { ActiveTxDeck } from "./ActiveTxDeck";
import { ReconSpoofDeck } from "./ReconSpoofDeck";
import { SubGhzDeck } from "./SubGhzDeck";
import { TpmsFleetDeck } from "./TpmsFleetDeck";

const TABS: { id: WorkspaceTab; label: string; icon: typeof AudioWaveform }[] = [
  { id: "subghz", label: "Sub-GHz OOK", icon: AudioWaveform },
  { id: "tpms", label: "TPMS Spy", icon: CircleDot },
  { id: "tx", label: "Active TX / Rolljam", icon: RadioTower },
  { id: "recon", label: "Recon / Spoof", icon: SatelliteDish },
];

export function WorkspaceTabs() {
  const tab = useHardwareStore((s) => s.workspaceTab);
  const setWorkspaceTab = useHardwareStore((s) => s.setWorkspaceTab);

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex shrink-0 gap-1 border-b border-slate-800 bg-slate-950 px-2">
        {TABS.map(({ id, label, icon: Icon }) => (
          <button
            key={id}
            type="button"
            onClick={() => setWorkspaceTab(id)}
            className={`flex items-center gap-1.5 border-b-2 px-3 py-2 text-[11px] ${
              tab === id
                ? "border-emerald-500 text-emerald-300"
                : "border-transparent text-slate-500 hover:text-slate-300"
            }`}
          >
            <Icon className="h-3.5 w-3.5" />
            {label}
          </button>
        ))}
      </div>
      <div className="min-h-0 flex-1 overflow-hidden">
        {tab === "subghz" && <SubGhzDeck />}
        {tab === "tpms" && <TpmsFleetDeck />}
        {tab === "tx" && <ActiveTxDeck />}
        {tab === "recon" && <ReconSpoofDeck />}
      </div>
    </div>
  );
}
