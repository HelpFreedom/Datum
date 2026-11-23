import { describe, expect, it } from "vitest";

import { deletedWhen } from "../Trash";

describe("корзина: когда удалено", () => {
  const now = new Date("2026-11-22T15:00:00");

  it("сегодняшнее — временем", () => {
    expect(deletedWhen("2026-11-22T14:05:00", now)).toBe("сегодня 14:05");
  });

  it("прошлое — датой и временем", () => {
    expect(deletedWhen("2026-11-21T09:30:00", now)).toBe("21.11 09:30");
  });

  it("нечитаемая дата — пусто, а не «Invalid Date»", () => {
    expect(deletedWhen("когда-то", now)).toBe("");
  });
});
