/**
 * Проекты как вещи: список для диалога, недавние, кеш. Окно получает метки,
 * а не пути, — и метка обязана вести только в то, что оболочка нашла сама.
 */
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";

import { describe, expect, it } from "vitest";

import {
  cacheBytes, clearCache, listProjects, loadRecent, projectCli, projectDirs, projectId,
  projectTitle, recentFile, rememberRecent, replaceRecent, thumbFile,
} from "../projects";

function project(root: string, folder: string, name?: string, parts: string[] = []): string {
  const dir = path.join(root, folder);
  fs.mkdirSync(path.join(dir, "parts"), { recursive: true });
  fs.mkdirSync(path.join(dir, "params"), { recursive: true });
  fs.writeFileSync(path.join(dir, "datum.toml"),
    (name === undefined ? "" : `name = ${JSON.stringify(name)}\n`) + "schema = 1\n\n[printer]\nname = \"не то\"\n");
  fs.writeFileSync(path.join(dir, "params", "base.yaml"), "# параметры\n");
  for (const part of parts) fs.writeFileSync(path.join(dir, "parts", part), "");
  return dir;
}

function temp(): string {
  return fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "datum-projects-")));
}

describe("название проекта", () => {
  it("берётся из верхней строки name, а не из таблицы ниже", () => {
    const root = temp();
    expect(projectTitle(project(root, "a", "Gear Cube «v2»"))).toBe("Gear Cube «v2»");
    expect(projectTitle(project(root, "b"))).toBe("b");
    expect(projectTitle(path.join(root, "нет"))).toBe("нет");
  });
});

describe("список проектов", () => {
  it("собирает «Без названия», недавние и открытый — без повторов и без чужих папок", () => {
    const root = temp();
    const elsewhere = temp();
    const a = project(root, "untitled-1", "Первый", ["a.py", "b.py", "notes.txt"]);
    const b = project(root, "рука-v3", "Рука");
    fs.mkdirSync(path.join(root, "просто-папка"));
    project(root, ".скрытый", "Скрытый");
    const mine = project(elsewhere, "пресс", "Пресс", ["p.py"]);
    const dirs = projectDirs(a, [mine, a, path.join(elsewhere, "удалён")], root);
    expect(dirs).toEqual([a, mine, b]);

    const old = new Date(Date.now() - 5 * 86_400_000);
    fs.utimesSync(path.join(mine, "datum.toml"), old, old);
    fs.utimesSync(path.join(mine, "params", "base.yaml"), old, old);
    fs.utimesSync(path.join(mine, "parts", "p.py"), old, old);
    const { entries, paths } = listProjects(a, [mine], root, { HOME: elsewhere });
    expect(entries.map((e) => e.name)).toEqual(["Первый", "Рука", "Пресс"]);
    expect(entries[0]).toMatchObject({ current: true, parts: 2, folder: "untitled-1", thumb: false });
    expect(entries[2]).toMatchObject({ current: false, parts: 1, where: "~" });
    expect(entries[2].modified).toBeLessThan(entries[1].modified);
    expect(paths.get(entries[2].id)).toBe(mine);
    expect(entries[0].id).toBe(projectId(a));
    expect(new Set(entries.map((e) => e.id)).size).toBe(3);
  });

  it("помнит картинку и называет пустой проект пустым", () => {
    const root = temp();
    const a = project(root, "untitled-1", "Без названия");
    fs.mkdirSync(path.join(a, "checks"));
    fs.writeFileSync(path.join(a, "checks", "checks.yaml"), "");
    fs.writeFileSync(path.join(a, "NOTES.md"), "");
    fs.mkdirSync(path.dirname(thumbFile(a)), { recursive: true });
    fs.writeFileSync(thumbFile(a), "png");
    const [entry] = listProjects("", [], root).entries;
    expect(entry.thumb).toBe(true);
    expect(entry.empty).toBe(true);
    expect(entry.current).toBe(false);
  });
});

describe("недавние", () => {
  it("свежий — первым, без повторов; переезд сохраняет место", () => {
    const env = { XDG_CONFIG_HOME: temp() };
    expect(loadRecent(env)).toEqual([]);
    rememberRecent("/p/a", env);
    rememberRecent("/p/b", env);
    rememberRecent("/p/a", env);
    expect(loadRecent(env)).toEqual(["/p/a", "/p/b"]);
    replaceRecent("/p/b", "/p/новое", env);
    expect(loadRecent(env)).toEqual(["/p/a", "/p/новое"]);
    replaceRecent("/p/нет", "/p/x", env);
    expect(loadRecent(env)).toEqual(["/p/a", "/p/новое"]);
  });

  it("испорченный файл читается как пустой, а чужое в нём отбрасывается", () => {
    const env = { XDG_CONFIG_HOME: temp() };
    fs.mkdirSync(path.dirname(recentFile(env)), { recursive: true });
    fs.writeFileSync(recentFile(env), "не json");
    expect(loadRecent(env)).toEqual([]);
    fs.writeFileSync(recentFile(env), JSON.stringify(["/p/a", 5, "относительный", null]));
    expect(loadRecent(env)).toEqual(["/p/a"]);
  });

  it("под сценарием проверки не читаются и не пишутся", () => {
    const env = { XDG_CONFIG_HOME: temp(), DATUM_SCENARIO: "x" };
    rememberRecent("/p/a", env);
    expect(fs.existsSync(recentFile(env))).toBe(false);
    expect(loadRecent(env)).toEqual([]);
  });
});

describe("кеш проекта", () => {
  it("считается по файлам и чистится целиком, не трогая остального", async () => {
    const dir = project(temp(), "a", "А");
    const cache = path.join(dir, ".datum", "cache", "objects", "ab");
    fs.mkdirSync(cache, { recursive: true });
    fs.writeFileSync(path.join(cache, "x.mesh"), Buffer.alloc(3000));
    fs.writeFileSync(path.join(dir, ".datum", "cache", "index.sqlite"), Buffer.alloc(500));
    fs.writeFileSync(path.join(dir, ".datum", "archive.json"), "{}");
    expect(await cacheBytes(dir)).toBe(3500);
    await clearCache(dir);
    expect(await cacheBytes(dir)).toBe(0);
    expect(fs.existsSync(path.join(dir, ".datum", "archive.json"))).toBe(true);
    expect(fs.existsSync(path.join(dir, "datum.toml"))).toBe(true);
  });
});

describe("правка названия и папки — бэкендом", () => {
  type Callback = (error: Error | null, stdout: string, stderr: string) => void;
  const runner = (reply: [Error | null, string, string], seen: string[][]) =>
    ((_exe: string, args: string[], _opts: unknown, done: Callback) => {
      seen.push(args);
      done(...reply);
    }) as unknown as Parameters<typeof projectCli>[3];

  it("переименование просит и папку, копия — нет; ответ — новый путь", async () => {
    const seen: string[][] = [];
    const ok = await projectCli("rename", "/p/a", "Gear Cube",
      runner([null, JSON.stringify({ path: "/p/Gear-Cube", name: "Gear Cube" }), ""], seen));
    expect(ok).toEqual({ ok: true, path: "/p/Gear-Cube", name: "Gear Cube" });
    expect(seen[0]).toEqual(["-m", "datum.cli", "rename", "--json", "--folder", "--", "/p/a", "Gear Cube"]);
    await projectCli("copy", "/p/a", "v2", runner([null, JSON.stringify({ path: "/p/v2" }), ""], seen));
    expect(seen[1]).not.toContain("--folder");
  });

  it("отказ операции приходит её словами", async () => {
    const refused = await projectCli("rename", "/p/a", "x",
      runner([new Error("код 3"), JSON.stringify({ error: "проект открыт в другом окне" }), ""], []));
    expect(refused).toEqual({ ok: false, error: "проект открыт в другом окне" });
    const broken = await projectCli("rename", "/p/a", "x", runner([new Error("код 1"), "", "Traceback…"], []));
    expect(broken).toEqual({ ok: false, error: "Traceback…" });
  });
});
