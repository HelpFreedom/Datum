/**
 * Показ агента в окне (`window_show` → `window.show`): разбор события,
 * свежесть (повтор после переподключения не применяется), что прятать.
 */
import { describe, expect, it } from "vitest";

import { describeShow, hiddenAfter, parseShow, SHOW_FRESH_S } from "../show";

const NOW = 1_800_000_000;
const event = (extra: Record<string, unknown>) => ({ t: "window.show", seq: 1, at: NOW, ...extra });

describe("показ агента", () => {
  it("разбирает детали, срез, шарнир и подпись", () => {
    const show = parseShow(event({
      parts: ["крышка"], isolate: true, section: { plane: "xz", at: 12.5 },
      joint: { part: "крышка", u: 30 }, note: "вот губка", actor: "agent",
    }), NOW);
    expect(show).toEqual({
      parts: ["крышка"], isolate: true, section: { plane: "xz", at: 12.5 },
      joint: { part: "крышка", u: 30 }, note: "вот губка", actor: "agent",
    });
  });

  it("не повторяет старый показ, досланный после переподключения", () => {
    expect(parseShow(event({ parts: ["крышка"] }), NOW + SHOW_FRESH_S + 1)).toBeNull();
    expect(parseShow(event({ parts: ["крышка"] }), NOW + SHOW_FRESH_S - 1)).not.toBeNull();
    expect(parseShow({ t: "window.show", seq: 1, parts: ["крышка"] }, NOW)).toBeNull();   // без времени
  });

  it("кривое и пустое не выдумывается в показ", () => {
    expect(parseShow(event({}), NOW)).toBeNull();
    expect(parseShow(event({ section: { plane: "ab", at: 1 } }), NOW)).toBeNull();
    expect(parseShow(event({ section: { plane: "xy", at: "x" } }), NOW)).toBeNull();
    expect(parseShow(event({ joint: { part: 3, u: 1 } }), NOW)).toBeNull();
  });

  it("isolate прячет всё, кроме показанного; без него показанное становится видимым", () => {
    const all = ["корпус", "крышка", "винт"];
    const show = parseShow(event({ parts: ["крышка"], isolate: true }), NOW)!;
    expect(hiddenAfter(show, all, [])).toEqual(["корпус", "винт"]);
    const plain = parseShow(event({ parts: ["крышка"] }), NOW)!;
    expect(hiddenAfter(plain, all, ["крышка", "винт"])).toEqual(["винт"]);
  });

  it("подпись говорит, кто показывает и что", () => {
    const show = parseShow(event({ parts: ["крышка"], section: { plane: "xy", at: 15 },
                                   note: "тонкое место", actor: "agent" }), NOW)!;
    expect(describeShow(show)).toBe("Агент показывает: крышка; срез по Z = 15 мм — тонкое место");
  });
});
