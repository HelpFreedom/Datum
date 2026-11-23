/**
 * Встроенный системный Claude Code.
 *
 * Datum НЕ ПИШЕТ своего агента. В окне — терминал, в котором запущен ваш
 * системный `claude`. Всё, что при нём, остаётся вашим и работает как обычно:
 * авторизация, выбор модели, настройки, навыки, плагины, хуки, slash-команды,
 * механизм разрешений, история сессий. Datum в это не вмешивается.
 *
 * Подключений два. Временный `--mcp-config`, указывающий на
 * `datum mcp --stdio`, и `--plugin-dir` с навыком Datum (`plugin.ts`) — где
 * бы ни лежал проект. Тот, увидев путь к файлу рукопожатия, становится тонким
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

import { pythonExe } from "./paths";
import { Backlog } from "./backlog";
import { agentArgs } from "./plugin";

export interface AgentEndpoint {
  port: number;
  token: string;
  project: string;
}

/** Куда класть временные файлы сеанса: как у Python-стороны. */
function runtimeDir(): string {
  const base = process.env.XDG_RUNTIME_DIR || os.tmpdir();
  const dir = path.join(base, "datum");
  fs.mkdirSync(dir, { recursive: true, mode: 0o700 });
  return dir;
}

/** Найти системный `claude`. Свой мы не носим и не подменяем. */
export function findClaude(): string | null {
  const explicit = process.env.DATUM_CLAUDE;
  if (explicit && fs.existsSync(explicit)) return explicit;
  const candidates = [
    path.join(os.homedir(), ".local", "bin", "claude"),
    "/usr/local/bin/claude",
    "/usr/bin/claude",
  ];
  for (const candidate of candidates) {
    if (fs.existsSync(candidate)) return candidate;
  }
  for (const dir of (process.env.PATH ?? "").split(":")) {
    const candidate = path.join(dir, "claude");
    if (dir && fs.existsSync(candidate)) return candidate;
  }
  return null;
}

export type StartResult = { ok: true; backlog?: string } | { ok: false; error: string };

export class Agent {
  private pty: IPty | null = null;
  private files: string[] = [];
  private dir = "";
  private readonly backlog = new Backlog();
  /** Когда запущен нынешний `claude`, мс; 0 — не запущен. */
  private started = 0;

  get running(): boolean {
    return this.pty !== null;
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
  private prepare(endpoint: AgentEndpoint): string {
    this.dir = runtimeDir();
    const tag = `${process.pid}-${Date.now().toString(36)}`;

    const handshake = path.join(this.dir, `agent-${tag}.json`);
    fs.writeFileSync(
      handshake,
      JSON.stringify({ port: endpoint.port, token: endpoint.token,
                       pid: process.pid, project: endpoint.project }),
      { mode: 0o600 },
    );

    const config = path.join(this.dir, `mcp-${tag}.json`);
    fs.writeFileSync(
      config,
      JSON.stringify(
        {
          mcpServers: {
            datum: {
              command: pythonExe,
              args: ["-m", "datum.cli", "mcp", "--stdio"],
              env: { DATUM_HANDSHAKE: handshake },
            },
          },
        },
        null,
        2,
      ),
      { mode: 0o600 },
    );

    this.files = [handshake, config];
    return config;
  }

  start(
    endpoint: AgentEndpoint,
    size: { cols: number; rows: number },
    onData: (chunk: string) => void,
    onExit: (code: number) => void,
  ): StartResult {
    // Уже работает — окно перезагрузилось (упал процесс отрисовки): отдаём
    // хвост вывода, и страница показывает экран как был. Данные дальше идут
    // тем же каналом ПОСЛЕ этого ответа, так что хвост и поток не расходятся.
    if (this.pty) {
      this.resize(size.cols, size.rows);
      return { ok: true, backlog: this.backlog.text() };
    }

    const claude = findClaude();
    if (!claude) {
      return {
        ok: false,
        error:
          "системный claude не найден. Datum не носит своего: поставьте Claude Code " +
          "или укажите путь переменной DATUM_CLAUDE",
      };
    }

    const config = this.prepare(endpoint);
    const launch = agentArgs(config);
    if (!launch.ok) {
      // Файлы сеанса (рукопожатие с токеном) уже записаны — убрать: прежде
      // они оставались, а следующий `prepare` забывал о них.
      this.cleanup();
      return launch;
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
      HTTP_PROXY: proxy,
      HTTPS_PROXY: proxy,
      http_proxy: proxy,
      https_proxy: proxy,
      NO_PROXY: "127.0.0.1,localhost,::1",
      no_proxy: "127.0.0.1,localhost,::1",
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
      pty = ptySpawn(claude, launch.args, {
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
        this.files = [];
        onExit(exitCode);
      }
    });
    return { ok: true };
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
