/**
 * Экран до ответа сервера и деталь после него обязаны совпасть: окно двигает
 * показанную деталь, а записывает сдвиг и углы, по которым сервер её соберёт.
 */
import * as THREE from "three";
import { describe, expect, it } from "vitest";

import { placementAfter, rotationOf, serverWorld, visualOffset } from "../placement";

const close = (a: THREE.Vector3, b: THREE.Vector3) => {
  expect(a.x).toBeCloseTo(b.x, 3);
  expect(a.y).toBeCloseTo(b.y, 3);
  expect(a.z).toBeCloseTo(b.z, 3);
};

describe("положение детали", () => {
  it("углы — в порядке Manifold.rotate: X, затем Y, затем Z", () => {
    // Замерено на сервере: rotate((90, 90, 0)) уводит +X в −Z.
    const v = new THREE.Vector3(1, 0, 0).applyQuaternion(rotationOf([90, 90, 0]));
    close(v, new THREE.Vector3(0, 0, -1));
  });

  it("то, что показано после жеста, — то, что соберёт сервер", () => {
    const before = { move: [12, -3, 4], rotate: [10, -20, 35], centre: [100, 80, 7] };
    const pivotNow = new THREE.Vector3(100 + 12, 80 - 3, 7 + 4);
    const pivotNext = pivotNow.clone().add(new THREE.Vector3(-25, 40, 0));
    const delta = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 0, 1), 0.7)
      .multiply(new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(1, 0, 0), -0.3));

    const after = placementAfter(before, pivotNext, delta);
    const shown = visualOffset(before, pivotNext, delta);
    for (const local of [new THREE.Vector3(90, 70, 0), new THREE.Vector3(110, 95, 14),
                         new THREE.Vector3(100, 80, 7)]) {
      // На экране: деталь стояла по-старому, её повернули и сдвинули целиком.
      const world0 = serverWorld(before, local);
      const onScreen = world0.clone().applyQuaternion(shown.quaternion).add(shown.position);
      // После ответа сервера: по записанным числам.
      const rebuilt = serverWorld({ ...after, centre: before.centre }, local);
      close(onScreen, rebuilt);
    }
  });

  it("сдвиг без поворота — чистые числа, без хвостов", () => {
    const before = { move: [0, 0, 0], rotate: [0, 0, 0], centre: [128, 128, 10] };
    const after = placementAfter(before, new THREE.Vector3(138.00000001, 128, 10), new THREE.Quaternion());
    expect(after).toEqual({ move: [10, 0, 0], rotate: [0, 0, 0] });
  });

  it("полный оборот — это ноль", () => {
    const before = { move: [0, 0, 0], rotate: [0, 0, 350], centre: [0, 0, 0] };
    const q = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 0, 1), (10 * Math.PI) / 180);
    expect(placementAfter(before, new THREE.Vector3(), q).rotate).toEqual([0, 0, 0]);
  });
});
