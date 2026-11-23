import { describe, expect, it } from "vitest";

import { RestartBudget } from "../restarts";

describe("RestartBudget", () => {
  it("даёт несколько попыток за окно времени и отказывает сверх", () => {
    const budget = new RestartBudget(3, 1000);
    expect([budget.allow(0), budget.allow(10), budget.allow(20)]).toEqual([true, true, true]);
    expect(budget.allow(30)).toBe(false);
    expect(budget.count).toBe(3);
  });

  it("старые попытки выходят из окна", () => {
    const budget = new RestartBudget(2, 1000);
    expect(budget.allow(0)).toBe(true);
    expect(budget.allow(100)).toBe(true);
    expect(budget.allow(500)).toBe(false);
    expect(budget.allow(1200)).toBe(true);
  });

  it("сброс начинает счёт заново", () => {
    const budget = new RestartBudget(1, 1000);
    expect(budget.allow(0)).toBe(true);
    expect(budget.allow(1)).toBe(false);
    budget.reset();
    expect(budget.allow(2)).toBe(true);
  });
});
