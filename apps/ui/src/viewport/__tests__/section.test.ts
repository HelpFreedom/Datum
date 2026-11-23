/**
 * Координатная арифметика сечения.
 *
 * Эти функции — ровно то место, где ошибка в знаке или перестановке осей не
 * видна ни в типах, ни в отчётах: крышка среза просто ляжет не туда, и понять
 * это можно будет только глазом, да и то не сразу. Отображение здесь обязано
 * совпадать с тем, что выверено на стороне сервера в test_measure.py.
 */
import * as THREE from "three";
import { describe, expect, it } from "vitest";

import { buildCap, clipPlane, toWorld, type SectionAnswer } from "../section";

describe("отображение плоскости сечения", () => {
  it("кладёт точку туда же, куда сервер её взял", () => {
    // xy (z = at) → (u, v, at)
    expect(toWorld("xy", 7, 1, 2).toArray()).toEqual([1, 2, 7]);
    // xz (y = at) → (u, at, v): вторая координата сечения — это Z
    expect(toWorld("xz", 7, 1, 2).toArray()).toEqual([1, 7, 2]);
    // yz (x = at) → (at, u, v)
    expect(toWorld("yz", 7, 1, 2).toArray()).toEqual([7, 1, 2]);
  });

  it("не путает оси у несимметричной точки", () => {
    const point = toWorld("xz", 5, 60, 20);
    expect(point.x).toBe(60);
    expect(point.y).toBe(5);
    expect(point.z).toBe(20);
  });
});

describe("плоскость отсечения", () => {
  it("оставляет то, что ДО неё, и срезает то, что за", () => {
    const plane = clipPlane("yz", 30);
    // Отрицательное расстояние — точка отсекается.
    expect(plane.distanceToPoint(new THREE.Vector3(10, 0, 0))).toBeGreaterThan(0);
    expect(plane.distanceToPoint(new THREE.Vector3(50, 0, 0))).toBeLessThan(0);
  });

  it("проходит ровно через заданную координату", () => {
    for (const [name, index] of [["yz", 0], ["xz", 1], ["xy", 2]] as const) {
      const plane = clipPlane(name, 12.5);
      const on = new THREE.Vector3();
      on.setComponent(index, 12.5);
      expect(plane.distanceToPoint(on)).toBeCloseTo(0, 9);
    }
  });
});

describe("крышка среза", () => {
  const square: SectionAnswer = {
    part: "проба",
    empty: false,
    contours: [{
      shell: [[0, 0], [10, 0], [10, 4], [0, 4]],
      holes: [],
    }],
  };

  it("ложится в плоскость среза, а не в плоскость XY", () => {
    const cap = buildCap(square, "xz", 7, new THREE.Color(0x808080));
    expect(cap).not.toBeNull();
    const position = cap!.geometry.getAttribute("position");
    for (let i = 0; i < position.count; i += 1) {
      expect(position.getY(i)).toBeCloseTo(7, 6);   // y = at
    }
    cap!.geometry.computeBoundingBox();
    const box = cap!.geometry.boundingBox!;
    expect(box.min.x).toBeCloseTo(0, 6);
    expect(box.max.x).toBeCloseTo(10, 6);
    expect(box.min.z).toBeCloseTo(0, 6);
    expect(box.max.z).toBeCloseTo(4, 6);
  });

  it("оставляет дыру дырой", () => {
    const withHole: SectionAnswer = {
      part: "проба",
      empty: false,
      contours: [{
        shell: [[0, 0], [10, 0], [10, 10], [0, 10]],
        holes: [[[3, 3], [7, 3], [7, 7], [3, 7]]],
      }],
    };
    const solid = buildCap({ ...withHole, contours: [{ shell: withHole.contours![0].shell, holes: [] }] },
                           "xy", 0, new THREE.Color(0x808080))!;
    const holed = buildCap(withHole, "xy", 0, new THREE.Color(0x808080))!;
    // У крышки с дырой треугольников не меньше, а площадь — меньше.
    expect(area(holed)).toBeLessThan(area(solid));
    expect(area(holed)).toBeCloseTo(100 - 16, 3);
  });

  it("не сама себя отсекает", () => {
    const cap = buildCap(square, "yz", 3, new THREE.Color(0x808080))!;
    const material = cap.material as THREE.Material;
    expect(material.clippingPlanes).toEqual([]);
  });

  it("на пустом срезе не даёт ничего", () => {
    expect(buildCap({ part: "п", empty: true }, "xy", 0, new THREE.Color())).toBeNull();
    expect(buildCap({ part: "п", empty: false, contours: [] }, "xy", 0, new THREE.Color()))
      .toBeNull();
  });
});

/** Площадь треугольников крышки — проверка, что дыра действительно вырезана. */
function area(mesh: THREE.Mesh): number {
  const position = mesh.geometry.getAttribute("position");
  const index = mesh.geometry.getIndex();
  const a = new THREE.Vector3();
  const b = new THREE.Vector3();
  const c = new THREE.Vector3();
  let total = 0;
  const count = index ? index.count : position.count;
  for (let i = 0; i < count; i += 3) {
    const [ia, ib, ic] = index
      ? [index.getX(i), index.getX(i + 1), index.getX(i + 2)]
      : [i, i + 1, i + 2];
    a.fromBufferAttribute(position as THREE.BufferAttribute, ia);
    b.fromBufferAttribute(position as THREE.BufferAttribute, ib);
    c.fromBufferAttribute(position as THREE.BufferAttribute, ic);
    total += b.clone().sub(a).cross(c.clone().sub(a)).length() / 2;
  }
  return total;
}
