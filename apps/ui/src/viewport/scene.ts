/**
 * Вьюпорт: сцена, камера, управление, загрузка деталей.
 *
 * three.js здесь императивный, без react-three-fiber, и это решение, а не
 * лень. Вьюпорт — долгоживущая поверхность на 60 кадров в секунду,
 * чувствительная к освобождению ресурсов GPU; примирителю между деревом React
 * и циклом отрисовки тут делать нечего.
 *
 * ОТРИСОВКА ПО ТРЕБОВАНИЮ. Кадр запрашивается, только когда что-то изменилось
 * или орбита ещё докатывается: пока человек набирает параметр, приложение не
 * ест процессор вовсе. Раньше цикл кадров крутился всегда и лишь пропускал
 * отрисовку — обещание «не ест вовсе» было неправдой.
 *
 * ЗАМЕНА ДЕТАЛИ — ПО ОТПЕЧАТКУ. Пришло `geom.updated` с тем же отпечатком —
 * не делаем ничего: ни запроса, ни разбора, ни выделения памяти на GPU. Именно
 * ради этого адрес детали и содержит отпечаток её содержимого.
 */
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { RoomEnvironment } from "three/examples/jsm/environments/RoomEnvironment.js";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";

import { Caliper, type RayHit } from "./caliper";
import type { Offsets } from "./explode";
import { Mover } from "./mover";
import type { PlacementDTO } from "./placement";
import { buildPlate, type Plate } from "./plate";
import { snap, type Readout, type SnapPoint, type Tool } from "./measure";
import { buildCap, clipPlane, type PlaneName, type SectionFn } from "./section";
import { VIEW_DIRECTIONS, viewUp } from "./orient";

export interface PartGeometry {
  name: string;
  digest: string;
  url: string;
  bbox: number[];
}

/** Образец — импортированная модель, показанная для сравнения, а не деталь. */
export interface GhostGeometry {
  name: string;
  digest: string;
  url: string;
  visible: boolean;
}

export type ViewName = "iso" | "top" | "bottom" | "front" | "back" | "left" | "right";

/** Запрос толщины на сервер: у окна для этого нет канонической геометрии. */
export type ProbeFn = (
  part: string,
  point: [number, number, number],
  direction: [number, number, number],
) => Promise<{ distance: number | null; thickness: number | null }>;

interface Loaded {
  digest: string;
  object: THREE.Object3D;
}

/** Точка замера — и попала ли она на крышку среза, а не на поверхность детали. */
type PickedPoint = SnapPoint & { onCap: boolean };

/** Выделение — цвет акцента окна (`--accent` в styles.css). */
const ACCENT = 0x3d9bff;
/** Замеры — тёплые: на холодной сцене и синем выделении они не теряются. */
const MEASURE = 0xffb547;
/** Образец — стекло цвета акцента: видно, где он, и видно сквозь него детали. */
const GHOST = 0x9ccaff;

/** Слушатель поворота камеры: куб видов и триада осей в углу. */
export type CameraListener = (camera: THREE.Camera) => void;

export class Viewport {
  private renderer: THREE.WebGLRenderer;
  private scene = new THREE.Scene();
  private camera: THREE.PerspectiveCamera;
  private controls: OrbitControls;
  private plate: Plate;
  private parts = new Map<string, Loaded>();
  /**
   * Загрузки в полёте: деталь → отпечаток, который грузится. Раньше деталь
   * попадала в `parts` только в колбэке загрузчика, и второй `sync`, пришедший
   * до конца первой загрузки, грузил её ещё раз: в сцене оставались сироты без
   * освобождения памяти, а при обратном порядке ответов побеждала УСТАРЕВШАЯ
   * версия.
   */
  private pending = new Map<string, string>();
  private partRoot = new THREE.Group();
  /**
   * Образцы — отдельно от деталей: они не ловят замер и выделение, не дают
   * крышек среза, не разъезжаются при разнесении и не бросают тени. Числа
   * снимаются только с деталей — с того, что проверяется и печатается.
   */
  private ghostRoot = new THREE.Group();
  private ghosts = new Map<string, Loaded>();
  /**
   * Проигрыватель симуляции: звенья механизма своими сетками (нулевая поза в
   * координатах сцены), поверх стола. Пока он открыт, детали и образцы
   * спрятаны и не ловят ни выделения, ни замера: на экране — механизм в
   * движении, а не проект (`viewport/sim.ts`).
   */
  private simRoot = new THREE.Group();
  private simLinks: THREE.Object3D[] = [];
  private simToken = 0;
  /** Материалы звеньев и их родные цвета — карта загрузки перекрашивает и возвращает. */
  private simPaint: { material: THREE.MeshStandardMaterial; base: THREE.Color }[][] = [];
  /** Поле МКЭ поверх звена: сетка с цветами вершин, дочерняя у звена (та же рамка). */
  private simStress: { link: number; object: THREE.Object3D } | null = null;
  private simStressToken = 0;
  private ghostPending = new Map<string, string>();
  private outline: THREE.BoxHelper | null = null;
  private selectedName: string | null = null;
  /** Доля полного разнесения, 0 — собрано. */
  private exploded = 0;
  private offsets: Offsets = {};
  /** Звенья не в собранном положении: имя → матрица движения (`joints.ts`). */
  private poses = new Map<string, THREE.Matrix4>();
  private loader = new GLTFLoader();
  private dirty = true;
  private shadowDirty = true;
  private gauge = 0;
  private frame = 0;
  private disposed = false;
  /** Вписывали ли камеру в содержимое хоть раз. */
  private framed = false;
  /**
   * Распорядился ли камерой человек: повернул, приблизил, выбрал вид. До того
   * пришедший образец перевписывает вид вместе с собой — иначе камера,
   * вписанная в кубик, оказывалась внутри большой модели, пришедшей следом.
   * После — вид больше не трогается: выбор человека не перебивают.
   */
  private cameraOwned = false;
  /**
   * Мир для отсечения и пролёта: центр и радиус содержимого вместе со столом.
   * Обновляется с геометрией (`fitShadow`), а не каждый кадр — обход сцены
   * ради габарита на каждом кадре был бы лишним.
   */
  private worldCentre = new THREE.Vector3();
  private worldRadius = 200;
  /** Радиус содержимого (деталей и образцов) — мера шага колеса. */
  private contentRadius = 50;
  private bed: [number, number, number];
  private hidden = new Set<string>();
  private cameraListeners = new Set<CameraListener>();
  private key: THREE.DirectionalLight;
  private environment: THREE.Texture;

  // ------------------------------------------------------------- замеры
  private tool: Tool = "none";
  private picked: PickedPoint[] = [];
  private marks = new THREE.Group();
  private raycaster = new THREE.Raycaster();
  private canvas: HTMLCanvasElement;
  private probeFn: ProbeFn | null = null;
  private onReadout: ((readout: Readout | null) => void) | null = null;
  /** Выбор щелчком: где нажали — щелчок ли это, а не начало поворота. */
  private press: { x: number; y: number; t: number } | null = null;
  private onSelect: ((name: string | null) => void) | null = null;
  /** Штангенциркуль: снап при наведении, размеры с подписями. */
  private caliper: Caliper;
  /** Перемещение и поворот детали мышью — правка положения в проекте. */
  private mover: Mover;
  private placements = new Map<string, PlacementDTO>();
  private placeFn: ((part: string, move: number[], rotate: number[]) => Promise<boolean>) | null = null;

  // ------------------------------------------------------------- сечение
  private sectionFn: SectionFn | null = null;
  private sectionPlane: PlaneName | null = null;
  private sectionAt = 0;
  private caps = new THREE.Group();
  private capTimer = 0;
  private capToken = 0;
  private capBusy = false;
  private capAgain = false;
  private onSectionProblem: ((problem: string | null) => void) | null = null;
  /** Номер замера толщины: поздний ответ прежнего щелчка не ложится поверх. */
  private probeTicket = 0;

  constructor(
    canvas: HTMLCanvasElement,
    token: string,
    bed: [number, number, number] = [256, 256, 256],
  ) {
    this.bed = bed;

    // Ось Z вверх задаётся ГЛОБАЛЬНО, до создания камеры и управления: иначе
    // OrbitControls возьмёт своё умолчание и будет вращать вокруг Y.
    THREE.Object3D.DEFAULT_UP.set(0, 0, 1);

    // Холст ПРОЗРАЧНЫЙ: фон с виньеткой рисует CSS под ним (`.viewport-host`).
    // Градиент в самой сцене стоил бы отдельного прохода на каждый кадр, а
    // на экране неотличим.
    this.renderer = new THREE.WebGLRenderer({
      canvas,
      antialias: true,
      alpha: true,
      powerPreference: "high-performance",
    });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.renderer.setClearColor(0x000000, 0);
    // Нейтральное тональное отображение: у ACES сдвигаются оттенки, а цвет
    // детали задан проектом и должен остаться тем, что в нём записано.
    this.renderer.toneMapping = THREE.NeutralToneMapping;
    this.renderer.shadowMap.enabled = true;
    // Тень мягкая — дисперсионная (VSM) с размытием. PCF в r186 даёт резкий
    // край, а PCFSoftShadowMap убран. Недостаток VSM — просвечивание на
    // сложных приёмниках — здесь не страшен: тень принимает только пол.
    this.renderer.shadowMap.type = THREE.VSMShadowMap;
    // Карта тени — по требованию (`invalidate`), а не на каждый кадр.
    this.renderer.shadowMap.autoUpdate = false;

    this.camera = new THREE.PerspectiveCamera(45, 1, 0.5, 5000);
    this.camera.up.set(0, 0, 1);

    this.controls = new OrbitControls(this.camera, canvas);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.12;
    // Приближение колесом — своё (`onWheel`): у OrbitControls шаг — доля
    // расстояния, и вблизи центра вращения колесо почти переставало двигать.
    this.controls.enableZoom = false;
    this.controls.screenSpacePanning = true;
    this.controls.mouseButtons = {
      LEFT: THREE.MOUSE.ROTATE,
      MIDDLE: THREE.MOUSE.PAN,
      RIGHT: THREE.MOUSE.PAN,
    };
    // Камера тени не меняет — карту перерисовывать незачем (`invalidate`).
    this.controls.addEventListener("change", () => this.invalidate(false));
    this.controls.addEventListener("start", () => {
      this.cameraOwned = true;
    });

    // Токен нужен и загрузчику: /obj/<отпечаток>.glb охраняется наравне с /api.
    // Заголовок не мешает кешу браузера — тот ключуется адресом.
    this.loader.setRequestHeader({ "X-Datum-Token": token });

    this.canvas = canvas;
    // Ручки перемещения — ПЕРВЫМИ: их обработчик нажатия должен отработать
    // раньше нашего, чтобы нажатие по ручке не стало ещё и выбором детали.
    this.mover = new Mover({
      scene: this.scene,
      camera: this.camera,
      canvas,
      orbit: this.controls,
      rayHit: (event) => this.rayHit(event),
      invalidate: () => this.invalidate(),
      commit: (part, move, rotate) =>
        this.placeFn ? this.placeFn(part, move, rotate) : Promise.resolve(false),
    });
    this.caliper = new Caliper({
      scene: this.scene,
      camera: this.camera,
      canvas,
      rayHit: (event) => this.rayHit(event),
      // Наведение штангенциркуля — подсветка и метки, теней они не бросают.
      invalidate: () => this.invalidate(false),
      onReadout: (reading) => this.onReadout?.(reading),
    });
    canvas.addEventListener("pointerdown", this.onPointerDown);
    canvas.addEventListener("pointermove", this.onPointerMove);
    canvas.addEventListener("pointerup", this.onPointerUp);
    canvas.addEventListener("pointercancel", this.onPointerCancel);
    canvas.addEventListener("pointerleave", this.onPointerLeave);
    canvas.addEventListener("wheel", this.onWheel, { passive: false });
    canvas.addEventListener("webglcontextlost", this.onContextLost);
    canvas.addEventListener("webglcontextrestored", this.onContextRestored);
    // Прибор памяти: раз в минуту строка в консоль окна (она уходит в
    // window.log). Окно однажды умерло от нехватки памяти после трёх часов
    // работы, и сказать, что росло, было нечем (аудит 2026-11-21).
    this.gauge = window.setInterval(() => console.info(this.memoryLine()), MEMORY_GAUGE_MS);

    // Отсечение — локальное: стол и крышки среза резать не надо.
    this.renderer.localClippingEnabled = true;

    this.plate = buildPlate(bed);
    this.scene.add(this.plate.group, this.partRoot, this.ghostRoot, this.simRoot, this.marks, this.caps);
    // Окружение — процедурная «комната» three: файла в бандле ей не нужно, а
    // блики и мягкие переходы на гранях читаются как объём даже при плоской
    // заливке.
    const pmrem = new THREE.PMREMGenerator(this.renderer);
    this.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
    pmrem.dispose();
    this.scene.environment = this.environment;
    this.scene.environmentIntensity = 0.55;
    this.key = this.addLights();
    this.setView("iso");
    this.invalidate();
  }

  /** Три источника поверх окружения. Возвращает ключевой: он даёт тень. */
  private addLights(): THREE.DirectionalLight {
    this.scene.add(new THREE.HemisphereLight(0xc4d2ea, 0x1a2230, 0.9));
    const key = new THREE.DirectionalLight(0xffffff, 1.9);
    key.castShadow = true;
    key.shadow.mapSize.set(2048, 2048);
    key.shadow.bias = -0.0004;
    key.shadow.normalBias = 0.6;
    key.shadow.radius = 10;
    key.shadow.blurSamples = 16;
    // Холодный контровой свет сзади: силуэт отделяется от тёмного фона.
    const rim = new THREE.DirectionalLight(0x8fb8ff, 0.9);
    rim.position.set(220, 300, 160);
    const fill = new THREE.DirectionalLight(0x9fb2d0, 0.35);
    fill.position.set(260, -180, 90);
    this.scene.add(key, key.target, rim, fill);
    this.fitShadow(key);
    return key;
  }

  /**
   * Навести тень на содержимое. Камера тени охватывает габарит деталей с
   * запасом: на весь стол карта в 2048 точек размазала бы тень мелкой детали
   * в пятно.
   */
  private fitShadow(key = this.key): void {
    const box = this.contentBox();
    const centre = box.getCenter(new THREE.Vector3());
    const radius = Math.max(Viewport.radiusOf(box), 20) * 1.6;
    key.target.position.set(centre.x, centre.y, 0);
    key.position.set(centre.x - radius * 0.55, centre.y - radius * 0.8, radius * 1.4);
    const cam = key.shadow.camera;
    cam.left = -radius;
    cam.right = radius;
    cam.top = radius;
    cam.bottom = -radius;
    cam.near = 0.5;
    cam.far = radius * 5;
    cam.updateProjectionMatrix();
    key.target.updateMatrixWorld();
    // Пятно света на полу — там же, где тень: на почти чёрном полу тень
    // не видна, а на освещённом она и держит деталь на столе.
    this.plate.setPool(centre.x, centre.y, radius * 1.25);
    this.worldCentre.copy(centre);
    this.contentRadius = Viewport.radiusOf(box);
    this.worldRadius = Math.max(this.contentRadius, Math.hypot(...this.bed) / 2);
  }

  /**
   * Ближняя и дальняя плоскости — по положению камеры, а не числами навсегда:
   * прежние 0.5…5000 мм обрезали всю сцену при отдалении, а вплотную к мелкой
   * детали ближняя срезала её саму. Отношение дальней к ближней держится в
   * пределах 2·10⁵ — точности буфера глубины на это хватает.
   */
  private fitClipping(): void {
    const cam = this.camera;
    const d = cam.position.distanceTo(this.controls.target);
    const far = cam.position.distanceTo(this.worldCentre) + this.worldRadius * 2;
    const near = Math.max(far / 2e5, Math.min(Math.max(d * 0.01, 0.01), 0.5));
    if (Math.abs(cam.far - far) > far * 0.01 || Math.abs(cam.near - near) > near * 0.01) {
      cam.near = near;
      cam.far = far;
      cam.updateProjectionMatrix();
    }
  }

  /**
   * Приближение колесом — ПОСТОЯННЫМ шагом (просьба владельца). У OrbitControls
   * шаг — доля расстояния до центра вращения: чем ближе, тем больше щелчков на
   * тот же путь, а вплотную колесо почти стоит. Здесь щелчок — всегда одно и
   * то же расстояние: `WHEEL_STEP` радиуса содержимого (деталь в пару
   * сантиметров — миллиметры, стол — сантиметры); с Shift — впятеро мельче.
   *
   * Камера идёт к точке под курсором: она и центр вращения сжимаются к точке
   * курсора на плоскости центра — как `zoomToCursor` у OrbitControls, только
   * на постоянный шаг. Дошли до этой плоскости — пролёт: камера и центр
   * сдвигаются вместе, и колесо ведёт дальше тем же шагом, не упираясь.
   *
   * Единственное исключение — далеко от сцены (дальше полутора исходных
   * видов): там шаг растёт с расстоянием, иначе из «улетевшего» вида
   * пришлось бы крутить сотни щелчков. В рабочем диапазоне скорость одна.
   */
  private onWheel = (event: WheelEvent): void => {
    // С Shift Chromium отдаёт вертикальное колесо как горизонтальное (deltaX).
    const delta = event.deltaY !== 0 ? event.deltaY : event.shiftKey ? event.deltaX : 0;
    if (!this.controls.enabled || delta === 0) return;
    event.preventDefault();
    const cam = this.camera.position;
    const target = this.controls.target;
    const d = cam.distanceTo(target);
    // Мышь даёт ±100…120 на щелчок, тачпад — мелкие порции; построчный режим
    // (deltaMode 1) — в строках.
    const ticks = Math.min(5, Math.abs(delta * (event.deltaMode === 1 ? 33 : 1)) / 100);
    let step = Math.max(this.contentRadius * WHEEL_STEP, 0.05) * ticks * (event.shiftKey ? 0.2 : 1);
    // «Далеко» — дальше полутора исходных видов (и не ближе FAR_RADII радиусов):
    // от вида «как при открытии» и ближе скорость одна.
    const far = Math.max(this.contentRadius * FAR_RADII, this.distanceFor(this.contentRadius) * 1.5);
    if (d > far) step = Math.max(step, d * 0.1 * ticks);
    const closer = delta < 0;

    const rect = this.canvas.getBoundingClientRect();
    this.raycaster.setFromCamera(
      new THREE.Vector2(((event.clientX - rect.left) / rect.width) * 2 - 1,
                        -((event.clientY - rect.top) / rect.height) * 2 + 1),
      this.camera,
    );
    const ray = this.raycaster.ray.direction;
    const view = target.clone().sub(cam).normalize();
    const cosine = Math.max(ray.dot(view), 1e-3);
    // Точка под курсором на плоскости центра вращения.
    const anchor = cam.clone().addScaledVector(ray, d / cosine);

    if (closer && d - step <= step) {
      // Плоскость центра рядом — пролёт: камера и центр вместе, центр — не
      // ближе двух шагов впереди, чтобы вращение не шло вокруг глаза.
      const shift = ray.clone().multiplyScalar(step);
      cam.add(shift);
      target.copy(cam).addScaledVector(view, Math.max(d, 2 * step));
    } else {
      // Единственный предел отдаления — 500 радиусов сцены: там она уже точка,
      // а без предела камера уходила на 10¹⁰ мм, и точности float не хватало.
      const next = closer ? d - step : Math.min(d + step, this.worldRadius * 500);
      const k = next / d;
      cam.sub(anchor).multiplyScalar(k).add(anchor);
      target.sub(anchor).multiplyScalar(k).add(anchor);
    }
    this.cameraOwned = true;
    this.invalidate();
  };

  /** Камера — туда, где она была при открытии проекта: изометрия на всё содержимое. */
  home(): void {
    this.setView("iso");
  }

  /** Что держит сцена — одной строкой для журнала. */
  memoryLine(): string {
    const info = this.renderer.info;
    const heap = (performance as unknown as { memory?: { usedJSHeapSize?: number } }).memory
      ?.usedJSHeapSize;
    return `[память] геометрий ${info.memory.geometries}, текстур ${info.memory.textures}, `
      + `программ ${info.programs?.length ?? 0}, деталей ${this.parts.size}, образцов `
      + `${this.ghosts.size}, куча ${heap ? Math.round(heap / 1048576) : "?"} МБ`;
  }

  // Контекст WebGL потерян (драйвер, нехватка видеопамяти): по умолчанию
  // браузер его не возвращает. Разрешаем вернуть, а вернувшийся — перерисовываем:
  // отрисовка по требованию, и сам кадр никто бы не запросил.
  private onContextLost = (event: Event): void => {
    event.preventDefault();
    console.warn("[окно] контекст WebGL потерян — жду возврата");
  };

  private onContextRestored = (): void => {
    console.warn("[окно] контекст WebGL возвращён — перерисовываю");
    this.invalidate();
  };

  /**
   * Нужен кадр. `shadow = false` — когда изменилось только то, что тени не
   * трогает (камера, наведение): карта тени (VSM 2048² с размытием — второй
   * проход всей геометрии) тогда не перерисовывается. Прежде она считалась на
   * КАЖДЫЙ кадр орбиты и наведения, хотя свет и детали стояли. По умолчанию —
   * перерисовать: забытый флаг даёт лишнюю работу, а не застывшую тень.
   */
  invalidate(shadow = true): void {
    this.dirty = true;
    if (shadow) this.shadowDirty = true;
    this.schedule();
  }

  private schedule(): void {
    if (this.frame || this.disposed) return;
    this.frame = requestAnimationFrame(this.loop);
  }

  /** Откуда и куда смотрит камера — окно сообщает это агенту. */
  view(): { position: number[]; target: number[]; up: number[]; fov: number } {
    const v = (p: THREE.Vector3) => [p.x, p.y, p.z];
    return { position: v(this.camera.position), target: v(this.controls.target), up: v(this.camera.up), fov: this.camera.fov };
  }

  /** Вернуть камеру туда, где она была (`view()`): «Вернуть как было» после показа. */
  restoreView(view: { position: number[]; target: number[]; up: number[] }): void {
    this.camera.position.set(view.position[0], view.position[1], view.position[2]);
    this.camera.up.set(view.up[0], view.up[1], view.up[2]);
    this.controls.target.set(view.target[0], view.target[1], view.target[2]);
    this.controls.update();
    this.invalidate();
  }

  /** Подписаться на движение камеры. Возвращает отписку. */
  onCamera(listener: CameraListener): () => void {
    this.cameraListeners.add(listener);
    listener(this.camera);
    return () => this.cameraListeners.delete(listener);
  }

  /** Показать или спрятать сетку и объём печати. */
  setGrid(visible: boolean): void {
    this.plate.setGrid(visible);
    this.invalidate();
  }

  /**
   * Спрятать детали. Спрятанная не ловит замер, не даёт крышку среза и не
   * участвует во «вписать» — иначе камера вписывала бы то, чего не видно.
   */
  setHidden(names: string[]): void {
    this.hidden = new Set(names);
    for (const [name, loaded] of this.parts) loaded.object.visible = !this.hidden.has(name);
    if (this.selectedName && this.hidden.has(this.selectedName)) this.select(this.selectedName);
    if (this.sectionPlane) this.scheduleCaps();
    this.fitShadow();
    this.invalidate();
  }

  resize(width: number, height: number): void {
    if (width < 1 || height < 1) return;
    this.renderer.setSize(width, height, false);
    this.caliper.resize(width, height);
    this.camera.aspect = width / height;
    this.camera.updateProjectionMatrix();
    this.invalidate();
  }

  // ------------------------------------------------------------- детали
  /**
   * Свести сцену к присланному набору. Неизменившиеся детали не трогаются.
   *
   * Прежняя версия детали остаётся на экране, пока грузится новая: окно не
   * мигает пустотой на каждой пересборке. Загрузка, которую успели перебить
   * новой версией или удалением детали, по приходу выбрасывается.
   */
  async sync(wanted: PartGeometry[]): Promise<void> {
    const byName = new Map(wanted.map((p) => [p.name, p]));

    for (const [name, loaded] of [...this.parts]) {
      if (!byName.has(name)) {
        this.partRoot.remove(loaded.object);
        disposeTree(loaded.object);
        this.parts.delete(name);
        // Разбор сетки удалённой детали (копии в Float64) штангенциркулю
        // больше не нужен: прежде он жил до следующей загрузки любой детали.
        this.caliper.forget();
      }
    }
    for (const name of [...this.pending.keys()]) {
      if (!byName.has(name)) this.pending.delete(name);    // опоздавшую выбросим
    }

    const jobs = wanted.filter((p) => {
      if (!p.url) return false;
      if (this.parts.get(p.name)?.digest === p.digest) {
        // Уже показана. Если в полёте была другая версия (деталь вернули к
        // прежнему виду) — она устарела: её ответ выбросится по приходу.
        this.pending.delete(p.name);
        return false;
      }
      if (this.pending.get(p.name) === p.digest) return false;  // уже грузится
      this.pending.set(p.name, p.digest);
      return true;
    });
    await Promise.all(jobs.map((p) => this.load(p)));

    // Первая геометрия — единственный момент, когда камеру двигают без
    // просьбы: до неё вписывать было не во что, а после человек уже сам
    // выбрал, куда смотреть, и перебивать его выбор нельзя.
    if (!this.framed && this.parts.size > 0) {
      this.framed = true;
      this.setView("iso");
    }
    if (jobs.length) this.fitShadow();
    // Геометрия сменилась — сменился и срез. Новая деталь без пересчёта
    // крышки выглядела бы полой.
    if (this.sectionPlane && jobs.length) {
      this.section(this.sectionPlane, this.sectionAt);
    }
    this.select(this.selectedName);                // рамка — по новому объекту
    this.invalidate();
  }

  private load(part: PartGeometry): Promise<void> {
    return new Promise((resolve) => {
      this.loader.load(
        part.url,
        (gltf) => {
          const object = gltf.scene;
          // Пока грузилось, деталь могли пересобрать ещё раз или удалить:
          // тогда этот ответ устарел, и показывать его нельзя.
          if (this.disposed || this.pending.get(part.name) !== part.digest) {
            disposeTree(object);
            return resolve();
          }
          this.pending.delete(part.name);
          const previous = this.parts.get(part.name);
          if (previous) {
            this.partRoot.remove(previous.object);
            disposeTree(previous.object);
          }
          object.name = part.name;
          object.visible = !this.hidden.has(part.name);
          object.traverse((node) => {
            if ((node as THREE.Mesh).isMesh) node.castShadow = true;
          });
          // Загрузчик three сам ставит flatShading, когда в файле нет NORMAL, —
          // ровно то, как должна выглядеть деталь CAD.
          this.parts.set(part.name, { digest: part.digest, object });
          // Новая геометрия — прежние черты для штангенциркуля недействительны.
          this.caliper.forget();
          // И показанный замер: прежде линии и число оставались на экране (и
          // уходили агенту как «замер, который видит человек»), а следующий
          // щелчок мерил от точки детали, которой уже нет (найдено обзором).
          if (previous) this.clearMeasurements();
          // Деталь могла пересобраться, пока вид разнесён: без этого она
          // прыгнула бы на место в сборке, а соседи остались бы разведёнными.
          this.placeExploded(part.name, object);
          // И звено, показанное открытым: пересобранная крышка не захлопывается.
          if (this.poses.has(part.name)) this.placePosed(part.name, object);
          this.partRoot.add(object);
          this.invalidate();
          resolve();
        },
        undefined,
        (error) => {
          if (this.pending.get(part.name) === part.digest) this.pending.delete(part.name);
          console.error(`деталь ${part.name} не загрузилась:`, error);
          resolve();
        },
      );
    });
  }

  // ------------------------------------------------------------ образцы
  /**
   * Свести образцы к присланному набору. Спрятанный образец не грузится
   * вовсе: модель в полтора миллиона треугольников — это десятки мегабайт, и
   * тянуть их ради невидимого незачем.
   */
  syncGhosts(wanted: GhostGeometry[]): void {
    const byName = new Map(wanted.map((g) => [g.name, g]));
    for (const [name, loaded] of [...this.ghosts]) {
      const want = byName.get(name);
      if (!want || want.digest !== loaded.digest) {
        this.ghostRoot.remove(loaded.object);
        disposeTree(loaded.object);
        this.ghosts.delete(name);
      } else {
        loaded.object.visible = want.visible;
      }
    }
    for (const name of [...this.ghostPending.keys()]) {
      const want = byName.get(name);
      if (!want || !want.visible || want.digest !== this.ghostPending.get(name)) {
        this.ghostPending.delete(name);          // опоздавший ответ выбросится
      }
    }
    for (const ghost of wanted) {
      if (!ghost.visible || !ghost.url || this.ghosts.has(ghost.name)) continue;
      if (this.ghostPending.get(ghost.name) === ghost.digest) continue;
      this.ghostPending.set(ghost.name, ghost.digest);
      this.loadGhost(ghost);
    }
    this.invalidate();
  }

  private loadGhost(ghost: GhostGeometry): void {
    this.loader.load(
      ghost.url,
      (gltf) => {
        const object = gltf.scene;
        if (this.disposed || this.ghostPending.get(ghost.name) !== ghost.digest) {
          disposeTree(object);
          return;
        }
        this.ghostPending.delete(ghost.name);
        object.name = ghost.name;
        object.traverse((node) => {
          const mesh = node as THREE.Mesh;
          if (!mesh.isMesh) return;
          for (const material of materialsOf(mesh)) material.dispose();
          mesh.material = new THREE.MeshStandardMaterial({
            color: GHOST,
            emissive: 0x0b2240,
            roughness: 0.45,
            metalness: 0,
            transparent: true,
            opacity: 0.24,
            // Без записи глубины детали внутри образца видны сквозь него.
            depthWrite: false,
          });
          mesh.castShadow = false;
        });
        this.ghosts.set(ghost.name, { digest: ghost.digest, object });
        this.ghostRoot.add(object);
        if (!this.cameraOwned) this.setView("iso");
        this.fitShadow();
        this.invalidate();
      },
      undefined,
      (error) => {
        if (this.ghostPending.get(ghost.name) === ghost.digest) this.ghostPending.delete(ghost.name);
        console.error(`образец ${ghost.name} не загрузился:`, error);
      },
    );
  }

  select(name: string | null): void {
    // Имя запоминается: после замены детали рамку надо построить заново —
    // старая держалась за освобождённый объект и показывала прежний габарит.
    this.selectedName = name;
    if (this.outline) {
      this.scene.remove(this.outline);
      this.outline.geometry.dispose();
      (this.outline.material as THREE.Material).dispose();
      this.outline = null;
    }
    // Выделенная деталь светится акцентом изнутри — едва, чтобы цвет детали
    // остался её цветом, — и обведена рамкой габарита.
    const glow = new THREE.Color(ACCENT).multiplyScalar(0.16);
    for (const [partName, part] of this.parts) {
      part.object.traverse((node) => {
        const mesh = node as THREE.Mesh;
        if (!mesh.isMesh) return;
        for (const material of materialsOf(mesh)) {
          const standard = material as THREE.MeshStandardMaterial;
          if (standard.emissive) standard.emissive.copy(partName === name ? glow : BLACK);
        }
      });
    }
    const loaded = name ? this.parts.get(name) : undefined;
    this.mover.target(name, loaded?.object ?? null, name ? this.placements.get(name) ?? null : null);
    if (loaded && loaded.object.visible) {
      this.outline = new THREE.BoxHelper(loaded.object, ACCENT);
      const material = this.outline.material as THREE.LineBasicMaterial;
      material.transparent = true;
      material.opacity = 0.75;
      this.scene.add(this.outline);
    }
    this.invalidate();
  }

  // --------------------------------------------------------- разнесение
  /**
   * Развести детали по смещениям `offsets`, взятым с долей `fraction`.
   *
   * ЗАМЕРЫ И СРЕЗ ПРИ ЭТОМ ГАСНУТ, и это не лень, а правило проекта: числа на
   * экране обязаны совпадать с числами в проверках. Расстояние между
   * разъехавшимися деталями — не расстояние в конструкции; крышка среза
   * считается сервером по КАНОНИЧЕСКОЙ геометрии и после сдвига оказалась бы
   * не там, где рез. Показать такое значило бы соврать убедительно.
   */
  explode(offsets: Offsets, fraction: number): void {
    this.offsets = offsets;
    const was = this.exploded;
    this.exploded = fraction;
    // Не разнесено и не было: трогать нечего. Смещения пересчитываются на
    // каждую новую геометрию, и прежде это ставило ВСЕ детали в ноль — в том
    // числе только что перенесённую, чья новая версия ещё грузится: она на
    // миг прыгала на старое место.
    if (fraction === 0 && was === 0) return;
    for (const [name, loaded] of this.parts) {
      const o = offsets[name] ?? [0, 0, 0];
      loaded.object.position.set(o[0] * fraction, o[1] * fraction, o[2] * fraction);
    }
    if (fraction > 0 && was === 0) {
      if (this.sectionPlane) this.section(null);
      if (this.tool !== "none") this.setTool("none");
      this.clearMeasurements();
    }
    this.outline?.update();
    this.fitShadow();
    this.invalidate();
  }

  /**
   * Показать звенья шарниров в положениях `poses` (пустое — все на местах).
   *
   * Это вид, как разнесение, и правило то же: замер и срез гаснут — сдвинутое
   * звено не там, где его меряют проверки и режет сервер. Разнесение и ход
   * сменяют друг друга в сторе, поэтому здесь они не складываются.
   */
  poseJoints(poses: Record<string, THREE.Matrix4>): void {
    const before = this.poses;
    this.poses = new Map(Object.entries(poses));
    if (before.size === 0 && this.poses.size === 0) return;
    for (const name of new Set([...before.keys(), ...this.poses.keys()])) {
      const loaded = this.parts.get(name);
      if (loaded) this.placePosed(name, loaded.object);
    }
    if (this.poses.size > 0 && before.size === 0) {
      if (this.sectionPlane) this.section(null);
      if (this.tool !== "none") this.setTool("none");
      this.clearMeasurements();
    }
    this.outline?.update();
    this.fitShadow();
    this.invalidate();
  }

  private placePosed(name: string, object: THREE.Object3D): void {
    const m = this.poses.get(name);
    if (m) {
      m.decompose(object.position, object.quaternion, object.scale);
      return;
    }
    // Звено вернулось. Поворот снимается ВСЕГДА: «Разнести» при открытой
    // крышке сначала разносит (положение), а потом сбрасывает ход — и без
    // этого крышка уехала бы в разнесённый вид повёрнутой.
    object.quaternion.identity();
    object.scale.set(1, 1, 1);
    const o = this.offsets[name] ?? [0, 0, 0];
    object.position.set(o[0] * this.exploded, o[1] * this.exploded, o[2] * this.exploded);
  }

  /** Применить текущее разнесение к только что пришедшей детали. */
  private placeExploded(name: string, object: THREE.Object3D): void {
    const o = this.offsets[name];
    if (!o || this.exploded === 0) return;
    object.position.set(o[0] * this.exploded, o[1] * this.exploded, o[2] * this.exploded);
  }

  // -------------------------------------------------------------- виды
  /**
   * На каком расстоянии объект радиуса `radius` виден целиком.
   *
   * Считать по одному вертикальному углу нельзя: у узкого окна по горизонтали
   * поле зрения МЕНЬШЕ вертикального, и деталь обрезается по бокам — ровно это
   * и случилось, когда справа появилась панель проверок.
   */
  private distanceFor(radius: number): number {
    const fovY = THREE.MathUtils.degToRad(this.camera.fov);
    const fovX = 2 * Math.atan(Math.tan(fovY / 2) * this.camera.aspect);
    const byHeight = radius / Math.sin(fovY / 2);
    const byWidth = radius / Math.sin(fovX / 2);
    return Math.max(byHeight, byWidth) * 1.08; // поля, чтобы не касалось краёв
  }

  private static radiusOf(box: THREE.Box3): number {
    return Math.max(box.getBoundingSphere(new THREE.Sphere()).radius, 5);
  }

  /** Камеру направил человек (клавиша, куб, меню): дальше её не перебивать. */
  ownCamera(): void {
    this.cameraOwned = true;
  }

  setView(view: ViewName): void {
    const box = this.contentBox();
    const centre = box.getCenter(new THREE.Vector3());
    const distance = this.distanceFor(Viewport.radiusOf(box));

    const direction = new THREE.Vector3(...VIEW_DIRECTIONS[view]).normalize();
    this.camera.up.set(...viewUp(view));

    this.camera.position.copy(centre).addScaledVector(direction, distance);
    this.controls.target.copy(centre);
    this.controls.update();
    this.invalidate();
  }

  /** Вписать: всё содержимое, одну деталь или несколько (показ агента). */
  fit(only?: string | string[] | null): void {
    const box = this.contentBox(only ?? undefined);
    const centre = box.getCenter(new THREE.Vector3());
    const distance = this.distanceFor(Viewport.radiusOf(box));
    const direction = this.camera.position
      .clone()
      .sub(this.controls.target)
      .normalize();
    this.camera.position.copy(centre).addScaledVector(direction, distance);
    this.controls.target.copy(centre);
    this.controls.update();
    this.invalidate();
  }

  /** Габарит содержимого; пока деталей нет — стол, чтобы было от чего плясать. */
  private contentBox(only?: string | string[]): THREE.Box3 {
    const box = new THREE.Box3();
    // «Вписать всё» — со всем, что видно, и с образцами тоже: иначе образец
    // рядом с деталью оказывался за краем экрана.
    const names = typeof only === "string" ? [only] : only;
    // Идёт показ симуляции — вписывается механизм: детали спрятаны.
    const source = this.simLinks.length
      ? this.simLinks
      : names && names.length
        ? names.map((name) => this.parts.get(name)?.object)
        : [...this.parts.values(), ...this.ghosts.values()].map((p) => p.object);
    let any = false;
    for (const object of source) {
      if (!object || !object.visible) continue;
      box.expandByObject(object);
      any = true;
    }
    if (!any) {
      box.setFromCenterAndSize(
        new THREE.Vector3(this.bed[0] / 2, this.bed[1] / 2, this.bed[2] / 6),
        new THREE.Vector3(this.bed[0], this.bed[1], this.bed[2] / 3),
      );
    }
    return box;
  }

  // -------------------------------------------------------------- сечение
  setSection(fn: SectionFn, onProblem?: (problem: string | null) => void): void {
    this.sectionFn = fn;
    this.onSectionProblem = onProblem ?? null;
  }

  /**
   * Поставить или убрать секущую плоскость.
   *
   * Отсечение включается сразу — оно мгновенно. Крышка считается на сервере и
   * потому ждёт паузы в движении: иначе каждое движение ползунка порождало бы
   * запрос, и к моменту ответа он уже устарел бы.
   */
  section(plane: PlaneName | null, at = 0): void {
    this.sectionPlane = plane;
    this.sectionAt = at;

    const planes = plane ? [clipPlane(plane, at)] : [];
    this.partRoot.traverse((node) => {
      const mesh = node as THREE.Mesh;
      if (!mesh.isMesh) return;
      for (const material of materialsOf(mesh)) {
        material.clippingPlanes = planes;
        material.clipShadows = true;
        // Отсечённая деталь смотрит на нас изнанкой стенок; без двусторонней
        // отрисовки на месте среза была бы дыра в никуда.
        material.side = plane ? THREE.DoubleSide : THREE.FrontSide;
        material.needsUpdate = true;
      }
    });

    // Любая смена среза делает устаревшими крышки, которые ещё в пути: без
    // этого поздний ответ клал их обратно после выключения среза (или поверх
    // разнесённых деталей) — найдено обзором.
    this.capToken += 1;
    window.clearTimeout(this.capTimer);
    this.clearCaps();
    if (plane) this.scheduleCaps();
    else this.onSectionProblem?.(null);
    this.invalidate();
  }

  private clearCaps(): void {
    for (const child of [...this.caps.children]) {
      this.caps.remove(child);
      const mesh = child as THREE.Mesh;
      mesh.geometry?.dispose();
      for (const material of materialsOf(mesh)) material.dispose();
    }
  }

  private scheduleCaps(): void {
    window.clearTimeout(this.capTimer);
    // 120 мс: меньше — и запросы пойдут очередью во время движения, больше —
    // и пауза становится заметной.
    this.capTimer = window.setTimeout(() => void this.rebuildCaps(), 120);
  }

  private async rebuildCaps(): Promise<void> {
    // Один запрос за раз. Пока он в пути, новые остановки ползунка не шлют
    // своих: запоминается только, что нужен ещё круг, — и он возьмёт уже
    // последнее положение. Так устаревшие срезы не считаются вовсе.
    if (this.capBusy) {
      this.capAgain = true;
      return;
    }
    this.capBusy = true;
    try {
      do {
        this.capAgain = false;
        const plane = this.sectionPlane;
        const fn = this.sectionFn;
        if (!plane || !fn) return;

        // Ответ может прийти после того, как срез сдвинули или выключили:
        // крышка от позиции, которую человек уже проехал, не кладётся.
        const token = this.capToken;
        const at = this.sectionAt;
        const wanted = [...this.parts.entries()].filter(([name]) => !this.hidden.has(name));
        if (!wanted.length) {
          this.clearCaps();
          this.onSectionProblem?.(null);
          continue;
        }
        let answer: Awaited<ReturnType<SectionFn>> | null = null;
        let problem: string | null = null;
        try {
          answer = await fn(wanted.map(([name]) => name), plane, at);
        } catch (e) {
          // Прежде отказ глотался, и срез молча оставался без крышек.
          problem = e instanceof Error ? e.message : String(e);
        }
        if (this.disposed) return;
        if (token !== this.capToken) continue;

        this.clearCaps();
        for (const [name, part] of wanted) {
          const cut = answer?.parts?.[name];
          if (!cut) continue;
          const cap = buildCap(cut, plane, at, colourOf(part.object));
          if (!cap) continue;
          // Чья крышка — для замера: по ней можно ставить точки, контуры
          // посчитаны сервером на канонической геометрии.
          cap.userData.part = name;
          this.caps.add(cap);
        }
        const failed = Object.keys(answer?.failed ?? {});
        if (!problem && failed.length) {
          problem = `срез без крышки у ${failed.join(", ")}: деталь не собралась`;
        }
        this.onSectionProblem?.(problem);
        this.invalidate();
      } while (this.capAgain);
    } finally {
      this.capBusy = false;
    }
  }

  // --------------------------------------------------------------- замеры
  setProbe(fn: ProbeFn, onReadout: (readout: Readout | null) => void): void {
    this.probeFn = fn;
    this.onReadout = onReadout;
  }

  setTool(tool: Tool): void {
    this.tool = tool;
    this.clearMeasurements();
    this.caliper.active = tool === "distance";
    this.mover.setMode(tool === "move" ? "move" : tool === "rotate" ? "rotate" : null);
    // Во время замера орбита мешает: случайный сдвиг мыши превращает клик в
    // поворот, и точка ставится не туда. Левая кнопка отдаётся замеру,
    // панорама и зум остаются. При перемещении орбита остаётся: тянут либо
    // ручку, либо саму деталь, а мимо них — крутят сцену.
    this.controls.enableRotate = tool === "none" || tool === "move" || tool === "rotate";
  }

  /** Положения деталей с сервера: от них считается жест перемещения. */
  setPlacements(placements: Record<string, PlacementDTO>): void {
    this.placements = new Map(Object.entries(placements));
    this.select(this.selectedName);
  }

  /** Как записать положение (операция part_place). */
  setPlaceHandler(fn: (part: string, move: number[], rotate: number[]) => Promise<boolean>): void {
    this.placeFn = fn;
  }

  clearMeasurements(): void {
    this.probeTicket += 1;
    this.caliper?.clear();
    this.picked = [];
    for (const child of [...this.marks.children]) {
      this.marks.remove(child);
      const mesh = child as THREE.Mesh;
      mesh.geometry?.dispose();
      const material = mesh.material as THREE.Material | THREE.Material[] | undefined;
      if (Array.isArray(material)) material.forEach((m) => m.dispose());
      else material?.dispose();
    }
    this.onReadout?.(null);
    this.invalidate();
  }

  /** Выбирать детали щелчком — при инструменте «Обзор». */
  setSelectHandler(fn: (name: string | null) => void): void {
    this.onSelect = fn;
  }

  /** Инструменты, при которых щелчок выбирает деталь. */
  private selecting(): boolean {
    return this.tool === "none" || this.tool === "move" || this.tool === "rotate";
  }

  private onPointerDown = (event: PointerEvent): void => {
    if (event.button !== 0) return;
    // Как в слайсере: схватил любую деталь — её и тащишь. Выделение здесь же,
    // сразу в сцене: ждать круга через React значило бы начать жест без цели.
    if (this.tool === "move" && !this.mover.overHandle) {
      const ray = this.rayHit(event);
      if (ray && ray.part && !ray.onCap && ray.part !== this.selectedName) {
        this.select(ray.part);
        this.onSelect?.(ray.part);
      }
    }
    // Ручка перемещения или хват самой детали — жест, а не щелчок.
    if (this.mover.overHandle || this.mover.pointerDown(event)) {
      this.press = null;
      return;
    }
    if (this.selecting()) {
      this.press = { x: event.clientX, y: event.clientY, t: performance.now() };
      return;
    }
    if (this.tool === "distance") {
      this.caliper.click(event);
      return;
    }
    const hit = this.pick(event);
    if (!hit) return;

    this.addMark(hit.point);
    this.picked.push(hit);

    if (this.tool === "probe") {
      this.picked = [];
      if (hit.onCap) {
        // Зонд меряет ЦЕЛУЮ каноническую деталь: срез на экране — только вид.
        // Толщина «по крышке» была бы про место, которого у детали нет.
        this.onReadout?.({
          tool: "probe",
          text: "по срезу не меряется",
          detail:
            "зонд идёт по целой детали, а срез — только вид. Щёлкните по стенке " +
            "детали или уберите срез",
        });
      } else {
        void this.runProbe(hit);
      }
    }
    this.invalidate();
  };

  /**
   * Щелчок — это нажатие без сдвига. Со сдвигом больше пары пикселей или
   * дольше полусекунды — это был поворот орбиты, и выделение не трогаем.
   */
  private onPointerMove = (event: PointerEvent): void => {
    this.mover.pointerMove(event);
    this.caliper.hover(event);
  };

  private onPointerLeave = (): void => {
    this.caliper.leave();
  };

  private onPointerUp = (event: PointerEvent): void => {
    if (this.mover.pointerUp(event)) return;
    const press = this.press;
    this.press = null;
    if (!press || !this.selecting() || event.button !== 0 || !this.onSelect) return;
    const moved = Math.hypot(event.clientX - press.x, event.clientY - press.y);
    if (moved > 4 || performance.now() - press.t > 500) return;
    const hit = this.pick(event);
    this.onSelect(hit ? hit.part || null : null);
  };

  /** Жест оборван системой: ни переноса, ни щелчка из него не выходит. */
  private onPointerCancel = (event: PointerEvent): void => {
    this.mover.pointerCancel(event);
    this.press = null;
  };

  private async runProbe(hit: SnapPoint): Promise<void> {
    if (!this.probeFn) return;
    // Ответ сервера приходит позже: инструмент могли сменить, замеры сбросить
    // (E, Esc) или щёлкнуть ещё раз — и прежний ответ возвращал на экран
    // «толщину стенки» не к месту и сообщал её агенту (найдено обзором).
    const ticket = ++this.probeTicket;
    this.onReadout?.({ tool: "probe", text: "считаю…", detail: hit.part });
    // Луч пускается СНАРУЖИ внутрь: старт отодвинут по нормали, иначе он
    // упёрся бы в ту самую поверхность, на которой стоит.
    const start = hit.point.clone().addScaledVector(hit.normal, 1.0);
    const direction = hit.normal.clone().negate();
    try {
      const result = await this.probeFn(
        hit.part,
        [start.x, start.y, start.z],
        [direction.x, direction.y, direction.z],
      );
      if (ticket !== this.probeTicket || this.disposed) return;
      const mm = (v: number | null) =>
        v === null ? "—" : `${v.toLocaleString("ru-RU", { maximumFractionDigits: 3 })} мм`;
      this.onReadout?.({
        tool: "probe",
        text: result.thickness === null ? "толщина не найдена" : mm(result.thickness),
        detail:
          `толщина стенки, посчитана на сервере по канонической геометрии\n` +
          `${hit.part} · ${hit.kind} · ` +
          `${hit.point.x.toFixed(2)}, ${hit.point.y.toFixed(2)}, ${hit.point.z.toFixed(2)}`,
      });
    } catch (e) {
      if (ticket !== this.probeTicket || this.disposed) return;
      this.onReadout?.({
        tool: "probe",
        text: "не удалось",
        detail: e instanceof Error ? e.message : String(e),
      });
    }
  }

  /**
   * Что под курсором — С УЧЁТОМ СРЕЗА.
   *
   * Луч three о `clippingPlanes` не знает: он попадал в отсечённую, невидимую
   * поверхность, и точка ставилась на верхнюю грань, которой на экране нет, а
   * зонд уходил на сервер с невидимой стороны. Попадания за плоскостью среза
   * отбрасываются, а крышки среза сами становятся целями.
   */
  /**
   * Попадание луча — С УЧЁТОМ СРЕЗА и видимости (см. `pick`), без снапа.
   * Снап решают сами инструменты: штангенциркуль ловит рёбра и окружности.
   */
  private rayHit(event: PointerEvent): RayHit | null {
    const rect = this.canvas.getBoundingClientRect();
    const x = event.clientX - rect.left;
    const y = event.clientY - rect.top;
    this.raycaster.setFromCamera(
      new THREE.Vector2((x / rect.width) * 2 - 1, -(y / rect.height) * 2 + 1),
      this.camera,
    );
    const hits = this.raycaster.intersectObjects(
      [...this.partRoot.children.filter((o) => o.visible && this.partRoot.visible), ...this.caps.children], true);
    const plane = this.sectionPlane ? clipPlane(this.sectionPlane, this.sectionAt) : null;
    const visible = hits.find((h) =>
      this.isCap(h.object) || !plane || plane.distanceToPoint(h.point) >= -1e-6);
    if (!visible) return null;
    const onCap = this.isCap(visible.object);
    const part = onCap ? String(visible.object.userData.part ?? "") : this.partOf(visible.object);
    return { hit: visible, part, onCap };
  }

  private pick(event: PointerEvent): PickedPoint | null {
    const rect = this.canvas.getBoundingClientRect();
    const x = event.clientX - rect.left;
    const y = event.clientY - rect.top;
    this.raycaster.setFromCamera(
      new THREE.Vector2((x / rect.width) * 2 - 1, -(y / rect.height) * 2 + 1),
      this.camera,
    );
    // Луч three о видимости не знает: спрятанная деталь ловила бы клики.
    const hits = this.raycaster.intersectObjects(
      [...this.partRoot.children.filter((o) => o.visible && this.partRoot.visible), ...this.caps.children], true);
    const plane = this.sectionPlane ? clipPlane(this.sectionPlane, this.sectionAt) : null;
    const visible = hits.find((h) =>
      this.isCap(h.object) || !plane || plane.distanceToPoint(h.point) >= -1e-6);
    if (!visible) return null;
    const onCap = this.isCap(visible.object);
    const part = onCap ? String(visible.object.userData.part ?? "") : this.partOf(visible.object);
    return { ...snap(visible, this.camera, rect.width, rect.height, part), onCap };
  }

  private isCap(object: THREE.Object3D): boolean {
    return object.parent === this.caps;
  }

  /** Чей это меш: у загруженного GLB имя стоит на корне сцены детали. */
  private partOf(object: THREE.Object3D): string {
    let node: THREE.Object3D | null = object;
    while (node) {
      if (node.parent === this.partRoot) return node.name;
      node = node.parent;
    }
    return "";
  }

  private addMark(point: THREE.Vector3): void {
    // Размер метки — ЭКРАННЫЙ (см. `loop`): шар в 0.35 мм терялся на детали в
    // 300 мм и закрывал собой деталь в 3 мм.
    const mark = new THREE.Mesh(
      new THREE.SphereGeometry(1, 12, 8),
      new THREE.MeshBasicMaterial({ color: MEASURE, depthTest: false }),
    );
    mark.position.copy(point);
    mark.userData.dot = true;
    mark.renderOrder = 10;           // поверх детали, иначе точка тонет в ней
    this.marks.add(mark);
  }

  // ------------------------------------------------------------- отрисовка
  private loop = (): void => {
    this.frame = 0;
    if (this.disposed) return;
    // update() возвращает true, пока орбита докатывается по инерции: тогда
    // нужен следующий кадр. Остальное время кадры не запрашиваются вовсе.
    const moved = this.controls.update();
    if (!moved && !this.dirty) return;
    this.dirty = false;
    this.fitClipping();
    this.outline?.update();
    // Метки замера — постоянного размера на экране, около 6 пикселей.
    const perPixel = (2 * Math.tan(THREE.MathUtils.degToRad(this.camera.fov) / 2)) /
      Math.max(1, this.canvas.clientHeight);
    for (const mark of this.marks.children) {
      if (!mark.userData.dot) continue;
      mark.scale.setScalar(this.camera.position.distanceTo(mark.position) * perPixel * 3);
    }
    this.renderer.shadowMap.needsUpdate = this.shadowDirty;
    this.shadowDirty = false;
    this.renderer.render(this.scene, this.camera);
    this.caliper.render(this.scene, this.camera, this.canvas.clientHeight);
    for (const listener of this.cameraListeners) listener(this.camera);
    if (moved) this.schedule();
  };

  // ------------------------------------------------------------ симуляция
  /** Показать механизм: загрузить звенья и спрятать детали. false — не вышло. */
  async showSimulation(links: { name: string; url: string; color: string }[]): Promise<boolean> {
    this.hideSimulation();
    const token = ++this.simToken;
    const loaded = await Promise.all(
      links.map(
        (link) =>
          new Promise<THREE.Object3D | null>((resolve) => {
            this.loader.load(
              link.url,
              (gltf) => resolve(gltf.scene),
              undefined,
              () => resolve(null),
            );
          }),
      ),
    );
    if (this.disposed || token !== this.simToken || loaded.some((o) => o === null)) {
      for (const o of loaded) if (o) disposeTree(o);
      return false;
    }
    this.simLinks = loaded as THREE.Object3D[];
    this.simPaint = [];
    for (const [k, object] of this.simLinks.entries()) {
      object.name = links[k].name;
      const paint: { material: THREE.MeshStandardMaterial; base: THREE.Color }[] = [];
      object.traverse((node) => {
        const mesh = node as THREE.Mesh;
        if (!mesh.isMesh) return;
        mesh.castShadow = true;
        // Свой материал у каждого звена: карта перекрашивает его, не трогая общий.
        const own = (materialsOf(mesh)[0] as THREE.MeshStandardMaterial).clone();
        mesh.material = own;
        paint.push({ material: own, base: own.color.clone() });
      });
      this.simPaint.push(paint);
      this.simRoot.add(object);
    }
    this.partRoot.visible = false;
    this.ghostRoot.visible = false;
    this.clearMeasurements();
    this.fitShadow();
    this.invalidate();
    return true;
  }

  /** Поставить звенья: звено → [x, y, z, qw, qx, qy, qz] (мм). */
  setSimulationPoses(poses: number[][]): void {
    for (const [k, object] of this.simLinks.entries()) {
      const p = poses[k];
      if (!p) continue;
      object.position.set(p[0], p[1], p[2]);
      object.quaternion.set(p[4], p[5], p[6], p[3]);
    }
    this.invalidate();
  }

  /** Карта загрузки: звено → доля предела (null — родные цвета). */
  setSimulationLoad(load: number[] | null, color: (u: number) => [number, number, number]): void {
    for (const [k, paint] of this.simPaint.entries()) {
      for (const { material, base } of paint) {
        if (load === null || load[k] === undefined) material.color.copy(base);
        else {
          const [r, g, b] = color(load[k]);
          material.color.setRGB(r / 255, g / 255, b / 255, THREE.SRGBColorSpace);
        }
      }
    }
    this.invalidate();
  }

  /**
   * Поле напряжений МКЭ на звене `link` (номер в записи) — сетка в системе звена
   * при нулевой позе, поэтому она дочерняя у звена и ходит вместе с ним. Родная
   * сетка звена на это время прячется. null — снять. false — не загрузилось.
   */
  async setSimulationStress(link: number | null, url: string | null): Promise<boolean> {
    const token = ++this.simStressToken;
    this.clearSimulationStress();
    if (link === null || url === null) return true;
    const host = this.simLinks[link];
    if (!host) return false;
    const object = await new Promise<THREE.Object3D | null>((resolve) => {
      this.loader.load(url, (gltf) => resolve(gltf.scene), undefined, () => resolve(null));
    });
    if (!object) return false;
    if (this.disposed || token !== this.simStressToken || this.simLinks[link] !== host) {
      disposeTree(object);
      return false;
    }
    object.traverse((node) => {
      const mesh = node as THREE.Mesh;
      if (!mesh.isMesh) return;
      mesh.castShadow = true;
      for (const m of materialsOf(mesh)) {
        // Цвета — в вершинах (COLOR_0); основной цвет белый, иначе он умножался бы на карту.
        (m as THREE.MeshStandardMaterial).vertexColors = true;
        m.needsUpdate = true;
      }
    });
    for (const child of host.children) child.visible = false;
    host.add(object);
    this.simStress = { link, object };
    this.invalidate();
    return true;
  }

  private clearSimulationStress(): void {
    const shown = this.simStress;
    if (!shown) return;
    this.simStress = null;
    const host = this.simLinks[shown.link];
    if (host) {
      host.remove(shown.object);
      for (const child of host.children) child.visible = true;
    }
    disposeTree(shown.object);
    this.invalidate();
  }

  hideSimulation(): void {
    this.simToken++;
    this.simStressToken++;
    this.clearSimulationStress();
    for (const paint of this.simPaint) for (const { material } of paint) material.dispose();
    this.simPaint = [];
    for (const object of this.simLinks) {
      this.simRoot.remove(object);
      disposeTree(object);
    }
    this.simLinks = [];
    this.partRoot.visible = true;
    this.ghostRoot.visible = true;
    this.invalidate();
  }

  dispose(): void {
    this.disposed = true;
    this.hideSimulation();
    this.canvas.removeEventListener("pointerdown", this.onPointerDown);
    this.canvas.removeEventListener("pointermove", this.onPointerMove);
    this.canvas.removeEventListener("pointerup", this.onPointerUp);
    this.canvas.removeEventListener("pointercancel", this.onPointerCancel);
    this.canvas.removeEventListener("pointerleave", this.onPointerLeave);
    this.canvas.removeEventListener("wheel", this.onWheel);
    this.canvas.removeEventListener("webglcontextlost", this.onContextLost);
    this.canvas.removeEventListener("webglcontextrestored", this.onContextRestored);
    window.clearInterval(this.gauge);
    this.clearMeasurements();
    this.caliper.dispose();
    this.mover.dispose();
    cancelAnimationFrame(this.frame);
    this.frame = 0;
    this.pending.clear();
    window.clearTimeout(this.capTimer);
    this.clearCaps();
    this.controls.dispose();
    for (const { object } of this.parts.values()) disposeTree(object);
    this.parts.clear();
    for (const { object } of this.ghosts.values()) disposeTree(object);
    this.ghosts.clear();
    this.ghostPending.clear();
    this.plate.dispose();
    this.environment.dispose();
    this.cameraListeners.clear();
    this.renderer.dispose();
  }
}

/** Шаг колеса — доля радиуса содержимого на щелчок. */
/** Как часто прибор памяти пишет строку в журнал окна. */
const MEMORY_GAUGE_MS = 60_000;

const WHEEL_STEP = 0.08;
/** Дальше стольких радиусов содержимого шаг колеса растёт с расстоянием. */
const FAR_RADII = 3;

const BLACK = new THREE.Color(0x000000);

function materialsOf(mesh: THREE.Mesh): THREE.Material[] {
  const material = mesh.material;
  return Array.isArray(material) ? material : [material];
}

/** Цвет детали: крышка среза должна быть её оттенка, а не случайной. */
function colourOf(root: THREE.Object3D): THREE.Color {
  let found: THREE.Color | null = null;
  root.traverse((node) => {
    const mesh = node as THREE.Mesh;
    if (found || !mesh.isMesh) return;
    const material = materialsOf(mesh)[0] as THREE.MeshStandardMaterial | undefined;
    if (material?.color) found = material.color;
  });
  return (found ?? new THREE.Color(0x9aa0a6)).clone();
}

/** Память GPU не освобождается сама: геометрию и материалы надо снимать руками. */
function disposeTree(root: THREE.Object3D): void {
  root.traverse((node) => {
    const mesh = node as THREE.Mesh;
    if (!mesh.isMesh) return;
    mesh.geometry.dispose();
    const material = mesh.material;
    if (Array.isArray(material)) material.forEach((m) => m.dispose());
    else material.dispose();
  });
}
