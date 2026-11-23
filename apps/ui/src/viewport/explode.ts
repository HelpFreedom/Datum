/**
 * Разнесённый вид: куда и насколько отодвинуть каждую деталь.
 *
 * Разносить по СМЕЩЕНИЮ ЦЕНТРОВ МАСС, как обычно делают, здесь было бы хуже:
 * такой вид показывает, где детали лежат, а спрашивают обычно другое — в каком
 * порядке и с какой стороны это собирается. Поэтому, когда у проекта есть
 * `assembly.yaml`, каждая деталь уезжает ВСТРЕЧНО СВОЕЙ ВСТАВКЕ, то есть туда,
 * откуда её принесли, а расстояние растёт по номеру в порядке сборки. Картинка
 * тогда читается как последовательность: первая деталь на месте, остальные
 * выстроены по дороге к ней.
 *
 * Без `assembly.yaml` остаётся обычное радиальное разнесение — оно ничего не
 * утверждает о порядке, и это честно: порядка мы не знаем.
 */

export interface AssemblyStep {
  method: string;
  insert: string[];
  distance: number;
}

export interface AssemblyInfo {
  order: string[];
  explode_gap: number;
  steps: Record<string, AssemblyStep>;
}

export type Vec3 = [number, number, number];
export type Offsets = Record<string, Vec3>;

/** Куда указывает имя направления. Совпадает с `datum.doc.assembly`. */
const AXES: Record<string, Vec3> = {
  "+X": [1, 0, 0],
  "-X": [-1, 0, 0],
  "+Y": [0, 1, 0],
  "-Y": [0, -1, 0],
  "+Z": [0, 0, 1],
  "-Z": [0, 0, -1],
};

/** Зазор, когда о сборке ничего не известно. То же число, что в модели. */
export const DEFAULT_GAP = 25;

/**
 * Умножить направление на длину, без минус-нуля.
 *
 * `-1 * 0` даёт в JS `-0`, и это не придирка: `-0` просачивается в позиции
 * объектов, в сравнения и в подписи («−0.00 мм»). Чистим у источника.
 */
function scaled(dir: Vec3, length: number): Vec3 {
  return dir.map((v) => (v * length === 0 ? 0 : v * length)) as Vec3;
}

function centre(box: number[] | undefined): Vec3 | null {
  if (!box || box.length !== 6) return null;
  return [(box[0] + box[3]) / 2, (box[1] + box[4]) / 2, (box[2] + box[5]) / 2];
}

function normalise(v: Vec3): Vec3 {
  const n = Math.hypot(v[0], v[1], v[2]);
  // Деталь, чей центр совпал с центром сборки, разносить некуда — и это
  // обычное дело для основания. Отправляем вверх: любой другой выбор был бы
  // такой же выдумкой, а вверх хотя бы не заслонит соседей.
  return n < 1e-6 ? [0, 0, 1] : [v[0] / n, v[1] / n, v[2] / n];
}

/** Радиальное направление «от середины сборки наружу». */
function radial(name: string, boxes: Record<string, number[]>): Vec3 {
  const own = centre(boxes[name]);
  if (!own) return [0, 0, 1];
  const all = Object.values(boxes)
    .map(centre)
    .filter((c): c is Vec3 => c !== null);
  if (all.length === 0) return [0, 0, 1];
  const mid: Vec3 = [
    all.reduce((s, c) => s + c[0], 0) / all.length,
    all.reduce((s, c) => s + c[1], 0) / all.length,
    all.reduce((s, c) => s + c[2], 0) / all.length,
  ];
  return normalise([own[0] - mid[0], own[1] - mid[1], own[2] - mid[2]]);
}

/**
 * Смещения при полном разнесении. Промежуточные состояния — просто доля от них,
 * поэтому считать это на каждый кадр ползунка не нужно.
 */
export function explodeOffsets(
  assembly: AssemblyInfo | null,
  boxes: Record<string, number[]>,
): Offsets {
  const gap = assembly?.explode_gap && assembly.explode_gap > 0
    ? assembly.explode_gap
    : DEFAULT_GAP;
  const out: Offsets = {};
  const order = assembly?.order ?? [];
  const inOrder = new Set(order);

  order.forEach((name, i) => {
    // Первая деталь — основание, её никуда не вставляли: она и остаётся на
    // месте, чтобы остальным было относительно чего разъезжаться.
    if (i === 0) {
      out[name] = [0, 0, 0];
      return;
    }
    const insert = assembly?.steps[name]?.insert ?? [];
    // Деталь уезжает ВСТРЕЧНО вставке — туда, откуда её принесли.
    const dir = insert.length > 0 && AXES[insert[0]]
      ? (AXES[insert[0]].map((v) => -v) as Vec3)
      : radial(name, boxes);
    out[name] = scaled(dir, gap * i);
  });

  // Детали вне порядка сборки. Они не часть последовательности, и делать вид,
  // что часть, нельзя: разносим радиально и ровно на один зазор, чтобы их было
  // видно отдельно и не спутать с шагом сборки.
  for (const name of Object.keys(boxes)) {
    if (inOrder.has(name)) continue;
    out[name] = scaled(radial(name, boxes), gap);
  }
  return out;
}
