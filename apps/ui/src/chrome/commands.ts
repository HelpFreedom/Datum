/**
 * Действия окна, общие для меню, клавиш и кнопок.
 *
 * Каждое — тонкая обёртка над операцией стора: своего смысла у меню нет, оно
 * лишь ещё одна дорога к той же кнопке. Поэтому всё, что может сделать меню,
 * описано здесь один раз.
 */
import { useDoc } from "../store/doc";
import { type LeftTab, moved, useUi } from "../store/ui";
import { openArchive, openProject, pickFile } from "../transport/endpoint";
import type { MenuItem } from "./Menu";
import { viewport, VIEWS } from "../viewport/control";
import { pickPlane, TOOLS } from "../viewport/Viewport";

/**
 * Открыть вкладку левой панели. В отличие от нажатия на рейку, уже открытую
 * НЕ сворачивает: из меню и команд просят показать, а не переключить.
 */
function openLeft(tab: LeftTab): void {
  const ui = useUi.getState();
  if (!(ui.leftOpen && ui.left === tab)) ui.showLeft(tab);
}

/** Прогнать проверки и показать их — то, ради чего Datum затевался. */
export function runChecks(): void {
  useUi.getState().showRight("checks");
  void useDoc.getState().runChecks();
}

/**
 * Чужая модель — на стол ОДНИМ шагом: выбрали файл, и каждый его объект уже
 * стоит деталью (`import_to_bed`). Прежде было три шага: диагноз, импорт,
 * «В деталь», — а из 3MF приходила одна деталь из многих.
 */
export async function importModel(): Promise<void> {
  const path = await pickFile();
  if (!path) return;
  openLeft("models");
  const ui = useUi.getState();
  const made = await useDoc.getState().importToBed(path, ui.importRepair);
  if (!made.length) return;
  viewport.fit();
  const report = useDoc.getState().lastImport;
  ui.notify({
    text: made.length === 1
      ? `На столе: ${made[0]}`
      : `На столе — деталей: ${made.length} (${made.join(", ")})`,
    tone: report && !report.fits ? "warn" : "good",
  });
}

/** Ошибка открытия проекта — в строку состояния; её держит `App`. */
let reportOpenError: (text: string) => void = () => undefined;
export function onOpenError(report: (text: string) => void): void {
  reportOpenError = report;
}

/** Пока поднимается сервер другого проекта, окно показывает прежний — и весть. */
export const opening = (name: string) =>
  useUi.getState().notify({ text: `Открываю ${name}…`, tone: "info" });

/** Отказ — весть убирается, причина — туда же, куда прежде. */
export function opened(r: { ok: boolean; error: string }): void {
  if (!r.ok) useUi.getState().notify(null);
  reportOpenError(r.ok ? "" : r.error);
}

export function chooseProject(): void {
  void openProject(opening).then(opened);
}

export function chooseArchive(): void {
  void openArchive(opening).then(opened);
}

/** Сохранить: туда же, где проект сохранён; впервые — спросить, куда. */
export function save(asNew = false): void {
  void useDoc.getState().saveProject(asNew);
}

/** Del: удалить выделенную деталь — сразу, без вопроса; вернуть — «Вернуть» или Ctrl+Z. */
export function deleteSelected(): void {
  const part = useDoc.getState().selected;
  if (part) void deleteNow(part);
}

let deleting = false;

/** Удалить деталь из проекта и предложить вернуть. */
export async function deleteNow(part: string): Promise<void> {
  // Второй Del, пока сервер уносит первую, удалял бы уже соседнюю выделенную.
  if (deleting) return;
  deleting = true;
  try {
    const done = await useDoc.getState().deletePart(part);
    if (!done) {
      useUi.getState().notify({ text: useDoc.getState().problems.build ?? "деталь не удалена", tone: "bad" });
      return;
    }
    const also = [
      done.parts.length > 1 ? `и ${done.parts.filter((n) => n !== part).join(", ")} из того же файла` : "",
      done.models.length ? "вместе с моделью" : "",
      done.checks_kept ? `проверки ${done.checks.join(", ")} остались в checks.yaml` : "",
    ].filter(Boolean).join(" · ");
    useUi.getState().notify({
      text: `«${part}» удалена${also ? ` — ${also}` : ""}`,
      tone: done.checks_kept ? "bad" : "info",
      action: { label: "Вернуть", run: () => void undo() },
    });
  } finally {
    deleting = false;
  }
}

/** Ctrl+Z: отменить последнюю правку — перенос, поворот или удаление. */
export async function undo(): Promise<void> {
  if (!useDoc.getState().history.length) {
    useUi.getState().notify({ text: "отменять нечего", tone: "info" });
    return;
  }
  const done = await useDoc.getState().undo();
  useUi.getState().notify(done
    ? { text: done, tone: "good" }
    : { text: useDoc.getState().problems.build ?? "не отменилось", tone: "bad" });
}

/** Что отменит Ctrl+Z — для меню. */
function undoLabel(): string {
  const last = useDoc.getState().history.at(-1);
  if (!last) return "Отменить";
  return last.kind === "delete" ? `Вернуть «${last.part}»` : `Отменить перенос «${last.part}»`;
}

/** Меню окна. Функция, а не константа: флажки читают состояние на момент открытия. */
export function menus(): { label: string; items: MenuItem[] }[] {
  const doc = useDoc.getState();
  const ui = useUi.getState();
  const exploded = ui.spread > 0;
  // Замер и срез гаснут и при разнесении, и при сдвинутом звене шарнира.
  const posed = moved(ui);
  const hasJoints = Object.keys(doc.info?.assembly?.joints ?? {}).length > 0;
  const focused = !ui.leftOpen && !ui.rightOpen && !ui.dockOpen;

  return [
    {
      label: "Файл",
      items: [
        {
          label: "Проекты…",
          keys: "Ctrl+O",
          onSelect: () => ui.setProjectsOpen(true),
          hint: "свои проекты: открыть, переименовать, сделать новый из этого",
        },
        { label: "Открыть проект…", onSelect: chooseProject, hint: "папка проекта; откроется в этом же окне" },
        {
          label: "Открыть архив .datum…",
          onSelect: chooseArchive,
          hint: "распаковывается рядом с архивом; уже распакованный открывается как есть",
        },
        { separator: true },
        {
          label: "Сохранить",
          keys: "Ctrl+S",
          disabled: doc.saving,
          onSelect: () => save(),
          hint: doc.info?.archive ? `в ${doc.info.archive.path}` : "проект ещё не сохранялся — спросит, куда",
        },
        {
          label: "Сохранить как…",
          keys: "Ctrl+Shift+S",
          disabled: doc.saving,
          onSelect: () => save(true),
          hint: "упаковать проект в новый файл .datum",
        },
        { separator: true },
        { label: "Импортировать модель…", onSelect: () => void importModel(), hint: "STL, 3MF, OBJ: каждый объект файла — деталью на стол" },
        { label: "Снять версию…", onSelect: () => openLeft("versions") },
        {
          label: "Выгрузить для печати",
          onSelect: () => void doc.exportParts(),
          disabled: doc.exporting || doc.building,
          hint: "3MF и STL в output/, в позе печати; с вердиктом проверок",
        },
        {
          label: "Нарезать в ElegooSlicer",
          onSelect: () => void doc.sliceParts(),
          disabled: doc.exporting || doc.building,
          hint: "проект слайсера и G-code в output/ по профилю из datum.toml; время и граммы — от слайсера",
        },
      ],
    },
    {
      label: "Правка",
      items: [
        { label: undoLabel(), keys: "Ctrl+Z", disabled: !doc.history.length, onSelect: () => void undo() },
        { separator: true },
        {
          label: "Удалить деталь",
          keys: "Del",
          disabled: !doc.selected,
          onSelect: deleteSelected,
          hint: "деталь уходит из проекта вместе со своей моделью и проверками; вернуть — Ctrl+Z",
        },
      ],
    },
    {
      label: "Вид",
      items: [
        ...VIEWS.map((view) => ({
          label: view.label,
          keys: view.hint,
          onSelect: () => viewport.setView(view.key),
        })),
        { separator: true },
        { label: "Вернуть камеру", keys: "H", onSelect: () => viewport.home() },
        { label: "Вписать всё", keys: "F", onSelect: () => viewport.fit() },
        {
          label: "Вписать выделенное",
          keys: "Shift+F",
          disabled: !doc.selected,
          onSelect: () => viewport.fit(doc.selected),
        },
        { separator: true },
        { label: "Сетка и объём печати", keys: "G", checked: ui.grid, onSelect: ui.toggleGrid },
        {
          label: exploded ? "Вернуть в сборку" : "Разнести детали",
          keys: "E",
          onSelect: () => ui.setSpread(exploded ? 0 : 1),
        },
        ...(hasJoints
          ? [{
              label: "Шарниры — в собранное положение",
              disabled: Object.keys(ui.joints).length === 0,
              onSelect: ui.resetJoints,
            }]
          : []),
        { label: "Только модель", keys: "Shift+Space", checked: focused, onSelect: ui.toggleFocus },
      ],
    },
    {
      label: "Инструменты",
      items: [
        ...TOOLS.map((tool) => ({
          label: tool.label,
          keys: tool.key2,
          checked: ui.tool === tool.key,
          disabled: posed && tool.key !== "none",
          onSelect: () => ui.setTool(tool.key),
        })),
        { separator: true },
        ...(["xy", "xz", "yz"] as const).map((plane) => ({
          label: `Срез по ${{ xy: "Z", xz: "Y", yz: "X" }[plane]}`,
          checked: ui.plane === plane,
          disabled: posed,
          onSelect: () => pickPlane(plane),
        })),
        { separator: true },
        { label: "Собрать", keys: "B", disabled: doc.building, onSelect: () => void doc.rebuild() },
        {
          label: "Черновик",
          checked: ui.draft,
          onSelect: () => {
            ui.toggleDraft();
            void doc.rebuild();
          },
          hint: "скрипт огрубляет сам (draft()): форма за секунды; проверки и выгрузка — всегда по точной сборке",
        },
        { label: "Проверить", keys: "C", disabled: doc.checking, onSelect: runChecks },
      ],
    },
    {
      label: "Окно",
      items: [
        { label: "Левая панель", checked: ui.leftOpen, onSelect: ui.toggleLeft },
        { label: "Правая панель", checked: ui.rightOpen, onSelect: ui.toggleRight },
        { label: "Нижняя панель", keys: "`", checked: ui.dockOpen, onSelect: ui.toggleDock },
        { separator: true },
        { heading: "Слева" },
        { label: "Сцена", onSelect: () => openLeft("scene") },
        { label: "Модели", onSelect: () => openLeft("models") },
        { label: "Версии", onSelect: () => openLeft("versions") },
        { heading: "Справа" },
        { label: "Свойства", onSelect: () => ui.showRight("props") },
        { label: "Проверки", onSelect: () => ui.showRight("checks") },
        { label: "Параметры", onSelect: () => ui.showRight("params") },
        { heading: "Снизу" },
        // Пункт меню — такая же просьба, как кнопка в доке и «Агент» в строке
        // состояния: открывает вкладку И запускает claude, если он ещё не запущен.
        { label: "Claude Code", onSelect: ui.askAgent },
        { label: "Лента", onSelect: () => ui.showDock("log") },
      ],
    },
    {
      label: "Справка",
      items: [{ label: "Клавиши", keys: "?", onSelect: () => ui.setKeysOpen(true) }],
    },
  ];
}
