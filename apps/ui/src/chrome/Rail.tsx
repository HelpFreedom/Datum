/**
 * Рейка слева: что показано в левой панели.
 *
 * Подписи — в разметке, но спрятаны от глаз (`sr-only`): иконки читаются
 * быстрее слов, а подпись нужна экранному диктору и сценариям проверки окна,
 * которые ищут вкладку по названию.
 */
import { Box, History, Keyboard, PackageOpen } from "lucide-react";

import { useDoc } from "../store/doc";
import { type LeftTab, useUi } from "../store/ui";

const ITEMS: { key: LeftTab; label: string; Icon: typeof Box }[] = [
  { key: "scene", label: "Сцена", Icon: Box },
  { key: "models", label: "Модели", Icon: PackageOpen },
  { key: "versions", label: "Версии", Icon: History },
];

export function Rail() {
  const left = useUi((s) => s.left);
  const open = useUi((s) => s.leftOpen);
  const { showLeft, setKeysOpen } = useUi.getState();
  const failed = useDoc((s) => Object.values(s.build).some((b) => !b.ok));

  return (
    <nav className="rail" aria-label="Левая панель">
      {ITEMS.map(({ key, label, Icon }) => (
        <button
          key={key}
          className="rail-button"
          aria-pressed={open && left === key}
          title={label}
          onClick={() => showLeft(key)}
        >
          <Icon size={20} strokeWidth={1.7} />
          {key === "scene" && failed && <span className="rail-badge bad" />}
          <span className="sr-only">{label}</span>
        </button>
      ))}
      <div className="rail-spacer" />
      <button className="rail-button" title="Клавиши (?)" onClick={() => setKeysOpen(true)}>
        <Keyboard size={20} strokeWidth={1.7} />
        <span className="sr-only">Клавиши</span>
      </button>
    </nav>
  );
}
