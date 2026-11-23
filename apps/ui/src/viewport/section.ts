/**
 * Секущая плоскость.
 *
 * Тонкий момент, ради которого всё и устроено именно так. Отсечение во время
 * перетаскивания делает сам three.js (`clippingPlanes`) — это мгновенно и не
 * стоит ничего. Но отсечённая деталь выглядит ПОЛОЙ: видно изнанку стенок, и
 * понять, где материал, а где пустота, нельзя.
 *
 * Поэтому, когда движение остановилось, окно спрашивает у сервера ТОЧНЫЕ
 * контуры среза и заливает по ним крышку. Крышка считается на канонической
 * геометрии — той же, по которой работают проверки, — значит показанный срез и
 * измеренный срез это ОДИН И ТОТ ЖЕ срез. Считай окно срез само по мешу, числа
 * на экране и в проверках однажды разошлись бы, и заметить это было бы нечем.
 */
import * as THREE from "three";

export type PlaneName = "xy" | "xz" | "yz";

export interface SectionContour {
  shell: [number, number][];
  holes: [number, number][][];
}

export interface SectionAnswer {
  part: string;
  empty: boolean;
  contours?: SectionContour[];
  area_mm2?: number;
  pieces?: number;
  holes?: number;
}

/** Срез всех названных деталей одной плоскостью — ответ `section_scene`. */
export interface SceneSectionAnswer {
  parts: Record<string, SectionAnswer>;
  /** Детали, которые срезать не вышло (не собрались): имя → причина. */
  failed: Record<string, string>;
}

/**
 * Запрос среза у сервера: окно само его не считает. ОДИН запрос на все
 * видимые детали: прежде на каждую остановку ползунка шло по заданию на
 * деталь, и у проекта в восемнадцать деталей лента и очередь заданий
 * забивались срезами.
 */
export type SectionFn = (
  parts: string[],
  plane: PlaneName,
  at: number,
) => Promise<SceneSectionAnswer>;

/**
 * Нормаль плоскости и знак.
 *
 * Отсекается то, что ЗА плоскостью по её нормали, поэтому нормаль смотрит в
 * сторону отбрасываемого: человек тянет плоскость «вглубь» детали и видит то,
 * что осталось до неё.
 */
const NORMALS: Record<PlaneName, THREE.Vector3> = {
  xy: new THREE.Vector3(0, 0, -1),
  xz: new THREE.Vector3(0, -1, 0),
  yz: new THREE.Vector3(-1, 0, 0),
};

export function clipPlane(plane: PlaneName, at: number): THREE.Plane {
  const normal = NORMALS[plane].clone();
  // Plane(n, d): n·p + d = 0. Нормаль отрицательная, поэтому d = +at.
  return new THREE.Plane(normal, at);
}

/**
 * Точка плоскости сечения обратно в пространство.
 *
 *     xy (z = at) → (u, v, at)
 *     xz (y = at) → (u, at, v)
 *     yz (x = at) → (at, u, v)
 *
 * Это ровно то отображение, что закреплено тестами на стороне сервера. Ошибка
 * здесь положила бы крышку не туда, и это было бы видно глазом — что и хорошо.
 */
export function toWorld(plane: PlaneName, at: number, u: number, v: number): THREE.Vector3 {
  if (plane === "xy") return new THREE.Vector3(u, v, at);
  if (plane === "xz") return new THREE.Vector3(u, at, v);
  return new THREE.Vector3(at, u, v);
}

/** Крышка среза для одной детали: плоская заливка по контурам. */
export function buildCap(
  answer: SectionAnswer,
  plane: PlaneName,
  at: number,
  color: THREE.Color,
): THREE.Mesh | null {
  if (answer.empty || !answer.contours?.length) return null;

  const shapes = answer.contours.map((piece) => {
    const shape = new THREE.Shape(
      piece.shell.map(([u, v]) => new THREE.Vector2(u, v)),
    );
    for (const hole of piece.holes) {
      shape.holes.push(
        new THREE.Path(hole.map(([u, v]) => new THREE.Vector2(u, v))),
      );
    }
    return shape;
  });

  // ShapeGeometry триангулирует с дырами (earcut внутри three) и кладёт
  // результат в плоскость XY. Дальше остаётся перенести его в нужную.
  const geometry = new THREE.ShapeGeometry(shapes);
  const position = geometry.getAttribute("position");
  const moved = new Float32Array(position.count * 3);
  for (let i = 0; i < position.count; i += 1) {
    const point = toWorld(plane, at, position.getX(i), position.getY(i));
    moved[i * 3] = point.x;
    moved[i * 3 + 1] = point.y;
    moved[i * 3 + 2] = point.z;
  }
  geometry.setAttribute("position", new THREE.BufferAttribute(moved, 3));
  geometry.computeVertexNormals();

  const mesh = new THREE.Mesh(
    geometry,
    new THREE.MeshBasicMaterial({
      // Крышка чуть светлее детали: срез должен читаться как срез, а не как
      // ещё одна грань.
      color: color.clone().lerp(new THREE.Color(0xffffff), 0.14),
      side: THREE.DoubleSide,
      // Крышку саму отсекать нельзя — она и есть граница отсечения.
      clippingPlanes: [],
    }),
  );
  mesh.renderOrder = 1;
  return mesh;
}
