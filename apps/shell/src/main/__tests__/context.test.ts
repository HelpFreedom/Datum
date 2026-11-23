/**
 * Размер контекста агента — из хвоста журнала сессии Claude Code. Формат
 * журнала чужой: всё, что не разобралось, — «числа нет», а не ошибка.
 */
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";

import { describe, expect, it } from "vitest";

import { contextSize, lastUsage, sessionDir } from "../context";

const turn = (usage: Record<string, number>, extra: Record<string, unknown> = {}) =>
  JSON.stringify({ type: "assistant", isSidechain: false, timestamp: "2026-11-22T10:00:00.000Z",
    message: { usage }, ...extra });

describe("каталог сессий", () => {
  it("всё, кроме латиницы и цифр, — дефис, как у Claude Code", () => {
    const env = { HOME: "/home/u" };
    expect(sessionDir("/home/u/.local/share/datum/untitled/untitled-20261031-202521", env))
      .toBe("/home/u/.claude/projects/-home-u--local-share-datum-untitled-untitled-20261031-202521");
    expect(sessionDir("/home/u/.local/share/datum/untitled/рука-v3", env))
      .toBe("/home/u/.claude/projects/-home-u--local-share-datum-untitled------v3");
    expect(sessionDir("/p", { HOME: "/h", CLAUDE_CONFIG_DIR: "/cfg" })).toBe("/cfg/projects/-p");
  });
});

describe("расход последнего хода", () => {
  it("складывает свежее, прочитанное из кеша и записанное в кеш", () => {
    const text = [
      turn({ input_tokens: 5, cache_read_input_tokens: 1000, cache_creation_input_tokens: 10 }),
      JSON.stringify({ type: "user", message: { content: "usage" } }),
      turn({ input_tokens: 2, cache_read_input_tokens: 207668, cache_creation_input_tokens: 860, output_tokens: 808 }),
    ].join("\n");
    expect(lastUsage(text)).toEqual({ tokens: 208530, at: Date.parse("2026-11-22T10:00:00.000Z") });
  });

  it("ход субагента и обрезанная строка — мимо", () => {
    const text = [
      '{"type":"assistant","message":{"usage":{"input_tok',     // обрезок начала хвоста
      turn({ input_tokens: 100, cache_read_input_tokens: 400_000 }),
      turn({ input_tokens: 50_000 }, { isSidechain: true }),
      "не json со словом \"usage\"",
    ].join("\n");
    expect(lastUsage(text)?.tokens).toBe(400_100);
  });

  it("нет ни одного хода с расходом — числа нет", () => {
    expect(lastUsage("")).toBeNull();
    expect(lastUsage(turn({ input_tokens: 0 }))).toBeNull();
    expect(lastUsage(JSON.stringify({ type: "assistant", message: { usage: "много" } }))).toBeNull();
  });
});

describe("размер контекста идущей сессии", () => {
  it("берёт самый свежий журнал и не выдаёт прошлый разговор за этот", () => {
    const home = fs.mkdtempSync(path.join(os.tmpdir(), "datum-context-"));
    const env = { HOME: home };
    const project = "/work/коробка";
    expect(contextSize(project, 0, env)).toBeNull();
    const dir = sessionDir(project, env);
    fs.mkdirSync(dir, { recursive: true });
    fs.writeFileSync(path.join(dir, "old.jsonl"), turn({ input_tokens: 900_000 }) + "\n");
    const past = new Date(Date.now() - 3_600_000);
    fs.utimesSync(path.join(dir, "old.jsonl"), past, past);
    fs.writeFileSync(path.join(dir, "new.jsonl"),
      "x".repeat(600_000) + "\n" + turn({ input_tokens: 3, cache_read_input_tokens: 120_000 }) + "\n");
    fs.writeFileSync(path.join(dir, "notes.txt"), turn({ input_tokens: 1 }));
    expect(contextSize(project, 0, env)?.tokens).toBe(120_003);
    expect(contextSize(project, Date.now() - 60_000, env)?.tokens).toBe(120_003);
    // Агент запущен только что, а журнал старше — это прошлая сессия.
    expect(contextSize(project, Date.now() + 60_000, env)).toBeNull();
  });
});
