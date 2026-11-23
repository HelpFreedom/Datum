/**
 * Черты детали для «штангенциркуля» — так, как их ищет ElegooSlicer.
 *
 * Разбор взят у слайсера (ElegooSlicer ← OrcaSlicer ← PrusaSlicer,
 * `libslic3r/Measure.cpp`), потому что он решает то, на чём спотыкался прежний
 * — по изломам рёбер всей сетки:
 *
 * 1. Сетка делится на ГРАНИ: соседние треугольники одной плоскости — одна
 *    грань. Черты ищутся только на контуре той грани, что под курсором, —
 *    поэтому ребро дна отверстия, лежащее на экране рядом с ободом, не
 *    ловится с верхней грани: оно не её.
 * 2. Контур грани разбирается так:
 *    - весь контур — окружность, если его точки лежат на ней и отрезки
 *      РАВНЫ (у скруглённого прямоугольника они то короткие в углах, то во всю
 *      сторону — окружностью он не выходит);
 *    - пять–восемь равных отрезков — правильный многоугольник: его рёбра и
 *      центр (шестигранник под гайку);
 *    - иначе в контуре ищутся дуги — участки с равным поворотом и равными
 *      отрезками, не меньше 5 точек и 81°; остальное — рёбра, соосные
 *      сливаются в одно.
 * 3. У наведения — ближайшая черта у точки попадания; у конца ребра — вершина;
 *    ничего рядом — вся грань.
 *
 * Своё добавлено в двух местах, оба — по замеру на живых деталях.
 * Грань стенки отверстия — узкая полоска между соседними; её край по ободу
 * отдаёт окружность соседней грани: иначе со стенки ловились бы только
 * отрезки. И ребро между гранями почти одной плоскости (меньше
 * `SOFT_EDGE_DEG`) — шов разбиения кривой поверхности, а не ребро детали: у
 * цилиндра на 64 гранях таких швов 64, и слайсер ловит каждый.
 *
 * Числа снимаются с вершин того же меша, что показан: у manifold3d вершины
 * отверстия лежат НА окружности, и подгонка даёт настоящий радиус. Это замер
 * окна (как расстояние точка-точка), а не проверки: толщину стенки меряет
 * сервер.
 *
 * Модуль без DOM — его берёт тест.
 */
import * as THREE from "three";

/** Больше этого сетка делится на грани дольше, чем ждёт наведение мыши. */
export const MAX_FEATURE_TRIANGLES = 1_500_000;

/** Излом меньше этого — шов разбиения кривой поверхности, а не ребро. */
export const SOFT_EDGE_DEG = 10;

/** Невязка подгонки окружности, мм (как у слайсера — 0.05). */
const FIT_MM = 0.05;
/** Равенство поворотов в контуре, рад (как у слайсера — 0.01). */
const TURN_EQ = 0.01;
/** Равенство длин отрезков — доля длины. */
const LEN_EQ = 0.02;
/** Самая пологая дуга, которую стоит ловить: 0.9 · 90°, как у слайсера. */
const MIN_ARC_RAD = 0.9 * (Math.PI / 2);

export interface PointFeature { kind: "point"; point: THREE.Vector3 }
export interface EdgeFeature {
  kind: "edge";
  a: THREE.Vector3;
  b: THREE.Vector3;
  /** Центр правильного многоугольника, стороной которого ребро служит. */
  centre?: THREE.Vector3;
  /** Одиночный отрезок сетки (не слитый) — и грань по другую его сторону. */
  chord?: { u: number; v: number; across: number };
}
export interface CircleFeature {
  kind: "circle";
  centre: THREE.Vector3;
  normal: THREE.Vector3;
  radius: number;
  /** Замкнутая окружность или дуга; у дуги — её угол, градусы. */
  closed: boolean;
  sweepDeg: number;
  /** Точки на окружности — чтобы подсветить её при наведении. */
  points: THREE.Vector3[];
}
export interface PlaneFeature {
  kind: "plane";
  index: number;
  normal: THREE.Vector3;
  /** Точка на грани: центр точек её контура. */
  origin: THREE.Vector3;
}
export type Feature = PointFeature | EdgeFeature | CircleFeature | PlaneFeature;

interface Loop {
  ids: number[];
  closed: boolean;
  /** Грань по другую сторону отрезка ids[k] → ids[k+1] (−1 — края нет). */
  across: number[];
  /** Отрезок — шов кривой поверхности (излом меньше SOFT_EDGE_DEG). */
  soft: boolean[];
}

const pairKey = (a: number, b: number, n: number) => (a < b ? a * n + b : b * n + a);

/** Черты сетки: грани и то, что на их контурах. Разбор грани — по первому наведению. */
export class MeshFeatures {
  readonly skipped: boolean;
  private readonly pts: Float64Array = new Float64Array(0);
  private readonly count: number = 0;
  private readonly tri: Int32Array = new Int32Array(0);
  private readonly faceNormal: Float64Array = new Float64Array(0);
  private readonly neighbour: Int32Array = new Int32Array(0);
  private readonly planeOfFace: Int32Array = new Int32Array(0);
  /** Грани-плоскости: нормаль и треугольники (плотно, по порядку номеров). */
  private readonly planeNormal: Float64Array = new Float64Array(0);
  private readonly planeStart: Int32Array = new Int32Array(1);
  private readonly planeFaces: Int32Array = new Int32Array(0);
  private readonly features = new Map<number, Feature[]>();
  /** Отрезок сетки → окружность соседней грани, по ободу которой он идёт. */
  private readonly rim = new Map<number, CircleFeature>();

  constructor(geometry: THREE.BufferGeometry) {
    const position = geometry.getAttribute("position") as THREE.BufferAttribute | undefined;
    const index = geometry.index;
    const faces = position ? (index ? index.count : position.count) / 3 : 0;
    this.skipped = !position || faces > MAX_FEATURE_TRIANGLES;
    if (this.skipped || !position) return;

    // Вершины сшиваются по координатам, с точностью до 10 нм: у three швы
    // развёртки (и крышки цилиндра) — отдельные вершины в тех же точках, и
    // даже не в тех же — sin(2π) не ноль. Соседство граней обязано видеть
    // такую вершину одной. Таблица — открытая адресация на типизированных
    // массивах: строковые ключи на миллион вершин стоили полсекунды.
    const n = position.count;
    const q = new Int32Array(n * 3);
    for (let i = 0; i < n; i += 1) {
      q[3 * i] = Math.round(position.getX(i) * 1e5);
      q[3 * i + 1] = Math.round(position.getY(i) * 1e5);
      q[3 * i + 2] = Math.round(position.getZ(i) * 1e5);
    }
    let size = 1;
    while (size < n * 2) size <<= 1;
    const table = new Int32Array(size).fill(-1);
    const weld = new Int32Array(n);
    const firstOf: number[] = [];                       // сшитая вершина → исходная
    for (let i = 0; i < n; i += 1) {
      const x = q[3 * i];
      const y = q[3 * i + 1];
      const z = q[3 * i + 2];
      let slot = (Math.imul(x, 73856093) ^ Math.imul(y, 19349663) ^ Math.imul(z, 83492791)) & (size - 1);
      for (;;) {
        const j = table[slot];
        if (j < 0) {
          table[slot] = i;
          weld[i] = firstOf.length;
          firstOf.push(i);
          break;
        }
        if (q[3 * j] === x && q[3 * j + 1] === y && q[3 * j + 2] === z) {
          weld[i] = weld[j];
          break;
        }
        slot = (slot + 1) & (size - 1);
      }
    }
    this.count = firstOf.length;
    this.pts = new Float64Array(this.count * 3);
    for (let v = 0; v < this.count; v += 1) {
      const i = firstOf[v];
      this.pts[3 * v] = position.getX(i);
      this.pts[3 * v + 1] = position.getY(i);
      this.pts[3 * v + 2] = position.getZ(i);
    }
    this.tri = new Int32Array(faces * 3);
    for (let f = 0; f < faces * 3; f += 1) this.tri[f] = weld[index ? index.getX(f) : f];

    // Нормали и площади граней.
    this.faceNormal = new Float64Array(faces * 3);
    const area = new Float64Array(faces);
    const P = this.pts;
    const T = this.tri;
    for (let f = 0; f < faces; f += 1) {
      const a = 3 * T[3 * f];
      const b = 3 * T[3 * f + 1];
      const c = 3 * T[3 * f + 2];
      const ux = P[b] - P[a], uy = P[b + 1] - P[a + 1], uz = P[b + 2] - P[a + 2];
      const vx = P[c] - P[a], vy = P[c + 1] - P[a + 1], vz = P[c + 2] - P[a + 2];
      let nx = uy * vz - uz * vy;
      let ny = uz * vx - ux * vz;
      let nz = ux * vy - uy * vx;
      const len = Math.hypot(nx, ny, nz);
      area[f] = len / 2;
      if (len > 0) {
        nx /= len;
        ny /= len;
        nz /= len;
      }
      this.faceNormal[3 * f] = nx;
      this.faceNormal[3 * f + 1] = ny;
      this.faceNormal[3 * f + 2] = nz;
    }

    // Соседи через ребро: у ребра ровно одно полуребро туда и одно обратно.
    // Иначе (три грани на ребре, перевёрнутая грань) ребро считается краем.
    // Полурёбра раскладываются по вершине-началу плотными массивами: карта на
    // полтора миллиона треугольников стоила бы сотни мегабайт.
    const halfEdges = faces * 3;
    const head = new Int32Array(halfEdges);             // конец полуребра
    for (let h = 0; h < halfEdges; h += 1) head[h] = T[h % 3 === 2 ? h - 2 : h + 1];
    const start = new Int32Array(this.count + 1);
    for (let h = 0; h < halfEdges; h += 1) start[T[h] + 1] += 1;
    for (let v = 0; v < this.count; v += 1) start[v + 1] += start[v];
    const fill = start.slice(0, this.count);
    const out = new Int32Array(halfEdges);
    for (let h = 0; h < halfEdges; h += 1) out[fill[T[h]]++] = h;
    const single = (from: number, target: number) => {
      let found = -1;
      for (let i = start[from]; i < start[from + 1]; i += 1) {
        if (head[out[i]] !== target) continue;
        if (found >= 0) return -2;
        found = out[i];
      }
      return found;
    };
    this.neighbour = new Int32Array(halfEdges).fill(-1);
    for (let h = 0; h < halfEdges; h += 1) {
      if (this.neighbour[h] >= 0) continue;
      const u = T[h];
      const v = head[h];
      const twin = single(v, u);
      if (twin < 0 || single(u, v) !== h) continue;
      this.neighbour[h] = (twin - (twin % 3)) / 3;
      this.neighbour[twin] = (h - (h % 3)) / 3;
    }

    // Грани-плоскости заливкой от крупных треугольников: у тонкой щепки
    // нормаль неточна, и от неё плоскость вышла бы кривой. Порядок — по
    // двоичному порядку площади (корзинами): полная сортировка полутора
    // миллионов стоила полсекунды, а нужна только «крупные раньше».
    // Сосед входит в плоскость, если все его вершины лежат на ней — по
    // расстоянию, а не по нормали: у длинной щепки нормаль гуляет, а точки — нет.
    const box = new THREE.Box3().setFromBufferAttribute(position);
    const tol = 1e-4 + 2e-6 * box.getSize(new THREE.Vector3()).length();
    const bucketOf = (f: number) => (area[f] > 0 ? Math.max(0, Math.min(79, 40 + Math.floor(Math.log2(area[f])))) : 0);
    const bucketStart = new Int32Array(81);
    for (let f = 0; f < faces; f += 1) bucketStart[80 - bucketOf(f)] += 1;
    for (let k = 1; k <= 80; k += 1) bucketStart[k] += bucketStart[k - 1];
    const order = new Int32Array(faces);
    const at = new Int32Array(81);
    at.set(bucketStart.subarray(0, 80), 1);
    for (let f = 0; f < faces; f += 1) order[at[80 - bucketOf(f)]++] = f;

    this.planeOfFace = new Int32Array(faces).fill(-1);
    const normals: number[] = [];
    const stack = new Int32Array(faces);
    for (const seed of order) {
      if (this.planeOfFace[seed] >= 0) continue;
      const plane = normals.length / 3;
      const nx = this.faceNormal[3 * seed];
      const ny = this.faceNormal[3 * seed + 1];
      const nz = this.faceNormal[3 * seed + 2];
      normals.push(nx, ny, nz);
      const s0 = 3 * T[3 * seed];
      const d = nx * P[s0] + ny * P[s0 + 1] + nz * P[s0 + 2];
      this.planeOfFace[seed] = plane;
      let top = 0;
      stack[top++] = seed;
      while (top) {
        const f = stack[--top];
        for (let j = 0; j < 3; j += 1) {
          const g = this.neighbour[3 * f + j];
          if (g < 0 || this.planeOfFace[g] >= 0) continue;
          const same = nx * this.faceNormal[3 * g] + ny * this.faceNormal[3 * g + 1] + nz * this.faceNormal[3 * g + 2];
          if (same < 0.5 && area[g] > 1e-9) continue;
          let flat = true;
          for (let k = 0; k < 3 && flat; k += 1) {
            const o = 3 * T[3 * g + k];
            flat = Math.abs(nx * P[o] + ny * P[o + 1] + nz * P[o + 2] - d) <= tol;
          }
          if (!flat) continue;
          this.planeOfFace[g] = plane;
          stack[top++] = g;
        }
      }
    }
    this.planeNormal = Float64Array.from(normals);
    const planes = normals.length / 3;
    this.planeStart = new Int32Array(planes + 1);
    for (let f = 0; f < faces; f += 1) this.planeStart[this.planeOfFace[f] + 1] += 1;
    for (let k = 0; k < planes; k += 1) this.planeStart[k + 1] += this.planeStart[k];
    const put = this.planeStart.slice(0, planes);
    this.planeFaces = new Int32Array(faces);
    for (let f = 0; f < faces; f += 1) this.planeFaces[put[this.planeOfFace[f]]++] = f;
  }

  private point(id: number, out: THREE.Vector3): THREE.Vector3 {
    return out.set(this.pts[3 * id], this.pts[3 * id + 1], this.pts[3 * id + 2]);
  }

  private vec(id: number): THREE.Vector3 {
    return this.point(id, new THREE.Vector3());
  }

  /** Номер грани-плоскости у треугольника. */
  planeOf(face: number): number {
    return this.skipped ? -1 : this.planeOfFace[face] ?? -1;
  }

  /** Треугольники грани — чтобы подсветить её. */
  facesOf(plane: number): Int32Array {
    if (plane < 0 || plane + 1 >= this.planeStart.length) return new Int32Array(0);
    return this.planeFaces.subarray(this.planeStart[plane], this.planeStart[plane + 1]);
  }

  private normalOf(plane: number): THREE.Vector3 {
    const n = this.planeNormal;
    return new THREE.Vector3(n[3 * plane], n[3 * plane + 1], n[3 * plane + 2]);
  }

  /** Черты на контуре грани; последняя — сама грань. */
  featuresOf(plane: number): Feature[] {
    if (plane < 0 || plane + 1 >= this.planeStart.length) return [];
    let found = this.features.get(plane);
    if (!found) {
      found = this.extract(plane);
      this.features.set(plane, found);
    }
    return found;
  }

  /**
   * Что поймать у точки `at` (координаты меша) на треугольнике `face`:
   * ближайшую черту не дальше `tol` мм, у конца ребра — вершину, иначе грань.
   */
  resolve(face: number, at: THREE.Vector3, tol: number): Feature | null {
    const plane = this.planeOf(face);
    if (plane < 0) return null;
    const features = this.featuresOf(plane);
    let best: Feature | null = null;
    let bestDist = tol;
    for (const f of features) {
      if (f.kind === "plane") continue;
      const d = distanceTo(f, at);
      if (d < bestDist) {
        bestDist = d;
        best = f;
      }
    }
    if (!best) return features[features.length - 1] ?? null;
    if (best.kind === "edge") {
      // У конца ребра — вершина. Предел не больше трети ребра: иначе у
      // короткого ребра середину было бы не поймать вовсе.
      const limit = Math.min(tol, best.a.distanceTo(best.b) / 3);
      if (at.distanceTo(best.a) < limit) return { kind: "point", point: best.a.clone() };
      if (at.distanceTo(best.b) < limit) return { kind: "point", point: best.b.clone() };
      if (best.chord && best.chord.across >= 0) {
        this.featuresOf(best.chord.across);
        const circle = this.rim.get(pairKey(best.chord.u, best.chord.v, this.count));
        if (circle) return circle;
      }
    }
    return best;
  }

  // ------------------------------------------------------------- контуры
  private loops(plane: number): Loop[] {
    const faces = this.facesOf(plane);
    const cosSoft = Math.cos(THREE.MathUtils.degToRad(SOFT_EDGE_DEG));
    // Полурёбра контура: сторона треугольника, за которой уже не эта грань.
    const from: number[] = [];
    const to: number[] = [];
    const across: number[] = [];
    const soft: boolean[] = [];
    const outgoing = new Map<number, number[]>();
    for (const f of faces) {
      for (let j = 0; j < 3; j += 1) {
        const g = this.neighbour[3 * f + j];
        const other = g < 0 ? -1 : this.planeOfFace[g];
        if (other === plane) continue;
        const h = from.length;
        from.push(this.tri[3 * f + j]);
        to.push(this.tri[3 * f + ((j + 1) % 3)]);
        across.push(other);
        let s = false;
        if (g >= 0) {
          const dot = this.faceNormal[3 * f] * this.faceNormal[3 * g] + this.faceNormal[3 * f + 1] * this.faceNormal[3 * g + 1]
            + this.faceNormal[3 * f + 2] * this.faceNormal[3 * g + 2];
          s = dot > cosSoft;
        }
        soft.push(s);
        const list = outgoing.get(from[h]);
        if (list) list.push(h);
        else outgoing.set(from[h], [h]);
      }
    }
    const used = new Uint8Array(from.length);
    const nextFrom = (v: number) => {
      for (const h of outgoing.get(v) ?? []) if (!used[h]) return h;
      return -1;
    };
    const out: Loop[] = [];
    for (let start = 0; start < from.length; start += 1) {
      if (used[start]) continue;
      const loop: Loop = { ids: [], closed: false, across: [], soft: [] };
      let h = start;
      for (;;) {
        used[h] = 1;
        loop.ids.push(from[h]);
        loop.across.push(across[h]);
        loop.soft.push(soft[h]);
        if (to[h] === from[start]) {
          loop.closed = true;
          break;
        }
        const next = nextFrom(to[h]);
        if (next < 0) {
          loop.ids.push(to[h]);               // открытая цепочка: последняя точка
          break;
        }
        h = next;
      }
      out.push(loop);
    }
    return out;
  }

  private extract(plane: number): Feature[] {
    const normal = this.normalOf(plane);
    const features: Feature[] = [];
    const centroid = new THREE.Vector3();
    let counted = 0;
    for (const loop of this.loops(plane)) {
      for (const id of loop.ids) {
        centroid.add(this.vec(id));
        counted += 1;
      }
      if (loop.closed) this.closedLoop(loop, normal, features);
      else {
        for (let k = 0; k + 1 < loop.ids.length; k += 1) {
          if (loop.soft[k]) continue;
          features.push(this.chordEdge(loop, k, loop.ids[k + 1]));
        }
      }
    }
    features.push({
      kind: "plane",
      index: plane,
      normal: normal.clone(),
      origin: counted ? centroid.multiplyScalar(1 / counted) : new THREE.Vector3(),
    });
    return features;
  }

  private chordEdge(loop: Loop, k: number, next: number): EdgeFeature {
    return {
      kind: "edge",
      a: this.vec(loop.ids[k]),
      b: this.vec(next),
      chord: { u: loop.ids[k], v: next, across: loop.across[k] },
    };
  }

  private remember(circle: CircleFeature, loop: Loop, chords: number[]): void {
    const n = loop.ids.length;
    for (const k of chords) this.rim.set(pairKey(loop.ids[k], loop.ids[(k + 1) % n], this.count), circle);
  }

  private closedLoop(loop: Loop, normal: THREE.Vector3, out: Feature[]): void {
    const n = loop.ids.length;
    const p = loop.ids.map((id) => this.vec(id));
    const len = p.map((q, k) => q.distanceTo(p[(k + 1) % n]));
    const all = Array.from({ length: n }, (_, k) => k);

    // Весь контур — окружность или правильный многоугольник.
    if (n > 4) {
      const fit = fitCircle(p, normal);
      const equal = len.every((l) => near(l, len[0]));
      if (fit && fit.error < Math.min(FIT_MM, 0.05 * fit.radius) && equal) {
        if (n <= 8) {
          for (const k of all) out.push({ ...this.chordEdge(loop, k, loop.ids[(k + 1) % n]), centre: fit.centre.clone() });
        } else {
          const circle: CircleFeature = {
            kind: "circle", centre: fit.centre, normal: normal.clone(), radius: fit.radius,
            closed: true, sweepDeg: 360, points: p,
          };
          out.push(circle);
          this.remember(circle, loop, all);
        }
        return;
      }
    }

    // Поворот контура в каждой вершине — со знаком, вокруг нормали грани.
    const turn = p.map((q, k) => {
      const e0 = q.clone().sub(p[(k - 1 + n) % n]);
      const e1 = p[(k + 1) % n].clone().sub(q);
      return Math.atan2(normal.dot(e0.clone().cross(e1)), e0.dot(e1));
    });
    // Вершины k и k+1 — внутри одной дуги: повороты равны и не нулевые, а
    // три хорды вокруг них — одной длины.
    const linked = all.map((k) => {
      const k1 = (k + 1) % n;
      return Math.abs(turn[k]) > 1e-3 && Math.abs(turn[k] - turn[k1]) < TURN_EQ
        && near(len[(k - 1 + n) % n], len[k]) && near(len[k], len[k1]);
    });
    const inArc = new Uint8Array(n);                     // хорда k — часть дуги
    const start = linked.findIndex((l, k) => !l && linked[(k + 1) % n]);
    if (start >= 0 && !linked.every(Boolean)) {
      // Обход от разрыва: каждая серия связанных вершин — кандидат в дугу.
      let k = (start + 1) % n;
      for (let seen = 0; seen < n;) {
        if (!linked[k]) {
          k = (k + 1) % n;
          seen += 1;
          continue;
        }
        const first = k;                                 // первая внутренняя вершина
        let last = k;
        while (linked[last] && seen < n) {
          last = (last + 1) % n;
          seen += 1;
        }
        // Вершины first..last внутренние; точки дуги — first−1 … last+1.
        const idx: number[] = [];
        for (let i = (first - 1 + n) % n; ; i = (i + 1) % n) {
          idx.push(i);
          if (i === (last + 1) % n) break;
        }
        const chords = idx.slice(0, -1);
        const pts = idx.map((i) => p[i]);
        const fit = pts.length >= 5 ? fitCircle(pts, normal) : null;
        if (fit && fit.error < Math.min(FIT_MM, 0.05 * fit.radius)) {
          const sweep = chords.reduce((s, c) => s + 2 * Math.asin(Math.min(1, len[c] / (2 * fit.radius))), 0);
          if (sweep > MIN_ARC_RAD) {
            const arc: CircleFeature = {
              kind: "circle", centre: fit.centre, normal: normal.clone(), radius: fit.radius,
              closed: false, sweepDeg: THREE.MathUtils.radToDeg(sweep), points: pts,
            };
            out.push(arc);
            this.remember(arc, loop, chords);
            for (const c of chords) inArc[c] = 1;
          }
        }
        k = (last + 1) % n;
      }
    }

    // Остальное — рёбра; соосные подряд — одно ребро.
    // Обход начинается с разрыва (дуга, шов) или с настоящего угла: иначе
    // сторона, на середине которой начался контур, вышла бы двумя рёбрами.
    const dir = (c: number) => p[(c + 1) % n].clone().sub(p[c]).normalize();
    const pieces: { a: number; b: number; chord: number }[] = [];
    let begin = all.findIndex((c) => inArc[c] || loop.soft[c]);
    if (begin < 0) begin = Math.max(0, all.findIndex((c) => dir((c - 1 + n) % n).dot(dir(c)) <= 1 - 1e-6));
    for (let s = 0; s < n; s += 1) {
      const c = (begin + s) % n;
      if (inArc[c] || loop.soft[c]) {
        pieces.push({ a: -1, b: -1, chord: -1 });        // разрыв: сливать через него нельзя
        continue;
      }
      pieces.push({ a: c, b: (c + 1) % n, chord: c });
    }
    let run: { a: number; b: number; chords: number[] } | null = null;
    const flush = () => {
      if (!run) return;
      if (run.chords.length === 1) {
        out.push(this.chordEdge(loop, run.chords[0], loop.ids[run.b]));
      } else {
        out.push({ kind: "edge", a: p[run.a].clone(), b: p[run.b].clone() });
      }
      run = null;
    };
    for (const piece of pieces) {
      if (piece.chord < 0) {
        flush();
        continue;
      }
      if (run) {
        if (run.b === piece.a && dir(run.chords[0]).dot(dir(piece.chord)) > 1 - 1e-6) {
          run.b = piece.b;
          run.chords.push(piece.chord);
          continue;
        }
        flush();
      }
      run = { a: piece.a, b: piece.b, chords: [piece.chord] };
    }
    flush();
  }
}

function near(a: number, b: number): boolean {
  return Math.abs(a - b) <= LEN_EQ * Math.max(a, b) + 1e-6;
}

/** Расстояние от точки до черты, мм. */
export function distanceTo(f: Feature, at: THREE.Vector3): number {
  switch (f.kind) {
    case "point":
      return at.distanceTo(f.point);
    case "edge": {
      const line = new THREE.Line3(f.a, f.b);
      return line.closestPointToPoint(at, true, new THREE.Vector3()).distanceTo(at);
    }
    case "circle":
      return at.distanceTo(closestOnCircle(f, at));
    case "plane":
      return Math.abs(at.clone().sub(f.origin).dot(f.normal));
  }
}

/** Ближайшая точка окружности (дуги — в её пределах). */
export function closestOnCircle(c: CircleFeature, at: THREE.Vector3): THREE.Vector3 {
  const rel = at.clone().sub(c.centre);
  const inPlane = rel.addScaledVector(c.normal, -rel.dot(c.normal));
  if (inPlane.lengthSq() < 1e-18) return c.points[0].clone();
  const onFull = c.centre.clone().addScaledVector(inPlane.normalize(), c.radius);
  if (c.closed) return onFull;
  // Дуга: точка годится, если лежит между концами; иначе — ближний конец.
  const first = c.points[0];
  const last = c.points[c.points.length - 1];
  const u = first.clone().sub(c.centre).normalize();
  const v = c.normal.clone().cross(u);
  const angle = (q: THREE.Vector3) => {
    const r = q.clone().sub(c.centre);
    let t = Math.atan2(r.dot(v), r.dot(u));
    if (t < 0) t += 2 * Math.PI;
    return t;
  };
  const end = angle(last);
  // Дуга идёт от первой точки к последней в ту сторону, где лежит вторая.
  const ccw = angle(c.points[1]) <= end;
  const t = angle(onFull);
  const inside = ccw ? t <= end : t >= end || t === 0;
  if (inside) return onFull;
  return at.distanceTo(first) < at.distanceTo(last) ? first.clone() : last.clone();
}

/** Окружность через точки в плоскости с нормалью `normal` (подгонка Каса). */
export function fitCircle(
  points: THREE.Vector3[],
  normal: THREE.Vector3,
): { centre: THREE.Vector3; radius: number; error: number } | null {
  if (points.length < 3) return null;
  const centroid = points.reduce((acc, q) => acc.add(q), new THREE.Vector3()).multiplyScalar(1 / points.length);
  const u = new THREE.Vector3().subVectors(points[0], centroid);
  u.addScaledVector(normal, -u.dot(normal));
  if (u.lengthSq() < 1e-18) return null;
  u.normalize();
  const v = new THREE.Vector3().crossVectors(normal, u);
  const flat = points.map((q) => {
    const d = q.clone().sub(centroid);
    return [d.dot(u), d.dot(v)];
  });
  // x² + y² + Dx + Ey + F = 0 наименьшими квадратами.
  const m = [0, 0, 0, 0, 0, 0, 0, 0, 0];
  const rhs = [0, 0, 0];
  for (const [x, y] of flat) {
    const row = [x, y, 1];
    const b = -(x * x + y * y);
    for (let i = 0; i < 3; i += 1) {
      rhs[i] += row[i] * b;
      for (let j = 0; j < 3; j += 1) m[i * 3 + j] += row[i] * row[j];
    }
  }
  const solved = solve3(m, rhs);
  if (!solved) return null;
  const [D, E, F] = solved;
  const cx = -D / 2;
  const cy = -E / 2;
  const r2 = cx * cx + cy * cy - F;
  if (!(r2 > 0)) return null;
  const radius = Math.sqrt(r2);
  let error = 0;
  for (const [x, y] of flat) error = Math.max(error, Math.abs(Math.hypot(x - cx, y - cy) - radius));
  const centre = centroid.clone().addScaledVector(u, cx).addScaledVector(v, cy);
  return { centre, radius, error };
}

/** Гаусс с выбором ведущего для 3×3. */
function solve3(m: number[], b: number[]): number[] | null {
  const a = [
    [m[0], m[1], m[2], b[0]],
    [m[3], m[4], m[5], b[1]],
    [m[6], m[7], m[8], b[2]],
  ];
  for (let c = 0; c < 3; c += 1) {
    let pivot = c;
    for (let r = c + 1; r < 3; r += 1) if (Math.abs(a[r][c]) > Math.abs(a[pivot][c])) pivot = r;
    if (Math.abs(a[pivot][c]) < 1e-12) return null;
    [a[c], a[pivot]] = [a[pivot], a[c]];
    for (let r = 0; r < 3; r += 1) {
      if (r === c) continue;
      const f = a[r][c] / a[c][c];
      for (let k = c; k < 4; k += 1) a[r][k] -= f * a[c][k];
    }
  }
  return [a[0][3] / a[0][0], a[1][3] / a[1][1], a[2][3] / a[2][2]];
}
