import { describe, expect, it } from "vitest";

import { contextLabel, tokensText } from "../context";

describe("подпись контекста агента", () => {
  it("пишет размер в тысячах и миллионах", () => {
    expect([0, 640, 84_300, 417_000, 1_200_000].map(tokensText))
      .toEqual(["0", "640", "84 тыс.", "417 тыс.", "1.2 млн"]);
  });

  it("называет цену хода и желтеет, когда пора начинать заново", () => {
    const small = contextLabel(84_000);
    expect(small.text).toBe("контекст 84 тыс.");
    expect(small.warn).toBe(false);
    expect(small.hint).toContain("ход стоит как 8 тыс. свежих");
    expect(small.hint).not.toContain("/clear");
    const big = contextLabel(420_000);
    expect(big.warn).toBe(true);
    expect(big.hint).toContain("ход стоит как 42 тыс. свежих");
    expect(big.hint).toContain("/clear");
  });
});
