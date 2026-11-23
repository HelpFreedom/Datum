/**
 * Навык Datum для встроенного Claude Code — плагином, по `--plugin-dir`.
 *
 * Прежде навык лежал в `.claude/skills/` репозитория, и Claude Code находил
 * его, поднимаясь по родительским каталогам: у проектов ВНЕ репозитория
 * (пустой «Без названия» в ~/.local/share, любой свой проект) навыка не было —
 * агент знал инструменты, но не правила работы. Плагин из репозитория
 * подключается к каждому запуску, где бы ни лежал проект, и всегда той версии,
 * что у самого Datum: в проекты ничего не копируется, и копиям нечему устаревать.
 *
 * Навык без плагина агент не получает — это отказ запуска, а не молчание:
 * агент без правил работает, но работает хуже, и заметить это не по чему.
 *
 * Модуль без electron и node-pty — его берёт тест.
 */
import * as fs from "node:fs";
import * as path from "node:path";

import { repoRoot } from "./paths";

export const pluginDir = path.join(repoRoot, "plugin");

/** Навыки плагина — имена каталогов с SKILL.md. */
export function pluginSkills(dir: string = pluginDir): string[] {
  try {
    return fs.readdirSync(path.join(dir, "skills"))
      .filter((name) => fs.existsSync(path.join(dir, "skills", name, "SKILL.md")))
      .sort();
  } catch {
    return [];
  }
}

/** Аргументы `claude` для окна — или отказ, если плагина нет. */
export function agentArgs(config: string, dir: string = pluginDir): { ok: true; args: string[] } | { ok: false; error: string } {
  if (!fs.existsSync(path.join(dir, ".claude-plugin", "plugin.json")) || !pluginSkills(dir).includes("datum")) {
    return {
      ok: false,
      error: `нет плагина с навыком Datum: ${dir} (нужны .claude-plugin/plugin.json и skills/datum/SKILL.md) — ` +
        "без него агент не знал бы правил работы с проектом",
    };
  }
  return { ok: true, args: ["--mcp-config", config, "--plugin-dir", dir] };
}
