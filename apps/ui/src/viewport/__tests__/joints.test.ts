/**
 * Ход шарнира в окне обязан совпадать с ходом в проверке: те же числа, что
 * держит `py/tests/unit/test_joints.py::test_the_rotation_follows_the_right_hand_rule…`.
 */
import * as THREE from "three";
import { describe, expect, it } from "vitest";

import { clampJoint, jointLabel, jointMatrix, sliderRange, type JointDTO } from "../joints";

const hinge = (axis: number[], origin: number[], range: [number, number] = [0, 180]): JointDTO => ({
  kind: "revolute",
  axis,
  origin,
  range,
  check: "крышка.шарнир",
});

const apply = (m: THREE.Matrix4, p: number[]) =>
  new THREE.Vector3(...(p as [number, number, number])).applyMatrix4(m).toArray().map((v) => +v.toFixed(9));

describe("шарнир в окне — то же движение, что в проверке", () => {
  it("поворот по правилу правой руки вокруг своей точки", () => {
    expect(apply(jointMatrix(hinge([0, 0, 1], [1, 1, 0]), 90), [2, 1, 0])).toEqual([1, 2, 0]);
  });

  it("крышка на петле -X поднимается, а не уходит под стол", () => {
    // Петля вдоль X у задней кромки (y = 151.5, z = 26.2), крышка — к −Y.
    const m = jointMatrix(hinge([-1, 0, 0], [0, 151.5, 26.2]), 90);
    const [, y, z] = apply(m, [30, 111.5, 26.2]);
    expect(y).toBeCloseTo(151.5, 6);
    expect(z).toBeCloseTo(66.2, 6);
  });

  it("ползун сдвигается вдоль оси на миллиметры", () => {
    const slider: JointDTO = { kind: "prismatic", axis: [0, -1, 0], origin: [0, 0, 0], range: [0, 25], check: "п.ход" };
    expect(apply(jointMatrix(slider, 7), [1, 2, 3])).toEqual([1, -5, 3]);
    expect(jointLabel(slider, 7)).toBe("7.00 мм");
  });

  it("ползунок — объявленный ход и собранное положение 0", () => {
    const j = hinge([0, 1, 0], [0, 0, 0], [5, 120]);
    expect(sliderRange(j)).toEqual([0, 120]);
    expect(clampJoint(j, 200)).toBe(120);
    expect(clampJoint(j, -3)).toBe(0);
    expect(sliderRange(hinge([0, 1, 0], [0, 0, 0], [-30, -5]))).toEqual([-30, 0]);
    expect(jointLabel(j, -0)).toBe("0.0°");
  });
});
