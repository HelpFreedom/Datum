/**
 * Ручка размера панели — за кромку, сколько угодно.
 *
 * Тянется ОТ показанного размера, а не от запомненного: в узком окне сетка
 * ужимает панель (`chrome/layout.ts`), и с запомненного числа ручка сперва
 * «стояла бы» на месте, пока мышь проходит невидимый запас. По той же причине
 * панель растёт только за счёт вьюпорта и только до его минимума — дальше
 * ручка за мышью не идёт, а не уходит туда, откуда её не вернуть.
 *
 * Запоминается размер на отпускании, а не на каждом движении: файл раскладки
 * пишет оболочка, и сотня записей за одно движение ей ни к чему. Двойной
 * щелчок — размер по умолчанию.
 */
import { useRef } from "react";

import { useUi } from "../store/ui";
import { saveLayout } from "../transport/endpoint";
import { VIEWPORT_MIN } from "./layout";

const persistLayout = () => saveLayout(useUi.getState().savedLayout());

/** Где ручка у панели: сверху (нижняя панель), справа (левая), слева (правая). */
export type GripEdge = "top" | "right" | "left";

interface Props {
  edge: GripEdge;
  /** Новый размер панели в пикселях; null — по умолчанию. */
  onSize: (px: number | null) => void;
}

export function Grip({ edge, onSize }: Props) {
  const drag = useRef<{ from: number; size: number; room: number } | null>(null);
  const vertical = edge === "top";

  return (
    <div
      className={`grip grip-${edge}`}
      role="separator"
      aria-orientation={vertical ? "horizontal" : "vertical"}
      title="Потяните, чтобы изменить размер; двойной щелчок — по умолчанию"
      onPointerDown={(e) => {
        const panel = (e.currentTarget as HTMLElement).parentElement;
        const centre = document.querySelector(".center");
        if (!panel) return;
        const box = panel.getBoundingClientRect();
        const view = centre?.getBoundingClientRect();
        drag.current = {
          from: vertical ? e.clientY : e.clientX,
          size: vertical ? box.height : box.width,
          room: Math.max(0, (vertical ? view?.height : view?.width) ?? 0) - VIEWPORT_MIN,
        };
        (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
        e.preventDefault();
      }}
      onPointerMove={(e) => {
        const d = drag.current;
        if (!d) return;
        const at = vertical ? e.clientY : e.clientX;
        // Ручка сверху и слева растит панель движением ПРОТИВ оси.
        const delta = edge === "right" ? at - d.from : d.from - at;
        onSize(d.size + Math.min(delta, Math.max(0, d.room)));
      }}
      onPointerUp={() => {
        if (!drag.current) return;
        drag.current = null;
        persistLayout();
      }}
      onDoubleClick={() => {
        onSize(null);
        persistLayout();
      }}
    />
  );
}
