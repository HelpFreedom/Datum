/**
 * Проекты как вещи: список для диалога «Файл → Проекты…», недавние, размер
 * кеша, переименование и «новый из этого» (решение владельца 2026-11-22:
 * диалог из меню, запуск по-прежнему открывает пустой проект).
 *
 * Окно не называет путей. Оно получает список с непрозрачным `id` и просит
 * «открой id» — оболочка сверяет его со своим свежим списком. Так страница не
 * может попросить открыть (или переименовать) произвольный каталог: только
 * проект Datum, который оболочка сама нашла или который человек уже открывал.
 *
 * Название и папку правит НЕ оболочка, а `datum rename` / `datum copy`
 * (`tools/ops_project.py`): правила имён и отказ «проект открыт в другом
 * окне» живут в одном месте и покрыты тестами бэкенда. Модуль без импорта
 * electron — его берёт тест.
 */
import { execFile } from "node:child_process";
import { createHash } from "node:crypto";
import * as fs from "node:fs";
import * as path from "node:path";

import { pythonExe, repoRoot } from "./paths";
import { pristine, untitledRoot } from "./untitled";

export interface ProjectEntry {
  /** Непрозрачная метка: по ней окно просит открыть, переименовать, скопировать. */
  id: string;
  /** Название из datum.toml. */
  name: string;
  /** Имя папки. */
  folder: string;
  /** Где лежит — для подписи (дом заменён на ~). */
  where: string;
  /** Последняя правка исходников проекта, мс. */
  modified: number;
  /** Сколько файлов деталей. */
  parts: number;
  /** Открыт в этом окне. */
  current: boolean;
  /** Нетронутый пустой проект «Без названия». */
  empty: boolean;
  /** Есть ли картинка (снимок окна при прошлом уходе с проекта). */
  thumb: boolean;
}

const RECENT_MAX = 30;
/** Что считается правкой проекта: исходники, а не кеш и не выгрузка. */
const SOURCES = ["datum.toml", "assembly.yaml", "placement.yaml", "JOURNAL.md", "NOTES.md"];
const SOURCE_DIRS = ["parts", "params", "checks", "lib"];

export function recentFile(env: NodeJS.ProcessEnv = process.env): string {
  const base = env.XDG_CONFIG_HOME || path.join(env.HOME || "", ".config");
  return path.join(base, "datum", "recent.json");
}

/** Недавно открытые каталоги проектов — те, что ещё существуют. */
export function loadRecent(env: NodeJS.ProcessEnv = process.env): string[] {
  if (env.DATUM_SCENARIO) return [];
  try {
    const data: unknown = JSON.parse(fs.readFileSync(recentFile(env), "utf-8"));
    if (!Array.isArray(data)) return [];
    return data.filter((p): p is string => typeof p === "string" && path.isAbsolute(p))
      .slice(0, RECENT_MAX);
  } catch {
    return [];
  }
}

/** Запомнить открытый проект первым в списке. Под сценарием — ничего не пишется. */
export function rememberRecent(project: string, env: NodeJS.ProcessEnv = process.env): void {
  if (env.DATUM_SCENARIO) return;
  const next = [project, ...loadRecent(env).filter((p) => p !== project)].slice(0, RECENT_MAX);
  writeRecent(next, env);
}

/** Проект переехал (переименована папка): в недавних — новый путь на том же месте. */
export function replaceRecent(from: string, to: string, env: NodeJS.ProcessEnv = process.env): void {
  if (env.DATUM_SCENARIO) return;
  const list = loadRecent(env);
  if (!list.includes(from)) return;
  writeRecent(list.map((p) => (p === from ? to : p)).filter((p, i, a) => a.indexOf(p) === i), env);
}

function writeRecent(list: string[], env: NodeJS.ProcessEnv): void {
  const file = recentFile(env);
  try {
    fs.mkdirSync(path.dirname(file), { recursive: true });
    const tmp = `${file}.${process.pid}.tmp`;
    fs.writeFileSync(tmp, JSON.stringify(list, null, 1));
    fs.renameSync(tmp, file);
  } catch {
    /* недавние — удобство: не записались — список просто короче */
  }
}

/** Название проекта из datum.toml (строка `name = "…"` верхнего уровня). */
export function projectTitle(dir: string): string {
  try {
    const text = fs.readFileSync(path.join(dir, "datum.toml"), "utf-8");
    for (const line of text.split("\n")) {
      if (/^\s*\[/.test(line)) break;                 // дальше — таблицы, не наше
      const m = /^\s*name\s*=\s*("(?:[^"\\]|\\.)*")\s*(?:#.*)?$/.exec(line);
      if (m) return String(JSON.parse(m[1]));
    }
  } catch {
    /* нет файла или он не читается — имя папки */
  }
  return path.basename(dir);
}

export function projectId(dir: string): string {
  return createHash("sha256").update(dir).digest("hex").slice(0, 16);
}

function newest(dir: string): number {
  let best = 0;
  const look = (file: string) => {
    try {
      const m = fs.statSync(file).mtimeMs;
      if (m > best) best = m;
    } catch {
      /* нет — и не надо */
    }
  };
  for (const name of SOURCES) look(path.join(dir, name));
  for (const sub of SOURCE_DIRS) {
    let names: string[] = [];
    try {
      names = fs.readdirSync(path.join(dir, sub));
    } catch {
      continue;
    }
    for (const name of names) {
      if (!name.startsWith(".") && name !== "__pycache__") look(path.join(dir, sub, name));
    }
  }
  return best;
}

function countParts(dir: string): number {
  try {
    return fs.readdirSync(path.join(dir, "parts")).filter((n) => n.endsWith(".py")).length;
  } catch {
    return 0;
  }
}

function tidy(dir: string, home: string): string {
  const parent = path.dirname(dir);
  return home && (parent === home || parent.startsWith(home + path.sep))
    ? `~${parent.slice(home.length)}` : parent;
}

/** Каталоги проектов: «Без названия» и свои, недавние, открытый сейчас. */
export function projectDirs(current: string, recent: string[], root: string = untitledRoot()): string[] {
  const found: string[] = [];
  const add = (dir: string) => {
    let real: string;
    try {
      real = fs.realpathSync(dir);
      if (!fs.statSync(path.join(real, "datum.toml")).isFile()) return;
    } catch {
      return;                                         // нет каталога или это не проект
    }
    if (!found.includes(real)) found.push(real);
  };
  if (current) add(current);
  for (const dir of recent) add(dir);
  let names: string[] = [];
  try {
    names = fs.readdirSync(root);
  } catch {
    names = [];
  }
  for (const name of names) {
    if (!name.startsWith(".")) add(path.join(root, name));
  }
  return found;
}

/** Список для диалога: открытый — первым, дальше по давности правки. */
export function listProjects(
  current: string, recent: string[], root: string = untitledRoot(),
  env: NodeJS.ProcessEnv = process.env,
): { entries: ProjectEntry[]; paths: Map<string, string> } {
  let here = "";
  try {
    here = current ? fs.realpathSync(current) : "";
  } catch {
    here = current;
  }
  const paths = new Map<string, string>();
  const entries: ProjectEntry[] = [];
  for (const dir of projectDirs(current, recent, root)) {
    const id = projectId(dir);
    paths.set(id, dir);
    entries.push({
      id,
      name: projectTitle(dir),
      folder: path.basename(dir),
      where: tidy(dir, env.HOME || ""),
      modified: Math.round(newest(dir)),
      parts: countParts(dir),
      current: dir === here,
      empty: pristine(dir),
      thumb: fs.existsSync(thumbFile(dir)),
    });
  }
  entries.sort((a, b) => Number(b.current) - Number(a.current) || b.modified - a.modified);
  return { entries, paths };
}

export function thumbFile(dir: string): string {
  return path.join(dir, ".datum", "thumb.png");
}

/** Размер кеша проекта (`.datum/cache`), байт. Ссылки не проходятся. */
export async function cacheBytes(dir: string): Promise<number> {
  let total = 0;
  const walk = async (folder: string): Promise<void> => {
    let items: fs.Dirent[];
    try {
      items = await fs.promises.readdir(folder, { withFileTypes: true });
    } catch {
      return;
    }
    for (const item of items) {
      const full = path.join(folder, item.name);
      if (item.isDirectory()) await walk(full);
      else if (item.isFile()) {
        try {
          total += (await fs.promises.stat(full)).size;
        } catch {
          /* файл исчез между чтением каталога и stat — кеш живой */
        }
      }
    }
  };
  await walk(path.join(dir, ".datum", "cache"));
  return total;
}

/** Очистить кеш НЕоткрытого проекта: он пересобирается при следующем открытии. */
export async function clearCache(dir: string): Promise<void> {
  await fs.promises.rm(path.join(dir, ".datum", "cache"), { recursive: true, force: true });
}

export type CliResult = { ok: true; path: string; name: string } | { ok: false; error: string };
type Runner = typeof execFile;

/**
 * `datum rename|copy --json [--folder] -- <проект> <название>` — правка
 * названия и папки бэкендом. Сервер проекта к этому моменту обязан быть
 * остановлен: операция сама откажет, если проект кем-то открыт.
 */
export function projectCli(
  command: "rename" | "copy", dir: string, name: string, run: Runner = execFile,
): Promise<CliResult> {
  // Проект и название — позиционные; «--» перед ними, чтобы название,
  // начинающееся с дефиса, не разобралось как ключ.
  const args = ["-m", "datum.cli", command, "--json"];
  if (command === "rename") args.push("--folder");
  args.push("--", dir, name);
  return new Promise((resolve) => {
    run(pythonExe, args, { cwd: repoRoot, timeout: 120_000, maxBuffer: 1 << 20 },
      (error, stdout, stderr) => {
        let answer: { error?: unknown; path?: unknown; name?: unknown } = {};
        try {
          answer = JSON.parse(String(stdout)) as typeof answer;
        } catch {
          /* не JSON — причина ниже */
        }
        if (typeof answer.error === "string" && answer.error) {
          resolve({ ok: false, error: answer.error });
        } else if (!error && typeof answer.path === "string" && answer.path) {
          resolve({ ok: true, path: answer.path, name: String(answer.name ?? name) });
        } else {
          resolve({
            ok: false,
            error: String(stderr).trim() || error?.message || "операция не ответила",
          });
        }
      });
  });
}
