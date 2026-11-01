import { useEffect, type ReactNode } from "react";

export function Modal({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  useEffect(() => {
    const k = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", k);
    return () => document.removeEventListener("keydown", k);
  }, [onClose]);
  return (
    <div className="modal-bg" onMouseDown={e => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="modal" role="dialog" aria-modal="true" aria-label={title}>
        <h3>{title}</h3>
        {children}
      </div>
    </div>
  );
}

export function Confirm({ title, body, action, onConfirm, onClose, busy, error }: {
  title: string; body: ReactNode; action: string; onConfirm: () => void; onClose: () => void;
  busy?: boolean; error?: string | null;
}) {
  return (
    <Modal title={title} onClose={onClose}>
      <p>{body}</p>
      {error && <p className="error">{error}</p>}
      <div className="actions">
        <button className="ghost" onClick={onClose}>Cancel</button>
        <button className="danger" onClick={onConfirm} disabled={busy} autoFocus>{busy ? "…" : action}</button>
      </div>
    </Modal>
  );
}
