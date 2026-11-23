/** Каркас: свёрнутая панель уходит из сетки целиком, а не оставляет полосу. */
import { describe, expect, it } from "vitest";

import { DOCK_MIN, frameLayout, PANEL_MIN, VIEWPORT_MIN } from "../chrome/layout";

const rowsOf = (areas: string) => areas.match(/"[^"]*"/g)!.map((r) => r.slice(1, -1).split(" "));
/** Дорожки сетки: пробел внутри скобок — даже вложенных, `minmax(160px, var(--right))` —
 *  дорожку не делит. */
function tracks(template: string): string[] {
  const out: string[] = [];
  let depth = 0;
  let current = "";
  for (const ch of template) {
    if (ch === "(") depth += 1;
    if (ch === ")") depth -= 1;
    if (ch === " " && depth === 0) {
      out.push(current);
      current = "";
    } else {
      current += ch;
    }
  }
  return [...out, current];
}

describe("раскладка каркаса", () => {
  it("всё открыто: четыре колонки, док под панелями, рейка во всю высоту", () => {
    const l = frameLayout({ leftOpen: true, rightOpen: true, dockOpen: true, dockHeight: 250 });
    expect(rowsOf(l.gridTemplateAreas)).toEqual([
      ["top", "top", "top", "top"],
      ["rail", "left", "center", "right"],
      ["rail", "dock", "dock", "dock"],
      ["status", "status", "status", "status"],
    ]);
    expect(tracks(l.gridTemplateColumns).length).toBe(4);
    expect(l.gridTemplateRows).toContain(`minmax(${DOCK_MIN}px, 250px)`);
  });

  it("размер панели — желаемый: без верхнего предела, а вьюпорт держит свой минимум", () => {
    const l = frameLayout({ leftOpen: true, rightOpen: true, dockOpen: true, dockHeight: 2000,
                            leftWidth: 1500, rightWidth: null });
    expect(tracks(l.gridTemplateColumns)).toEqual([
      "var(--rail)",
      `minmax(${PANEL_MIN}px, 1500px)`,
      `minmax(${VIEWPORT_MIN}px, 1fr)`,
      `minmax(${PANEL_MIN}px, var(--right))`,
    ]);
    expect(tracks(l.gridTemplateRows)).toEqual([
      "var(--top)", `minmax(${VIEWPORT_MIN}px, 1fr)`, `minmax(${DOCK_MIN}px, 2000px)`, "var(--status)",
    ]);
  });

  it("свёрнутые панели не оставляют колонок и строк", () => {
    const l = frameLayout({ leftOpen: false, rightOpen: false, dockOpen: false, dockHeight: 250 });
    expect(rowsOf(l.gridTemplateAreas)).toEqual([
      ["top", "top"],
      ["rail", "center"],
      ["status", "status"],
    ]);
    expect(l.gridTemplateColumns).toBe(`var(--rail) minmax(${VIEWPORT_MIN}px, 1fr)`);
    expect(tracks(l.gridTemplateRows).length).toBe(3);
  });

  it("каждая строка областей одной ширины — иначе сетка недействительна", () => {
    for (const leftOpen of [true, false])
      for (const rightOpen of [true, false])
        for (const dockOpen of [true, false]) {
          const l = frameLayout({ leftOpen, rightOpen, dockOpen, dockHeight: 200 });
          const widths = new Set(rowsOf(l.gridTemplateAreas).map((r) => r.length));
          expect(widths.size).toBe(1);
          expect([...widths][0]).toBe(tracks(l.gridTemplateColumns).length);
        }
  });
});
