import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";

import { describe, expect, it } from "vitest";

import { archiveFromArgv, unpackArchive } from "../archive";

describe("архив в аргументах", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "datum-archive-"));
  const file = path.join(root, "проект.datum");
  fs.writeFileSync(file, "zip");
  const cacheDir = path.join(root, "проект", ".datum");
  fs.mkdirSync(cacheDir, { recursive: true });

  it("находит файл .datum среди ключей Electron", () => {
    expect(archiveFromArgv(["electron", "--enable-features=X", "main.js", file])).toBe(file);
  });

  it("каталог .datum/ — кеш проекта, а не архив", () => {
    expect(archiveFromArgv(["electron", cacheDir])).toBeNull();
  });

  it("нет файла — нет архива", () => {
    expect(archiveFromArgv(["electron", path.join(root, "нет.datum")])).toBeNull();
  });
});

describe("распаковка через операцию unpack", () => {
  // Подставной запуск: проверяется разбор ответа, а не Python.
  const fake = (stdout: string, stderr = "", error: Error | null = null) =>
    ((_cmd: string, args: readonly string[], _opts: unknown, done: (...a: unknown[]) => void) => {
      expect(args.slice(0, 4)).toEqual(["-m", "datum.cli", "unpack", "/a.datum"]);
      expect(args).toContain("--json");
      done(error, stdout, stderr);
    }) as never;

  it("ответ с каталогом — успех", async () => {
    const out = await unpackArchive("/a.datum", fake(JSON.stringify({ project: "/a", reused: true })));
    expect(out).toEqual({ ok: true, project: "/a", reused: true });
  });

  it("отказ операции — её же словами", async () => {
    const out = await unpackArchive("/a.datum",
      fake(JSON.stringify({ error: "в архиве путь вне проекта" }), "", new Error("код 3")));
    expect(out).toEqual({ ok: false, error: "в архиве путь вне проекта" });
  });

  it("ответа нет — говорим, что было в stderr", async () => {
    const out = await unpackArchive("/a.datum", fake("", "Traceback: …", new Error("код 1")));
    expect(out).toEqual({ ok: false, error: "Traceback: …" });
  });
});
