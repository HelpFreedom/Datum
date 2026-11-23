/** Правая сторона: свойства, проверки, параметры. */
import { useDoc } from "../store/doc";
import { type RightTab, useUi } from "../store/ui";
import { Checks } from "./Checks";
import { Params } from "./Params";
import { Properties } from "./Properties";

const TABS: { key: RightTab; label: string }[] = [
  { key: "props", label: "Свойства" },
  { key: "checks", label: "Проверки" },
  { key: "params", label: "Параметры" },
];

export function Side() {
  const tab = useUi((s) => s.right);
  const { showRight } = useUi.getState();
  // Счётчик на вкладке: сколько требует внимания, видно и из соседней.
  const failing = useDoc((s) => s.checks?.results.filter((r) => r.status === "fail" || r.status === "error").length ?? 0);
  const unknown = useDoc((s) => s.params?.summary.unknowns.length ?? 0);
  return (
    <>
      <div className="panel-head tabs-head">
        <div className="tabs underline" role="tablist" aria-label="Правая панель">
          {TABS.map(({ key, label }) => (
            <button
              key={key}
              id={`tab-${key}`}
              role="tab"
              aria-selected={tab === key}
              aria-controls="side-panel"
              onClick={() => showRight(key)}
            >
              {label}
              {key === "checks" && failing > 0 && <span className="count bad">{failing}</span>}
              {key === "params" && unknown > 0 && <span className="count warn">{unknown}</span>}
            </button>
          ))}
        </div>
      </div>
      <div id="side-panel" className="scroll pad" role="tabpanel" aria-labelledby={`tab-${tab}`}>
        {tab === "props" && <Properties />}
        {tab === "checks" && <Checks />}
        {tab === "params" && <Params />}
      </div>
    </>
  );
}
