/**
 * «Штангенциркуль»: грани, рёбра и окружности, которые ловятся при наведении.
 * Проверяется на сетках, где ответ известен заранее.
 */
import * as THREE from "three";
import { describe, expect, it } from "vitest";

import { type CircleFeature, type EdgeFeature, type Feature, fitCircle, MeshFeatures } from "../features";

/** Плита толщиной 4 мм по контуру (Z вверх), с отверстиями. */
function plate(outline: THREE.Shape, holes: THREE.Path[] = [], segments = 32): THREE.BufferGeometry {
  outline.holes.push(...holes);
  return new THREE.ExtrudeGeometry(outline, { depth: 4, bevelEnabled: false, curveSegments: segments });
}

function rect(w: number, h: number): THREE.Shape {
  return new THREE.Shape().moveTo(-w / 2, -h / 2).lineTo(w / 2, -h / 2).lineTo(w / 2, h / 2).lineTo(-w / 2, h / 2);
}

function roundHole(r: number, x = 0, y = 0): THREE.Path {
  return new THREE.Path().absarc(x, y, r, 0, Math.PI * 2, true);
}

/** Треугольник, в котором лежит точка (по ближайшему центру — для плоских граней). */
function faceAt(g: THREE.BufferGeometry, at: THREE.Vector3): number {
  const mesh = new THREE.Mesh(g);
  const ray = new THREE.Raycaster(at.clone().add(new THREE.Vector3(0, 0, 50)), new THREE.Vector3(0, 0, -1));
  const [hit] = ray.intersectObject(mesh);
  return hit.faceIndex!;
}

const circles = (fs: Feature[]) => fs.filter((f): f is CircleFeature => f.kind === "circle");
const edges = (fs: Feature[]) => fs.filter((f): f is EdgeFeature => f.kind === "edge");

describe("черты детали", () => {
  it("у коробки грань — четыре ребра и сама грань", () => {
    const g = new THREE.BoxGeometry(10, 20, 30);
    const m = new MeshFeatures(g);
    const top = m.featuresOf(m.planeOf(faceAt(g, new THREE.Vector3(1, 1, 0))));
    expect(edges(top)).toHaveLength(4);
    expect(circles(top)).toHaveLength(0);
    expect(top.at(-1)!.kind).toBe("plane");
  });

  it("у отверстия в плите — окружность его радиуса на верхней грани", () => {
    const g = plate(rect(40, 30), [roundHole(4, 5, 3)]);
    const m = new MeshFeatures(g);
    const top = m.featuresOf(m.planeOf(faceAt(g, new THREE.Vector3(-15, -10, 0))));
    const [hole] = circles(top);
    expect(circles(top)).toHaveLength(1);
    expect(hole.closed).toBe(true);
    expect(hole.radius).toBeCloseTo(4, 5);                  // вершины лежат на окружности
    expect(hole.centre.x).toBeCloseTo(5, 5);
    expect(hole.centre.y).toBeCloseTo(3, 5);
    expect(hole.centre.z).toBeCloseTo(4, 5);
    // Внешний контур — четыре ребра: стороны не дробятся на куски.
    expect(edges(top)).toHaveLength(4);
  });

  it("наведение: у обода — окружность, у угла — вершина, в стороне — грань", () => {
    const g = plate(rect(40, 30), [roundHole(4, 5, 3)]);
    const m = new MeshFeatures(g);
    const at = (x: number, y: number) => m.resolve(faceAt(g, new THREE.Vector3(x, y, 0)), new THREE.Vector3(x, y, 4), 0.6);
    expect(at(5 + 4.3, 3)?.kind).toBe("circle");
    const corner = at(19.7, 14.7);
    expect(corner?.kind).toBe("point");
    expect((corner as { point: THREE.Vector3 }).point.x).toBeCloseTo(20, 6);
    expect(at(19.7, 0)?.kind).toBe("edge");
    expect(at(-10, -5)?.kind).toBe("plane");
  });

  it("со стенки отверстия у обода ловится та же окружность", () => {
    const g = plate(rect(40, 30), [roundHole(4)]);
    const m = new MeshFeatures(g);
    const mesh = new THREE.Mesh(g);
    // Луч изнутри отверстия в стенку, чуть ниже верхнего края.
    const ray = new THREE.Raycaster(new THREE.Vector3(0, 0, 3.8), new THREE.Vector3(1, 0.1, 0).normalize());
    const [hit] = ray.intersectObject(mesh);
    const f = m.resolve(hit.faceIndex!, hit.point, 0.5);
    expect(f?.kind).toBe("circle");
    expect((f as CircleFeature).radius).toBeCloseTo(4, 5);
    // В середине стенки — не шов разбиения, а сама грань.
    const [mid] = new THREE.Raycaster(new THREE.Vector3(0, 0, 2), new THREE.Vector3(1, 0.1, 0).normalize()).intersectObject(mesh);
    expect(m.resolve(mid.faceIndex!, mid.point, 0.5)?.kind).toBe("plane");
  });

  it("скруглённый прямоугольник — четыре дуги и четыре ребра, не окружность", () => {
    // Основание ячейки Gridfinity: 41.5 мм, углы R4. Вершины на одном
    // расстоянии от центра — прежний разбор принимал это за «окружность R 25.5».
    const s = 41.5 / 2;
    const r = 4;
    const shape = new THREE.Shape()
      .moveTo(-s + r, -s).lineTo(s - r, -s).absarc(s - r, -s + r, r, -Math.PI / 2, 0, false)
      .lineTo(s, s - r).absarc(s - r, s - r, r, 0, Math.PI / 2, false)
      .lineTo(-s + r, s).absarc(-s + r, s - r, r, Math.PI / 2, Math.PI, false)
      .lineTo(-s, -s + r).absarc(-s + r, -s + r, r, Math.PI, 1.5 * Math.PI, false);
    const g = plate(shape, [], 8);
    const m = new MeshFeatures(g);
    const top = m.featuresOf(m.planeOf(faceAt(g, new THREE.Vector3(0, 0, 0))));
    const arcs = circles(top);
    expect(arcs).toHaveLength(4);
    for (const arc of arcs) {
      expect(arc.closed).toBe(false);
      expect(arc.radius).toBeCloseTo(4, 4);
      expect(arc.sweepDeg).toBeCloseTo(90, 1);
    }
    expect(edges(top)).toHaveLength(4);
  });

  it("полукруг — дуга и прямое ребро, хотя все точки контура на одной окружности", () => {
    // Концы прямой стороны тоже лежат на окружности: без проверки равенства
    // отрезков контур вышел бы «окружностью» — как у слайсера, отрезки обязаны
    // быть равными.
    const g = plate(new THREE.Shape().moveTo(-10, 0).absarc(0, 0, 10, Math.PI, 0, true).lineTo(-10, 0), [], 16);
    const m = new MeshFeatures(g);
    const top = m.featuresOf(m.planeOf(faceAt(g, new THREE.Vector3(0, 3, 0))));
    const found = circles(top);
    expect(found).toHaveLength(1);
    expect(found[0].closed).toBe(false);
    expect(found[0].sweepDeg).toBeCloseTo(180, 1);
    expect(edges(top)).toHaveLength(1);
  });

  it("шестигранник под гайку — шесть рёбер с центром", () => {
    const hex = new THREE.Path();
    for (let i = 0; i <= 6; i += 1) {
      const a = (i * Math.PI) / 3;
      const p = [7 + 3 * Math.cos(a), -2 + 3 * Math.sin(-a)] as const;
      if (i === 0) hex.moveTo(...p);
      else hex.lineTo(...p);
    }
    const g = plate(rect(40, 30), [hex]);
    const m = new MeshFeatures(g);
    const top = m.featuresOf(m.planeOf(faceAt(g, new THREE.Vector3(-15, -10, 0))));
    const sides = edges(top).filter((e) => e.centre);
    expect(sides).toHaveLength(6);
    expect(sides[0].centre!.x).toBeCloseTo(7, 5);
    expect(sides[0].centre!.y).toBeCloseTo(-2, 5);
  });

  it("у цилиндра three швы между гранями стенки — не рёбра", () => {
    const g = new THREE.CylinderGeometry(3, 3, 10, 48);
    const m = new MeshFeatures(g);
    const all = new Set<number>();
    for (let f = 0; f < g.index!.count / 3; f += 1) all.add(m.planeOf(f));
    const found = [...all].flatMap((p) => m.featuresOf(p));
    // Два торца — окружности; у 48 полосок стенки рёбер-швов нет, их края по
    // ободам — отрезки тех же окружностей.
    expect(found.filter((f) => f.kind === "circle")).toHaveLength(2);
    const seams = edges(found).filter((e) => Math.abs(e.a.y - e.b.y) > 1);
    expect(seams).toHaveLength(0);
  });

  it("подгонка окружности даёт центр, радиус и невязку", () => {
    const n = new THREE.Vector3(0, 0, 1);
    const pts = Array.from({ length: 12 }, (_, i) => {
      const a = (i / 12) * Math.PI * 2;
      return new THREE.Vector3(2 + 5 * Math.cos(a), -1 + 5 * Math.sin(a), 3);
    });
    const fit = fitCircle(pts, n)!;
    expect(fit.radius).toBeCloseTo(5, 9);
    expect(fit.centre.x).toBeCloseTo(2, 9);
    expect(fit.error).toBeLessThan(1e-9);
  });
});
