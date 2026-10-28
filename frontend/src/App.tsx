import { HardwareHeader } from "@/components/HardwareHeader";
import { MayhemBridgeDock } from "@/components/MayhemBridgeDock";
import { SpectrumWaterfall } from "@/components/SpectrumWaterfall";
import { WorkspaceTabs } from "@/components/WorkspaceTabs";

export default function App() {
  return (
    <div className="flex h-[100vh] w-[100vw] flex-col overflow-hidden bg-slate-950 text-slate-100">
      <HardwareHeader />
      <div className="grid min-h-0 flex-1 grid-cols-[1fr_280px]">
        <main className="grid min-h-0 min-w-0 grid-rows-[minmax(220px,38%)_minmax(0,1fr)]">
          <SpectrumWaterfall />
          <WorkspaceTabs />
        </main>
        <MayhemBridgeDock />
      </div>
    </div>
  );
}
