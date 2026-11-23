/**
 * Раскладка каркаса — именованными областями сетки из ОТКРЫТЫХ панелей.
 *
 * Нулевые колонки оставляли бы за собой двойной зазор (`gap` не знает, что
 * колонка пуста); здесь свёрнутая панель просто исчезает из раскладки, а
 * рейка слева тянется на всю высоту между верхней строкой и строкой состояния.
 */
/**
 * Размеры панелей. Тянутся сколько угодно (решение владельца) — верхнего
 * предела у панели нет, его держит сама сетка: дорожка вьюпорта не уже и не
 * ниже `VIEWPORT_MIN`, а заданный размер панели для сетки — желаемый, не
 * обязательный (`minmax(предел, размер)`): в маленьком окне панели ужимаются
 * сами, вьюпорт не пропадает. Нижний предел — не ограничение, а ручка: панель
 * тоньше уже не за что ухватить, а у нижней не стало бы видно вкладок.
 */
export const PANEL_MIN = 160;
export const DOCK_MIN = 80;
export const VIEWPORT_MIN = 160;
/** Высота нижней панели по умолчанию; ширины боковых — в CSS (`--left`, `--right`). */
export const DOCK_DEFAULT = 250;

export interface Open {
  leftOpen: boolean;
  rightOpen: boolean;
  dockOpen: boolean;
  dockHeight: number;
  /** Заданные человеком ширины; null — умолчание CSS (ужимается в узком окне). */
  leftWidth?: number | null;
  rightWidth?: number | null;
}

export interface Layout {
  gridTemplateAreas: string;
  gridTemplateColumns: string;
  gridTemplateRows: string;
}

const side = (width: number | null | undefined, fallback: string) =>
  `minmax(${PANEL_MIN}px, ${width ? `${width}px` : fallback})`;

export function frameLayout({ leftOpen, rightOpen, dockOpen, dockHeight,
                              leftWidth = null, rightWidth = null }: Open): Layout {
  const cols: [string, string][] = [["rail", "var(--rail)"]];
  if (leftOpen) cols.push(["left", side(leftWidth, "var(--left)")]);
  cols.push(["center", `minmax(${VIEWPORT_MIN}px, 1fr)`]);
  if (rightOpen) cols.push(["right", side(rightWidth, "var(--right)")]);

  const across = (name: string) => `"${cols.map(() => name).join(" ")}"`;
  const areas = [
    across("top"),
    `"${cols.map(([name]) => name).join(" ")}"`,
    ...(dockOpen ? [`"rail ${cols.slice(1).map(() => "dock").join(" ")}"`] : []),
    across("status"),
  ];
  // Высота дока — желаемая: в низком окне его ужимает сетка, а не доля окна
  // (прежняя «не выше 32 % окна» и была тем пределом, которого просили не ставить).
  const rows = [
    "var(--top)",
    `minmax(${VIEWPORT_MIN}px, 1fr)`,
    ...(dockOpen ? [`minmax(${DOCK_MIN}px, ${dockHeight}px)`] : []),
    "var(--status)",
  ];
  return {
    gridTemplateAreas: areas.join(" "),
    gridTemplateColumns: cols.map(([, width]) => width).join(" "),
    gridTemplateRows: rows.join(" "),
  };
}
