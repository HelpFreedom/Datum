/**
 * Окно без проекта — пустой проект «Без названия». Нетронутый пустой берётся
 * снова, начатая работа — никогда.
 */
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";

import { describe, expect, it } from "vitest";

import { findPristine, pristine, untitledName, untitledProject, untitledRoot } from "../untitled";

/** Пустой проект, как его делает `datum init`. */
function empty(root: string, name: string): string {
  const dir = path.join(root, name);
  for (const sub of ["parts", "params", "checks"]) fs.mkdirSync(path.join(dir, sub), { recursive: true });
  fs.writeFileSync(path.join(dir, "datum.toml"), "schema = 1\n");
  fs.writeFileSync(path.join(dir, "params", "base.yaml"), "# параметры\n");
  fs.writeFileSync(path.join(dir, "checks", "checks.yaml"), "# проверки\n");
  fs.writeFileSync(path.join(dir, "NOTES.md"), "# Без названия\n");
  return dir;
}

function later(file: string) {
  const t = new Date(Date.now() + 60_000);
  fs.utimesSync(file, t, t);
}

describe("пустой проект «Без названия»", () => {
  it("живёт по XDG, имя папки — ASCII и по времени", () => {
    expect(untitledRoot({ XDG_DATA_HOME: "/x" })).toBe(path.join("/x", "datum", "untitled"));
    expect(untitledRoot({ HOME: "/home/u" })).toBe(path.join("/home/u", ".local", "share", "datum", "untitled"));
    expect(untitledName(new Date(2026, 8, 22, 7, 5, 3))).toBe("untitled-20260922-070503");
  });

  it("начатая работа — не пустой проект, в каком бы виде она ни была", () => {
    const root = fs.mkdtempSync(path.join(os.tmpdir(), "datum-untitled-"));
    expect(pristine(empty(root, "untitled-1"))).toBe(true);
    const part = empty(root, "untitled-2");
    fs.writeFileSync(path.join(part, "parts", "box.py"), "");
    const model = empty(root, "untitled-3");
    fs.mkdirSync(path.join(model, "imported", "m"), { recursive: true });
    const saved = empty(root, "untitled-4");
    fs.mkdirSync(path.join(saved, ".datum"));
    fs.writeFileSync(path.join(saved, ".datum", "archive.json"), "{}");
    const edited = empty(root, "untitled-5");
    later(path.join(edited, "params", "base.yaml"));
    const placed = empty(root, "untitled-6");
    fs.writeFileSync(path.join(placed, "placement.yaml"), "");
    for (const dir of [part, model, saved, edited, placed]) expect(pristine(dir), dir).toBe(false);
    expect(pristine(path.join(root, "нет-такого"))).toBe(false);
  });

  it("берётся самый новый нетронутый; начатые не трогаются", () => {
    const root = fs.mkdtempSync(path.join(os.tmpdir(), "datum-untitled-"));
    empty(root, "untitled-20260101-000000");
    const newest = empty(root, "untitled-20260202-000000");
    const started = empty(root, "untitled-20260303-000000");
    fs.writeFileSync(path.join(started, "parts", "box.py"), "");
    empty(root, "чужая-папка");
    expect(findPristine(root)).toBe(newest);
  });

  it("нетронутого нет — создаётся новый командой init", async () => {
    const root = fs.mkdtempSync(path.join(os.tmpdir(), "datum-untitled-"));
    const calls: string[][] = [];
    const fake = ((_: string, args: string[], __: unknown, done: (e: Error | null, o: string, s: string) => void) => {
      calls.push(args);
      empty(path.dirname(args.at(-1)!), path.basename(args.at(-1)!));
      done(null, "", "");
    }) as unknown as typeof import("node:child_process").execFile;
    const now = new Date(2026, 8, 22, 12, 0, 0);
    const first = await untitledProject(fake, root, now);
    expect(first).toEqual({ ok: true, project: path.join(root, "untitled-20260922-120000"), reused: false });
    expect(calls[0].slice(0, 5)).toEqual(["-m", "datum.cli", "init", "--name", "Без названия"]);
    // Второй запуск — тот же нетронутый, без нового init.
    const again = await untitledProject(fake, root, now);
    expect(again).toEqual({ ok: true, project: first.ok ? first.project : "", reused: true });
    expect(calls).toHaveLength(1);
    // Начали работать — следующий запуск получит новую папку, в ту же секунду тоже.
    fs.writeFileSync(path.join(root, "untitled-20260922-120000", "parts", "box.py"), "");
    const next = await untitledProject(fake, root, now);
    expect(next).toEqual({ ok: true, project: path.join(root, "untitled-20260922-120000-2"), reused: false });
  });

  it("init отказал — честная ошибка", async () => {
    const root = fs.mkdtempSync(path.join(os.tmpdir(), "datum-untitled-"));
    const fail = ((_: string, __: string[], ___: unknown, done: (e: Error | null, o: string, s: string) => void) =>
      done(new Error("код 3"), "", "ОШИБКА: нет места")) as unknown as typeof import("node:child_process").execFile;
    expect(await untitledProject(fail, root)).toEqual({ ok: false, error: "ОШИБКА: нет места" });
  });
});
