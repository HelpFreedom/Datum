/**
 * Строка состояния: связь, агент, проект и то, что блокирует печать.
 *
 * «Не измерено» живёт здесь, а не только в панели параметров: пока есть
 * незамеренное, снимок не готов к печати, и это должно быть видно из любой
 * вкладки.
 */
import { ChevronDown, Printer, Triangle } from "lucide-react";
import { useEffect, useState } from "react";

import { useDoc } from "../store/doc";
import { type AgentState, useUi } from "../store/ui";
import { chooseProject, onOpenError } from "./commands";
import { contextLabel } from "./context";

/** Как часто спрашивается размер контекста идущего агента. */
const CONTEXT_POLL_MS = 15_000;

type AgentContext = () => Promise<{ tokens: number; at: number } | null>;
const agentContext = (): AgentContext | undefined =>
  (window as unknown as { datum?: { agent?: { context?: AgentContext } } }).datum?.agent?.context;

const AGENT: Record<AgentState, { text: string; tone: string }> = {
  idle: { text: "не запущен", tone: "dim" },
  starting: { text: "запускается", tone: "busy" },
  running: { text: "работает", tone: "good" },
  exited: { text: "завершился", tone: "dim" },
  failed: { text: "не запустился", tone: "bad" },
};

export function StatusBar() {
  const connected = useDoc((s) => s.connected);
  const projectName = useDoc((s) => s.projectName);
  const parts = useDoc((s) => s.parts);
  const info = useDoc((s) => s.info);
  const checks = useDoc((s) => s.checks);
  const selected = useDoc((s) => s.selected);
  const agent = useUi((s) => s.agent);
  const draft = Object.values(parts).some((p) => p.draft);
  const { askAgent } = useUi.getState();
  const [openError, setOpenError] = useState("");

  useEffect(() => onOpenError(setOpenError), []);

  // Размер контекста агента — пока он работает. Число берёт оболочка из
  // журнала его сессии; нет его — строки просто нет.
  const [context, setContext] = useState<number | null>(null);
  useEffect(() => {
    const ask = agentContext();
    if (agent !== "running" || !ask) {
      setContext(null);
      return undefined;
    }
    let alive = true;
    const poll = () => {
      void ask().then((c) => {
        if (alive) setContext(c ? c.tokens : null);
      }).catch(() => undefined);
    };
    poll();
    const timer = window.setInterval(poll, CONTEXT_POLL_MS);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, [agent]);
  const contextNote = context ? contextLabel(context) : null;

  const total = Object.values(parts).reduce((sum, p) => sum + p.tris, 0);
  const unknowns = info?.params?.blocking_unknowns ?? [];
  const printer = info?.printer;
  const state = AGENT[agent];

  return (
    <footer className="status">
      <button className="status-item" title="Системный claude в окне проекта" onClick={askAgent}>
        <span className={`led ${state.tone}`} /> Агент
        <span className="status-dim">{state.text}</span>
      </button>
      {contextNote && (
        <span
          className={`status-item ${contextNote.warn ? "warn" : "status-dim"}`}
          data-status="context"
          title={contextNote.hint}
        >
          {contextNote.text}
        </span>
      )}
      <span className="status-item" title={connected ? "сервер проекта отвечает" : "связи с сервером нет"}>
        <span className={`led ${connected ? "good" : "bad"}`} /> Сервер
      </span>
      <button
        className="status-item"
        title="Открыть другой проект (в этом же окне)"
        onClick={chooseProject}
      >
        {projectName || "проект"} <ChevronDown size={13} />
      </button>
      <span className="status-item status-dim">
        {Object.keys(parts).length} дет. · {total.toLocaleString("ru-RU")} треуг.
      </span>
      {draft && (
        <span className="status-item warn" title="Показана черновая сборка; проверки и выгрузка идут по точной">
          черновик
        </span>
      )}
      {openError && <span className="status-item bad">{openError}</span>}
      {unknowns.length > 0 && (
        <span className="status-item warn" title="Пока есть незамеренное, снимок не готов к печати">
          <Triangle size={11} /> не измерено: {unknowns.join(", ")}
        </span>
      )}

      <span className="status-spacer" />

      {selected && <span className="status-item status-dim">выделено: {selected}</span>}
      {checks && !checks.empty && (
        <span className={`status-item ${checks.ok ? (checks.n_warn ? "warn" : "good") : "bad"}`}>
          проверки {checks.n_pass}/{checks.results.length}
        </span>
      )}
      {printer && (
        <span className="status-item status-dim" title="Принтер проекта из datum.toml">
          <Printer size={13} /> {printer.material} · сопло {printer.nozzle} · стол{" "}
          {printer.bed.join("×")}
        </span>
      )}
    </footer>
  );
}
