/**
 * Агент показывает человеку в окне (`window_show` → событие `window.show`).
 *
 * Показ — это ВИД: выделение, скрытое, срез, поза шарнира, камера. Проект не
 * меняется. Всё идёт через те же сеттеры, что и щелчки человека, поэтому
 * правила окна (срез гаснет при сдвинутом звене, разнесение и ход сменяют
 * друг друга) действуют и здесь, а окно потом сообщит новый вид агенту
 * обычным `window_report`.
 *
 * Прежний вид запоминается, и подпись показа несёт кнопку «Вернуть как было»:
 * человек не обязан восстанавливать руками то, что сдвинул агент.
 *
 * Идея — из cadcraft (агент правит вид окна по каналу управления); у нас
 * агенту по-прежнему нельзя ПОДДЕЛАТЬ «что видит человек» — можно лишь
 * показать, и человек это видит и отменяет.
 */
import { useDoc } from "../store/doc";
import { useUi } from "../store/ui";
import { viewport, type CameraView } from "./control";
import type { PlaneName } from "./section";

export interface ShowEvent {
  parts: string[];
  isolate: boolean;
  section: { plane: PlaneName; at: number } | null;
  joint: { part: string; u: number } | null;
  note: string;
  actor?: string;
}

/** Что было до показа — ровно то, что показ трогает. */
export interface ViewBefore {
  selected: string | null;
  hidden: string[];
  plane: PlaneName | null;
  at: number;
  spread: number;
  joints: Record<string, number>;
  camera: CameraView | null;
}

/** Сколько висит подпись показа: её читают, а не просто замечают. */
export const SHOW_NOTICE_MS = 30_000;

/** Показ старше — не показ, а повтор: сервер досылает пропущенные события
 *  после переподключения (перезагрузка страницы), и старый показ применился
 *  бы снова поверх того, что человек уже сделал. */
export const SHOW_FRESH_S = 15;

/** Разобрать событие сервера; чужое, кривое или старое — null (показ не
 *  выдумывается и не повторяется). `now` — секунды, как `at` сервера. */
export function parseShow(event: Record<string, unknown>, now = Date.now() / 1000): ShowEvent | null {
  const at0 = Number(event.at);
  if (!Number.isFinite(at0) || Math.abs(now - at0) > SHOW_FRESH_S) return null;
  const parts = Array.isArray(event.parts) ? event.parts.map(String) : [];
  const raw = event.section as { plane?: unknown; at?: unknown } | null | undefined;
  const plane = raw && ["xy", "xz", "yz"].includes(String(raw.plane)) ? (String(raw.plane) as PlaneName) : null;
  const at = raw ? Number(raw.at) : NaN;
  const j = event.joint as { part?: unknown; u?: unknown } | null | undefined;
  const joint = j && typeof j.part === "string" && Number.isFinite(Number(j.u))
    ? { part: j.part, u: Number(j.u) } : null;
  const note = typeof event.note === "string" ? event.note : "";
  const section = plane && Number.isFinite(at) ? { plane, at } : null;
  if (!parts.length && !section && !joint && !note) return null;
  return { parts, isolate: Boolean(event.isolate), section, joint, note,
           actor: typeof event.actor === "string" ? event.actor : undefined };
}

/** Какие детали прятать: при `isolate` — все, кроме показанных; иначе
 *  показанные обязаны быть видны (спрятанную не покажешь). */
export function hiddenAfter(show: ShowEvent, all: string[], hidden: string[]): string[] {
  if (show.isolate && show.parts.length) return all.filter((n) => !show.parts.includes(n));
  return hidden.filter((n) => !show.parts.includes(n));
}

/** Ось, поперёк которой режет плоскость, — подпись полосы среза окна. */
const AXIS_OF: Record<PlaneName, string> = { xy: "Z", xz: "Y", yz: "X" };

/** Подпись показа словами. */
export function describeShow(show: ShowEvent): string {
  const who = show.actor === "agent" ? "Агент показывает" : "Показано";
  const what = [
    show.parts.length ? show.parts.join(", ") : "",
    // Плоскость — так, как её зовёт полоса среза окна: xz — «срез по Y».
    show.section ? `срез по ${AXIS_OF[show.section.plane]} = ${show.section.at} мм` : "",
    show.joint ? `${show.joint.part} в положении ${show.joint.u}` : "",
  ].filter(Boolean).join("; ");
  return [who + (what ? `: ${what}` : ""), show.note].filter(Boolean).join(" — ");
}

/** Применить показ к окну. Возвращает прежний вид (для «Вернуть как было»). */
export function applyShow(show: ShowEvent): ViewBefore {
  const ui = useUi.getState();
  const doc = useDoc.getState();
  const before: ViewBefore = {
    selected: doc.selected,
    hidden: [...ui.hidden],
    plane: ui.plane,
    at: ui.at,
    spread: ui.spread,
    joints: { ...ui.joints },
    camera: viewport.camera(),
  };

  ui.setHidden(hiddenAfter(show, Object.keys(doc.parts), ui.hidden));
  if (show.section) {
    // Срез — по собранному изделию: разнесение и ход убираются, иначе окно
    // само погасило бы срез (`moved`).
    ui.setSpread(0);
    ui.resetJoints();
    ui.setPlane(show.section.plane, show.section.at);
  }
  if (show.joint) ui.setJoint(show.joint.part, show.joint.u);
  if (show.parts.length) {
    doc.select(show.parts[0]);
    viewport.fit(show.parts);
  }
  ui.notify({
    text: describeShow(show),
    tone: "info",
    ms: SHOW_NOTICE_MS,
    action: { label: "Вернуть как было", run: () => restoreView(before) },
  });
  return before;
}

/** «Вернуть как было»: вид до показа. */
export function restoreView(before: ViewBefore): void {
  const ui = useUi.getState();
  ui.setHidden(before.hidden);
  ui.resetJoints();
  ui.setSpread(before.spread);
  for (const [name, u] of Object.entries(before.joints)) ui.setJoint(name, u);
  ui.setPlane(before.plane, before.at);
  useDoc.getState().select(before.selected);
  if (before.camera) viewport.restore(before.camera);
  ui.notify(null);
}
