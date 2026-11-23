/**
 * Геометрия куба видов и триады осей — отдельно от разметки, чтобы её брал тест.
 *
 * Как грань ставится. Для взгляда на грань снаружи (по нормали n, «вверх» на
 * экране — u) вправо смотрит u × n, вниз −u. Такой базис вместе с переводом
 * мира в экран CSS (ось Y вниз) даёт собственный поворот, и грань видна
 * лицом. Экран CSS = diag(1, −1, 1) · Rᵀ · мир, где R — поворот камеры.
 */
import * as THREE from "three";

import type { ViewName } from "./scene";

type Vec = [number, number, number];

/** Откуда смотрит камера в каждом виде — единственное определение. */
export const VIEW_DIRECTIONS: Record<ViewName, Vec> = {
  iso: [1, -1, 0.8],
  top: [0, 0, 1],
  bottom: [0, 0, -1],
  front: [0, -1, 0],
  back: [0, 1, 0],
  left: [-1, 0, 0],
  right: [1, 0, 0],
};

/**
 * «Вверх» камеры. Взгляд строго вдоль Z совпал бы с осью «вверх», и камера
 * потеряла бы ориентацию. Подменяем «вверх» на ось Y — так же поступает слайсер.
 */
export function viewUp(view: ViewName): Vec {
  if (view === "top") return [0, 1, 0];
  if (view === "bottom") return [0, -1, 0];
  return [0, 0, 1];
}

/** Грани куба — виды вдоль осей; изометрии своей грани нет. */
export const FACES: { view: Exclude<ViewName, "iso">; label: string }[] = [
  { view: "top", label: "Сверху" },
  { view: "bottom", label: "Снизу" },
  { view: "front", label: "Спереди" },
  { view: "back", label: "Сзади" },
  { view: "right", label: "Справа" },
  { view: "left", label: "Слева" },
];

/**
 * Грань куба в его координатах (мировых): столбцы — вправо, вниз, нормаль,
 * центр. Нормаль и «верх» — ровно те, с которыми камера встаёт в этот вид,
 * поэтому по щелчку грань оказывается прямо перед глазами и надписью вверх.
 */
export function faceMatrix(view: ViewName, half: number): THREE.Matrix4 {
  const normal = new THREE.Vector3(...VIEW_DIRECTIONS[view]).normalize();
  const up = new THREE.Vector3(...viewUp(view));
  const right = up.clone().cross(normal);
  const down = up.clone().negate();
  return new THREE.Matrix4().set(
    right.x, down.x, normal.x, normal.x * half,
    right.y, down.y, normal.y, normal.y * half,
    right.z, down.z, normal.z, normal.z * half,
    0, 0, 0, 1,
  );
}

/** Мир → экран CSS для поворота камеры: diag(1,−1,1) · Rᵀ. */
export function screenOf(camera: THREE.Camera): THREE.Matrix4 {
  const m = new THREE.Matrix4().makeRotationFromQuaternion(camera.quaternion).transpose();
  return new THREE.Matrix4().makeScale(1, -1, 1).multiply(m);
}

/** Матрица для `transform: matrix3d(...)`: и у three, и у CSS порядок по столбцам. */
export const css = (m: THREE.Matrix4): string => `matrix3d(${m.elements.join(",")})`;
