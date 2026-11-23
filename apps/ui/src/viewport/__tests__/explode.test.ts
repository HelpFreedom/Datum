import { describe, expect, it } from "vitest";

import { DEFAULT_GAP, explodeOffsets, type AssemblyInfo } from "../explode";

const BOXES = {
  корпус: [0, 0, 0, 60, 40, 20],
  крышка: [2, 2, 13, 58, 38, 17],
};

function assembly(over: Partial<AssemblyInfo> = {}): AssemblyInfo {
  return {
    order: ["корпус", "крышка"],
    explode_gap: 25,
    steps: { корпус: { method: "rigid", insert: [], distance: 60 },
             крышка: { method: "snap", insert: ["-Z"], distance: 20 } },
    ...over,
  };
}

describe("разнесённый вид", () => {
  it("основание остаётся на месте", () => {
    expect(explodeOffsets(assembly(), BOXES).корпус).toEqual([0, 0, 0]);
  });

  it("деталь уезжает ВСТРЕЧНО своей вставке", () => {
    // Крышка вдавливается вниз (−Z), значит на разнесённом виде она снимается
    // вверх: иначе картинка показывала бы, как её вдавливают в дно.
    expect(explodeOffsets(assembly(), BOXES).крышка).toEqual([0, 0, 25]);
  });

  it("расстояние растёт по номеру в порядке сборки", () => {
    const a = assembly({
      order: ["к", "а", "б"],
      steps: {
        к: { method: "rigid", insert: [], distance: 60 },
        а: { method: "rigid", insert: ["-Z"], distance: 60 },
        б: { method: "rigid", insert: ["-Z"], distance: 60 },
      },
    });
    const out = explodeOffsets(a, { к: [0, 0, 0, 10, 10, 10] });
    expect(out.а).toEqual([0, 0, 25]);
    expect(out.б).toEqual([0, 0, 50]);
  });

  it("зазор берётся из модели сборки", () => {
    expect(explodeOffsets(assembly({ explode_gap: 8 }), BOXES).крышка)
      .toEqual([0, 0, 8]);
  });

  it("без объявленного направления разносит радиально", () => {
    const a = assembly({
      steps: { корпус: { method: "rigid", insert: [], distance: 60 },
               крышка: { method: "rigid", insert: [], distance: 60 } },
    });
    const out = explodeOffsets(a, {
      корпус: [0, 0, 0, 10, 10, 10],     // центр (5,5,5)
      крышка: [0, 0, 10, 10, 10, 20],    // центр (5,5,15) — выше середины
    });
    expect(out.крышка[2]).toBeGreaterThan(0);
    expect(out.крышка[0]).toBeCloseTo(0);
  });

  it("деталь вне порядка сборки не выдаётся за шаг сборки", () => {
    // Её отодвигают на ОДИН зазор радиально: она видна отдельно, но её место в
    // картинке ничего не утверждает — порядка для неё не объявляли.
    const out = explodeOffsets(assembly(), { ...BOXES, кольцо: [70, 0, 0, 80, 10, 10] });
    expect(Math.hypot(...out.кольцо)).toBeCloseTo(25);
    expect(out.кольцо[0]).toBeGreaterThan(0);
  });

  it("без модели сборки остаётся радиальное разнесение", () => {
    const out = explodeOffsets(null, BOXES);
    expect(Math.hypot(...out.корпус)).toBeCloseTo(DEFAULT_GAP);
    expect(Math.hypot(...out.крышка)).toBeCloseTo(DEFAULT_GAP);
  });

  it("совпадение центров не даёт нулевого деления", () => {
    const out = explodeOffsets(null, { а: [0, 0, 0, 10, 10, 10] });
    expect(out.а.every(Number.isFinite)).toBe(true);
    expect(Math.hypot(...out.а)).toBeCloseTo(DEFAULT_GAP);
  });

  it("деталь без габарита не роняет расчёт", () => {
    const out = explodeOffsets(assembly(), { корпус: BOXES.корпус, крышка: [] });
    expect(out.крышка.every(Number.isFinite)).toBe(true);
  });
});
