/**
 * Навык Datum приходит к встроенному агенту плагином — в любом проекте. Без
 * плагина агент не запускается: без правил он работал бы хуже, и незаметно.
 */
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";

import { describe, expect, it } from "vitest";

import { agentArgs, pluginDir, pluginSkills } from "../plugin";

describe("навык Datum у встроенного агента", () => {
  it("плагин из репозитория — с манифестом и навыком datum", () => {
    expect(fs.existsSync(path.join(pluginDir, ".claude-plugin", "plugin.json"))).toBe(true);
    const manifest = JSON.parse(fs.readFileSync(path.join(pluginDir, ".claude-plugin", "plugin.json"), "utf8"));
    expect(manifest.name).toBe("datum");
    expect(pluginSkills()).toContain("datum");
    // Сессии в самом репозитории видят ТОТ ЖЕ навык — ссылкой, а не копией.
    const inRepo = path.join(pluginDir, "..", ".claude", "skills", "datum");
    expect(fs.realpathSync(inRepo)).toBe(fs.realpathSync(path.join(pluginDir, "skills", "datum")));
  });

  it("агент запускается с MCP и с плагином", () => {
    const launch = agentArgs("/run/datum/mcp.json");
    expect(launch).toEqual({ ok: true, args: ["--mcp-config", "/run/datum/mcp.json", "--plugin-dir", pluginDir] });
  });

  it("плагина нет — отказ, а не агент без правил", () => {
    const empty = fs.mkdtempSync(path.join(os.tmpdir(), "datum-plugin-"));
    const launch = agentArgs("/x.json", empty);
    expect(launch.ok).toBe(false);
    expect(launch.ok ? "" : launch.error).toContain("навыком Datum");
  });
});
