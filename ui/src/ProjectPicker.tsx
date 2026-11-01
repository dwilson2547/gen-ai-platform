import { useEffect, useRef, useState } from "react";
import type { Project } from "./api.ts";

// Searchable project combobox: type to filter, arrows + Enter to pick, Escape to close.
export function ProjectPicker({ projects, value, onChange, disabled }: {
  projects: Project[]; value: string | null; onChange: (id: string) => void; disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [hi, setHi] = useState(0);
  const box = useRef<HTMLDivElement>(null);
  const current = projects.find(p => p.id === value) ?? projects.find(p => p.is_default) ?? null;
  const shown = projects.filter(p => p.name.toLowerCase().includes(q.trim().toLowerCase()));

  useEffect(() => {
    const close = (e: MouseEvent) => { if (box.current && !box.current.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);
  useEffect(() => setHi(0), [q, open]);

  const pick = (p: Project) => { onChange(p.id); setOpen(false); setQ(""); };
  return (
    <div className="combo" ref={box}>
      <button type="button" className="combo-btn" disabled={disabled} onClick={() => setOpen(o => !o)}
        aria-haspopup="listbox" aria-expanded={open} title="Project the images go into">
        <span className="combo-label">{current ? current.name : "Project"}</span>
        {current && <span className={`vis ${current.visibility}`}>{current.visibility}</span>}
        <span aria-hidden>▾</span>
      </button>
      {open && (
        <div className="combo-pop">
          <input autoFocus placeholder="Find a project…" value={q} onChange={e => setQ(e.target.value)}
            onKeyDown={e => {
              if (e.key === "ArrowDown") { e.preventDefault(); setHi(h => Math.min(h + 1, shown.length - 1)); }
              else if (e.key === "ArrowUp") { e.preventDefault(); setHi(h => Math.max(h - 1, 0)); }
              else if (e.key === "Enter") { e.preventDefault(); if (shown[hi]) pick(shown[hi]); }
              else if (e.key === "Escape") setOpen(false);
            }} />
          <ul role="listbox">
            {shown.length === 0 && <li className="muted small none">No project matches.</li>}
            {shown.map((p, i) => (
              <li key={p.id} role="option" aria-selected={p.id === current?.id}
                className={(i === hi ? "hi " : "") + (p.id === current?.id ? "sel" : "")}
                onMouseEnter={() => setHi(i)} onMouseDown={e => { e.preventDefault(); pick(p); }}>
                <span>{p.name}</span><span className={`vis ${p.visibility}`}>{p.visibility}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
