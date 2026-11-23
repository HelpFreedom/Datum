/**
 * Куб видов: грань, по которой щёлкнули, обязана оказаться лицом к зрителю и
 * надписью вверх — в той самой позе камеры, куда её ставит `setView`.
 *
 * Знак здесь легко потерять: мир правый, экран CSS с осью Y вниз — левый, и
 * ошибка в одном множителе даёт куб, вывернутый наизнанку, или надписи вверх
 * ногами. На глаз это видно не сразу: у куба шесть граней, и половина из них
 * на экране всё равно выглядит правдоподобно.
 */
import * as THREE from "three";
import { describe, expect, it } from "vitest";

import { FACES, faceMatrix, screenOf, VIEW_DIRECTIONS, viewUp } from "../orient";

THREE.Object3D.DEFAULT_UP.set(0, 0, 1);

/** Камера в позе вида — так же, как ставит её сцена. */
function cameraFor(view: keyof typeof VIEW_DIRECTIONS): THREE.PerspectiveCamera {
  const camera = new THREE.PerspectiveCamera();
  camera.up.set(...viewUp(view));
  camera.position.set(...VIEW_DIRECTIONS[view]).normalize().multiplyScalar(100);
  camera.lookAt(0, 0, 0);
  camera.updateMatrixWorld();
  return camera;
}

const close = (v: THREE.Vector3, x: number, y: number, z: number) => {
  expect(v.x).toBeCloseTo(x, 9);
  expect(v.y).toBeCloseTo(y, 9);
  expect(v.z).toBeCloseTo(z, 9);
};

describe("куб видов", () => {
  for (const face of FACES) {
    it(`«${face.label}» в своём виде — лицом к зрителю, надписью вверх`, () => {
      const m = screenOf(cameraFor(face.view)).multiply(faceMatrix(face.view, 1));
      // Столбцы итоговой матрицы — куда уходят оси грани на экране CSS.
      const right = new THREE.Vector3().setFromMatrixColumn(m, 0);
      const down = new THREE.Vector3().setFromMatrixColumn(m, 1);
      const normal = new THREE.Vector3().setFromMatrixColumn(m, 2);
      close(right, 1, 0, 0);   // строка текста идёт вправо
      close(down, 0, 1, 0);    // и вниз по экрану — то есть надпись не перевёрнута
      close(normal, 0, 0, 1);  // нормаль — на зрителя: грань видна лицом
    });
  }

  it("базис каждой грани — поворот, а не отражение", () => {
    // CSS прячет изнанку по знаку нормали; отражённый базис показал бы
    // грань изнанкой — зеркальный текст там, где должна быть лицевая сторона.
    for (const face of FACES) {
      const m = screenOf(cameraFor(face.view)).multiply(faceMatrix(face.view, 1));
      expect(new THREE.Matrix3().setFromMatrix4(m).determinant()).toBeCloseTo(1, 9);
    }
  });

  it("противоположная грань в том же виде — к зрителю изнанкой", () => {
    const camera = cameraFor("front");
    const back = screenOf(camera).multiply(faceMatrix("back", 1));
    expect(new THREE.Vector3().setFromMatrixColumn(back, 2).z).toBeCloseTo(-1, 9);
  });
});
