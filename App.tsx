import { useCallback, useEffect, useState } from "react";
import type { OrderState } from "./components/OrderPanel";
import { IconBridge, IconList, IconMoon, IconShield, IconSun, IconWorkbench } from "./components/Icons";
import { Method } from "./pages/Method";
import { ShortageMonitor } from "./pages/ShortageMonitor";
import { Workbench } from "./pages/Workbench";
import type { ShortageRecord } from "./types";

type Route = "workbench" | "shortages" | "method";
const ROUTES: { id: Route; label: string; icon: JSX.Element }[] = [
  { id: "workbench", label: "Workbench", icon: <IconWorkbench /> },
  { id: "shortages", label: "Shortages", icon: <IconList /> },
  { id: "method", label: "Method", icon: <IconShield /> },
];

const fromHash = (): Route => {
  const h = window.location.hash.replace("#/", "") as Route;
  return ROUTES.some((r) => r.id === h) ? h : "workbench";
};

type Theme = "light" | "dark";

export default function App() {
  const [route, setRoute] = useState<Route>(fromHash);
  const [prefill, setPrefill] = useState<Partial<OrderState> | null>(null);
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
    const onHash = () => setRoute(fromHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  useEffect(() => {
    document.title = `${ROUTES.find((r) => r.id === route)?.label} | Shortfall Bridge AI`;
    document.getElementById("main")?.focus();
  }, [route]);

  const go = (r: Route) => { window.location.hash = `/${r}`; };
  const consumePrefill = useCallback(() => setPrefill(null), []);

  const findAlternatives = (r: ShortageRecord) => {
    const route = r.routes[0] ?? "";
    setPrefill({ drug: r.ingredients[0] ?? r.generic_name, route, dosageForm: r.dosage_form ?? "" });
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
        {route === "workbench" && <Workbench prefill={prefill} consumePrefill={consumePrefill} />}
        {route === "shortages" && <ShortageMonitor onFindAlternatives={findAlternatives} />}
        {route === "method" && <Method />}
      </main>
    </div>
  );
}
