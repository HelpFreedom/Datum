/**
 * Верхняя строка: меню, чем занят проект, главная кнопка и знак Datum.
 *
 * Посередине — не заголовок ради заголовка, а состояние: собирается ли,
 * проверяется ли, есть ли связь. Это первое, что нужно знать, взглянув на
 * окно, — и раньше оно было разбросано по трём углам.
 */
import { Play } from "lucide-react";

import { useDoc } from "../store/doc";
import { useUi } from "../store/ui";
import { menus, runChecks } from "./commands";
import { Logo } from "./Logo";
import { MenuBar } from "./Menu";

function useActivity(): { text: string; tone: string } {
  const connected = useDoc((s) => s.connected);
  const building = useDoc((s) => s.building);
  const progress = useDoc((s) => s.buildProgress);
  const checking = useDoc((s) => s.checking);
  const exporting = useDoc((s) => s.exporting);
  const working = useDoc((s) => s.working);
  const broken = useDoc((s) => Boolean(s.problems.project));
  const checks = useDoc((s) => s.checks);

  if (!connected) return { text: "нет связи с сервером", tone: "bad" };
  if (broken) return { text: "проект не открылся", tone: "bad" };
  // Ход сборки приходит и от чужой сборки (агент): он виден, даже когда
  // кнопку «Собрать» нажимали не здесь.
  if (progress) {
    return { text: `собрано ${progress.done} из ${progress.total}…`, tone: "busy" };
  }
  if (building) return { text: "собирается…", tone: "busy" };
  if (checking) return { text: "проверяется…", tone: "busy" };
  if (exporting) return { text: "выгружается…", tone: "busy" };
  if (working) return { text: "работает…", tone: "busy" };
  if (!checks) return { text: "собрано", tone: "dim" };
  if (checks.empty) return { text: "проверок нет", tone: "warn" };
  if (!checks.ok) return { text: `не прошло проверок: ${checks.n_fail}`, tone: "bad" };
  return { text: checks.n_warn ? `проверено, внимание: ${checks.n_warn}` : "все проверки прошли", tone: checks.n_warn ? "warn" : "good" };
}

/**
 * Где проект сохранён архивом. Рабочая папка пишется сразу, поэтому
 * «несохранённое» здесь — не риск потерять правку, а расхождение с архивом:
 * то, чего не будет у того, кому этот файл отдадут.
 */
function ArchiveMark() {
  const saved = useDoc((s) => s.info?.archive ?? null);
  const saving = useDoc((s) => s.saving);
  const problem = useDoc((s) => s.problems.archive);
  if (problem) {
    return <span className="topbar-archive bad" title={problem}>не сохранено: {problem}</span>;
  }
  if (saving) return <span className="topbar-archive busy">сохраняется…</span>;
  if (!saved) return null;
  const when = saved.saved ? new Date(saved.saved).toLocaleString("ru-RU") : "";
  const status = !saved.exists
    ? "файла архива нет — Ctrl+S запишет его заново"
    : saved.dirty
      ? "есть несохранённое — Ctrl+S"
      : "сохранено";
  const behind = saved.dirty || !saved.exists;
  // Коротко — имя и точка; полностью — в подсказке: в узком окне строка
  // делит место с меню и кнопкой «Проверить».
  return (
    <span
      className={`topbar-archive ${behind ? "warn" : "dim"}`}
      title={`${saved.path}\n${status}${when ? `\nсохранено ${when}` : ""}`}
    >
      <span className={`led ${behind ? "warn" : "good"}`} />
      {saved.name}
      <span className="sr-only"> — {status}</span>
    </span>
  );
}

export function TopBar() {
  // Меню читает флажки из обоих сторов в момент отрисовки — подписка на них
  // и держит пункты-переключатели честными.
  useUi();
  const projectName = useDoc((s) => s.projectName);
  useDoc((s) => s.selected);
  useDoc((s) => s.building);
  useDoc((s) => s.saving);
  useDoc((s) => s.info?.archive);
  const checking = useDoc((s) => s.checking);
  const activity = useActivity();

  return (
    <header className="topbar">
      <MenuBar menus={menus()} />
      <div className="topbar-title" aria-live="polite">
        <span className="topbar-project">{projectName || "Datum"}</span>
        <span className="topbar-dot" />
        <span className={`topbar-activity ${activity.tone}`}>{activity.text}</span>
        <ArchiveMark />
      </div>
      <div className="topbar-right">
        <button className="primary" onClick={runChecks} disabled={checking} title="Прогнать все проверки проекта (C)">
          {checking ? "Проверяю…" : "Проверить"} <Play size={13} fill="currentColor" />
        </button>
        <Logo />
      </div>
    </header>
  );
}
