/**
 * Архив проекта `.datum` со стороны оболочки: найти в аргументах, распаковать.
 *
 * Распаковывает НЕ оболочка, а операция `unpack` — та же, что у командной
 * строки и агента. Охрана от враждебного архива (пути наружу, ссылки,
 * zip-бомба) живёт в одном месте, `datum/doc/archive.py`; вторая распаковка
 * на Node разошлась бы с ней в первой же мелочи.
 *
 * Модуль без импорта electron — так его берёт тест (как `navigation.ts`).
 */
import { execFile } from "node:child_process";
import * as fs from "node:fs";
import * as path from "node:path";

import { pythonExe, repoRoot } from "./paths";

export const ARCHIVE_SUFFIX = ".datum";

/** Первый аргумент — ФАЙЛ `.datum`. Каталог `.datum/` (кеш проекта) — не архив. */
export function archiveFromArgv(argv: string[]): string | null {
  for (const arg of argv.slice(1)) {
    if (arg.startsWith("-") || !arg.toLowerCase().endsWith(ARCHIVE_SUFFIX)) continue;
    const candidate = path.resolve(arg);
    try {
      if (fs.statSync(candidate).isFile()) return candidate;
    } catch {
      /* нет такого файла — не архив */
    }
  }
  return null;
}

export type UnpackResult =
  | { ok: true; project: string; reused: boolean }
  | { ok: false; error: string };

type Runner = typeof execFile;

/** Распаковать архив операцией `unpack` и вернуть каталог проекта. */
export function unpackArchive(file: string, run: Runner = execFile): Promise<UnpackResult> {
  return new Promise((resolve) => {
    run(
      pythonExe,
      ["-m", "datum.cli", "unpack", file, "--json"],
      { cwd: repoRoot, timeout: 300_000, maxBuffer: 1 << 20 },
      (error, stdout, stderr) => {
        let answer: { project?: string; reused?: boolean; error?: string } | null = null;
        try {
          answer = JSON.parse(String(stdout));
        } catch {
          /* ответа нет — ниже скажем, что было */
        }
        if (answer?.project && !answer.error) {
          resolve({ ok: true, project: answer.project, reused: Boolean(answer.reused) });
        } else {
          resolve({
            ok: false,
            error: answer?.error || String(stderr).trim() || error?.message || "архив не распаковался",
          });
        }
      },
    );
  });
}
