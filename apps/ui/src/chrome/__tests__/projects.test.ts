import { describe, expect, it } from "vitest";

import { ago, bytesText, partsText } from "../projects";

describe("подписи диалога проектов", () => {
  const now = new Date(2026, 10, 22, 15, 0).getTime();

  it("называет день правки словами, а давнее — датой", () => {
    expect(ago(new Date(2026, 10, 22, 9, 5).getTime(), now)).toBe("сегодня 09:05");
    expect(ago(new Date(2026, 10, 21, 23, 50).getTime(), now)).toBe("вчера 23:50");
    expect(ago(new Date(2026, 10, 19, 12, 0).getTime(), now)).toBe("3 дня назад");
    expect(ago(new Date(2026, 10, 16, 12, 0).getTime(), now)).toBe("6 дней назад");
    expect(ago(new Date(2026, 9, 3, 12, 0).getTime(), now)).toBe("03.10.2026");
    expect(ago(0, now)).toBe("не правился");
  });

  it("пишет размер кеша в привычных единицах", () => {
    expect(bytesText(0)).toBe("0");
    expect(bytesText(300)).toBe("1 КБ");
    expect(bytesText(412 * 1024)).toBe("412 КБ");
    expect(bytesText(38 * 1024 * 1024)).toBe("38 МБ");
    expect(bytesText(1.4 * 1024 ** 3)).toBe("1.4 ГБ");
  });

  it("склоняет детали", () => {
    expect([0, 1, 3, 5, 11, 18, 21, 22, 112].map(partsText)).toEqual([
      "без деталей", "1 деталь", "3 детали", "5 деталей", "11 деталей", "18 деталей",
      "21 деталь", "22 детали", "112 деталей",
    ]);
  });
});
