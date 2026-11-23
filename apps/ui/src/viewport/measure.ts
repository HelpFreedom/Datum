/**
 * Измерительные инструменты вьюпорта.
 *
 * РАЗДЕЛЕНИЕ ТРУДА ЗДЕСЬ НЕ СЛУЧАЙНО. Расстояние точка-точка и координаты
 * считаются в окне: ответ нужен мгновенно, а вершины меша — те же самые, что в
 * каноническом теле, потому что GLB пишется без слияния вершин. Толщина стенки
 * считается НА СЕРВЕРЕ: у окна на руках тесселяция, годная чтобы показать, а
 * число, которое человек запишет в параметр, обязано совпасть с тем, что
 * увидит проверка `min_wall`.
 *
 * СНАП. Голый луч попадает в случайную точку грани, и «расстояние между двумя
 * отверстиями» так не померить. Поэтому кандидаты — вершины треугольника, его
 * середины рёбер и центр грани; берётся ближайший в пределах 8 пикселей ЭКРАНА,
 * а не миллиметров: допуск должен быть одинаков на любом увеличении.
 */
import * as THREE from "three";

/** Инструменты вьюпорта: обзор, замеры и правка положения детали. */
export type Tool = "none" | "distance" | "probe" | "move" | "rotate";

export const SNAP_PX = 8;

export interface SnapPoint {
  point: THREE.Vector3;
  normal: THREE.Vector3;
  kind: "вершина" | "середина ребра" | "центр грани" | "поверхность";
  part: string;
}

/**
 * Кандидаты на снап, СГРУППИРОВАННЫЕ ПО ОСМЫСЛЕННОСТИ.
 *
 * Вершина — точка настоящей геометрии: она есть и в каноническом теле. Середина
 * ребра и центр грани — точки РАЗБИЕНИЯ: у прямоугольной грани, разрезанной на
 * два треугольника, диагональ вообще не существует как ребро детали, и её
 * середина человеку ничего не говорит. Поэтому вершины пробуются первыми, и
 * только если ни одна не попала в допуск — остальное.
 */
function candidates(hit: THREE.Intersection): { point: THREE.Vector3; kind: SnapPoint["kind"] }[][] {
  const mesh = hit.object as THREE.Mesh;
  const face = hit.face;
  const position = mesh.geometry?.getAttribute("position");
  if (!face || !position) return [];

  const corners = [face.a, face.b, face.c].map((index) =>
    new THREE.Vector3().fromBufferAttribute(position as THREE.BufferAttribute, index)
      .applyMatrix4(mesh.matrixWorld),
  );
  const [a, b, c] = corners;
  return [
    [
      { point: a, kind: "вершина" as const },
      { point: b, kind: "вершина" as const },
      { point: c, kind: "вершина" as const },
    ],
    [
      { point: a.clone().add(b).multiplyScalar(0.5), kind: "середина ребра" as const },
      { point: b.clone().add(c).multiplyScalar(0.5), kind: "середина ребра" as const },
      { point: c.clone().add(a).multiplyScalar(0.5), kind: "середина ребра" as const },
      {
        point: a.clone().add(b).add(c).multiplyScalar(1 / 3),
        kind: "центр грани" as const,
      },
    ],
  ];
}

/** Ближайший кандидат в пределах SNAP_PX пикселей экрана; иначе — сама точка. */
export function snap(
  hit: THREE.Intersection,
  camera: THREE.Camera,
  width: number,
  height: number,
  part: string,
): SnapPoint {
  const toScreen = (v: THREE.Vector3) => {
    const p = v.clone().project(camera);
    return new THREE.Vector2(((p.x + 1) / 2) * width, ((1 - p.y) / 2) * height);
  };

  const target = toScreen(hit.point);
  let best: { point: THREE.Vector3; kind: SnapPoint["kind"] } | null = null;
  for (const group of candidates(hit)) {
    let bestDistance = SNAP_PX;
    for (const candidate of group) {
      const distance = toScreen(candidate.point).distanceTo(target);
      if (distance < bestDistance) {
        bestDistance = distance;
        best = candidate;
      }
    }
    if (best) break;            // вершина нашлась — к точкам разбиения не идём
  }

  const normal = hit.face
    ? hit.face.normal
        .clone()
        .applyNormalMatrix(
          new THREE.Matrix3().getNormalMatrix((hit.object as THREE.Mesh).matrixWorld),
        )
        .normalize()
    : new THREE.Vector3(0, 0, 1);

  return {
    point: (best?.point ?? hit.point).clone(),
    normal,
    kind: best?.kind ?? "поверхность",
    part,
  };
}

/** Элемент, выбранный штангенциркулем, — в мировых координатах, для сервера. */
export type PickedElement = { kind: string; part: string } & (
  | { type: "point"; point: number[] }
  | { type: "edge"; a: number[]; b: number[] }
  | { type: "circle"; centre: number[]; normal: number[]; radius: number; closed: boolean }
  | { type: "plane"; origin: number[]; normal: number[]; point: number[] }
);

/** Подпись замера: что показать человеку. */
export interface Readout {
  tool: Tool;
  text: string;
  detail: string;
  /** Что выбрано штангенциркулем — окно сообщает это агенту (`window_report`). */
  picked?: PickedElement[];
}
