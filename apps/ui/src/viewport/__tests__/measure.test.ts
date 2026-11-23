/**
 * Снап точки замера.
 *
 * Главное, что здесь закреплено, — ПРЕДПОЧТЕНИЕ ВЕРШИН. Это не вкус: вершина
 * есть и в каноническом теле, а середина ребра может оказаться серединой
 * ДИАГОНАЛИ разбиения, которой у детали нет вовсе. На первом же настоящем
 * замере снап прилип именно к такой диагонали.
 */
import * as THREE from "three";
import { describe, expect, it } from "vitest";

import { snap, SNAP_PX } from "../measure";

const WIDTH = 800;
const HEIGHT = 600;

/** Камера, смотрящая на плоскость XY сверху: экран ≈ координаты, удобно считать. */
function camera(): THREE.OrthographicCamera {
  const cam = new THREE.OrthographicCamera(-50, 50, 37.5, -37.5, 0.1, 1000);
  cam.up.set(0, 1, 0);
  cam.position.set(0, 0, 100);
  cam.lookAt(0, 0, 0);
  cam.updateMatrixWorld(true);
  cam.updateProjectionMatrix();
  return cam;
}

/** Один треугольник как цель луча. */
function hitOn(point: THREE.Vector3): THREE.Intersection {
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.Float32BufferAttribute(
    [0, 0, 0, 40, 0, 0, 0, 30, 0], 3));
  const mesh = new THREE.Mesh(geometry, new THREE.MeshBasicMaterial());
  mesh.updateMatrixWorld(true);
  return {
    distance: 1,
    point: point.clone(),
    object: mesh,
    face: { a: 0, b: 1, c: 2, normal: new THREE.Vector3(0, 0, 1), materialIndex: 0 },
  } as unknown as THREE.Intersection;
}

function screenPixelsPerMm(): number {
  // Камера шириной 100 мм на 800 пикселей.
  return WIDTH / 100;
}

describe("снап", () => {
  it("у самого угла берёт вершину", () => {
    const result = snap(hitOn(new THREE.Vector3(0.1, 0.1, 0)), camera(),
                        WIDTH, HEIGHT, "деталь");
    expect(result.kind).toBe("вершина");
    expect(result.point.x).toBeCloseTo(0, 6);
    expect(result.point.y).toBeCloseTo(0, 6);
  });

  it("предпочитает вершину, даже когда точка разбиения БЛИЖЕ", () => {
    // Середина ребра (0,0)–(40,0) лежит в (20,0). Ставим попадание чуть правее
    // вершины (0,0), но так, чтобы обе оказались в допуске… это невозможно для
    // такого длинного ребра, поэтому берём короткий треугольник.
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", new THREE.Float32BufferAttribute(
      [0, 0, 0, 1, 0, 0, 0, 1, 0], 3));
    const mesh = new THREE.Mesh(geometry, new THREE.MeshBasicMaterial());
    mesh.updateMatrixWorld(true);
    const hit = {
      distance: 1,
      point: new THREE.Vector3(0.45, 0.02, 0),   // ближе к середине (0.5, 0)
      object: mesh,
      face: { a: 0, b: 1, c: 2, normal: new THREE.Vector3(0, 0, 1), materialIndex: 0 },
    } as unknown as THREE.Intersection;

    const result = snap(hit, camera(), WIDTH, HEIGHT, "деталь");
    expect(result.kind).toBe("вершина");
  });

  it("вдали от всего оставляет саму точку поверхности", () => {
    const point = new THREE.Vector3(15, 10, 0);
    const result = snap(hitOn(point), camera(), WIDTH, HEIGHT, "деталь");
    expect(result.kind).toBe("поверхность");
    expect(result.point.x).toBeCloseTo(15, 6);
  });

  it("допуск меряется в пикселях экрана, а не в миллиметрах", () => {
    // На таком увеличении 8 пикселей — это 1 мм.
    const mm = SNAP_PX / screenPixelsPerMm();
    expect(mm).toBeCloseTo(1, 6);

    const near = snap(hitOn(new THREE.Vector3(mm * 0.5, 0, 0)), camera(),
                      WIDTH, HEIGHT, "деталь");
    const far = snap(hitOn(new THREE.Vector3(mm * 3, 0, 0)), camera(),
                     WIDTH, HEIGHT, "деталь");
    expect(near.kind).toBe("вершина");
    expect(far.kind).toBe("поверхность");
  });

  it("запоминает, чья это деталь", () => {
    const result = snap(hitOn(new THREE.Vector3(0, 0, 0)), camera(),
                        WIDTH, HEIGHT, "крышка");
    expect(result.part).toBe("крышка");
  });
});
