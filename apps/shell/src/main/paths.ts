/** Где что лежит. Одно место, чтобы пути не разъезжались по файлам. */
import * as fs from "node:fs";
import * as path from "node:path";

/** Корень репозитория: apps/shell/dist/main/… → четыре уровня вверх. */
export const repoRoot = path.resolve(__dirname, "..", "..", "..", "..");

export const pythonExe = process.env.DATUM_PYTHON
  ? path.resolve(process.env.DATUM_PYTHON)
  : path.join(repoRoot, ".venv", "bin", "python");

export const uiDist = path.join(repoRoot, "apps", "ui", "dist");

function isProject(dir: string): boolean {
  try {
    return fs.existsSync(path.join(dir, "datum.toml"));
  } catch {
    return false;
  }
}

/**
 * Проект, названный в аргументах, или null — тогда окно откроет пустой проект
 * «Без названия» (`untitled.ts`), а не последний открытый: так решил владелец.
 */
export function projectFromArgv(argv: string[]): string | null {
  // Electron отдаёт свои аргументы вперемешку со своими же ключами; берём
  // первый существующий каталог, а не «последний аргумент»: иначе ключ вроде
  // --enable-features превратился бы в путь к проекту.
  for (const arg of argv.slice(1)) {
    if (arg.startsWith("-")) continue;
    const candidate = path.resolve(arg);
    if (candidate !== __filename && isProject(candidate)) {
      return candidate;
    }
  }
  return null;
}
