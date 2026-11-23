/**
 * Сколько контекста занято у встроенного агента — число для строки в доке.
 *
 * Разбор сессий (аудит 2026-11-21): 71 % расхода — повторное чтение контекста.
 * Каждый ход перечитывает его целиком, и ход при 400 тыс. токенов стоит как
 * 40 тыс. свежих входных, что бы он ни делал. Человеку это число не было видно
 * нигде, а решает оно всё: закончили этап — записали решения и `/clear`.
 *
 * Число берётся из журнала сессии Claude Code (`usage` последнего хода
 * главного агента): `~/.claude/projects/<путь проекта, где всё, кроме латиницы
 * и цифр, — дефис>/<сессия>.jsonl`. Читается только хвост файла и только
 * числа расхода — текст разговора в окно не идёт. Формат журнала — не наш:
 * не нашли или не разобрали — числа просто нет. Модуль без импорта electron.
 */
import * as fs from "node:fs";
import * as path from "node:path";

export interface ContextSize {
  /** Токенов в контексте на последнем ходе главного агента. */
  tokens: number;
  /** Когда был этот ход, мс. */
  at: number;
}

/** Сколько с конца журнала читается: последний ход с расходом — в хвосте. */
const TAIL_BYTES = 512 * 1024;

/** Каталог сессий Claude Code для проекта. */
export function sessionDir(project: string, env: NodeJS.ProcessEnv = process.env): string {
  const base = env.CLAUDE_CONFIG_DIR || path.join(env.HOME || "", ".claude");
  return path.join(base, "projects", project.replace(/[^A-Za-z0-9]/g, "-"));
}

/** Расход последнего хода главного агента из хвоста журнала (строки JSON). */
export function lastUsage(text: string): ContextSize | null {
  const lines = text.split("\n");
  for (let i = lines.length - 1; i >= 0; i -= 1) {
    const line = lines[i];
    if (!line.includes('"usage"')) continue;
    let record: unknown;
    try {
      record = JSON.parse(line);
    } catch {
      continue;                                   // первая строка хвоста — обрезок
    }
    if (!record || typeof record !== "object") continue;
    const r = record as {
      type?: unknown; isSidechain?: unknown; timestamp?: unknown;
      message?: { usage?: Record<string, unknown> };
    };
    // Субагент (критик) ведёт свой контекст — он не про главный разговор.
    if (r.type !== "assistant" || r.isSidechain === true) continue;
    const usage = r.message?.usage;
    if (!usage || typeof usage !== "object") continue;
    const part = (key: string) => {
      const v = usage[key];
      return typeof v === "number" && Number.isFinite(v) && v > 0 ? v : 0;
    };
    const tokens = part("input_tokens") + part("cache_read_input_tokens")
      + part("cache_creation_input_tokens");
    if (!(tokens > 0)) continue;
    const at = typeof r.timestamp === "string" ? Date.parse(r.timestamp) : NaN;
    return { tokens: Math.round(tokens), at: Number.isFinite(at) ? at : 0 };
  }
  return null;
}

/**
 * Размер контекста идущей сессии проекта. `since` — когда запущен агент:
 * журнал, не тронутый с тех пор, — прошлый разговор, а не этот.
 */
export function contextSize(
  project: string, since = 0, env: NodeJS.ProcessEnv = process.env,
): ContextSize | null {
  const dir = sessionDir(project, env);
  let newest = "";
  let newestTime = 0;
  let names: string[];
  try {
    names = fs.readdirSync(dir);
  } catch {
    return null;
  }
  for (const name of names) {
    if (!name.endsWith(".jsonl")) continue;
    try {
      const m = fs.statSync(path.join(dir, name)).mtimeMs;
      if (m > newestTime) {
        newestTime = m;
        newest = path.join(dir, name);
      }
    } catch {
      /* файл исчез — журнал живой */
    }
  }
  if (!newest || newestTime < since) return null;
  try {
    const size = fs.statSync(newest).size;
    const fd = fs.openSync(newest, "r");
    try {
      const length = Math.min(size, TAIL_BYTES);
      const buffer = Buffer.alloc(length);
      fs.readSync(fd, buffer, 0, length, size - length);
      return lastUsage(buffer.toString("utf-8"));
    } finally {
      fs.closeSync(fd);
    }
  } catch {
    return null;
  }
}
