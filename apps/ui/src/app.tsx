/**
 * Каркас окна.
 *
 *   верхняя строка:  меню · проект и чем он занят · «Проверить» · знак Datum
 *   рейка │ левая панель │ вьюпорт │ правая панель
 *         │ нижняя панель: Claude Code, лента
 *   строка состояния
 *
 * Панели — отдельные карточки на тёмном поле, а не полосы впритык: так
 * вьюпорт читается главным, а панели — инструментами вокруг него.
 */
import { useEffect, useRef } from "react";

import { Dock } from "./chrome/Dock";
import { Grip } from "./chrome/Grip";
import { installHotkeys } from "./chrome/hotkeys";
import { KeysDialog } from "./chrome/KeysDialog";
import { ProjectsDialog } from "./chrome/ProjectsDialog";
import { NoticeBar } from "./chrome/Notice";
import { frameLayout } from "./chrome/layout";
import { Rail } from "./chrome/Rail";
import { StatusBar } from "./chrome/StatusBar";
import { TopBar } from "./chrome/TopBar";
import { Fault } from "./fault";
import { Models } from "./panels/Models";
import { Parts } from "./panels/Parts";
import { Side } from "./panels/Side";
import { Versions } from "./panels/Versions";
import { useDoc } from "./store/doc";
import { useUi } from "./store/ui";
import { endpoint, insideShell } from "./transport/endpoint";
import { EventStream } from "./transport/ws";
import { ViewportPanel } from "./viewport/Viewport";

function NotInShell() {
  return (
    <div className="empty" style={{ padding: 32, maxWidth: "34rem" }}>
      <p>
        Эта страница открыта не из Datum. Токена, которым подписываются запросы к
        серверу, ей взять неоткуда — он приходит из оболочки, а не из страницы.
      </p>
      <p>Запустите приложение командой <code>npm start</code>.</p>
    </div>
  );
}

/**
 * Проект не открылся — чаще всего синтаксическая ошибка в скрипте детали или
 * описании проверки. Окно при этом живо: агент в терминале может поправить
 * файл, наблюдатель пришлёт `doc.changed`, и карточка сама исчезнет.
 */
function ProjectProblem({ text }: { text: string }) {
  const refreshAll = useDoc((s) => s.refreshAll);
  return (
    <div className="project-problem" role="alert">
      <div className="project-problem-title">Проект не открылся</div>
      <pre>{text}</pre>
      <div className="sub">
        Поправьте файл — окно перечитает проект само, как только он сохранится.
      </div>
      <button onClick={() => void refreshAll()}>Перечитать сейчас</button>
    </div>
  );
}

function LeftPanel() {
  const tab = useUi((s) => s.left);
  if (tab === "models") return <Models />;
  if (tab === "versions") return <Versions />;
  return <Parts />;
}

export function App() {
  const projectProblem = useDoc((s) => s.problems.project);
  const leftOpen = useUi((s) => s.leftOpen);
  const rightOpen = useUi((s) => s.rightOpen);
  const dockOpen = useUi((s) => s.dockOpen);
  const dockHeight = useUi((s) => s.dockHeight);
  const leftWidth = useUi((s) => s.leftWidth);
  const rightWidth = useUi((s) => s.rightWidth);
  const { setLeftWidth, setRightWidth } = useUi.getState();
  const started = useRef(false);

  useEffect(() => {
    if (!insideShell() || started.current) return;
    started.current = true;
    const { applyEvent, setConnected, setProject, refreshAll } = useDoc.getState();

    const stream = new EventStream();
    stream.on(applyEvent);
    stream.onStatus(setConnected);

    void (async () => {
      const point = await endpoint();
      setProject(point.project);
      await stream.start();
      // Первая сборка сразу: окно без геометрии бесполезно, а всё, что нужно
      // для неё, уже посчитано или лежит в кеше. Проверки следом — увидеть
      // красное раньше, чем начнёшь что-то менять, важнее, чем полсекунды.
      await refreshAll();
    })();

    return () => stream.stop();
  }, []);

  useEffect(() => installHotkeys(), []);

  if (!insideShell()) return <NotInShell />;

  const layout = frameLayout({ leftOpen, rightOpen, dockOpen, dockHeight, leftWidth, rightWidth });

  return (
    <div className="frame" style={layout}>
      <TopBar />
      <Rail />
      <aside className="left panel" style={{ display: leftOpen ? "flex" : "none" }}>
        <Grip edge="right" onSize={setLeftWidth} />
        <Fault area="Левая панель">
          <LeftPanel />
        </Fault>
      </aside>
      <main className="center">
        {projectProblem && <ProjectProblem text={projectProblem} />}
        <Fault area="Вьюпорт">
          <ViewportPanel />
        </Fault>
      </main>
      <aside className="right panel" style={{ display: rightOpen ? "flex" : "none" }}>
        <Grip edge="left" onSize={setRightWidth} />
        <Fault area="Правая панель">
          <Side />
        </Fault>
      </aside>
      <Dock />
      <StatusBar />
      <KeysDialog />
      <ProjectsDialog />
      <NoticeBar />
    </div>
  );
}
