/**
 * Терминал со встроенным системным Claude Code.
 *
 * Datum не пишет своего агента, своего чата, своих диалогов разрешений и своего
 * управления сессиями. Здесь — ваш системный `claude` как есть, со всеми его
 * настройками, навыками и механизмом разрешений. Окно только показывает его
 * вывод и передаёт нажатия.
 *
 * Панель не размонтируется при закрытии: разговор с агентом обязан пережить
 * случайное нажатие на «скрыть». Скрывается она стилем, а процесс живёт до
 * закрытия приложения.
 *
 * ЗАПУСК — ПО ПРОСЬБЕ. Нижняя панель открыта с первого кадра, и поднимать
 * агента от одного её вида значило бы запускать `claude` каждому, кто просто
 * открыл окно. Поэтому до первой просьбы — карточка с кнопкой; просьба — это
 * кнопка, пункт меню или «Агент» в строке состояния.
 *
 * ПОСЛЕ ПАДЕНИЯ ОКНА claude продолжает работать в оболочке. Перезагруженная
 * страница спрашивает, жив ли он, и подключается сама: оболочка отдаёт хвост
 * вывода, и экран терминала — как был, разговор — тот же.
 */
import { FitAddon } from "@xterm/addon-fit";
import { Terminal as Xterm } from "@xterm/xterm";
import { Play, RotateCcw, SquareTerminal } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import "@xterm/xterm/css/xterm.css";

import { useDoc } from "../store/doc";
import { useUi } from "../store/ui";

interface AgentBridge {
  start: (cols: number, rows: number) => Promise<{ ok: true; backlog?: string } | { ok: false; error: string }>;
  status?: () => Promise<{ running: boolean }>;
  write: (data: string) => void;
  resize: (cols: number, rows: number) => void;
  onData: (callback: (chunk: string) => void) => () => void;
  onExit: (callback: (code: number) => void) => () => void;
}

const bridge = (): AgentBridge | undefined =>
  (window as unknown as { datum?: { agent?: AgentBridge } }).datum?.agent;

/** Шрифт терминала: тот же моноширинный, что у чисел в окне. */
const MONO = '"JetBrains Mono Variable", "DejaVu Sans Mono", monospace';

/** Цвета под тему окна; фон — ровно фон нижней панели (`--dock`). */
const THEME = {
  background: "#0c1118",
  foreground: "#d5dde8",
  cursor: "#5fb3ff",
  cursorAccent: "#0c1118",
  selectionBackground: "#1f3552",
  black: "#141b24",
  red: "#ff6b7a",
  green: "#3ee08f",
  yellow: "#ffc861",
  blue: "#5aa9ff",
  magenta: "#c79bff",
  cyan: "#53d6e8",
  white: "#d5dde8",
  brightBlack: "#5d6b7e",
  brightRed: "#ff8a96",
  brightGreen: "#6cf0ab",
  brightYellow: "#ffd98a",
  brightBlue: "#86c1ff",
  brightMagenta: "#dbbcff",
  brightCyan: "#86e6f2",
  brightWhite: "#f3f6fb",
};

export function TerminalPanel({ visible }: { visible: boolean }) {
  const hostRef = useRef<HTMLDivElement>(null);
  const termRef = useRef<Xterm | null>(null);
  const fitRef = useRef<FitAddon | null>(null);
  const started = useRef(false);
  const [problem, setProblem] = useState<string | null>(null);
  // Завершился ли claude: тогда вместо терминала без процесса — кнопка
  // перезапуска. Раньше для этого приходилось закрывать и открывать панель.
  const [exited, setExited] = useState<number | null>(null);
  const [wanted, setWanted] = useState(false);
  const [ready, setReady] = useState(false);
  const ask = useUi((s) => s.agentAsk);
  const project = useDoc((s) => s.project);
  const { setAgent } = useUi.getState();

  const cleanupRef = useRef<(() => void) | null>(null);

  // Просьба снаружи — «Агент» в строке состояния или меню.
  useEffect(() => {
    if (ask > 0) setWanted(true);
  }, [ask]);

  // claude уже работает — окно перезагрузилось после падения: подключаемся
  // сразу, просьба была ещё до падения.
  useEffect(() => {
    void bridge()?.status?.().then((s) => {
      if (s.running) setWanted(true);
    }).catch(() => undefined);
  }, []);

  // Создание — при первой просьбе на ВИДИМОЙ панели. xterm меряет узел,
  // чтобы посчитать размер знакоместа; открытый в скрытой панели, он получает
  // нули — отсюда и не работавший ввод, и сорванный расчёт строк. Шрифт
  // дожидаемся по той же причине: знакоместо меряется по нему.
  useEffect(() => {
    const host = hostRef.current;
    const agent = bridge();
    if (!visible || !wanted || !host || !agent || termRef.current) return;
    let cancelled = false;

    void document.fonts.load(`13px ${MONO}`).catch(() => undefined).then(() => {
      if (cancelled || termRef.current) return;
      const term = new Xterm({
        fontFamily: MONO,
        fontSize: 13,
        lineHeight: 1.3,
        cursorBlink: true,
        scrollback: 20000,
        theme: THEME,
        // Агент рисует рамки и полосы прогресса псевдографикой; без этого рвётся.
        allowProposedApi: true,
      });
      const fit = new FitAddon();
      term.loadAddon(fit);
      term.open(host);
      termRef.current = term;
      fitRef.current = fit;

      term.onData((data) => agent.write(data));
      const offData = agent.onData((chunk) => term.write(chunk));
      const offExit = agent.onExit((code) => {
        term.writeln(`\r\n\x1b[90m— claude завершился, код ${code} —\x1b[0m`);
        started.current = false;
        setExited(code);
        setAgent("exited");
      });

      const observer = new ResizeObserver(() => {
        if (host.clientHeight < 10) return;     // панель скрыта или ещё не разложена
        try {
          fit.fit();
          agent.resize(term.cols, term.rows);
        } catch {
          /* размер ещё не устоялся */
        }
      });
      observer.observe(host);

      cleanupRef.current = () => {
        observer.disconnect();
        offData();
        offExit();
        term.dispose();
        termRef.current = null;
      };
      setReady(true);
    });
    return () => {
      cancelled = true;
    };
  }, [visible, wanted, setAgent]);

  // Разбор — только при закрытии окна. Скрытие панели терминал не трогает:
  // разговор с агентом обязан пережить случайное нажатие на «скрыть».
  useEffect(() => () => cleanupRef.current?.(), []);

  /** Поднять claude в терминале — при первой просьбе и по кнопке. */
  const launch = () => {
    const term = termRef.current;
    const fit = fitRef.current;
    const agent = bridge();
    if (!term || !fit || !agent || started.current) return;
    started.current = true;
    setProblem(null);
    setExited(null);
    setAgent("starting");
    try {
      fit.fit();
    } catch {
      /* размер ещё не устоялся */
    }
    agent
      .start(term.cols, term.rows)
      .then((result) => {
        if (!result.ok) {
          setProblem(result.error);
          setAgent("failed");
          started.current = false;
        } else {
          if (result.backlog !== undefined) {
            // Подключились к работающему: экран — из хвоста. То, что успело
            // прийти до ответа, в хвосте уже есть, поэтому сначала — сброс.
            term.reset();
            term.write(result.backlog);
            // Толчок размером: TUI claude перерисуется целиком под этот терминал.
            agent.resize(term.cols, Math.max(5, term.rows - 1));
            window.setTimeout(() => agent.resize(term.cols, term.rows), 120);
          }
          setAgent("running");
          term.focus();
        }
      })
      // Отказ самого IPC раньше не ловился: флаг «запущен» оставался поднятым,
      // и терминал молчал без объяснения.
      .catch((e: unknown) => {
        setProblem(e instanceof Error ? e.message : String(e));
        setAgent("failed");
        started.current = false;
      });
  };

  // Терминал готов — запускаем: просьба уже была, иначе его бы не создали.
  useEffect(() => {
    if (!ready || started.current || exited !== null) return;
    const id = window.setTimeout(launch, 80);
    return () => window.clearTimeout(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready]);

  // Возврат фокуса после показа панели: без этого нажатия уходят кнопке,
  // которой её открыли, и человек печатает в пустоту.
  useEffect(() => {
    if (!visible || !ready) return;
    const id = window.setTimeout(() => termRef.current?.focus(), 200);
    return () => window.clearTimeout(id);
  }, [visible, ready]);

  const available = Boolean(bridge());

  return (
    <div className="terminal" style={{ display: visible ? "flex" : "none" }}>
      {!wanted && (
        <div className="agent-intro">
          <div className="agent-intro-icon">
            <SquareTerminal size={26} strokeWidth={1.5} />
          </div>
          <div className="agent-intro-text">
            <div className="agent-intro-title">Claude Code</div>
            <div className="agent-intro-sub">
              Ваш системный <code>claude</code> в каталоге{" "}
              <code>{project.split("/").slice(-2).join("/") || "проекта"}</code>. Работает теми
              же операциями, что и окно: его сборки и правки появляются здесь сразу.
            </div>
          </div>
          <button className="primary" disabled={!available} onClick={() => setWanted(true)}>
            <Play size={13} fill="currentColor" /> Запустить Claude Code
          </button>
        </div>
      )}
      {problem && <div className="alert">{problem}</div>}
      {exited !== null && !problem && (
        <div className="terminal-exit">
          <span className="dim">claude завершился (код {exited}).</span>
          <button className="ghost" onClick={launch}>
            <RotateCcw size={13} /> Перезапустить
          </button>
        </div>
      )}
      <div
        ref={hostRef}
        className="terminal-host"
        style={{ display: wanted ? "block" : "none" }}
        onMouseDown={() => termRef.current?.focus()}
      />
    </div>
  );
}
