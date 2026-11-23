import { describe, expect, it } from "vitest";

import { shownInLog } from "../Log";

describe("лента: служебные вызовы окна", () => {
  it("скрыты, пока не попросили показать", () => {
    expect(shownInLog({ service: true, status: "done" }, false)).toBe(false);
    expect(shownInLog({ service: true, status: "done" }, true)).toBe(true);
  });

  it("обычные вызовы — человека и агента — видны всегда", () => {
    expect(shownInLog({ status: "done" }, false)).toBe(true);
    expect(shownInLog({ service: false, status: "cancelled" }, false)).toBe(true);
  });

  it("отказ и идущее не прячутся, даже служебные", () => {
    expect(shownInLog({ service: true, status: "failed" }, false)).toBe(true);
    expect(shownInLog({ service: true, status: "running" }, false)).toBe(true);
  });
});
