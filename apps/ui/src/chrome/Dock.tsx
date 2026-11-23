/**
 * Нижняя панель: терминал агента и лента операций.
 *
 * Высоту тянут за верхнюю кромку (`Grip`), сколько угодно. Терминал при этом
 * переразмечается сам (его `ResizeObserver`), а свёрнутая панель терминал не
 * убивает — только прячет.
 */
import { ChevronDown, ListTree, SquareTerminal } from "lucide-react";
import { Fault } from "../fault";
import { Log } from "../panels/Log";
import { TerminalPanel } from "../panels/Terminal";
import { useDoc } from "../store/doc";
import { type DockTab, useUi } from "../store/ui";
import { Grip } from "./Grip";
import { DOCK_DEFAULT } from "./layout";

const TABS: { key: DockTab; label: string; Icon: typeof ListTree }[] = [
  { key: "agent", label: "Claude Code", Icon: SquareTerminal },
  { key: "log", label: "Лента", Icon: ListTree },
];

export function Dock() {
  const tab = useUi((s) => s.dock);
  const open = useUi((s) => s.dockOpen);
  const { showDock, toggleDock, setDockHeight } = useUi.getState();
  const running = useDoc((s) => s.jobs.filter((j) => j.status === "running").length);

  return (
    <section className="dock panel" style={{ display: open ? "flex" : "none" }} aria-label="Нижняя панель">
      <Grip edge="top" onSize={(px) => setDockHeight(px ?? DOCK_DEFAULT)} />
      <div className="panel-head">
        <div className="tabs underline" role="tablist" aria-label="Нижняя панель">
          {TABS.map(({ key, label, Icon }) => (
            <button
              key={key}
              role="tab"
              aria-selected={tab === key}
              onClick={() => showDock(key)}
            >
              <Icon size={15} />
              {label}
              {key === "log" && running > 0 && <span className="count busy">{running}</span>}
            </button>
          ))}
        </div>
        <div className="panel-actions">
          <button className="icon-button small" title="Свернуть (`)" aria-label="Свернуть" onClick={toggleDock}>
            <ChevronDown size={16} />
          </button>
        </div>
      </div>
      <div className="dock-body">
        <Fault area="Терминал">
          <TerminalPanel visible={open && tab === "agent"} />
        </Fault>
        {tab === "log" && (
          <div className="scroll pad">
            <Fault area="Лента">
              <Log />
            </Fault>
          </div>
        )}
      </div>
    </section>
  );
}
