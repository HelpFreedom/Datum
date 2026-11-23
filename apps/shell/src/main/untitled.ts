/**
 * Окно, запущенное без проекта, открывает ПУСТОЙ проект «Без названия» (решение
 * владельца) — а не последний открытый и не пример: новый запуск — новая
 * работа, а прежнюю открывают «Файл → Открыть проект…» или `datum <каталог>`.
 *
 * Проект — обычная папка в `$XDG_DATA_HOME/datum/untitled/untitled-<время>`;
 * сохраняют его «Сохранить как…» в архив `.datum`. Чтобы от каждого запуска
 * не оставалось по пустой папке, нетронутый пустой проект берётся снова.
 * «Нетронутый» — строго: нет деталей, импорта и версий, ни разу не сохранялся,
 * и файлы не правились после создания. Сомнение — новый проект: взять чужую
 * начатую работу за пустую хуже, чем завести лишнюю папку.
 *
 * Создаёт проект НЕ оболочка, а `datum init` — шаблон живёт в одном
 * месте, `datum/doc/scaffold.py`. Модуль без импорта electron — его берёт тест.
 */
import { execFile } from "node:child_process";
import * as fs from "node:fs";
import * as path from "node:path";

import { pythonExe, repoRoot } from "./paths";

export const UNTITLED_NAME = "Без названия";
const PREFIX = "untitled-";

/** Где живут проекты «Без названия» (XDG). */
export function untitledRoot(env: NodeJS.ProcessEnv = process.env): string {
  const base = env.XDG_DATA_HOME || path.join(env.HOME || "", ".local", "share");
  return path.join(base, "datum", "untitled");
}

/** Имя папки: ASCII, по времени — новее идёт позже. */
export function untitledName(now: Date): string {
  const p = (n: number, w = 2) => String(n).padStart(w, "0");
  return `${PREFIX}${now.getFullYear()}${p(now.getMonth() + 1)}${p(now.getDate())}-`
    + `${p(now.getHours())}${p(now.getMinutes())}${p(now.getSeconds())}`;
}

function entries(dir: string): string[] {
  try {
    return fs.readdirSync(dir);
  } catch {
    return [];
  }
}

/** Проект пуст и не тронут с создания — его можно открыть вместо нового. */
export function pristine(dir: string): boolean {
  try {
    const born = fs.statSync(path.join(dir, "datum.toml")).mtimeMs;
    if (entries(path.join(dir, "parts")).length) return false;
    if (entries(path.join(dir, "imported")).length) return false;
    if (entries(path.join(dir, "snapshots")).length) return false;
    if (entries(path.join(dir, "lib")).length) return false;
    if (fs.existsSync(path.join(dir, ".datum", "archive.json"))) return false;   // сохранялся
    for (const rel of ["params/base.yaml", "checks/checks.yaml", "NOTES.md", "datum.toml"]) {
      if (fs.statSync(path.join(dir, rel)).mtimeMs > born + 5_000) return false;
    }
    for (const rel of ["assembly.yaml", "placement.yaml", "checks/custom.py"]) {
      if (fs.existsSync(path.join(dir, rel))) return false;
    }
    return true;
  } catch {
    return false;                                   // чего-то нет — не наш пустой проект
  }
}

/** Самый новый нетронутый пустой проект, если есть. */
export function findPristine(root: string = untitledRoot()): string | null {
  const names = entries(root).filter((n) => n.startsWith(PREFIX)).sort().reverse();
  for (const name of names) {
    const dir = path.join(root, name);
    if (pristine(dir)) return dir;
  }
  return null;
}

export type UntitledResult = { ok: true; project: string; reused: boolean } | { ok: false; error: string };

type Runner = typeof execFile;

/** Пустой проект для окна без аргументов: нетронутый прежний или новый. */
export function untitledProject(
  run: Runner = execFile,
  root: string = untitledRoot(),
  now: Date = new Date(),
): Promise<UntitledResult> {
  const found = findPristine(root);
  if (found) return Promise.resolve({ ok: true, project: found, reused: true });
  let dir = path.join(root, untitledName(now));
  for (let i = 2; fs.existsSync(dir); i += 1) dir = path.join(root, `${untitledName(now)}-${i}`);
  return new Promise((resolve) => {
    run(
      pythonExe,
      ["-m", "datum.cli", "init", "--name", UNTITLED_NAME, dir],
      { cwd: repoRoot, timeout: 60_000, maxBuffer: 1 << 20 },
      (error, _stdout, stderr) => {
        if (!error && fs.existsSync(path.join(dir, "datum.toml"))) {
          resolve({ ok: true, project: dir, reused: false });
        } else {
          resolve({ ok: false, error: String(stderr).trim() || error?.message || "пустой проект не создался" });
        }
      },
    );
  });
}
