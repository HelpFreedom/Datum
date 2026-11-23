/**
 * Стол, сетка, оси и тень.
 *
 * Ось Z вверх — как при печати и как во всей остальной Datum. У three.js по
 * умолчанию вверх Y, поэтому ось задаётся явно и камере, и управлению: иначе
 * орбита вращалась бы вокруг не той оси, а «вид сверху» смотрел бы сбоку.
 *
 * Начало координат — в УГЛУ стола, а не в центре. Так строят детали все скрипты
 * рабочего процесса CAD (координаты у них положительные), и так же показывает
 * стол слайсер.
 *
 * СЕТКА РИСУЕТСЯ ШЕЙДЕРОМ, а не линиями. Линии в WebGL толщиной ровно в
 * пиксель и без сглаживания: вдали они сливались в муар, и мелкую сетку
 * приходилось прятать по расстоянию камеры скачком. Здесь каждая линия
 * сглажена по своей экранной ширине (`fwidth`), мелкая гаснет плавно ровно
 * тогда, когда её шаг становится меньше пары пикселей, а пол растворяется к
 * краям, не обрываясь.
 */
import * as THREE from "three";

/** Цвета — sRGB, как в CSS; в шейдер они уходят линейными (THREE.Color). */
const COLOR_MINOR = 0x1f2a3a;
const COLOR_MAJOR = 0x2c3b52;
const COLOR_BED = 0x3a5070;
const COLOR_X = 0xff4d5e;
const COLOR_Y = 0x38e08a;
const COLOR_Z = 0x3d8bff;
const COLOR_VOLUME = 0x2a3a52;
const COLOR_POOL = 0x2c4262;

const VERTEX = /* glsl */ `
  uniform vec2 uOffset;
  varying vec2 vPos;
  void main() {
    vPos = position.xy + uOffset;
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
  }
`;

const FRAGMENT = /* glsl */ `
  uniform vec2 uBed;
  uniform float uFade;
  uniform vec3 uMinor;
  uniform vec3 uMajor;
  uniform vec3 uBedColor;
  uniform vec3 uAxisX;
  uniform vec3 uAxisY;
  uniform vec3 uPool;
  uniform vec3 uPoolColor;
  varying vec2 vPos;

  // Линия сетки шагом step: 1 на линии, 0 между, край сглажен в пиксель.
  float lines(vec2 p, float step) {
    vec2 coord = p / step;
    vec2 width = fwidth(coord);
    vec2 g = abs(fract(coord - 0.5) - 0.5) / max(width, vec2(1e-6));
    return 1.0 - min(min(g.x, g.y), 1.0);
  }

  void main() {
    vec2 p = vPos;
    // Сколько миллиметров приходится на пиксель: по этому гасится мелкая сетка.
    float mmPerPx = max(fwidth(p.x), fwidth(p.y));
    float minorFade = 1.0 - smoothstep(0.25, 0.7, mmPerPx);
    float majorFade = 1.0 - smoothstep(2.5, 7.0, mmPerPx);

    vec2 centre = uBed * 0.5;
    float reach = length(p - centre);
    float fade = 1.0 - smoothstep(uFade * 0.35, uFade, reach);

    bool onBed = p.x >= 0.0 && p.y >= 0.0 && p.x <= uBed.x && p.y <= uBed.y;
    float inside = onBed ? 1.0 : 0.45;

    vec3 colour = vec3(0.0);
    float alpha = 0.0;

    // Заливка стола: чуть светлее пола, чтобы рабочее поле читалось.
    if (onBed) { colour = uBedColor; alpha = 0.07; }

    // Пятно света под деталями (uPool: центр и радиус): на нём видна тень.
    float d = length(p - uPool.xy) / uPool.z;
    float pool = exp(-d * d * 2.2);
    colour = mix(colour, uPoolColor, pool);
    alpha = max(alpha, pool * 0.85);

    float minor = lines(p, 1.0) * minorFade * 0.55 * inside;
    float major = lines(p, 10.0) * majorFade * inside;
    colour = mix(colour, uMinor, minor);
    alpha = max(alpha, minor * 0.9);
    colour = mix(colour, uMajor, major);
    alpha = max(alpha, major);

    // Кромка стола.
    vec2 edgeDist = min(abs(p), abs(p - uBed)) / max(fwidth(p), vec2(1e-6));
    bool alongX = p.x >= 0.0 && p.x <= uBed.x;
    bool alongY = p.y >= 0.0 && p.y <= uBed.y;
    float edge = 0.0;
    if (alongX) edge = max(edge, 1.0 - min(edgeDist.y, 1.0));
    if (alongY) edge = max(edge, 1.0 - min(edgeDist.x, 1.0));
    colour = mix(colour, uBedColor * 1.6, edge);
    alpha = max(alpha, edge * 0.9);

    alpha *= fade;

    // Оси с ореолом: ядро в пиксель и мягкое свечение вокруг. Ореол не гаснет
    // вместе с полом так быстро — оси видны дальше сетки.
    float axisFade = 1.0 - smoothstep(uFade * 0.5, uFade * 1.1, reach);
    float dx = abs(p.y) / max(fwidth(p.y), 1e-6);
    float dy = abs(p.x) / max(fwidth(p.x), 1e-6);
    float coreX = 1.0 - min(dx / 1.2, 1.0);
    float coreY = 1.0 - min(dy / 1.2, 1.0);
    float glowX = exp(-dx / 5.0) * 0.45;
    float glowY = exp(-dy / 5.0) * 0.45;
    float ax = max(coreX, glowX) * axisFade;
    float ay = max(coreY, glowY) * axisFade;
    colour = mix(colour, uAxisX, ax);
    alpha = max(alpha, ax);
    colour = mix(colour, uAxisY, ay);
    alpha = max(alpha, ay);

    gl_FragColor = vec4(colour, alpha);
    #include <colorspace_fragment>
  }
`;

export interface Plate {
  group: THREE.Group;
  /** Пол, принимающий тень деталей. Свет ставит сцена — ей виден габарит. */
  shadow: THREE.Mesh;
  setGrid: (visible: boolean) => void;
  /** Пятно света на полу: центр и радиус в мм. */
  setPool: (x: number, y: number, radius: number) => void;
  dispose: () => void;
}

export function buildPlate(bed: [number, number, number]): Plate {
  const [x, y, z] = bed;
  const span = Math.max(x, y) * 4;
  const group = new THREE.Group();

  // Пол — квадрат со стороной в четыре стола, с центром на столе: сетка
  // уходит за край и растворяется, как на полу мастерской.
  const floorGeometry = new THREE.PlaneGeometry(span, span);
  const floor = new THREE.Mesh(
    floorGeometry,
    new THREE.ShaderMaterial({
      vertexShader: VERTEX,
      fragmentShader: FRAGMENT,
      uniforms: {
        uOffset: { value: new THREE.Vector2(0, 0) },
        uBed: { value: new THREE.Vector2(x, y) },
        uFade: { value: span / 2 },
        uMinor: { value: new THREE.Color(COLOR_MINOR) },
        uMajor: { value: new THREE.Color(COLOR_MAJOR) },
        uBedColor: { value: new THREE.Color(COLOR_BED) },
        uAxisX: { value: new THREE.Color(COLOR_X) },
        uAxisY: { value: new THREE.Color(COLOR_Y) },
        uPool: { value: new THREE.Vector3(x / 2, y / 2, Math.max(x, y) / 3) },
        uPoolColor: { value: new THREE.Color(COLOR_POOL) },
      },
      transparent: true,
      depthWrite: false,
      side: THREE.DoubleSide,
      // Дно детали лежит ровно на z=0: без смещения сетка рисовалась бы
      // поверх него при взгляде снизу.
      polygonOffset: true,
      polygonOffsetFactor: 1,
      polygonOffsetUnits: 1,
    }),
  );
  // Геометрия центрирована, а сетке нужны мировые координаты — сдвиг и в
  // положении, и в шейдере.
  floor.position.set(x / 2, y / 2, 0);
  (floor.material as THREE.ShaderMaterial).uniforms.uOffset.value.set(x / 2, y / 2);
  floor.renderOrder = -2;

  const shadow = new THREE.Mesh(
    new THREE.PlaneGeometry(span, span),
    new THREE.ShadowMaterial({ color: 0x000000, opacity: 0.62, depthWrite: false }),
  );
  shadow.position.set(x / 2, y / 2, 0);
  shadow.receiveShadow = true;
  shadow.renderOrder = -1;

  // Ось Z — вертикаль из начала координат, на высоту объёма печати.
  const zAxis = new THREE.Line(
    new THREE.BufferGeometry().setFromPoints([
      new THREE.Vector3(0, 0, 0),
      new THREE.Vector3(0, 0, z),
    ]),
    new THREE.LineBasicMaterial({ color: COLOR_Z, transparent: true, opacity: 0.7 }),
  );

  // Объём печати: показывает, что деталь в него помещается, не занимая экран.
  const volume = new THREE.LineSegments(
    new THREE.EdgesGeometry(new THREE.BoxGeometry(x, y, z)),
    new THREE.LineBasicMaterial({ color: COLOR_VOLUME, transparent: true, opacity: 0.45 }),
  );
  volume.position.set(x / 2, y / 2, z / 2);

  group.add(floor, shadow, zAxis, volume);

  return {
    group,
    shadow,
    setGrid: (visible) => {
      floor.visible = visible;
      volume.visible = visible;
    },
    setPool: (px, py, radius) => {
      (floor.material as THREE.ShaderMaterial).uniforms.uPool.value.set(px, py, radius);
    },
    dispose: () => {
      for (const object of [floor, shadow, zAxis, volume]) {
        object.geometry.dispose();
        (object.material as THREE.Material).dispose();
      }
      group.clear();
    },
  };
}
