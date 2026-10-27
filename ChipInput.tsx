import { useId, useState } from "react";
import { IconClose } from "./Icons";

interface Props {
  label: string;
  hint: string;
  values: string[];
  onChange: (v: string[]) => void;
  placeholder: string;
}

/** Free text list entry. Enter or comma adds, Backspace on empty removes the last entry. */
export function ChipInput({ label, hint, values, onChange, placeholder }: Props) {
  const id = useId();
  const [draft, setDraft] = useState("");
  const add = () => {
    const v = draft.trim().replace(/,$/, "");
    if (v && !values.some((x) => x.toLowerCase() === v.toLowerCase())) onChange([...values, v]);
    setDraft("");
  };
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <p className="field__hint" id={`${id}-hint`}>{hint}</p>
      <div className="chips" onClick={() => document.getElementById(id)?.focus()}>
        {values.map((v) => (
          <span key={v} className="chip">
            {v}
            <button type="button" className="chip__remove" aria-label={`Remove ${v}`} onClick={() => onChange(values.filter((x) => x !== v))}>
              <IconClose size={12} />
            </button>
          </span>
        ))}
        <input
          id={id}
          aria-describedby={`${id}-hint`}
          value={draft}
          placeholder={values.length ? "" : placeholder}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={add}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === ",") {
              e.preventDefault();
              add();
            } else if (e.key === "Backspace" && !draft && values.length) {
              onChange(values.slice(0, -1));
            }
          }}
        />
      </div>
    </div>
  );
}
