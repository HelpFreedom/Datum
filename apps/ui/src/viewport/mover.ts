/**
 * Перемещение и поворот детали мышью — правка проекта, а не вида.
 *
 * Жест двигает ПОКАЗАННУЮ деталь, а по отпусканию уходит на сервер числами
 * (`part_place`): сдвиг и углы в placement.yaml. Сервер пересобирает деталь на
 * новом месте, проверки видят её там же, и прежняя, сдвинутая на экране,
 * заменяется новой. Как считаются числа — `placement.ts` (и его тест): экран
 * до ответа и деталь после него совпадают.
 *
 * Два способа. Ручки `TransformControls` — вдоль оси или по кольцу. И само тело
 * детали: схватил — и повёл по столу, в горизонтальной плоскости, как в
 * слайсере. Шаг — 1 мм и 15°, с Shift — 0.1 мм и 1°.
 *
 * Пока сервер не ответил, вторая правка той же детали не начинается: она
 * считалась бы от положения, которого ещё нет.
 */
import * as THREE from "three";
import type { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { TransformControls } from "three/examples/jsm/controls/TransformControls.js";

import type { RayHit } from "./caliper";
import { placementAfter, pivotOf, type PlacementDTO, rotationOf, visualOffset } from "./placement";

export type MoveMode = "move" | "rotate";

const STEP_MM = 1;
const FINE_MM = 0.1;
const STEP_DEG = 15;
const FINE_DEG = 1;

interface Options {
  scene: THREE.Scene;
  camera: THREE.PerspectiveCamera;
  canvas: HTMLCanvasElement;
  orbit: OrbitControls;
  rayHit: (event: PointerEvent) => RayHit | null;
  invalidate: () => void;
  /** Записать положение; true — сервер принял. */
  commit: (part: string, move: number[], rotate: number[]) => Promise<boolean>;
}

export class Mover {
  private readonly opts: Options;
  private readonly gizmo: TransformControls;
  private readonly pivot = new THREE.Object3D();
  private mode: MoveMode | null = null;
  private part: string | null = null;
  private object: THREE.Object3D | null = null;
  private placement: PlacementDTO | null = null;
  /**
   * Деталь, чьё положение ушло на сервер и ещё не вернулось. Вернулось — это
   * И новая геометрия, И новое положение: они приходят порознь (событие
   * `geom.updated` раньше ответа сборки), и ручки, поставленные по одному
   * новому объекту, встали бы по прежнему положению.
   */
  private pending: { part: string; object: THREE.Object3D | null; placement: PlacementDTO | null } | null = null;
  private drag: { plane: THREE.Plane; start: THREE.Vector3; pivot0: THREE.Vector3 } | null = null;
  private fine = false;

  constructor(opts: Options) {
    this.opts = opts;
    this.gizmo = new TransformControls(opts.camera, opts.canvas);
    this.gizmo.setSpace("world");
    this.gizmo.setSize(0.9);
    this.applySnap();
    opts.scene.add(this.pivot, this.gizmo.getHelper());
    this.gizmo.addEventListener("objectChange", this.follow);
    this.gizmo.addEventListener("dragging-changed", this.onDragging);
    this.gizmo.addEventListener("change", () => opts.invalidate());
    window.addEventListener("keydown", this.onKey);
    window.addEventListener("keyup", this.onKey);
  }

  /** Ручки видны? Тогда клики по ним — их, а не выбор детали. */
  get busy(): boolean {
    return this.gizmo.dragging || this.drag !== null;
  }

  /** Курсор над ручкой — нажатие достанется ей. */
  get overHandle(): boolean {
    return this.gizmo.axis !== null;
  }

  setMode(mode: MoveMode | null): void {
    this.mode = mode;
    this.reattach();
  }

  /** Кого двигать: выделенная деталь, её объект и положение с сервера. */
  target(part: string | null, object: THREE.Object3D | null, placement: PlacementDTO | null): void {
    // Пришли новая геометрия этой детали и её новое положение — сервер принял.
    if (this.pending && part === this.pending.part && object !== this.pending.object
      && placement !== this.pending.placement) this.pending = null;
    this.part = part;
    this.object = object;
    this.placement = placement;
    this.reattach();
  }

  private reattach(): void {
    this.gizmo.detach();
    const ready = this.mode && this.part && this.object && this.placement && this.object.visible
      && this.pending?.part !== this.part;
    if (!ready) {
      this.opts.invalidate();
      return;
    }
    this.pivot.position.copy(pivotOf(this.placement!));
    this.pivot.quaternion.identity();
    this.pivot.updateMatrixWorld();
    this.gizmo.setMode(this.mode === "move" ? "translate" : "rotate");
    this.gizmo.attach(this.pivot);
    this.opts.invalidate();
  }

  /** Показанная деталь идёт за опорой — до ответа сервера. */
  private follow = (): void => {
    if (!this.object || !this.placement) return;
    const { position, quaternion } = visualOffset(this.placement, this.pivot.position, this.pivot.quaternion);
    this.object.position.copy(position);
    this.object.quaternion.copy(quaternion);
    this.opts.invalidate();
  };

  private onDragging = (event: { value: unknown }): void => {
    const dragging = Boolean(event.value);
    // Орбита не должна крутить сцену, пока тянут ручку.
    this.opts.orbit.enabled = !dragging;
    if (!dragging) void this.finish();
  };

  // ------------------------------------------------ тело детали по столу
  /** Нажатие по выделенной детали в режиме «Перемещение» — начало переноса. */
  pointerDown(event: PointerEvent): boolean {
    if (this.mode !== "move" || !this.part || !this.object || !this.placement) return false;
    if (event.button !== 0 || this.overHandle || this.pending?.part === this.part) return false;
    const ray = this.opts.rayHit(event);
    if (!ray || ray.part !== this.part || ray.onCap) return false;
    // Плоскость переноса — горизонталь через точку хвата: деталь едет по столу.
    const plane = new THREE.Plane(new THREE.Vector3(0, 0, 1), -ray.hit.point.z);
    this.drag = { plane, start: ray.hit.point.clone(), pivot0: this.pivot.position.clone() };
    this.opts.orbit.enabled = false;
    this.opts.canvas.setPointerCapture(event.pointerId);
    return true;
  }

  pointerMove(event: PointerEvent): void {
    if (!this.drag) return;
    const at = this.planeHit(event, this.drag.plane);
    if (!at) return;
    const step = this.fine ? FINE_MM : STEP_MM;
    const dx = Math.round((at.x - this.drag.start.x) / step) * step;
    const dy = Math.round((at.y - this.drag.start.y) / step) * step;
    this.pivot.position.set(this.drag.pivot0.x + dx, this.drag.pivot0.y + dy, this.drag.pivot0.z);
    this.pivot.updateMatrixWorld();
    this.follow();
  }

  pointerUp(event: PointerEvent): boolean {
    if (!this.drag) return false;
    this.drag = null;
    this.opts.orbit.enabled = true;
    if (this.opts.canvas.hasPointerCapture(event.pointerId)) {
      this.opts.canvas.releasePointerCapture(event.pointerId);
    }
    void this.finish();
    return true;
  }

  /**
   * Жест оборван системой (`pointercancel`: окно потеряло указатель, сенсорный
   * жест перехватила ОС). Прежде такого обработчика не было: перенос оставался
   * «взведённым», орбита — выключенной до следующего щелчка. Оборванный жест —
   * НЕ правка проекта: деталь возвращается туда, где она есть в проекте.
   */
  pointerCancel(event: PointerEvent): boolean {
    if (!this.drag) return false;
    this.drag = null;
    this.opts.orbit.enabled = true;
    if (this.opts.canvas.hasPointerCapture(event.pointerId)) {
      this.opts.canvas.releasePointerCapture(event.pointerId);
    }
    this.reset();
    return true;
  }

  private planeHit(event: PointerEvent, plane: THREE.Plane): THREE.Vector3 | null {
    const rect = this.opts.canvas.getBoundingClientRect();
    const ndc = new THREE.Vector2(
      ((event.clientX - rect.left) / rect.width) * 2 - 1,
      -((event.clientY - rect.top) / rect.height) * 2 + 1,
    );
    const ray = new THREE.Raycaster();
    ray.setFromCamera(ndc, this.opts.camera);
    return ray.ray.intersectPlane(plane, new THREE.Vector3());
  }

  // ------------------------------------------------------------ итог жеста
  private async finish(): Promise<void> {
    const part = this.part;
    const before = this.placement;
    if (!part || !before) return;
    const after = placementAfter(before, this.pivot.position, this.pivot.quaternion);
    // Поворот сверяется как поворот, а не тройкой углов: один и тот же может
    // записаться по-разному (±180°).
    const same = after.move.every((v, i) => Math.abs(v - (before.move[i] ?? 0)) < 1e-4)
      && rotationOf(after.rotate).angleTo(rotationOf(before.rotate)) < 1e-6;
    if (same) {
      this.reset();
      return;
    }
    this.pending = { part, object: this.object, placement: before };
    this.gizmo.detach();
    const ok = await this.opts.commit(part, after.move, after.rotate);
    if (!ok) {
      // Сервер не принял — деталь возвращается туда, где она есть в проекте.
      this.pending = null;
      this.reset();
    }
  }

  /** Вернуть показанную деталь на её место в проекте. */
  private reset(): void {
    if (this.object) {
      this.object.position.set(0, 0, 0);
      this.object.quaternion.identity();
    }
    this.reattach();
  }

  private applySnap(): void {
    this.gizmo.setTranslationSnap(this.fine ? FINE_MM : STEP_MM);
    this.gizmo.setRotationSnap(THREE.MathUtils.degToRad(this.fine ? FINE_DEG : STEP_DEG));
  }

  private onKey = (event: KeyboardEvent): void => {
    if (event.key !== "Shift") return;
    const fine = event.type === "keydown";
    if (fine === this.fine) return;
    this.fine = fine;
    this.applySnap();
  };

  dispose(): void {
    window.removeEventListener("keydown", this.onKey);
    window.removeEventListener("keyup", this.onKey);
    this.gizmo.detach();
    this.gizmo.dispose();
    this.opts.scene.remove(this.pivot, this.gizmo.getHelper());
  }
}
