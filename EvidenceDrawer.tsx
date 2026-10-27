import { useEffect, useRef } from "react";
import type { Citation } from "../types";
import { SECTION_LABEL } from "../labels";
import { IconClose, IconExternal } from "./Icons";

interface Props {
  citations: Citation[];
  focusId: string | null;
  heading: string;
  onClose: () => void;
}

/** Modal side sheet. Focus moves in on open, is trapped while open and returns to the trigger on close. */
export function EvidenceDrawer({ citations, focusId, heading, onClose }: Props) {
  const panel = useRef<HTMLDivElement>(null);
  const returnTo = useRef<Element | null>(null);

  useEffect(() => {
    returnTo.current = document.activeElement;
    panel.current?.querySelector<HTMLElement>("[data-autofocus]")?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
      if (e.key === "Tab" && panel.current) {
        const f = panel.current.querySelectorAll<HTMLElement>("a[href], button:not([disabled])");
        if (!f.length) return;
        const first = f[0];
        const last = f[f.length - 1];
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }
    };
    document.addEventListener("keydown", onKey);
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = "";
      (returnTo.current as HTMLElement | null)?.focus?.();
    };
  }, [onClose]);

  useEffect(() => {
    if (focusId) document.getElementById(`ev-${focusId}`)?.scrollIntoView({ block: "start" });
  }, [focusId]);

  return (
    <div className="scrim" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div ref={panel} className="drawer" role="dialog" aria-modal="true" aria-labelledby="drawer-title">
        <header className="drawer__head">
          <div>
            <h2 id="drawer-title">{heading}</h2>
            <p className="muted">{citations.length} passage{citations.length === 1 ? "" : "s"} from FDA structured product labels</p>
          </div>
          <button className="icon-btn" onClick={onClose} aria-label="Close evidence" data-autofocus>
            <IconClose />
          </button>
        </header>
        <ol className="evidence">
          {citations.map((c, i) => (
            <li key={c.chunk_id} id={`ev-${c.chunk_id}`} className={`evidence__item ${c.chunk_id === focusId ? "is-focus" : ""}`}>
              <div className="evidence__meta">
                <span className="evidence__ref">[{i + 1}]</span>
                <span className="evidence__section">{SECTION_LABEL[c.section] ?? c.section}</span>
              </div>
              <h3 className="evidence__title">{c.title}</h3>
              <blockquote className="label-text">{c.snippet}</blockquote>
              <p className="evidence__foot">
                {c.effective_time && <span>Label effective {new Date(c.effective_time).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" })}</span>}
                <a href={c.dailymed_url} target="_blank" rel="noreferrer">
                  Open full label on DailyMed <IconExternal size={14} />
                  <span className="visually-hidden">(opens in a new tab)</span>
                </a>
              </p>
            </li>
          ))}
        </ol>
      </div>
    </div>
  );
}
