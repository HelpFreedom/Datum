/**
 * Встроенные системные Claude Code и Codex.
 *
 * Datum запускает системный агент в терминале проекта. Его собственные
 * настройки и сессии работают как обычно:
 * авторизация, выбор модели, настройки, навыки, плагины, хуки, slash-команды,
 * механизм разрешений, история сессий. Datum в это не вмешивается.
 *
 * Claude Code получает временный `--mcp-config` и плагин Datum. Codex получает
 * настройку MCP через `-c` на время этого процесса. В обоих случаях MCP-клиент,
 * увидев путь к файлу рукопожатия, становится тонким
 * клиентом РАБОТАЮЩЕГО сервера — поэтому всё, что агент делает, идёт тем же
 * путём, что кнопка в окне, и видно немедленно.
 *
 * Глобальные настройки MCP пользователя не трогаются: конфиг временный и лежит
 * в рантайм-каталоге.
 */
import { spawn as ptySpawn, type IPty } from "node-pty";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { randomUUID } from "node:crypto";

import { pythonExe } from "./paths";
import { Backlog } from "./backlog";
import { agentArgs } from "./plugin";

export interface AgentEndpoint {
  port: number;
  token: string;
  project: string;
}

export type AgentKind = "claude" | "codex";
const AGENTS = {
  claude: { label: "Claude Code", override: "DATUM_CLAUDE" },
  codex: { label: "Codex", override: "DATUM_CODEX" },
} as const;

/** Куда класть временные файлы сеанса: как у Python-стороны. */
function runtimeDir(): string {
  const base = process.env.XDG_RUNTIME_DIR || os.tmpdir();
  const dir = path.join(base, "datum");
  fs.mkdirSync(dir, { recursive: true, mode: 0o700 });
  return dir;
}

/** Найти системный агент или явно заданный путь. */
function findAgent(kind: AgentKind): string | null {
  const explicit = process.env[AGENTS[kind].override];
  if (explicit && fs.existsSync(explicit)) return explicit;
  const candidates = [
    path.join(os.homedir(), ".local", "bin", kind),
    `/usr/local/bin/${kind}`,
    `/usr/bin/${kind}`,
  ];
  for (const candidate of candidates) {
    if (fs.existsSync(candidate)) return candidate;
  }
  for (const dir of (process.env.PATH ?? "").split(path.delimiter)) {
    const candidate = path.join(dir, kind);
    if (dir && fs.existsSync(candidate)) return candidate;
  }
  return null;
}

export type StartResult = { ok: true; kind: AgentKind; backlog?: string } | { ok: false; error: string };

export class Agent {
  private pty: IPty | null = null;
  private files: string[] = [];
  private provider: AgentKind | null = null;
  private readonly backlog = new Backlog();
  /** Когда запущен нынешний `claude`, мс; 0 — не запущен. */
  private started = 0;

  get running(): boolean {
    return this.pty !== null;
  }

  get kind(): AgentKind | null {
    return this.provider;
  }

  /** Время запуска идущего `claude` — чтобы не принять прошлую сессию за эту. */
  get startedAt(): number {
    return this.pty ? this.started : 0;
  }

  /**
   * Файлы сеанса: рукопожатие для MCP-клиента и конфиг для `claude`.
   *
   * Токен кладётся в ФАЙЛ с правами 0600, а не в окружение: окружение видно в
   * /proc/<pid>/environ любому процессу пользователя. В окружении — только
   * путь.
   */
  private prepare(endpoint: AgentEndpoint, kind: AgentKind): ReturnType<typeof agentArgs> {
    const dir = runtimeDir();
    const tag = randomUUID();

    const handshake = path.join(dir, `agent-${tag}.json`);
    this.files = [handshake];
    fs.writeFileSync(
      handshake,
      JSON.stringify({ port: endpoint.port, token: endpoint.token,
                       pid: process.pid, project: endpoint.project }),
      { mode: 0o600, flag: "wx" },
    );

    const args = ["-m", "datum.cli", "mcp", "--stdio"];
    if (kind === "codex") {
      // Отдельное имя не смешивает stdio с пользовательским сервером `datum`.
      const settings = `mcp_servers.datum_window={command=${JSON.stringify(pythonExe)},` +
        `args=${JSON.stringify(args)},env={DATUM_HANDSHAKE=${JSON.stringify(handshake)}},` +
        "startup_timeout_sec=30,required=true}";
      return { ok: true, args: ["-c", settings] };
    }

    const config = path.join(dir, `mcp-${tag}.json`);
    this.files.push(config);
    fs.writeFileSync(
      config,
      JSON.stringify(
        {
          mcpServers: {
            datum: {
              command: pythonExe,
              args,
              env: { DATUM_HANDSHAKE: handshake },
            },
          },
        },
        null,
        2,
      ),
      { mode: 0o600, flag: "wx" },
    );

    return agentArgs(config);
  }

  start(
    endpoint: AgentEndpoint,
    size: { cols: number; rows: number },
    onData: (chunk: string) => void,
    onExit: (code: number) => void,
    kind: AgentKind = "claude",
  ): StartResult {
    // Уже работает — окно перезагрузилось (упал процесс отрисовки): отдаём
    // хвост вывода, и страница показывает экран как был. Данные дальше идут
    // тем же каналом ПОСЛЕ этого ответа, так что хвост и поток не расходятся.
    if (this.pty && this.provider) {
      this.resize(size.cols, size.rows);
      return { ok: true, kind: this.provider, backlog: this.backlog.text() };
    }

    const executable = findAgent(kind);
    if (!executable) {
      return {
        ok: false,
        error: `Системный ${kind} не найден. Установите ${AGENTS[kind].label} ` +
          `в той же системе, где работает Datum, или укажите путь в ${AGENTS[kind].override}.`,
      };
    }

    // Прокси: требование пользователя — встроенный claude ходит через
    // 127.0.0.1:10808. Замер показал, что порт принимает и HTTP, и SOCKS5, но
    // Node понимает только HTTP-форму.
    //
    // NO_PROXY обязателен: без него MCP-клиент пошёл бы к НАШЕМУ ЖЕ бэкенду
    // через прокси, и ничего бы не работало.
    const proxy = process.env.DATUM_PROXY ?? "http://127.0.0.1:10808";
    const env: Record<string, string> = {
      ...(process.env as Record<string, string>),
      TERM: "xterm-256color",
      ...(kind === "claude" ? {
        HTTP_PROXY: proxy, HTTPS_PROXY: proxy, http_proxy: proxy, https_proxy: proxy,
        NO_PROXY: "127.0.0.1,localhost,::1", no_proxy: "127.0.0.1,localhost,::1",
      } : {}),
    };
    delete env.DATUM_HANDSHAKE;   // его получает только MCP-клиент, из конфига

    // Метки сессии, ЗАПУСТИВШЕЙ Datum. Если Datum открыт из другого Claude
    // Code, встроенный агент унаследовал бы их и счёл себя потомком чужой
    // сессии: делил бы сокет обмена сообщениями и токен, а запись истории
    // отключалась бы с предупреждением про CLAUDE_CODE_CHILD_SESSION.
    //
    // Это именно дескрипторы сеанса, а не настройка: настройки Claude Code
    // живут в ~/.claude/settings.json, и их это не касается. Пользовательские
    // ANTHROPIC_* под правило не попадают и наследуются как прежде.
    for (const name of Object.keys(env)) {
      if (name.startsWith("CLAUDE_CODE_") ||
          name === "CLAUDECODE" ||
          name === "CLAUDE_PID" ||
          name === "CLAUDE_EFFORT" ||
          name === "CLAUDE_BRIDGE_SESSION") {
        delete env[name];
      }
    }

    let pty: IPty;
    try {
      const launch = this.prepare(endpoint, kind);
      if (!launch.ok) {
        this.cleanup();
        return launch;
      }
      pty = ptySpawn(executable, launch.args, {
        name: "xterm-256color",
        cols: Math.max(20, size.cols),
        rows: Math.max(5, size.rows),
        // cwd — каталог проекта, поэтому Read/Edit/Grep у агента естественно
        // попадают в parts/, params/ и NOTES.md.
        cwd: endpoint.project,
        env,
      });
    } catch (e) {
      this.cleanup();
      return { ok: false, error: e instanceof Error ? e.message : String(e) };
    }
    this.pty = pty;
    this.provider = kind;
    this.started = Date.now();

    // Обработчики привязаны к СВОЕМУ процессу и своим файлам (идея из Kadr).
    // `stop` не ждёт смерти процесса: убитый claude может выйти уже после
    // того, как запущен следующий, — и прежде его onExit обнулял ссылку на
    // НОВЫЙ процесс (тот оставался без управления), стирал его файлы и
    // сообщал окну «агент вышел», а его поздний вывод ложился в чужой экран.
    const files = [...this.files];
    this.backlog.clear();
    pty.onData((chunk) => {
      if (this.pty !== pty) return;
      this.backlog.push(chunk);
      onData(chunk);
    });
    pty.onExit(({ exitCode }) => {
      removeFiles(files);
      if (this.pty === pty) {
        this.pty = null;
        this.provider = null;
        this.files = [];
        onExit(exitCode);
      }
    });
    return { ok: true, kind };
  }

  write(data: string): void {
    this.pty?.write(data);
  }

  resize(cols: number, rows: number): void {
    try {
      this.pty?.resize(Math.max(20, cols), Math.max(5, rows));
    } catch {
      /* терминал мог уже закрыться — не беда */
    }
  }

  stop(): void {
    const pty = this.pty;
    this.pty = null;
    this.provider = null;
    try {
      pty?.kill();
    } catch {
      /* уже мёртв */
    }
    this.cleanup();
  }

  private cleanup(): void {
    removeFiles(this.files);
    this.files = [];
  }
}

/** Удалить файлы сеанса; не вышло — пусть останутся, чем уронить выход. */
function removeFiles(files: string[]): void {
  for (const file of files) {
    try {
      fs.rmSync(file, { force: true });
    } catch {
      /* пусть останется */
    }
  }
}
