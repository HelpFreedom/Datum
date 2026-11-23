/** Справка «Клавиши» — та же таблица, по которой клавиши и работают. */
import { X } from "lucide-react";

import { useUi } from "../store/ui";
import { KEYS } from "./hotkeys";

export function KeysDialog() {
  const open = useUi((s) => s.keysOpen);
  const { setKeysOpen } = useUi.getState();
  if (!open) return null;
  return (
    <div className="modal-backdrop" onClick={() => setKeysOpen(false)}>
      <div
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-label="Клавиши"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="modal-head">
          <h3>Клавиши</h3>
          <button className="icon-button small" aria-label="Закрыть" onClick={() => setKeysOpen(false)}>
            <X size={15} />
          </button>
        </div>
        <div className="keys-grid">
          {KEYS.map((row) => (
            <div key={row.keys} className="keys-row">
              <span className="keys-what">{row.what}</span>
              <span className="keys-combo">
                {row.keys.split("+").map((k) => (
                  <kbd key={k}>{k}</kbd>
                ))}
              </span>
            </div>
          ))}
        </div>
        <div className="sub">Буквы работают в любой раскладке: окно слушает клавишу, а не символ.</div>
      </div>
    </div>
  );
}
