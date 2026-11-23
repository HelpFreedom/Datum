/**
 * Проигрыватель симуляции: разбор записи и положение звеньев между кадрами.
 */
import { describe, expect, it } from "vitest";

import { parsePlayback, posesAt, segment, slerp, valueAt } from "../sim";

const ROT90Z = [Math.SQRT1_2, 0, 0, Math.SQRT1_2]; // поворот на 90° вокруг Z (w, x, y, z)

function record(extra: Record<string, unknown> = {}) {
  return {
    mechanism: "поворотник",
    scenario: "разворот",
    links: [
      { name: "основание", url: "/obj/aa.glb", color: "#9aa4b2" },
      { name: "рычаг", url: "/obj/bb.glb", color: "#c9772e" },
    ],
    t: [0, 1],
    poses: [
      [
        [0, 0, 0, 1, 0, 0, 0],
        [0, 0, 0, 1, 0, 0, 0],
      ],
      [
        [0, 0, 0, 1, 0, 0, 0],
        [10, 0, 0, ...ROT90Z],
      ],
    ],
    joints: ["рычаг"],
    q: [[0], [90]],
    series: { t: [0, 1], required: [[0, 0.5]], applied: null, limits: [0.8], units: ["Н·м"] },
    verdict: [{ id: "x", status: "pass", message: "ok" }],
    ...extra,
  };
}

describe("запись прогона", () => {
  it("правильная разбирается", () => {
    const p = parsePlayback(record());
    expect(p?.links.map((l) => l.name)).toEqual(["основание", "рычаг"]);
    expect(p?.series.limits).toEqual([0.8]);
  });

  it("чужая или испорченная — null, а не падение", () => {
    expect(parsePlayback(null)).toBeNull();
    expect(parsePlayback(record({ t: [0, 1, 2] }))).toBeNull(); // кадров меньше, чем времён
    expect(parsePlayback(record({ t: [1, 0] }))).toBeNull(); // время назад
    expect(parsePlayback(record({ links: [{ name: "a", url: "https://evil/x.glb" }, { name: "b", url: "/obj/b.glb" }] }))).toBeNull();
    expect(parsePlayback(record({ poses: [[[0, 0, 0, 1, 0, 0]], [[0, 0, 0, 1, 0, 0, 0]]] }))).toBeNull();
  });
});

describe("между кадрами", () => {
  it("отрезок и доля", () => {
    expect(segment([0, 1, 3], 2)).toEqual({ i: 1, f: 0.5 });
    expect(segment([0, 1, 3], -5)).toEqual({ i: 0, f: 0 });
    expect(segment([0, 1, 3], 9)).toEqual({ i: 2, f: 0 });
  });

  it("середина пути: положение пополам, поворот на 45°", () => {
    const p = parsePlayback(record())!;
    const [, lever] = posesAt(p, 0.5);
    expect(lever[0]).toBeCloseTo(5);
    const half = Math.cos(Math.PI / 8); // 45° → w = cos(22.5°)
    expect(lever[3]).toBeCloseTo(half);
    expect(lever[6]).toBeCloseTo(Math.sin(Math.PI / 8));
  });

  it("slerp идёт кратчайшим путём и остаётся единичным", () => {
    const q = slerp([1, 0, 0, 0], [-1, 0, 0, 0], 0.5); // тот же поворот с другим знаком
    expect(Math.hypot(...q)).toBeCloseTo(1);
    expect(Math.abs(q[0])).toBeCloseTo(1);
  });

  it("ряд моментов — линейно", () => {
    expect(valueAt([0, 1], [0, 0.5], 0.5)).toBeCloseTo(0.25);
  });
});

describe("карта загрузки", () => {
  it("загрузка между кадрами — линейно; неверная форма — без карты", async () => {
    const { loadAt } = await import("../sim");
    const p = parsePlayback(record({ load: { links: ["основание", "рычаг"], u: [[0, 0.2], [0, 1.0]] } }))!;
    const mid = loadAt(p, 0.5)!;
    expect(mid[0]).toBeCloseTo(0);
    expect(mid[1]).toBeCloseTo(0.6);
    expect(parsePlayback(record({ load: { u: [[0, 1]] } }))!.load).toBeNull();
  });

  it("цвет: зелёный свободно, красный на пределе и выше", async () => {
    const { heatColor } = await import("../sim");
    expect(heatColor(0)).toEqual([47, 214, 138]);
    expect(heatColor(1)).toEqual([255, 59, 78]);
    expect(heatColor(3)).toEqual([255, 59, 78]);
    expect(heatColor(Number.NaN)).toEqual([255, 59, 78]); // неизвестно — как предел, не как «свободно»
    const mid = heatColor(0.65);
    expect(mid[0]).toBeGreaterThan(200);
  });
});

describe("напряжения МКЭ", () => {
  it("свои звенья с сеткой на сервере — берутся, чужое — отбрасывается", () => {
    const good = { link: "рычаг", t: 0.4, url: "/obj/cc.glb", value_mpa: 4.6, basis: "пик",
                   peak_mpa: 5.1, strength_mpa: 30, u: 0.153 };
    const p = parsePlayback(record({ stress: [
      good,
      { ...good, link: "чужое" },                       // нет такого звена
      { ...good, url: "https://evil/x.glb" },            // не наш сервер
      { ...good, value_mpa: Number.NaN },                // не число
      { ...good, strength_mpa: null, u: null },          // без материала — законно
    ] }))!;
    expect(p.stress.map((s) => s.url)).toEqual(["/obj/cc.glb", "/obj/cc.glb"]);
    expect(p.stress[0].u).toBeCloseTo(0.153);
    expect(p.stress[1].strengthMpa).toBeNull();
    expect(parsePlayback(record())!.stress).toEqual([]);   // старая запись — без поля
  });
});

describe("почему напряжения не посчитались", () => {
  it("окно показывает слова сервера, а не свою догадку", async () => {
    const { stressProblems } = await import("../sim");
    expect(stressProblems([{ status: "pass", message: "ok" }])).toBeNull();
    expect(stressProblems([{ status: "skip", message: "куб: считать нечего: у звеньев сердце…\nподробно" }]))
      .toBe("куб: считать нечего: у звеньев сердце…");
    expect(stressProblems([{ status: "pass", message: "a" }, { status: "error", message: "звено б: сетка" }]))
      .toBe("звено б: сетка");
    expect(stressProblems(undefined)).toBeNull();
  });
});
