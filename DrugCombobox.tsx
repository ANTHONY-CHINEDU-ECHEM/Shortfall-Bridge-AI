import { forwardRef, useEffect, useId, useRef, useState } from "react";
import { api } from "../api";
import type { DrugSuggestion } from "../types";

interface Props {
  value: string;
  onChange: (v: string) => void;
  invalid?: boolean;
  describedBy?: string;
}

/** WAI ARIA 1.2 combobox with list autocomplete. Arrow keys move, Enter selects, Escape closes. */
export const DrugCombobox = forwardRef<HTMLInputElement, Props>(function DrugCombobox(
  { value, onChange, invalid, describedBy },
  ref,
) {
  const id = useId();
  const listId = `${id}-list`;
  const [items, setItems] = useState<DrugSuggestion[]>([]);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  const [loading, setLoading] = useState(false);
  const skipNext = useRef(false);

  useEffect(() => {
    if (skipNext.current) {
      skipNext.current = false;
      return;
    }
    const q = value.trim();
    if (q.length < 2) {
      setItems([]);
      setOpen(false);
      return;
    }
    const ctrl = new AbortController();
    const t = window.setTimeout(async () => {
      setLoading(true);
      try {
        const res = await api.searchDrugs(q, ctrl.signal);
        setItems(res);
        // Only open if the user is still in the field; avoids a stale list over results.
        setOpen(res.length > 0 && document.activeElement?.id === "drug-input");
        setActive(-1);
      } catch {
        /* suggestions are optional; typing still works */
      } finally {
        setLoading(false);
      }
    }, 180);
    return () => {
      ctrl.abort();
      window.clearTimeout(t);
    };
  }, [value]);

  const choose = (s: DrugSuggestion) => {
    skipNext.current = true;
    onChange(s.ingredient);
    setOpen(false);
  };

  return (
    <div className="combo">
      <input
        ref={ref}
        id="drug-input"
        type="text"
        role="combobox"
        aria-expanded={open}
        aria-controls={listId}
        aria-autocomplete="list"
        aria-activedescendant={active >= 0 ? `${listId}-${active}` : undefined}
        aria-invalid={invalid || undefined}
        aria-describedby={describedBy}
        autoComplete="off"
        spellCheck={false}
        placeholder="Generic or brand name"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onBlur={() => window.setTimeout(() => setOpen(false), 120)}
        onKeyDown={(e) => {
          if (!open || !items.length) return;
          if (e.key === "ArrowDown") {
            e.preventDefault();
            setActive((a) => (a + 1) % items.length);
          } else if (e.key === "ArrowUp") {
            e.preventDefault();
            setActive((a) => (a <= 0 ? items.length - 1 : a - 1));
          } else if (e.key === "Enter" && active >= 0) {
            e.preventDefault();
            choose(items[active]);
          } else if (e.key === "Escape") {
            setOpen(false);
          }
        }}
      />
      {loading && <span className="combo__spinner" aria-hidden="true" />}
      {open && (
        <ul id={listId} role="listbox" className="combo__list">
          {items.map((s, i) => (
            <li
              key={s.ingredient + s.display}
              id={`${listId}-${i}`}
              role="option"
              aria-selected={i === active}
              className={`combo__option ${i === active ? "is-active" : ""}`}
              onMouseDown={(e) => {
                e.preventDefault();
                choose(s);
              }}
            >
              <span>{s.display}</span>
              {s.in_current_shortage && <span className="tag tag--shortage">In shortage</span>}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
});
