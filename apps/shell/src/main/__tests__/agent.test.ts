/**
 * Терминал агента: поздний выход ОСТАНОВЛЕННОГО claude не трогает новый.
 *
 * `stop` не ждёт смерти процесса. Прежде его onExit, пришедший после запуска
 * следующего, обнулял ссылку на НОВЫЙ процесс (тот оставался без управления),
 * стирал его файлы сеанса и говорил окну «агент вышел»; поздний вывод
 * старого ложился в новый экран. Идея проверки — из Kadr.
 */
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

interface FakePty {
  pid: number;
  data: (chunk: string) => void;
  exit: (code: number) => void;
  killed: boolean;
  command: string;
  args: string[];
  options: { cwd: string; env: Record<string, string> };
}
const spawned: FakePty[] = [];

vi.mock("node-pty", () => ({
  spawn: (command: string, args: string[], options: FakePty["options"]) => {
    const fake: FakePty = {
      pid: 1000 + spawned.length, data: () => {}, exit: () => {}, killed: false,
      command, args, options,
    };
    spawned.push(fake);
    return {
      pid: fake.pid,
      onData: (cb: (chunk: string) => void) => { fake.data = cb; },
      onExit: (cb: (e: { exitCode: number }) => void) => { fake.exit = (code) => cb({ exitCode: code }); },
      write: () => {},
      resize: () => {},
      kill: () => { fake.killed = true; },
    };
  },
}));

const { Agent } = await import("../agent");

const endpoint = { port: 1, token: "t", project: "/tmp" };
const size = { cols: 80, rows: 24 };
let runtime = "";
const saved = { ...process.env };

beforeEach(() => {
  spawned.length = 0;
  runtime = fs.mkdtempSync(path.join(os.tmpdir(), "datum-agent-"));
  process.env.XDG_RUNTIME_DIR = runtime;
  process.env.DATUM_CLAUDE = "/bin/sh";               // существует; не запускается
});
afterEach(() => {
  process.env = { ...saved };
  fs.rmSync(runtime, { recursive: true, force: true });
});

const sessionFiles = () => fs.readdirSync(path.join(runtime, "datum"));

describe("агент", () => {
  it("поздний выход остановленного claude не трогает новый", () => {
    const agent = new Agent();
    const exits: number[] = [];
    const screen: string[] = [];
    expect(agent.start(endpoint, size, (c) => screen.push(c), (c) => exits.push(c)).ok).toBe(true);
    const [first] = spawned;

    agent.stop();                                     // убит, но ещё не вышел
    expect(first.killed).toBe(true);
    expect(agent.start(endpoint, size, (c) => screen.push(c), (c) => exits.push(c)).ok).toBe(true);
    const second = spawned[1];
    const files = sessionFiles();
    expect(files.length).toBe(2);                     // рукопожатие и конфиг нового

    first.data("поздний вывод старого");
    first.exit(129);

    expect(agent.running).toBe(true);                 // новый по-прежнему под управлением
    expect(exits).toEqual([]);                        // окну не сказано «вышел»
    expect(screen).toEqual([]);                       // чужой вывод не лёг на экран
    expect(sessionFiles()).toEqual(files);            // файлы нового целы

    second.data("свой вывод");
    second.exit(0);
    expect(screen).toEqual(["свой вывод"]);
    expect(exits).toEqual([0]);
    expect(agent.running).toBe(false);
    expect(sessionFiles()).toEqual([]);               // свои файлы убраны при выходе
  });

  it("обычный выход по-прежнему доходит до окна и убирает файлы", () => {
    const agent = new Agent();
    const exits: number[] = [];
    agent.start(endpoint, size, () => {}, (c) => exits.push(c));
    expect(sessionFiles().length).toBe(2);
    spawned[0].exit(3);
    expect(exits).toEqual([3]);
    expect(agent.running).toBe(false);
    expect(sessionFiles()).toEqual([]);
  });

  it("повторный запуск при живом процессе отдаёт хвост, а не второй процесс", () => {
    const agent = new Agent();
    agent.start(endpoint, size, () => {}, () => {});
    spawned[0].data("экран до перезагрузки окна");
    const again = agent.start(endpoint, size, () => {}, () => {});
    expect(spawned.length).toBe(1);
    expect(again).toEqual({ ok: true, kind: "claude", backlog: "экран до перезагрузки окна" });
  });

  it("запускает Codex в проекте с временным MCP и без принудительного прокси Claude", () => {
    process.env.DATUM_CODEX = process.execPath;
    process.env.HTTPS_PROXY = "http://127.0.0.1:8080";
    process.env.DATUM_PROXY = "http://127.0.0.1:10808";
    const agent = new Agent();
    const project = { ...endpoint, token: "fixture-token", project: "/tmp/проект с пробелом" };
    expect(agent.start(project, size, () => {}, () => {}, "codex")).toEqual({ ok: true, kind: "codex" });
    const [terminal] = spawned;
    const [handshake] = sessionFiles().map((file) => path.join(runtime, "datum", file));
    expect(sessionFiles()).toHaveLength(1);
    expect(terminal.command).toBe(process.execPath);
    expect(terminal.options.cwd).toBe(project.project);
    expect(terminal.options.env.HTTPS_PROXY).toBe(process.env.HTTPS_PROXY);
    expect(terminal.options.env.DATUM_HANDSHAKE).toBeUndefined();
    expect(terminal.args[0]).toBe("-c");
    expect(terminal.args[1]).toContain("mcp_servers.datum_window=");
    expect(terminal.args[1]).toContain('args=["-m","datum.cli","mcp","--stdio"]');
    expect(terminal.args[1]).toContain(`DATUM_HANDSHAKE=${JSON.stringify(handshake)}`);
    expect(terminal.args.join(" ")).not.toContain(project.token);
    expect(JSON.parse(fs.readFileSync(handshake, "utf8"))).toMatchObject(project);
    expect(agent.kind).toBe("codex");
    expect(agent.start(project, size, () => {}, () => {})).toMatchObject({ ok: true, kind: "codex" });
    expect(spawned).toHaveLength(1);
    terminal.exit(0);
    expect(agent.kind).toBeNull();
    expect(sessionFiles()).toEqual([]);
  });
});
