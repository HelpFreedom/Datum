import { describe, expect, it } from "vitest";

import { amount, unit } from "../units";

describe("единицы", () => {
  it("переводит известные", () => {
    expect(unit("mm")).toBe("мм");
    expect(unit("mm3")).toBe("мм³");
    expect(unit("deg")).toBe("°");
    expect(unit("MPa")).toBe("МПа");
  });

  it("незнакомую оставляет как есть, а не выбрасывает", () => {
    expect(unit("парсек")).toBe("парсек");
    expect(unit("")).toBe("");
    expect(unit(undefined)).toBe("");
  });

  it("число без единицы не обрастает пробелом", () => {
    expect(amount(2.5)).toBe("2,5");
  });

  it("пустое значение — прочерк, а не «null»", () => {
    expect(amount(null)).toBe("—");
    expect(amount(undefined, "mm")).toBe("—");
    expect(amount(Number.NaN)).toBe("—");
  });

  it("не округляет до неузнаваемости", () => {
    // Разделитель разрядов у ru-RU — НЕРАЗРЫВНЫЙ пробел (U+00A0), а не
    // обычный. На глаз строки неотличимы, поэтому он записан кодом.
    expect(amount(12145.92, "mm3")).toBe("12\u00A0145,92 мм³");
  });

  it("разделитель разрядов неразрывный", () => {
    expect(amount(1000)).toContain("\u00A0");
    expect(amount(1000)).not.toContain(" 0");
  });
});
