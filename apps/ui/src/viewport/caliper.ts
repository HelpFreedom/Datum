/**
 * «Штангенциркуль» — замер, как в ElegooSlicer.
 *
 * При наведении инструмент подсвечивает, за что зацепится: вершину, ребро,
 * окружность или дугу (с диаметром), центр отверстия или всю грань. Что
 * ловится — решает `features.ts` (разбор грани у слайсера). Щелчок выбирает
 * подсвеченное; второй щелчок — второй элемент, и между ними замер:
 * расстояние (с проекциями ΔX ΔY ΔZ) или угол — смотря что выбрано. Одна
 * окружность сразу даёт диаметр, одно ребро — длину. Третий щелчок начинает
 * новый замер. С Shift ставится просто точка на поверхности.
 *
 * Центр окружности ловится, пока курсор внутри неё: у сквозного отверстия под
 * центром пусто, луч ни во что не попадает, и без этого центр было бы не
 * поймать — у слайсера для этого сфера-ручка в центре подсвеченной окружности.
 *
 * Допуски — в пикселях экрана: одинаковы на любом увеличении.
 */
import * as THREE from "three";
import { Line2 } from "three/examples/jsm/lines/Line2.js";
import { LineGeometry } from "three/examples/jsm/lines/LineGeometry.js";
import { LineMaterial } from "three/examples/jsm/lines/LineMaterial.js";
import { CSS2DObject, CSS2DRenderer } from "three/examples/jsm/renderers/CSS2DRenderer.js";

import { type CircleFeature, closestOnCircle, type Feature, MeshFeatures } from "./features";
import type { PickedElement } from "./measure";

/** Как далеко от черты курсор ещё ловит её, пиксели. */
export const FEATURE_PX = 8;
/** Как близко к центру окружности курсор ловит центр, пиксели. */
export const CENTRE_PX = 10;

export type SnapKind = "вершина" | "ребро" | "окружность" | "дуга" | "центр окружности" |
  "центр дуги" | "центр многоугольника" | "грань" | "точка";

/** Круглое, у чего есть центр: окружность, дуга, правильный многоугольник. */
export interface Round {
  centre: THREE.Vector3;
  /** Нормаль плоскости окружности (у многоугольника не нужна — нулевая). */
  normal: THREE.Vector3;
  radius: number;
  closed: boolean;
  /** Точки обода — для подсветки и для того, чтобы знать его размер на экране. */
  points: THREE.Vector3[];
  polygon: boolean;
}

/** Пойманное — в мировых координатах. */
export interface Snap {
  kind: SnapKind;
  feature: Feature;
  /** Где стоит подпись; у грани — точка, куда щёлкнули. */
  point: THREE.Vector3;
  part: string;
  /** Окружность (или многоугольник), к которой относится пойманное. */
  round?: Round;
  /** Грань: откуда взять её треугольники для подсветки. */
  face?: { mesh: THREE.Mesh; features: MeshFeatures; plane: number };
  /** Одинаковый ключ — то же самое пойманное: перерисовывать незачем. */
  key: string;
}

export interface RayHit {
  hit: THREE.Intersection;
  part: string;
  onCap: boolean;
}

const mm = (v: number, digits = 2) => v.toLocaleString("ru-RU", { maximumFractionDigits: digits });
const deg = (v: number) => `${mm(v, 2)}°`;
const key = (v: THREE.Vector3) => `${v.x.toFixed(4)},${v.y.toFixed(4)},${v.z.toFixed(4)}`;

/** Черта сетки → в мир. Движения деталей жёсткие: расстояния не меняются. */
export function toWorld(f: Feature, matrix: THREE.Matrix4): Feature {
  const normalMatrix = new THREE.Matrix3().getNormalMatrix(matrix);
  const p = (v: THREE.Vector3) => v.clone().applyMatrix4(matrix);
  const n = (v: THREE.Vector3) => v.clone().applyMatrix3(normalMatrix).normalize();
  switch (f.kind) {
    case "point":
      return { kind: "point", point: p(f.point) };
    case "edge":
      return { kind: "edge", a: p(f.a), b: p(f.b), centre: f.centre && p(f.centre) };
    case "circle":
      return { ...f, centre: p(f.centre), normal: n(f.normal), points: f.points.map(p) };
    case "plane":
      return { ...f, normal: n(f.normal), origin: p(f.origin) };
  }
}

function roundOf(f: Feature): Round | undefined {
  if (f.kind === "circle") {
    return { centre: f.centre, normal: f.normal, radius: f.radius, closed: f.closed, points: f.points, polygon: false };
  }
  if (f.kind === "edge" && f.centre) {
    return {
      centre: f.centre, normal: new THREE.Vector3(), radius: f.a.distanceTo(f.centre), closed: true,
      points: [f.a, f.b], polygon: true,
    };
  }
  return undefined;
}

function toScreen(v: THREE.Vector3, camera: THREE.Camera, width: number, height: number): THREE.Vector2 {
  const p = v.clone().project(camera);
  return new THREE.Vector2(((p.x + 1) / 2) * width, ((1 - p.y) / 2) * height);
}

/** Миллиметров на пиксель у точки — чтобы допуск в пикселях перевести в мм. */
function worldPerPixel(camera: THREE.Camera, at: THREE.Vector3, height: number): number {
  const persp = camera as THREE.PerspectiveCamera;
  const distance = camera.position.distanceTo(at);
  return (2 * distance * Math.tan(THREE.MathUtils.degToRad(persp.fov ?? 45) / 2)) / Math.max(1, height);
}

/** Центр круглого — если курсор рядом с ним на экране. */
export function centreSnap(
  round: Round, part: string, cursor: THREE.Vector2, camera: THREE.Camera, width: number, height: number,
): Snap | null {
  if (toScreen(round.centre, camera, width, height).distanceTo(cursor) > CENTRE_PX) return null;
  const kind: SnapKind = round.polygon ? "центр многоугольника" : round.closed ? "центр окружности" : "центр дуги";
  return {
    kind, feature: { kind: "point", point: round.centre.clone() }, point: round.centre.clone(),
    part, round, key: `${part}|centre|${key(round.centre)}`,
  };
}

/** Курсор ещё внутри круглого на экране (с запасом допуска)? */
export function insideRound(round: Round, cursor: THREE.Vector2, camera: THREE.Camera, width: number, height: number): boolean {
  const centre = toScreen(round.centre, camera, width, height);
  let reach = 0;
  for (const q of round.points) reach = Math.max(reach, toScreen(q, camera, width, height).distanceTo(centre));
  return centre.distanceTo(cursor) <= reach + FEATURE_PX;
}

/**
 * Что поймать у точки попадания. Чистая функция: сцена, камера и размер
 * экрана приходят снаружи, — её берёт тест.
 */
export function resolveSnap(
  ray: RayHit,
  features: MeshFeatures | null,
  camera: THREE.Camera,
  height: number,
  free = false,
): Snap {
  const { hit, part, onCap } = ray;
  const surface = (): Snap => ({
    kind: "точка", feature: { kind: "point", point: hit.point.clone() }, point: hit.point.clone(),
    part, key: `${part}|free|${key(hit.point)}`,
  });
  const mesh = hit.object as THREE.Mesh;
  if (free || onCap || !features || features.skipped || hit.faceIndex == null) return surface();

  const tol = FEATURE_PX * worldPerPixel(camera, hit.point, height);
  const local = mesh.worldToLocal(hit.point.clone());
  const found = features.resolve(hit.faceIndex, local, tol);
  if (!found) return surface();
  const f = toWorld(found, mesh.matrixWorld);
  switch (f.kind) {
    case "point":
      return { kind: "вершина", feature: f, point: f.point, part, key: `${part}|v|${key(f.point)}` };
    case "edge": {
      const q = new THREE.Line3(f.a, f.b).closestPointToPoint(hit.point, true, new THREE.Vector3());
      return { kind: "ребро", feature: f, point: q, part, round: roundOf(f), key: `${part}|e|${key(f.a)}|${key(f.b)}` };
    }
    case "circle":
      return {
        kind: f.closed ? "окружность" : "дуга", feature: f, point: closestOnCircle(f, hit.point), part,
        round: roundOf(f), key: `${part}|c|${key(f.centre)}|${f.radius.toFixed(4)}`,
      };
    case "plane":
      return {
        kind: "грань", feature: f, point: hit.point.clone(), part,
        face: { mesh, features, plane: f.index }, key: `${part}|p|${f.index}|${mesh.geometry.uuid}`,
      };
  }
}

/** Подпись пойманного — рядом с курсором. */
export function snapLabel(snap: Snap): string {
  const f = snap.feature;
  if (f.kind === "circle") return f.closed ? `окружность · Ø ${mm(f.radius * 2)}` : `дуга · R ${mm(f.radius)}`;
  if (f.kind === "point" && snap.round && !snap.round.polygon) {
    return snap.round.closed ? `${snap.kind} · Ø ${mm(snap.round.radius * 2)}` : `${snap.kind} · R ${mm(snap.round.radius)}`;
  }
  if (f.kind === "edge") return `ребро · ${mm(f.a.distanceTo(f.b))}`;
  return snap.kind;
}

// ---------------------------------------------------------------- замер
export interface Measurement {
  text: string;
  detail: string;
  /** Размерная линия — между какими точками (если есть что соединять). */
  line?: [THREE.Vector3, THREE.Vector3];
  /** Где поставить подпись. */
  at: THREE.Vector3;
}

const PARALLEL = Math.cos(THREE.MathUtils.degToRad(0.5));

function onLine(p: THREE.Vector3, a: THREE.Vector3, b: THREE.Vector3, clamp: boolean): THREE.Vector3 {
  return new THREE.Line3(a, b).closestPointToPoint(p, clamp, new THREE.Vector3());
}

function onPlane(p: THREE.Vector3, origin: THREE.Vector3, normal: THREE.Vector3): THREE.Vector3 {
  return p.clone().addScaledVector(normal, -p.clone().sub(origin).dot(normal));
}

/** Ближайшие точки двух отрезков (для скрещивающихся рёбер). */
function closestBetween(a0: THREE.Vector3, a1: THREE.Vector3, b0: THREE.Vector3, b1: THREE.Vector3): [THREE.Vector3, THREE.Vector3] {
  // Поочерёдные проекции на выпуклые отрезки сходятся к ближайшей паре; 64
  // шагов с запасом хватает на доли микрона для непараллельных рёбер.
  let p = a0.clone().add(a1).multiplyScalar(0.5);
  let q = onLine(p, b0, b1, true);
  for (let i = 0; i < 64; i += 1) {
    p = onLine(q, a0, a1, true);
    q = onLine(p, b0, b1, true);
  }
  return [p, q];
}

/** Расстояние; проекции ΔX ΔY ΔZ дописывает `measureBetween` — от первого выбранного. */
function distance(d: number, from: THREE.Vector3, to: THREE.Vector3, what: string): Measurement & { delta: true } {
  return {
    text: `${mm(d, 3)} мм`,
    detail: what,
    line: d > 1e-9 ? [from.clone(), to.clone()] : undefined,
    at: from.clone().add(to).multiplyScalar(0.5),
    delta: true,
  };
}

function angle(value: number, at: THREE.Vector3, what: string): Measurement {
  return { text: deg(value), detail: `${what}\nсмежный: ${deg(180 - value)}`, at };
}

const RANK = { point: 0, edge: 1, circle: 2, plane: 3 } as const;

/**
 * Замер между двумя выбранными элементами — как у слайсера. Считается в
 * порядке «проще — первым» (точка, ребро, окружность, грань), а проекции
 * ΔX ΔY ΔZ — в порядке щелчков: от первого выбранного ко второму.
 */
export function measureBetween(first: Snap, second: Snap): Measurement {
  const swapped = RANK[first.feature.kind] > RANK[second.feature.kind];
  const m = measureOrdered(swapped ? second : first, swapped ? first : second);
  if (!("delta" in m)) return m;
  const { delta: _, ...plain } = m as Measurement & { delta?: true };
  const line = plain.line && (swapped ? [plain.line[1], plain.line[0]] as [THREE.Vector3, THREE.Vector3] : plain.line);
  const d = line ? line[1].clone().sub(line[0]) : new THREE.Vector3();
  return {
    ...plain,
    line,
    detail: plain.detail.replace(/^([^\n]*)/, `$1\nΔX ${mm(d.x, 3)} · ΔY ${mm(d.y, 3)} · ΔZ ${mm(d.z, 3)}`),
  };
}

function measureOrdered(a: Snap, b: Snap): Measurement {
  const fa = a.feature;
  const fb = b.feature;
  const circleName = (c: CircleFeature) => (c.closed ? "окружности" : "дуги");

  if (fa.kind === "point") {
    const p = fa.point;
    if (fb.kind === "point") return distance(p.distanceTo(fb.point), p, fb.point, `${a.kind} — ${b.kind}`);
    if (fb.kind === "edge") {
      const q = onLine(p, fb.a, fb.b, true);
      const m = distance(p.distanceTo(q), p, q, `${a.kind} — ребро`);
      const inf = onLine(p, fb.a, fb.b, false).distanceTo(p);
      if (Math.abs(inf - p.distanceTo(q)) > 1e-6) m.detail += `\nдо продолжения ребра: ${mm(inf, 3)} мм`;
      return m;
    }
    if (fb.kind === "circle") {
      const m = distance(p.distanceTo(fb.centre), p, fb.centre, `${a.kind} — центр ${circleName(fb)}`);
      m.detail += `\nдо ${circleName(fb)}: ${mm(closestOnCircle(fb, p).distanceTo(p), 3)} мм`;
      return m;
    }
    const q = onPlane(p, fb.origin, fb.normal);
    return distance(p.distanceTo(q), p, q, `${a.kind} — грань, по нормали`);
  }
  if (fa.kind === "edge") {
    const u = fa.b.clone().sub(fa.a).normalize();
    const mid = fa.a.clone().add(fa.b).multiplyScalar(0.5);
    if (fb.kind === "edge") {
      const v = fb.b.clone().sub(fb.a).normalize();
      if (Math.abs(u.dot(v)) >= PARALLEL) {
        const q = onLine(mid, fb.a, fb.b, false);
        return distance(mid.distanceTo(q), mid, q, "рёбра параллельны — расстояние между ними");
      }
      const [p, q] = closestBetween(fa.a, fa.b, fb.a, fb.b);
      const m = angle(THREE.MathUtils.radToDeg(Math.acos(Math.min(1, Math.abs(u.dot(v))))),
        p.clone().add(q).multiplyScalar(0.5), "угол между рёбрами");
      if (p.distanceTo(q) > 1e-6) {
        m.detail += `\nкратчайшее расстояние: ${mm(p.distanceTo(q), 3)} мм`;
        m.line = [p, q];
      }
      return m;
    }
    if (fb.kind === "circle") {
      const q = onLine(fb.centre, fa.a, fa.b, true);
      return distance(fb.centre.distanceTo(q), q, fb.centre, `ребро — центр ${circleName(fb)}`);
    }
    if (fb.kind === "plane") {
      const sin = Math.abs(u.dot(fb.normal));
      if (sin <= Math.sin(THREE.MathUtils.degToRad(0.5))) {
        const q = onPlane(mid, fb.origin, fb.normal);
        return distance(mid.distanceTo(q), mid, q, "ребро параллельно грани — расстояние");
      }
      return angle(THREE.MathUtils.radToDeg(Math.asin(Math.min(1, sin))), mid, "угол между ребром и гранью");
    }
  }
  if (fa.kind === "circle") {
    if (fb.kind === "circle") {
      const m = distance(fa.centre.distanceTo(fb.centre), fa.centre, fb.centre, "между центрами");
      const size = (c: CircleFeature) => (c.closed ? `Ø ${mm(c.radius * 2, 3)}` : `R ${mm(c.radius, 3)}`);
      m.detail += `\n${size(fa)} и ${size(fb)} мм`;
      return m;
    }
    if (fb.kind === "plane") {
      const q = onPlane(fa.centre, fb.origin, fb.normal);
      return distance(fa.centre.distanceTo(q), fa.centre, q, `центр ${circleName(fa)} — грань, по нормали`);
    }
  }
  // Остались две грани: пары упорядочены по RANK.
  const na = (fa as { normal: THREE.Vector3 }).normal;
  const nb = fb.kind === "plane" ? fb.normal : na;
  const cos = Math.abs(na.dot(nb));
  if (fb.kind === "plane" && cos >= PARALLEL) {
    const q = onPlane(a.point, fb.origin, fb.normal);
    return distance(a.point.distanceTo(q), a.point, q, "грани параллельны — расстояние между ними");
  }
  return angle(THREE.MathUtils.radToDeg(Math.acos(Math.min(1, cos))),
    a.point.clone().add(b.point).multiplyScalar(0.5), "угол между гранями");
}

/** Что сказать об одном выбранном элементе. */
export function describeOne(s: Snap): { text: string; detail: string } {
  const f = s.feature;
  const at = (v: THREE.Vector3) => `${mm(v.x)}, ${mm(v.y)}, ${mm(v.z)}`;
  const next = "\nвыберите второй элемент (Shift — точка на поверхности)";
  switch (f.kind) {
    case "circle":
      return {
        text: f.closed ? `Ø ${mm(f.radius * 2, 3)} мм` : `R ${mm(f.radius, 3)} мм`,
        detail: `${f.closed ? "окружность" : `дуга ${mm(f.sweepDeg, 1)}°`} · R ${mm(f.radius, 3)} мм · `
          + `центр ${at(f.centre)} (${s.part})${next}`,
      };
    case "edge":
      return { text: `${mm(f.a.distanceTo(f.b), 3)} мм`, detail: `длина ребра (${s.part})${next}` };
    case "plane":
      return {
        text: "грань",
        detail: `нормаль ${mm(f.normal.x, 3)}, ${mm(f.normal.y, 3)}, ${mm(f.normal.z, 3)} (${s.part})${next}`,
      };
    case "point":
      return { text: at(f.point), detail: `${s.kind} (${s.part})${next}` };
  }
}

// ------------------------------------------------------------------ инструмент
const MEASURE = 0xffb547;
const HOVER = 0x5fe0ff;

export interface Reading {
  tool: "distance";
  text: string;
  detail: string;
  picked: PickedElement[];
}

/** Выбранное — числами, для сервера: агент видит, что выбрал человек. */
export function pickedOf(s: Snap): PickedElement {
  const v = (p: THREE.Vector3) => [p.x, p.y, p.z];
  const f = s.feature;
  const head = { kind: s.kind, part: s.part };
  switch (f.kind) {
    case "point":
      return { ...head, type: "point", point: v(f.point) };
    case "edge":
      return { ...head, type: "edge", a: v(f.a), b: v(f.b) };
    case "circle":
      return { ...head, type: "circle", centre: v(f.centre), normal: v(f.normal), radius: f.radius, closed: f.closed };
    case "plane":
      return { ...head, type: "plane", origin: v(f.origin), normal: v(f.normal), point: v(s.point) };
  }
}

interface Options {
  scene: THREE.Scene;
  camera: THREE.PerspectiveCamera;
  canvas: HTMLCanvasElement;
  rayHit: (event: PointerEvent) => RayHit | null;
  invalidate: () => void;
  onReadout: (reading: Reading | null) => void;
}

export class Caliper {
  private readonly opts: Options;
  private readonly labels: CSS2DRenderer;
  private readonly hoverGroup = new THREE.Group();
  private readonly marks = new THREE.Group();
  private readonly features = new Map<string, MeshFeatures>();
  private readonly fat = new Set<LineMaterial>();
  private hovered: Snap | null = null;
  /** Последнее круглое под курсором — его центр ловится, пока курсор внутри. */
  private round: { round: Round; part: string } | null = null;
  private first: Snap | null = null;
  private done = false;
  private hoverLabel: CSS2DObject;
  private pendingHover: PointerEvent | null = null;
  private hoverFrame = 0;
  private size = new THREE.Vector2(1, 1);
  active = false;

  constructor(opts: Options) {
    this.opts = opts;
    this.labels = new CSS2DRenderer();
    const el = this.labels.domElement;
    el.className = "caliper-labels";
    opts.canvas.parentElement?.appendChild(el);
    const tag = document.createElement("div");
    tag.className = "snap-label";
    this.hoverLabel = new CSS2DObject(tag);
    this.hoverLabel.visible = false;
    this.hoverGroup.add(this.hoverLabel);
    opts.scene.add(this.hoverGroup, this.marks);
  }

  resize(width: number, height: number): void {
    this.labels.setSize(width, height);
    this.size.set(width, height);
    for (const m of this.fat) m.resolution.set(width, height);
  }

  /** Черты детали — один раз на её геометрию: разбор тяжёлой сетки — доли секунды. */
  private featuresOf(mesh: THREE.Mesh): MeshFeatures {
    const id = mesh.geometry.uuid;
    let found = this.features.get(id);
    if (!found) {
      found = new MeshFeatures(mesh.geometry);
      this.features.set(id, found);
    }
    return found;
  }

  /** Забыть черты: деталь пересобрали — геометрия другая. */
  forget(): void {
    this.features.clear();
    this.round = null;
  }

  hover(event: PointerEvent): void {
    if (!this.active) return;
    // Пока нажата кнопка — крутят или двигают вид: ловить нечего.
    if (event.buttons) return;
    // Не чаще кадра: разбор под курсором не должен отставать от мыши.
    this.pendingHover = event;
    if (this.hoverFrame) return;
    this.hoverFrame = requestAnimationFrame(() => {
      this.hoverFrame = 0;
      const e = this.pendingHover;
      this.pendingHover = null;
      if (e) this.updateHover(e);
    });
  }

  private updateHover(event: PointerEvent): void {
    const snap = this.snapAt(event);
    const changed = snap?.key !== this.hovered?.key;
    this.hovered = snap;
    if (changed) this.drawHover();
    else if (snap) this.placeHoverLabel(snap);
    this.opts.invalidate();
  }

  private snapAt(event: PointerEvent): Snap | null {
    const rect = this.opts.canvas.getBoundingClientRect();
    const cursor = new THREE.Vector2(event.clientX - rect.left, event.clientY - rect.top);
    const { camera } = this.opts;
    const free = event.shiftKey;
    // Центр последнего круглого — даже там, где под курсором пусто.
    if (this.round && !free) {
      if (!insideRound(this.round.round, cursor, camera, rect.width, rect.height)) this.round = null;
      else {
        const centre = centreSnap(this.round.round, this.round.part, cursor, camera, rect.width, rect.height);
        if (centre) return centre;
      }
    }
    const ray = this.opts.rayHit(event);
    if (!ray) return null;
    const mesh = ray.hit.object as THREE.Mesh;
    const features = !ray.onCap && mesh.isMesh ? this.featuresOf(mesh) : null;
    const snap = resolveSnap(ray, features, camera, rect.height, free);
    if (snap.round && !free) this.round = { round: snap.round, part: snap.part };
    return snap;
  }

  /** Щелчок: выбрать подсвеченное; второй — замер; третий — заново. */
  click(event: PointerEvent): void {
    if (!this.active) return;
    const snap = this.snapAt(event) ?? this.hovered;
    if (!snap) return;
    if (this.done) {
      this.clearGroup(this.marks);
      this.first = null;
      this.done = false;
    }
    if (!this.first) {
      this.first = snap;
      this.drawSnap(snap, MEASURE, this.marks);
      this.opts.onReadout({ tool: "distance", ...describeOne(snap), picked: [pickedOf(snap)] });
    } else {
      if (snap.key === this.first.key) return;          // тот же элемент ещё раз
      this.drawSnap(snap, MEASURE, this.marks);
      const m = measureBetween(this.first, snap);
      this.addDimension(m);
      this.opts.onReadout({
        tool: "distance",
        text: m.text,
        detail: `${m.detail}\nот: ${snapLabel(this.first)} (${this.first.part})\nдо: ${snapLabel(snap)} (${snap.part})`,
        picked: [pickedOf(this.first), pickedOf(snap)],
      });
      this.first = null;
      this.done = true;
    }
    this.opts.invalidate();
  }

  private addDimension(m: Measurement): void {
    if (m.line) {
      const line = new THREE.Line(
        new THREE.BufferGeometry().setFromPoints(m.line),
        new THREE.LineBasicMaterial({ color: MEASURE, depthTest: false }),
      );
      line.renderOrder = 10;
      this.marks.add(line);
      for (const p of m.line) this.addDot(p, MEASURE, this.marks);
    }
    const tag = document.createElement("div");
    tag.className = "dim-label";
    tag.textContent = m.text;
    const label = new CSS2DObject(tag);
    label.position.copy(m.at);
    this.marks.add(label);
  }

  // --------------------------------------------------------------- рисование
  private addDot(point: THREE.Vector3, color: number, group: THREE.Group): void {
    const dot = new THREE.Mesh(
      new THREE.SphereGeometry(1, 12, 8),
      new THREE.MeshBasicMaterial({ color, depthTest: false }),
    );
    dot.position.copy(point);
    dot.userData.dot = true;
    dot.renderOrder = 11;
    group.add(dot);
  }

  /** Толстая линия постоянной экранной ширины: WebGL рисует обычные в пиксель. */
  private addLine(points: THREE.Vector3[], color: number, group: THREE.Group, closed = false): void {
    if (points.length < 2) return;
    const flat: number[] = [];
    for (const p of closed ? [...points, points[0]] : points) flat.push(p.x, p.y, p.z);
    const geometry = new LineGeometry();
    geometry.setPositions(flat);
    const material = new LineMaterial({ color, linewidth: 3, depthTest: false, transparent: true });
    material.resolution.copy(this.size);
    this.fat.add(material);
    const line = new Line2(geometry, material);
    line.computeLineDistances();
    line.renderOrder = 10;
    group.add(line);
  }

  /** Подсветка грани — её собственными треугольниками, поверх детали. */
  private addFace(face: NonNullable<Snap["face"]>, color: number, group: THREE.Group): void {
    const { mesh, features, plane } = face;
    const position = mesh.geometry.getAttribute("position") as THREE.BufferAttribute;
    const index = mesh.geometry.index;
    const faces = features.facesOf(plane);
    const out = new Float32Array(faces.length * 9);
    for (let i = 0; i < faces.length; i += 1) {
      for (let j = 0; j < 3; j += 1) {
        const v = index ? index.getX(3 * faces[i] + j) : 3 * faces[i] + j;
        out[9 * i + 3 * j] = position.getX(v);
        out[9 * i + 3 * j + 1] = position.getY(v);
        out[9 * i + 3 * j + 2] = position.getZ(v);
      }
    }
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", new THREE.BufferAttribute(out, 3));
    const overlay = new THREE.Mesh(geometry, new THREE.MeshBasicMaterial({
      color, transparent: true, opacity: 0.32, depthWrite: false, side: THREE.DoubleSide,
      polygonOffset: true, polygonOffsetFactor: -2, polygonOffsetUnits: -2,
    }));
    overlay.matrixAutoUpdate = false;
    mesh.updateMatrixWorld();
    overlay.matrix.copy(mesh.matrixWorld);
    overlay.renderOrder = 9;
    group.add(overlay);
  }

  /** Показать пойманное: точку, ребро, окружность с центром или грань. */
  private drawSnap(snap: Snap, color: number, group: THREE.Group): void {
    const f = snap.feature;
    if (f.kind === "plane" && snap.face) this.addFace(snap.face, color, group);
    if (f.kind === "edge") {
      this.addLine([f.a, f.b], color, group);
      if (f.centre) this.addDot(f.centre, color, group);
    }
    if (f.kind === "circle") {
      this.addLine(circlePoints(f), color, group, f.closed);
      this.addDot(f.centre, color, group);
    }
    if (f.kind === "point") {
      this.addDot(f.point, color, group);
      const round = snap.round;
      if (round && !round.polygon) {
        const circle: CircleFeature = { kind: "circle", ...round, sweepDeg: 360 };
        this.addLine(circlePoints(circle), color, group, round.closed);
      }
    }
  }

  private clearGroup(group: THREE.Group, keep?: THREE.Object3D): void {
    for (const child of [...group.children]) {
      if (child === keep) continue;
      group.remove(child);
      if (child instanceof CSS2DObject) {
        child.element.remove();
        continue;
      }
      const mesh = child as THREE.Mesh;
      mesh.geometry?.dispose();
      const material = mesh.material as THREE.Material | undefined;
      if (material instanceof LineMaterial) this.fat.delete(material);
      material?.dispose?.();
    }
  }

  private placeHoverLabel(snap: Snap): void {
    this.hoverLabel.element.textContent = snapLabel(snap);
    this.hoverLabel.element.dataset.kind = snap.kind;
    this.hoverLabel.position.copy(snap.point);
  }

  private drawHover(): void {
    this.clearGroup(this.hoverGroup, this.hoverLabel);
    const snap = this.hovered;
    if (!snap) {
      this.hoverLabel.visible = false;
      return;
    }
    this.drawSnap(snap, HOVER, this.hoverGroup);
    this.placeHoverLabel(snap);
    this.hoverLabel.visible = true;
  }

  /** Снять подсказку наведения — курсор ушёл с вьюпорта или сменили инструмент. */
  leave(): void {
    this.hovered = null;
    this.round = null;
    this.drawHover();
    this.opts.invalidate();
  }

  clear(): void {
    this.first = null;
    this.done = false;
    this.hovered = null;
    this.round = null;
    this.clearGroup(this.marks);
    this.drawHover();
    this.opts.onReadout(null);
    this.opts.invalidate();
  }

  /** После кадра: метки — постоянного экранного размера, подписи — на местах. */
  render(scene: THREE.Scene, camera: THREE.PerspectiveCamera, heightPx: number): void {
    const perPixel = (2 * Math.tan(THREE.MathUtils.degToRad(camera.fov) / 2)) / Math.max(1, heightPx);
    for (const group of [this.marks, this.hoverGroup]) {
      for (const child of group.children) {
        if (child.userData.dot) {
          child.scale.setScalar(camera.position.distanceTo(child.position) * perPixel * 3.5);
        }
      }
    }
    this.labels.render(scene, camera);
  }

  dispose(): void {
    cancelAnimationFrame(this.hoverFrame);
    this.clear();
    this.hoverLabel.element.remove();
    this.labels.domElement.remove();
    this.features.clear();
  }
}

/** Окружность для подсветки — гладкая, а не по вершинам сетки. */
function circlePoints(c: CircleFeature): THREE.Vector3[] {
  if (!c.closed) return c.points;
  const u = c.points[0].clone().sub(c.centre).normalize();
  const v = c.normal.clone().cross(u);
  return Array.from({ length: 96 }, (_, i) => {
    const t = (i / 96) * Math.PI * 2;
    return c.centre.clone().addScaledVector(u, c.radius * Math.cos(t)).addScaledVector(v, c.radius * Math.sin(t));
  });
}
