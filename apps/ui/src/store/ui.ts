/**
 * Состояние окна, а не документа: что открыто, что выбрано из инструментов.
 *
 * Отдельно от `doc`, потому что у этих двух разные хозяева. Документ — это
 * то, что посчитал сервер, и окно ему только верит. Здесь — то, как человек
 * разложил себе экран; сервер о нём не знает и знать не должен.
 *
 * Инструменты вьюпорта (замер, срез, разнесение) живут здесь, а не внутри
 * вьюпорта: до них дотягиваются и меню, и полоса инструментов, и клавиши, и
 * все они обязаны видеть одно и то же — иначе кнопка выглядит нажатой, а
 * инструмента нет.
 */
import { create } from "zustand";

import { DOCK_DEFAULT, DOCK_MIN, PANEL_MIN } from "../chrome/layout";
import type { Tool } from "../viewport/measure";
import type { PlaneName } from "../viewport/section";

/** Что показано в левой панели; выбирается рейкой слева. */
export type LeftTab = "scene" | "models" | "versions";
/** Вкладки правой панели. */
export type RightTab = "props" | "checks" | "params";
/** Вкладки нижней панели. */
export type DockTab = "agent" | "log";

/** Что с терминалом агента — для строки состояния. */
export type AgentState = "idle" | "starting" | "running" | "exited" | "failed";

/** Короткое сообщение внизу вьюпорта — с действием («Вернуть»), если есть. */
export interface Notice {
  text: string;
  tone: "good" | "warn" | "bad" | "info";
  action?: { label: string; run: () => void };
  /** Сколько висит, мс; по умолчанию 8 с. Подпись показа агента — дольше. */
  ms?: number;
}

/** Что помнится между запусками (файл оболочки, `main/layout.ts`). */
export interface SavedLayout {
  left?: number;
  right?: number;
  dock?: number;
}

interface UiState {
  left: LeftTab;
  leftOpen: boolean;
  right: RightTab;
  rightOpen: boolean;
  dock: DockTab;
  dockOpen: boolean;
  dockHeight: number;
  /** Ширины боковых панелей; null — умолчание из CSS (оно ужимается в узком окне). */
  leftWidth: number | null;
  rightWidth: number | null;
  agent: AgentState;

  tool: Tool;
  plane: PlaneName | null;
  at: number;
  spread: number;
  /**
   * Показанный ход шарниров: звено → положение (° или мм). Нет записи — звено
   * в собранном положении. Вид, а не правка: в проект не пишется.
   */
  joints: Record<string, number>;
  /** Скрытые детали. Скрытая не ловит замер и не даёт крышку среза. */
  hidden: string[];
  /**
   * Видимость образцов (импортированных моделей) по воле человека. Нет
   * записи — действует умолчание: образец виден, пока модель не стала деталью
   * (иначе на столе была бы она дважды, и в разных местах).
   */
  ghostShown: Record<string, boolean>;
  grid: boolean;
  /**
   * Черновая сборка: окно просит `part_build(draft=True)`, скрипт видит
   * `draft()` и огрубляет сам (шаг поля). Проверки и выгрузка всегда идут по
   * точной сборке — черновик только для глаза.
   */
  draft: boolean;

  keysOpen: boolean;
  /** Открыт диалог «Файл → Проекты…». */
  projectsOpen: boolean;
  notice: Notice | null;
  /** Ремонт чужой модели при импорте: auto, light, remesh или none. */
  importRepair: string;

  showLeft: (tab: LeftTab) => void;
  toggleLeft: () => void;
  showRight: (tab: RightTab) => void;
  toggleRight: () => void;
  showDock: (tab: DockTab) => void;
  toggleDock: () => void;
  setDockHeight: (px: number) => void;
  setLeftWidth: (px: number | null) => void;
  setRightWidth: (px: number | null) => void;
  /** Поставить размеры, запомненные с прошлого запуска. */
  applyLayout: (saved: SavedLayout) => void;
  /** Что запомнить: только заданное человеком. */
  savedLayout: () => SavedLayout;
  setAgent: (state: AgentState) => void;
  /** Всё, кроме вьюпорта, — убрать; повторно — вернуть как было. */
  toggleFocus: () => void;

  setTool: (tool: Tool) => void;
  setPlane: (plane: PlaneName | null, at?: number) => void;
  setAt: (at: number) => void;
  setSpread: (spread: number) => void;
  /** Показать звено в положении u; 0 — вернуть на место. */
  setJoint: (name: string, u: number) => void;
  /** Все звенья — в собранное положение. */
  resetJoints: () => void;
  toggleHidden: (name: string) => void;
  /** Спрятать ровно эти (показ агента и возврат «как было»). */
  setHidden: (names: string[]) => void;
  setGhostShown: (name: string, shown: boolean) => void;
  toggleGrid: () => void;
  toggleDraft: () => void;
  setKeysOpen: (open: boolean) => void;
  setProjectsOpen: (open: boolean) => void;
  setImportRepair: (level: string) => void;
  notify: (notice: Notice | null) => void;
}

let noticeTimer = 0;

/** Что было открыто до «фокуса» — чтобы вернуть ровно это. */
let beforeFocus: { leftOpen: boolean; rightOpen: boolean; dockOpen: boolean } | null = null;

export const useUi = create<UiState>((set, get) => ({
  left: "scene",
  leftOpen: true,
  right: "checks",
  rightOpen: true,
  dock: "agent",
  dockOpen: true,
  dockHeight: DOCK_DEFAULT,
  leftWidth: null,
  rightWidth: null,
  agent: "idle",

  tool: "none",
  plane: null,
  at: 0,
  spread: 0,
  joints: {},
  hidden: [],
  ghostShown: {},
  grid: true,
  draft: false,

  keysOpen: false,
  projectsOpen: false,
  notice: null,
  importRepair: "auto",

  // Нажатие на уже открытую вкладку рейки сворачивает панель — как в
  // редакторах кода: панель убирается тем же движением, каким открылась.
  showLeft: (tab) =>
    set((s) => (s.left === tab && s.leftOpen ? { leftOpen: false } : { left: tab, leftOpen: true })),
  toggleLeft: () => set((s) => ({ leftOpen: !s.leftOpen })),
  showRight: (tab) => set({ right: tab, rightOpen: true }),
  toggleRight: () => set((s) => ({ rightOpen: !s.rightOpen })),
  showDock: (tab) => set({ dock: tab, dockOpen: true }),
  toggleDock: () => set((s) => ({ dockOpen: !s.dockOpen })),
  setDockHeight: (px) => set({ dockHeight: Math.round(Math.max(DOCK_MIN, px)) }),
  setLeftWidth: (px) => set({ leftWidth: px === null ? null : Math.round(Math.max(PANEL_MIN, px)) }),
  setRightWidth: (px) => set({ rightWidth: px === null ? null : Math.round(Math.max(PANEL_MIN, px)) }),
  applyLayout: (saved) => {
    const ok = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v) && v > 0;
    const s = get();
    if (ok(saved.left)) s.setLeftWidth(saved.left);
    if (ok(saved.right)) s.setRightWidth(saved.right);
    if (ok(saved.dock)) s.setDockHeight(saved.dock);
  },
  savedLayout: () => {
    const s = get();
    const out: SavedLayout = { dock: s.dockHeight };
    if (s.leftWidth !== null) out.left = s.leftWidth;
    if (s.rightWidth !== null) out.right = s.rightWidth;
    return out;
  },
  setAgent: (agent) => set({ agent }),
  toggleFocus: () => {
    const s = get();
    const focused = !s.leftOpen && !s.rightOpen && !s.dockOpen;
    if (focused) {
      set(beforeFocus ?? { leftOpen: true, rightOpen: true, dockOpen: true });
      beforeFocus = null;
    } else {
      beforeFocus = { leftOpen: s.leftOpen, rightOpen: s.rightOpen, dockOpen: s.dockOpen };
      set({ leftOpen: false, rightOpen: false, dockOpen: false });
    }
  },

  // Разнесение и ход шарнира гасят замер и срез: при сдвинутых деталях их
  // числа не были бы числами конструкции. Правило держится здесь, в одном
  // месте, — и для кнопок, и для меню, и для клавиш. Разнесение и ход друг
  // друга сменяют: разнести детали с открытой крышкой значило бы показать
  // положение, которого нет ни в сборке, ни в проверке.
  setTool: (tool) => set((s) => ({ tool: moved(s) ? "none" : tool })),
  setPlane: (plane, at) =>
    set((s) => (moved(s) ? { plane: null } : { plane, at: at ?? s.at })),
  setAt: (at) => set({ at }),
  setSpread: (spread) =>
    set(spread > 0 ? { spread, tool: "none", plane: null, joints: {} } : { spread }),
  setJoint: (name, u) =>
    set((s) => {
      const joints = { ...s.joints };
      if (u === 0 || !Number.isFinite(u)) {
        delete joints[name];
        return { joints };
      }
      joints[name] = u;
      return { joints, spread: 0, tool: "none", plane: null };
    }),
  resetJoints: () => set({ joints: {} }),
  toggleHidden: (name) =>
    set((s) => ({
      hidden: s.hidden.includes(name) ? s.hidden.filter((n) => n !== name) : [...s.hidden, name],
    })),
  setHidden: (names) => set({ hidden: [...new Set(names)] }),
  setGhostShown: (name, shown) =>
    set((s) => ({ ghostShown: { ...s.ghostShown, [name]: shown } })),
  toggleGrid: () => set((s) => ({ grid: !s.grid })),
  toggleDraft: () => set((s) => ({ draft: !s.draft })),
  setKeysOpen: (open) => set({ keysOpen: open }),
  setProjectsOpen: (open) => set({ projectsOpen: open }),
  setImportRepair: (level) => set({ importRepair: level }),
  // Уведомление живёт восемь секунд — ровно чтобы успеть нажать «Вернуть».
  notify: (notice) => {
    if (typeof window !== "undefined") window.clearTimeout(noticeTimer);
    set({ notice });
    if (notice && typeof window !== "undefined") {
      noticeTimer = window.setTimeout(() => set({ notice: null }), notice.ms ?? 8000);
    }
  },
}));

/** Детали на экране не в собранном положении: разнесены или звено сдвинуто. */
export function moved(s: { spread: number; joints: Record<string, number> }): boolean {
  return s.spread > 0 || Object.keys(s.joints).length > 0;
}

/** Виден ли образец: воля человека, а без неё — пока модель не стала деталью. */
export function ghostVisible(ghostShown: Record<string, boolean>, name: string, used: boolean): boolean {
  return ghostShown[name] ?? !used;
}
