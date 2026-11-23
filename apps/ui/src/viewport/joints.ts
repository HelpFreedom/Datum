/**
 * Ход шарнира в окне: то же жёсткое движение, что развёртывает проверка
 * (`checks/motion.py::joint_matrix`) — поворот на u° вокруг оси через точку по
 * правилу правой руки или сдвиг на u мм вдоль оси.
 *
 * Это ВИД, а не правка: положение звена в проект не пишется, а замер и срез,
 * пока звено не в собранном положении, гаснут — по тому же правилу, что при
 * разнесении (числа на экране обязаны быть числами конструкции).
 *
 * Модуль без DOM — его берёт тест.
 */
import * as THREE from "three";

export interface JointDTO {
  kind: "revolute" | "prismatic";
  /** Единичный вектор оси — сервер разбирает имя направления сам. */
  axis: number[];
  origin: number[];
  range: [number, number];
  /** id выведенной проверки хода — `крышка.шарнир`. */
  check: string;
}

/** Матрица звена в положении u — мир после движения = M · мир до. */
export function jointMatrix(joint: JointDTO, u: number): THREE.Matrix4 {
  const axis = new THREE.Vector3(...(joint.axis as [number, number, number])).normalize();
  if (joint.kind === "prismatic") {
    return new THREE.Matrix4().makeTranslation(axis.x * u, axis.y * u, axis.z * u);
  }
  const o = new THREE.Vector3(...(joint.origin as [number, number, number]));
  const turn = new THREE.Matrix4().makeRotationAxis(axis, THREE.MathUtils.degToRad(u));
  // p′ = R(p − o) + o
  return new THREE.Matrix4()
    .makeTranslation(o.x, o.y, o.z)
    .multiply(turn)
    .multiply(new THREE.Matrix4().makeTranslation(-o.x, -o.y, -o.z));
}

/**
 * Пределы ползунка: объявленный ход и собранное положение 0 (как деталь
 * построена) — ход часто начинают чуть дальше нуля, чтобы не считать
 * задуманное касание, а вернуть звено на место всё равно нужно.
 */
export function sliderRange(joint: JointDTO): [number, number] {
  return [Math.min(0, joint.range[0]), Math.max(0, joint.range[1])];
}

/** Положение в пределах ползунка. */
export function clampJoint(joint: JointDTO, u: number): number {
  const [lo, hi] = sliderRange(joint);
  return Math.min(hi, Math.max(lo, u));
}

/** Подпись положения: градусы у поворота, миллиметры у сдвига. */
export function jointLabel(joint: JointDTO, u: number): string {
  const v = Math.abs(u) < 1e-9 ? 0 : u;
  return joint.kind === "revolute" ? `${v.toFixed(1)}°` : `${v.toFixed(2)} мм`;
}

/** Шаг ползунка: десятая градуса или сотая миллиметра хода — плавно, но не дрожит. */
export function jointStep(joint: JointDTO): number {
  const [lo, hi] = sliderRange(joint);
  const span = hi - lo;
  return joint.kind === "revolute" ? Math.min(0.5, span / 200) : Math.min(0.1, span / 200);
}
