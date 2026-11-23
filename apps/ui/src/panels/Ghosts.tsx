/**
 * Образцы — импортированные модели на столе — и превращение модели в деталь.
 *
 * Образец виден сразу после импорта: видно, что пришло и где оно стоит. Это
 * только вид — проверки и выгрузка его не касаются. Деталью модель становится
 * по кнопке: появляется `parts/<имя>.py`, который берёт её и ставит в центр
 * стола, и дальше с ней работает всё, что работает с деталью.
 */
import { Eye, EyeOff, FilePlus2, Ghost } from "lucide-react";

import type { ImportedView } from "../store/doc";
import { useDoc } from "../store/doc";
import { ghostVisible, useUi } from "../store/ui";

/** Глаз образца и «В деталь» — одинаковые в «Сцене» и в «Моделях». */
export function GhostActions({ view }: { view: ImportedView }) {
  const shownMap = useUi((s) => s.ghostShown);
  const busy = useDoc((s) => s.working);
  const used = view.used_by.length > 0;
  const shown = ghostVisible(shownMap, view.name, used);
  const { setGhostShown } = useUi.getState();

  const make = async () => {
    const part = await useDoc.getState().makePart(view.name);
    // Деталь стоит в центре стола, а образец — там, где его положил чужой
    // файл: показанные оба, они выглядели бы двумя разными моделями.
    if (part) setGhostShown(view.name, false);
  };

  return (
    <span className="ghost-actions">
      {!used && (
        <button
          className="chip-button"
          disabled={busy}
          title="Создать parts/…py: модель станет деталью в центре стола — с замером, проверками и выгрузкой"
          onClick={() => void make()}
        >
          <FilePlus2 size={13} />
          В деталь
        </button>
      )}
      <button
        className="part-eye"
        title={shown ? "Спрятать образец" : "Показать образец на столе"}
        aria-label={shown ? `Спрятать образец ${view.name}` : `Показать образец ${view.name}`}
        aria-pressed={shown}
        onClick={() => setGhostShown(view.name, !shown)}
      >
        {shown ? <Eye size={15} /> : <EyeOff size={15} />}
      </button>
    </span>
  );
}

/** Раздел «Образцы» в панели сцены. */
export function GhostList() {
  const ghosts = useDoc((s) => s.ghosts);
  if (ghosts.length === 0) return null;
  return (
    <div className="section">
      <div className="section-head static">
        <span>Образцы</span>
        <span className="count">{ghosts.length}</span>
      </div>
      <div className="part-list">
        {ghosts.map((view) => (
          <div key={view.name} className="part ghost">
            <div className="part-main static">
              <span className="part-icon ghost-icon">
                <Ghost size={16} />
              </span>
              <span className="part-text">
                <span className="name">{view.name}</span>
                <span className="sub">
                  {view.tris.toLocaleString("ru-RU")} треуг. ·{" "}
                  {view.used_by.length ? `деталь: ${view.used_by.join(", ")}` : "только вид"}
                </span>
              </span>
            </div>
            <GhostActions view={view} />
          </div>
        ))}
      </div>
    </div>
  );
}
