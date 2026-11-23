import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";

import { describe, expect, it } from "vitest";

import { projectFromArgv } from "../paths";

function project(root: string, name: string): string {
  const dir = path.join(root, name);
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(path.join(dir, "datum.toml"), "schema = 1\n");
  return dir;
}

describe("какой проект открыть", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "datum-paths-"));

  it("первый каталог-проект среди аргументов, ключи Electron — мимо", () => {
    const a = project(root, "a");
    expect(projectFromArgv(["electron", "--enable-features=X", a])).toBe(a);
  });

  it("без проекта в аргументах — никакого: окно откроет пустой «Без названия»", () => {
    fs.mkdirSync(path.join(root, "not-a-project"), { recursive: true });
    expect(projectFromArgv(["electron", "main.js"])).toBeNull();
    expect(projectFromArgv(["electron", path.join(root, "not-a-project")])).toBeNull();
  });
});
