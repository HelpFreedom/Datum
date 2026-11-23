/**
 * Штангенциркуль: что ловится при наведении и что меряется между выбранным.
 * Плита 40×30×4 с отверстием R4 в центре, Z вверх; камера сверху.
 */
import * as THREE from "three";
import { describe, expect, it } from "vitest";

import { centreSnap, insideRound, measureBetween, resolveSnap, type Snap, snapLabel } from "../caliper";
import { MeshFeatures } from "../features";

const W = 800;
const H = 600;

function setup() {
  const shape = new THREE.Shape().moveTo(-20, -15).lineTo(20, -15).lineTo(20, 15).lineTo(-20, 15);
  shape.holes.push(new THREE.Path().absarc(0, 0, 4, 0, Math.PI * 2, true));
  const geometry = new THREE.ExtrudeGeometry(shape, { depth: 4, bevelEnabled: false, curveSegments: 32 });
  const mesh = new THREE.Mesh(geometry, new THREE.MeshBasicMaterial());
  mesh.position.set(100, 50, 0);                            // деталь не в начале координат
  mesh.updateMatrixWorld(true);
  const camera = new THREE.PerspectiveCamera(45, W / H, 0.5, 5000);
  camera.position.set(100, 50.001, 90);
  camera.up.set(0, 1, 0);
  camera.lookAt(100, 50, 0);
  camera.updateMatrixWorld(true);
  const features = new MeshFeatures(geometry);
  const cursorOf = (world: THREE.Vector3) => {
    const p = world.clone().project(camera);
    return new THREE.Vector2(((p.x + 1) / 2) * W, ((1 - p.y) / 2) * H);
  };
  /** Навести на точку мира (луч из камеры через неё). */
  const at = (world: THREE.Vector3, free = false): Snap | null => {
    const p = world.clone().project(camera);
    const ray = new THREE.Raycaster();
    ray.setFromCamera(new THREE.Vector2(p.x, p.y), camera);
    const [hit] = ray.intersectObject(mesh);
    if (!hit) return null;
    return resolveSnap({ hit, part: "плита", onCap: false }, features, camera, H, free);
  };
  /** Навести на грань со стороны — лучом по направлению. */
  const side = (from: THREE.Vector3, dir: THREE.Vector3): Snap => {
    const [hit] = new THREE.Raycaster(from, dir.normalize()).intersectObject(mesh);
    return resolveSnap({ hit, part: "плита", onCap: false }, features, camera, H);
  };
  return { at, side, camera, cursorOf };
}

const w = (x: number, y: number, z: number) => new THREE.Vector3(100 + x, 50 + y, z);

describe("штангенциркуль: наведение", () => {
  it("у обода отверстия — окружность Ø8 в мировых координатах", () => {
    const snap = setup().at(w(4.4, 0.3, 4))!;
    expect(snap.kind).toBe("окружность");
    expect(snapLabel(snap)).toBe("окружность · Ø 8");
    const f = snap.feature as { centre: THREE.Vector3; radius: number };
    expect(f.radius).toBeCloseTo(4, 5);
    expect(f.centre.x).toBeCloseTo(100, 5);
    expect(f.centre.y).toBeCloseTo(50, 5);
    expect(f.centre.z).toBeCloseTo(4, 5);
  });

  it("у угла — вершина, у стороны — ребро, в стороне от всего — грань", () => {
    const { at } = setup();
    const corner = at(w(19.8, 14.8, 4))!;
    expect(corner.kind).toBe("вершина");
    expect((corner.feature as { point: THREE.Vector3 }).point.x).toBeCloseTo(120, 5);
    expect(at(w(19.8, 3, 4))!.kind).toBe("ребро");
    expect(at(w(-10, -7, 4))!.kind).toBe("грань");
  });

  it("с Shift — просто точка на поверхности", () => {
    const snap = setup().at(w(19.8, 14.8, 4), true)!;
    expect(snap.kind).toBe("точка");
    expect((snap.feature as { point: THREE.Vector3 }).point.x).toBeCloseTo(119.8, 4);
  });

  it("центр отверстия ловится, пока курсор внутри окружности — и там, где под курсором пусто", () => {
    const { at, camera, cursorOf } = setup();
    const rim = at(w(4.4, 0.3, 4))!;
    expect(rim.round).toBeTruthy();
    // Над центром сквозного отверстия луч ни во что не попадает.
    expect(at(w(0, 0, 4))).toBeNull();
    const centre = centreSnap(rim.round!, rim.part, cursorOf(w(0.1, 0, 4)), camera, W, H)!;
    expect(centre.kind).toBe("центр окружности");
    expect(snapLabel(centre)).toBe("центр окружности · Ø 8");
    expect(centre.point.x).toBeCloseTo(100, 5);
    // Внутри окружности, но далеко от центра — ещё «внутри», но не центр.
    expect(insideRound(rim.round!, cursorOf(w(3, 0, 4)), camera, W, H)).toBe(true);
    expect(centreSnap(rim.round!, rim.part, cursorOf(w(3, 0, 4)), camera, W, H)).toBeNull();
    expect(insideRound(rim.round!, cursorOf(w(12, 0, 4)), camera, W, H)).toBe(false);
  });
});

describe("штангенциркуль: замер между выбранным", () => {
  it("две параллельные грани — толщина плиты", () => {
    const { at, side } = setup();
    const top = at(w(-10, -7, 4))!;
    const bottom = side(w(-10, -7, -20), new THREE.Vector3(0, 0, 1));
    expect(bottom.kind).toBe("грань");
    const m = measureBetween(top, bottom);
    expect(m.text).toBe("4 мм");
    expect(m.detail).toContain("параллельны");
  });

  it("верх и торец — угол 90°", () => {
    const { at, side } = setup();
    const top = at(w(-10, -7, 4))!;
    const end = side(w(40, 0, 2), new THREE.Vector3(-1, 0, 0));
    expect(end.kind).toBe("грань");
    expect(measureBetween(top, end).text).toBe("90°");
  });

  it("вершина и окружность — до центра, и отдельно до обода", () => {
    const { at } = setup();
    const m = measureBetween(at(w(19.8, 14.8, 4))!, at(w(4.4, 0.3, 4))!);
    expect(m.text).toBe(`${(25).toLocaleString("ru-RU")} мм`);   // 3-4-5: (20, 15) от центра
    expect(m.detail).toContain("до окружности: 21 мм");
  });

  it("проекции ΔX ΔY ΔZ — от первого выбранного ко второму, в каком порядке ни щёлкни", () => {
    const { at } = setup();
    const circle = at(w(4.4, 0.3, 4))!;
    const corner = at(w(19.8, 14.8, 4))!;
    expect(measureBetween(circle, corner).detail).toContain("ΔX 20 · ΔY 15 · ΔZ 0");
    expect(measureBetween(corner, circle).detail).toContain("ΔX -20 · ΔY -15 · ΔZ 0");
  });

  it("вершина и ребро — по перпендикуляру к ребру", () => {
    const { at } = setup();
    const m = measureBetween(at(w(-19.8, -14.8, 4))!, at(w(19.8, 3, 4))!);
    expect(m.text).toBe("40 мм");
    expect(m.line![1].x).toBeCloseTo(120, 5);
  });

  it("два соседних ребра — угол 90°", () => {
    const { at } = setup();
    const m = measureBetween(at(w(19.8, 3, 4))!, at(w(5, 14.8, 4))!);
    expect(m.text).toBe("90°");
  });
});
