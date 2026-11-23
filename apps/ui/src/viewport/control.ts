/**
 * Команды вьюпорту от тех, кто сцены не держит: меню, куб видов, клавиши.
 *
 * Сцена — императивный объект, и в дереве React её нет (см. Viewport.tsx).
 * Состояние инструментов лежит в `store/ui`, а разовые действия — «встать в
 * вид сверху», «вписать» — идут сюда: состоянием они не являются, и держать
 * их в сторе значило бы изобретать счётчики нажатий.
 *
 * Пока сцены нет (ждёт токен), команды просто некому выполнить — и это
 * правильно: камеры, которую двигать, тоже ещё нет.
 */
import type { ViewName } from "./scene";

/** Где камера: откуда смотрит, куда и где верх. */
export interface CameraView {
  position: number[];
  target: number[];
  up: number[];
  fov: number;
}

export interface ViewportCommands {
  setView: (view: ViewName) => void;
  fit: (only?: string | string[] | null) => void;
  /** Вернуть камеру туда, где она была при открытии проекта. */
  home: () => void;
  /** Где камера сейчас — чтобы вернуть её после показа агента. */
  camera: () => CameraView | null;
  restore: (view: CameraView) => void;
  /** Проигрыватель симуляции: показать звенья, поставить по кадру, закрыть. */
  showSim: (links: { name: string; url: string; color: string }[]) => Promise<boolean>;
  poseSim: (poses: number[][]) => void;
  /** Карта загрузки звеньев (доля предела) или null — родные цвета. */
  loadSim: (load: number[] | null) => void;
  hideSim: () => void;
  /** Поле МКЭ на звене (номер в записи); null — снять. */
  stressSim: (link: number | null, url: string | null) => Promise<boolean>;
}

let target: ViewportCommands | null = null;

export function bindViewport(commands: ViewportCommands | null): void {
  target = commands;
}

export const viewport: ViewportCommands = {
  setView: (view) => target?.setView(view),
  fit: (only) => target?.fit(only),
  home: () => target?.home(),
  camera: () => target?.camera() ?? null,
  restore: (view) => target?.restore(view),
  showSim: (links) => target?.showSim(links) ?? Promise.resolve(false),
  poseSim: (poses) => target?.poseSim(poses),
  loadSim: (load) => target?.loadSim(load),
  hideSim: () => target?.hideSim(),
  stressSim: (link, url) => target?.stressSim(link, url) ?? Promise.resolve(false),
};

/** Виды, их подписи и клавиши — одна таблица для меню, списка и клавиатуры. */
export const VIEWS: { key: ViewName; label: string; hint: string }[] = [
  { key: "iso", label: "Изометрия", hint: "1" },
  { key: "top", label: "Сверху", hint: "2" },
  { key: "front", label: "Спереди", hint: "3" },
  { key: "right", label: "Справа", hint: "4" },
  { key: "left", label: "Слева", hint: "5" },
  { key: "back", label: "Сзади", hint: "6" },
  { key: "bottom", label: "Снизу", hint: "7" },
];
