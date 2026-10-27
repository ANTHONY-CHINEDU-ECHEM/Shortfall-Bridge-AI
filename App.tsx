import { useCallback, useEffect, useState } from "react";
import { EngineStatus } from "./components/EngineStatus";
import type { OrderState } from "./components/OrderPanel";
import { IconBridge, IconCalc, IconChart, IconList, IconMoon, IconShield, IconSun, IconWorkbench } from "./components/Icons";
import { decodeOrder } from "./share";
import { Calculators } from "./pages/Calculators";
import { Insights } from "./pages/Insights";
import { Method } from "./pages/Method";
import { ShortageMonitor } from "./pages/ShortageMonitor";
import { Workbench } from "./pages/Workbench";
import type { ShortageRecord } from "./types";

type Route = "workbench" | "shortages" | "insights" | "calculators" | "method";
const ROUTES: { id: Route; label: string; icon: JSX.Element }[] = [
  { id: "workbench", label: "Workbench", icon: <IconWorkbench /> },
  { id: "shortages", label: "Shortages", icon: <IconList /> },
  { id: "insights", label: "Insights", icon: <IconChart /> },
  { id: "calculators", label: "Calculators", icon: <IconCalc /> },
  { id: "method", label: "Method", icon: <IconShield /> },
];

function parseHash(): { route: Route; query: string } {
  const raw = window.location.hash.replace(/^#\/?/, "");
  const [path, query = ""] = raw.split("?");
  const route = (ROUTES.some((r) => r.id === path) ? path : "workbench") as Route;
  return { route, query };
}

type Theme = "light" | "dark";

export default function App() {
  const [{ route, query }, setLoc] = useState(parseHash);
  const [prefill, setPrefill] = useState<Partial<OrderState> | null>(null);
  const [autoRun, setAutoRun] = useState(false);
  const [openRef, setOpenRef] = useState<string | null>(null);
  const [shortageQuery, setShortageQuery] = useState("");
  const [theme, setTheme] = useState<Theme>(() => {
    try {
      const saved = localStorage.getItem("sfb-theme");
      if (saved === "light" || saved === "dark") return saved;
    } catch { /* storage unavailable */ }
    return window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  });

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try { localStorage.setItem("sfb-theme", theme); } catch { /* ignore */ }
  }, [theme]);

  useEffect(() => {
    const onHash = () => setLoc(parseHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  // Shared links: #/workbench?drug=...&run=1 prefills (and runs); #/workbench?ref=r_... opens a stored result.
  useEffect(() => {
    if (route !== "workbench" || !query) return;
    const decoded = decodeOrder(query);
    if (decoded?.ref) setOpenRef(decoded.ref);
    else if (decoded) {
      setPrefill(decoded.order);
      setAutoRun(decoded.run);
    }
    window.history.replaceState(null, "", "#/workbench");
  }, [route, query]);

  useEffect(() => {
    document.title = `${ROUTES.find((r) => r.id === route)?.label} | Shortfall Bridge AI`;
    document.getElementById("main")?.focus();
  }, [route]);

  const go = (r: Route) => { window.location.hash = `/${r}`; };
  const consumePrefill = useCallback(() => setPrefill(null), []);

  const findAlternatives = (r: ShortageRecord) => {
    const fam = r.routes[0] ?? (/inject|vial|infusion/i.test(`${r.dosage_form} ${r.presentation}`) ? "intravenous" : "");
    setPrefill({ drug: r.ingredients[0] ?? r.generic_name, route: fam, dosageForm: r.dosage_form ?? "" });
    setAutoRun(false);
    go("workbench");
  };

  return (
    <div className="shell">
      <a className="skip" href="#main">Skip to content</a>
      <nav className="rail" aria-label="Primary">
        <div className="brand" aria-label="Shortfall Bridge AI">
          <span className="brand__mark"><IconBridge size={22} /></span>
          <span className="brand__name">Shortfall<br />Bridge</span>
        </div>
        <ul className="rail__list">
          {ROUTES.map((r) => (
            <li key={r.id}>
              <a href={`#/${r.id}`} className="rail__link" aria-current={route === r.id ? "page" : undefined}>
                {r.icon}
                <span>{r.label}</span>
              </a>
            </li>
          ))}
        </ul>
        <button className="rail__theme icon-btn" onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
          aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} theme`}>
          {theme === "dark" ? <IconSun /> : <IconMoon />}
        </button>
      </nav>
      <main id="main" tabIndex={-1} className="main">
        <EngineStatus />
        {route === "workbench" && (
          <Workbench prefill={prefill} autoRun={autoRun} openRef={openRef} consumePrefill={consumePrefill} />
        )}
        {route === "shortages" && <ShortageMonitor onFindAlternatives={findAlternatives} initialQuery={shortageQuery} />}
        {route === "insights" && <Insights onSearch={(q) => { setShortageQuery(q); go("shortages"); }} />}
        {route === "calculators" && <Calculators />}
        {route === "method" && <Method />}
      </main>
    </div>
  );
}
