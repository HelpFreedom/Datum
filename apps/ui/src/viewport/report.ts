/**
 * Окно сообщает серверу, что видит человек, — чтобы это видел и агент.
 *
 * Агент в терминале видит проект, но не окно: выделение, скрытое, срез,
 * камеру и замер штангенциркулем знает только окно. Оно шлёт их операцией
 * `window_report` (её нет у агента, и сервер не примет её от него), а агент
 * читает `window_state` и смотрит на стол `scene_view` — в том числе отсюда,
 * откуда смотрит человек.
 *
 * Шлётся после затишья и только изменившееся: камера меняется каждый кадр, пока
 * её крутят, а серверу нужно, где она остановилась. Числа округляются — иначе
 * дрожь демпфирования орбиты слала бы «новое» состояние, которого человек не
 * видит. Отказ сервера окну не мешает: это сведения для агента, а не правка.
 *
 * Модуль без DOM и без сцены — состояние собирает вызывающий; его берёт тест.
 */
import type { PickedElement, Readout } from "./measure";

export const QUIET_MS = 400;

export interface WindowState {
  selected: string | null;
  /** Детали, загруженные окном: по ним сервер проверяет имена показа агента. */
  parts: string[];
  hidden: string[];
  ghosts: string[];
  tool: string;
  section: { plane: string; at: number } | null;
  spread: number;
  /** Показанный ход шарниров: звено → положение; нет записи — на месте. */
  joints?: Record<string, number>;
  camera: { position: number[]; target: number[]; up: number[]; fov: number } | null;
  readout: { tool: "distance" | "probe"; text: string; detail: string } | null;
  picked: PickedElement[];
}

const round = (v: number, digits: number) => {
  const k = 10 ** digits;
  const r = Math.round(v * k) / k;
  return Object.is(r, -0) ? 0 : r;
};

/** Округлить числа состояния: 0.1 мм у точек, 0.001 у направлений. */
export function normalise(state: WindowState): WindowState {
  const vec = (v: number[], digits: number) => v.map((x) => round(x, digits));
  const picked = state.picked.map((p) => {
    const out: Record<string, unknown> = { ...p };
    for (const key of ["point", "a", "b", "centre", "origin"]) {
      if (Array.isArray(out[key])) out[key] = vec(out[key] as number[], 4);
    }
    if (Array.isArray(out.normal)) out.normal = vec(out.normal as number[], 4);
    if (typeof out.radius === "number") out.radius = round(out.radius, 4);
    return out as PickedElement;
  });
  return {
    ...state,
    section: state.section && { ...state.section, at: round(state.section.at, 3) },
    spread: round(state.spread, 3),
    ...(state.joints && {
      joints: Object.fromEntries(Object.entries(state.joints).map(([k, v]) => [k, round(v, 2)])),
    }),
    camera: state.camera && {
      position: vec(state.camera.position, 1),
      target: vec(state.camera.target, 1),
      up: vec(state.camera.up, 3),
      fov: round(state.camera.fov, 2),
    },
    picked,
  };
}

/** Карточка замера — в то, что знает сервер (штангенциркуль или толщина). */
export function readoutOf(readout: Readout | null): Pick<WindowState, "readout" | "picked"> {
  if (!readout || (readout.tool !== "distance" && readout.tool !== "probe")) return { readout: null, picked: [] };
  return {
    readout: { tool: readout.tool, text: readout.text, detail: readout.detail },
    picked: readout.picked ?? [],
  };
}

export class WindowReporter {
  private timer: ReturnType<typeof setTimeout> | null = null;
  private last = "";
  private latest: WindowState | null = null;

  constructor(private readonly send: (state: WindowState) => Promise<unknown>, private readonly quietMs = QUIET_MS) {}

  /** Состояние изменилось — отправить, когда уляжется. */
  update(state: WindowState): void {
    this.latest = normalise(state);
    if (this.timer) clearTimeout(this.timer);
    this.timer = setTimeout(() => this.flush(), this.quietMs);
  }

  private flush(): void {
    this.timer = null;
    const state = this.latest;
    if (!state) return;
    const text = JSON.stringify(state);
    if (text === this.last) return;
    this.last = text;
    this.send(state).catch((error: unknown) => {
      // Не дошло — пошлём снова при следующей перемене: «прошлое» забываем.
      this.last = "";
      console.warn("окно не сообщило серверу, что показывает:", error);
    });
  }

  dispose(): void {
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
  }
}
