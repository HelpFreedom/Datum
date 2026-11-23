/**
 * Клавиши окна. Одна таблица — её же показывает справка «Клавиши».
 *
 * Клавиши видов 1–7 и «вписать» — как в слайсере. Всё остальное — одна буква
 * без модификаторов: Ctrl и Alt заняты чужими сочетаниями (вкладки, меню,
 * терминал), а буква рядом с действием запоминается сама.
 */
import { useDoc } from "../store/doc";
import { useUi } from "../store/ui";
import { viewport, VIEWS } from "../viewport/control";
import { deleteSelected, runChecks, save, undo } from "./commands";

export const KEYS: { keys: string; what: string }[] = [
  ...VIEWS.map((v) => ({ keys: v.hint, what: `Вид: ${v.label.toLowerCase()}` })),
  { keys: "H", what: "Вернуть камеру: изометрия на всё, как при открытии" },
  { keys: "F", what: "Вписать всё" },
  { keys: "Shift+F", what: "Вписать выделенное" },
  { keys: "G", what: "Сетка и объём печати" },
  { keys: "E", what: "Разнести детали / вернуть в сборку" },
  { keys: "D", what: "Штангенциркуль: расстояние, рёбра, окружности" },
  { keys: "M", what: "Перемещение детали (тянуть саму деталь или ручки)" },
  { keys: "R", what: "Поворот детали" },
  { keys: "Shift", what: "При переносе и повороте — мелкий шаг: 0.1 мм и 1°" },
  { keys: "Del", what: "Удалить выделенную деталь (вернуть — Ctrl+Z)" },
  { keys: "Ctrl+Z", what: "Отменить перенос, поворот или удаление" },
  { keys: "T", what: "Толщина стенки" },
  { keys: "Esc", what: "Обзор: снять инструмент, закрыть окно" },
  { keys: "B", what: "Собрать" },
  { keys: "C", what: "Проверить" },
  { keys: "`", what: "Нижняя панель" },
  { keys: "Shift+Space", what: "Только модель — убрать панели" },
  { keys: "Ctrl+O", what: "Проекты: открыть, переименовать, новый из этого" },
  { keys: "Ctrl+S", what: "Сохранить проект (.datum)" },
  { keys: "Ctrl+Shift+S", what: "Сохранить как…" },
  { keys: "?", what: "Эта справка" },
  { keys: "ЛКМ", what: "Вращать" },
  { keys: "ПКМ / СКМ", what: "Двигать" },
  { keys: "Колесо", what: "К курсору постоянным шагом, сквозь центр — дальше; с Shift — мелко" },
];

function inTerminal(target: EventTarget | null): boolean {
  return target instanceof HTMLElement && Boolean(target.closest(".terminal"));
}

function typing(target: EventTarget | null): boolean {
  // Событие может прийти и не от элемента (окно, документ) — тогда никто не
  // печатает.
  if (!(target instanceof HTMLElement)) return false;
  const el = target;
  // Ползунок и флажок — не набор текста: после ползунка среза или разнесения
  // клавиши (Esc, 1–7, F, H, E, Del, Ctrl+Z) иначе молчали, пока не щёлкнешь
  // по сцене (найдено обзором).
  if (el instanceof HTMLInputElement && ["range", "checkbox", "radio", "button"].includes(el.type)) {
    return false;
  }
  if (/^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName) || el.isContentEditable) return true;
  // Терминал агента ловит клавиши сам: вид не должен прыгать от набора.
  return inTerminal(el);
}

export function installHotkeys(): () => void {
  const onKey = (event: KeyboardEvent) => {
    if (event.defaultPrevented) return;
    // Ctrl+S — как в любой программе, в том числе из поля ввода. Кроме
    // терминала агента: там это его собственная клавиша.
    if (event.ctrlKey && !event.altKey && !event.metaKey && event.code === "KeyS"
        && !inTerminal(event.target)) {
      event.preventDefault();
      save(event.shiftKey);
      return;
    }
    // Ctrl+O — проекты, как «открыть» в любой программе (кроме терминала агента).
    if (event.ctrlKey && !event.altKey && !event.metaKey && !event.shiftKey
        && event.code === "KeyO" && !inTerminal(event.target)) {
      event.preventDefault();
      useUi.getState().setProjectsOpen(true);
      return;
    }
    if (typing(event.target)) return;
    // Ctrl+Z — вне полей ввода: в поле это отмена набранного текста.
    if (event.ctrlKey && !event.altKey && !event.metaKey && !event.shiftKey && event.code === "KeyZ") {
      event.preventDefault();
      void undo();
      return;
    }
    if (event.ctrlKey || event.altKey || event.metaKey) return;
    const ui = useUi.getState();
    const doc = useDoc.getState();

    // По ФИЗИЧЕСКОЙ клавише (`code`), а не по букве: в русской раскладке «F»
    // приходит как «А», и клавиши молчали бы у половины набора.
    const digit = /^Digit([1-7])$/.exec(event.code);
    if (digit && !event.shiftKey) {
      viewport.setView(VIEWS[Number(digit[1]) - 1].key);
      event.preventDefault();
      return;
    }
    switch (event.code) {
      case "KeyF":
        viewport.fit(event.shiftKey ? doc.selected : undefined);
        break;
      case "KeyH":
        viewport.home();
        break;
      case "KeyG":
        ui.toggleGrid();
        break;
      case "KeyE":
        ui.setSpread(ui.spread > 0 ? 0 : 1);
        break;
      case "KeyD":
        ui.setTool(ui.tool === "distance" ? "none" : "distance");
        break;
      case "KeyT":
        ui.setTool(ui.tool === "probe" ? "none" : "probe");
        break;
      case "KeyM":
        ui.setTool(ui.tool === "move" ? "none" : "move");
        break;
      case "KeyR":
        ui.setTool(ui.tool === "rotate" ? "none" : "rotate");
        break;
      case "Delete":
        deleteSelected();
        break;
      case "KeyB":
        if (!doc.building) void doc.rebuild();
        break;
      case "KeyC":
        if (!doc.checking) runChecks();
        break;
      case "Backquote":
        ui.toggleDock();
        break;
      case "Slash":
        if (!event.shiftKey) return;
        ui.setKeysOpen(!ui.keysOpen);
        break;
      case "Escape":
        if (ui.projectsOpen) ui.setProjectsOpen(false);
        else if (ui.keysOpen) ui.setKeysOpen(false);
        else ui.setTool("none");
        break;
      case "Space":
        if (!event.shiftKey) return;
        ui.toggleFocus();
        break;
      default:
        return;
    }
    event.preventDefault();
  };
  window.addEventListener("keydown", onKey);
  return () => window.removeEventListener("keydown", onKey);
}
