/**
 * Размеры панелей между запусками: один файл на все проекты, читается как
 * чужой, под сценарием проверки не трогается вовсе.
 */
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";

import { describe, expect, it } from "vitest";

import { layoutFile, loadLayout, sanitizeLayout, saveLayout } from "../layout";

const home = () => fs.mkdtempSync(path.join(os.tmpdir(), "datum-layout-"));

describe("раскладка окна", () => {
  it("живёт по XDG, одна на все проекты", () => {
    expect(layoutFile({ XDG_CONFIG_HOME: "/x" })).toBe(path.join("/x", "datum", "layout.json"));
    expect(layoutFile({ HOME: "/home/u" })).toBe(path.join("/home/u", ".config", "datum", "layout.json"));
  });

  it("запомненное читается обратно", () => {
    const env = { XDG_CONFIG_HOME: home() };
    saveLayout({ left: 410.4, right: 900, dock: 333 }, env);
    expect(loadLayout(env)).toEqual({ left: 410, right: 900, dock: 333 });
  });

  it("из файла берутся только числа в разумных пределах", () => {
    expect(sanitizeLayout({ left: "300", right: -5, dock: Infinity, extra: 7 })).toEqual({});
    expect(sanitizeLayout({ left: 1e9, dock: 120 })).toEqual({ dock: 120 });
    expect(sanitizeLayout(null)).toEqual({});
    expect(sanitizeLayout([1, 2])).toEqual({});
  });

  it("испорченный файл — умолчания, а не отказ", () => {
    const env = { XDG_CONFIG_HOME: home() };
    fs.mkdirSync(path.dirname(layoutFile(env)), { recursive: true });
    fs.writeFileSync(layoutFile(env), "{не json");
    expect(loadLayout(env)).toEqual({});
  });

  it("сценарий проверки раскладку человека не читает и не пишет", () => {
    const dir = home();
    saveLayout({ left: 500 }, { XDG_CONFIG_HOME: dir });
    const scenario = { XDG_CONFIG_HOME: dir, DATUM_SCENARIO: "scripts/ui/snapshot.cjs" };
    expect(loadLayout(scenario)).toEqual({});
    saveLayout({ left: 200 }, scenario);
    expect(loadLayout({ XDG_CONFIG_HOME: dir })).toEqual({ left: 500 });
  });
});
