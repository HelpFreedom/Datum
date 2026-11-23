/**
 * Перетаскивание и поворот детали — в числах, которые запишет сервер.
 *
 * Сервер ставит деталь так (`doc/placement.py`): повернуть вокруг центра
 * габарита c, какой её вернул скрипт, затем сдвинуть на move. Мир = R(x − c) +
 * c + move, где R — углы X→Y→Z (матрица Rz·Ry·Rx, Euler «ZYX» у three).
 *
 * Окно показывает уже поставленную деталь и крутит её вокруг ОПОРЫ p = c +
 * move — той самой точки, которую поворот сервера не сдвигает. Жест даёт сдвиг
 * опоры в p′ и добавочный поворот Q; новое положение: R′ = Q·R, move′ = p′ − c.
 * Тогда экран до ответа сервера и деталь после него совпадают точно — это и
 * проверяет тест.
 *
 * Модуль без DOM — его берёт тест.
 */
import * as THREE from "three";

export interface PlacementDTO {
  move: number[];
  rotate: number[];
  centre: number[];
}

const DEG = Math.PI / 180;

/** Поворот положения: углы X→Y→Z, как у Manifold.rotate. */
export function rotationOf(rotateDeg: number[]): THREE.Quaternion {
  const [x = 0, y = 0, z = 0] = rotateDeg;
  return new THREE.Quaternion().setFromEuler(new THREE.Euler(x * DEG, y * DEG, z * DEG, "ZYX"));
}

/** Опора жеста — точка, которую поворот сервера оставляет на месте. */
export function pivotOf(p: PlacementDTO): THREE.Vector3 {
  return new THREE.Vector3(...(p.centre as [number, number, number]))
    .add(new THREE.Vector3(...(p.move as [number, number, number])));
}

/** Число для файла: без «-0» и хвостов float. */
const clean = (v: number, digits: number) => {
  const r = Number(v.toFixed(digits));
  return Object.is(r, -0) ? 0 : r;
};

/** Угол в (−180, 180]: 360° и 0° — одно и то же, и в файл идёт короткое. */
const wrap = (deg: number) => {
  let d = ((deg + 180) % 360 + 360) % 360 - 180;
  if (d === -180) d = 180;
  return d;
};

/** Новое положение после жеста: опору унесли в `pivot`, повернули на `delta`. */
export function placementAfter(
  before: PlacementDTO,
  pivot: THREE.Vector3,
  delta: THREE.Quaternion,
): { move: number[]; rotate: number[] } {
  const turned = delta.clone().multiply(rotationOf(before.rotate));
  const euler = new THREE.Euler().setFromQuaternion(turned, "ZYX");
  const c = new THREE.Vector3(...(before.centre as [number, number, number]));
  const move = pivot.clone().sub(c);
  return {
    move: [clean(move.x, 4), clean(move.y, 4), clean(move.z, 4)],
    rotate: [euler.x, euler.y, euler.z].map((a) => clean(wrap(a / DEG), 3)),
  };
}

/**
 * Как сдвинуть показанную деталь, пока сервер не прислал новую: объект стоял
 * в мировых координатах, опора ушла из p в p′ с поворотом Q → позиция p′ − Q·p.
 */
export function visualOffset(
  before: PlacementDTO,
  pivot: THREE.Vector3,
  delta: THREE.Quaternion,
): { position: THREE.Vector3; quaternion: THREE.Quaternion } {
  const p = pivotOf(before);
  return {
    position: pivot.clone().sub(p.clone().applyQuaternion(delta)),
    quaternion: delta.clone(),
  };
}

/** Мировая точка детали по формуле сервера — для теста и проверок. */
export function serverWorld(p: { move: number[]; rotate: number[]; centre: number[] }, local: THREE.Vector3): THREE.Vector3 {
  const c = new THREE.Vector3(...(p.centre as [number, number, number]));
  return local.clone().sub(c).applyQuaternion(rotationOf(p.rotate)).add(c)
    .add(new THREE.Vector3(...(p.move as [number, number, number])));
}
