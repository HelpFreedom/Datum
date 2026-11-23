/**
 * Дочерний Python-сервер: запуск, рукопожатие, остановка.
 *
 * Порт и токен приходят ФАЙЛОМ, а не аргументами командной строки: аргументы
 * видны в `ps` любому процессу на машине, а токен — это ключ от сервера. Файл
 * создаётся с правами 0600 и удаляется сразу после чтения.
 *
 * Сервер поднимается на СЛУЧАЙНОМ порту петлевого интерфейса, поэтому адрес
 * заранее неизвестен и угадать его снаружи нельзя.
 */
import { type ChildProcess, spawn } from "node:child_process";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";

import { pythonExe, repoRoot, uiDist } from "./paths";

export interface Handshake {
  port: number;
  token: string;
  pid: number;
  project: string;
}

const READY = "DATUM_READY";
const START_TIMEOUT_MS = 30_000;

export class Backend {
  private child: ChildProcess | null = null;
  private handshakePath = "";
  private stderr = "";
  private line = "";
  /**
   * Сервер умер САМ (не по `stop`) уже после готовности. Прежде этого никто не
   * ловил: слушатель выхода жил только на время старта, и окно вечно стучалось
   * в мёртвый порт с надписью «нет связи» (аудит 2026-11-21). Память ест
   * именно сервер, так что убивает ядро чаще всего его.
   */
  onDeath: ((why: string) => void) | null = null;

  get running(): boolean {
    return this.child !== null && this.child.exitCode === null;
  }

  async start(project: string): Promise<Handshake> {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "datum-shell-"));
    this.handshakePath = path.join(dir, "handshake.json");

    // Окружение потомка чистится от прокси: сервер живёт на петле, и ходить к
    // самому себе через прокси он не должен. Остальное наследуется — venv,
    // локаль, DISPLAY.
    const env = { ...process.env };
    delete env.HTTP_PROXY;
    delete env.HTTPS_PROXY;
    delete env.http_proxy;
    delete env.https_proxy;
    env.NO_PROXY = "127.0.0.1,localhost,::1";

    this.child = spawn(
      pythonExe,
      ["-m", "datum.cli", "serve", project,
       "--handshake", this.handshakePath, "--ui", uiDist,
       // Сервер умрёт сам, если оболочка исчезнет. Полагаться на уборку
       // здесь нельзя: оболочку могут убить сигналом, и тогда обработчики
       // выхода не выполняются вовсе — после прогонов накопилось 27
       // осиротевших серверов, каждый со своим портом.
       "--parent-pid", String(process.pid)],
      { cwd: repoRoot, env, stdio: ["ignore", "pipe", "pipe"] },
    );

    this.child.stderr?.on("data", (chunk: Buffer) => {
      const text = chunk.toString();
      this.stderr += text;
      if (this.stderr.length > 16_000) this.stderr = this.stderr.slice(-16_000);
      // И в журнал, построчно: трассировка работающего сервера прежде жила
      // только в этом хвосте и пропадала вместе с процессом.
      this.line += text;
      const lines = this.line.split("\n");
      this.line = lines.pop() ?? "";
      for (const one of lines) if (one.trim()) console.error(`  сервер: ${one}`);
    });

    const ready = await this.waitForReady();
    if (!ready) {
      const why = this.stderr.trim() || "сервер не сообщил о готовности";
      this.stop();
      throw new Error(`Python-сервер не запустился.\n\n${why}`);
    }

    const child = this.child;
    child?.on("exit", (code, signal) => {
      // `stop()` обнуляет `this.child` ДО сигнала: свой выход смертью не считается.
      if (this.child !== child) return;
      this.child = null;
      const tail = this.stderr.trim().split("\n").slice(-3).join(" / ");
      this.onDeath?.(`код ${code ?? "нет"}${signal ? `, сигнал ${signal}` : ""}`
        + (tail ? `: ${tail}` : ""));
    });

    const raw = fs.readFileSync(this.handshakePath, "utf8");
    fs.rmSync(dir, { recursive: true, force: true });   // файл одноразовый
    return JSON.parse(raw) as Handshake;
  }

  /** Ждём маркер в stdout, а не появление файла: файл может быть ещё недописан. */
  private waitForReady(): Promise<boolean> {
    return new Promise((resolve) => {
      let out = "";
      let settled = false;
      const finish = (value: boolean) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        resolve(value);
      };
      const timer = setTimeout(() => finish(false), START_TIMEOUT_MS);

      this.child?.stdout?.on("data", (chunk: Buffer) => {
        out += chunk.toString();
        if (out.includes(READY)) finish(fs.existsSync(this.handshakePath));
      });
      this.child?.on("exit", () => finish(false));
      this.child?.on("error", (e) => {
        this.stderr += `\n${e.message}`;
        finish(false);
      });
    });
  }

  /**
   * Остановить и ДОЖДАТЬСЯ выхода: каталог проекта собираются переименовать, и
   * сервер не должен в этот момент ни писать в него, ни числиться живым в
   * объявлениях (операция переименования откажет, пока проект кем-то открыт).
   */
  stopAndWait(timeoutMs = 6000): Promise<void> {
    const child = this.child;
    if (!child || child.exitCode !== null) {
      this.stop();
      return Promise.resolve();
    }
    return new Promise((resolve) => {
      const timer = setTimeout(resolve, timeoutMs);
      child.once("exit", () => {
        clearTimeout(timer);
        resolve();
      });
      this.stop();
    });
  }

  stop(): void {
    const child = this.child;
    this.child = null;
    if (!child || child.exitCode !== null) return;
    child.kill("SIGTERM");
    // Сервер сам снимает незавершённые задания, но если он завис — добиваем,
    // иначе процесс переживёт окно и останется сиротой.
    setTimeout(() => {
      if (child.exitCode === null) child.kill("SIGKILL");
    }, 3000);
  }
}
