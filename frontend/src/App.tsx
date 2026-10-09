import { CurrentPage } from "./cockpit/Pages";
import { Shell } from "./cockpit/Shell";
import { CockpitProvider } from "./cockpit/store";

/**
 * SkillForge Supervisor Cockpit.
 * Single main view + overlays (see the Documentation Package, section 1 "App Flow").
 * Pages: Dasbor (overview), Cockpit (disruption → proposals → approval), Lantai Pabrik (floor map).
 */
export default function App() {
  return (
    <CockpitProvider>
      <Shell>
        <CurrentPage />
      </Shell>
    </CockpitProvider>
  );
}
