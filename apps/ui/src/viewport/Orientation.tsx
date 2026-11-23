/**
 * Куб видов и триада осей — где у сцены верх, перед и оси.
 *
 * Оба следят за камерой по подписке и двигают DOM напрямую, мимо состояния
 * React: поворот камеры идёт шестьдесят раз в секунду, и перерисовывать ради
 * него дерево компонентов было бы расточительно.
 *
 * Куб — CSS 3D без перспективы, то есть ортографический: он показывает
 * ориентацию, а не глубину. Грани кликабельны и ведут в тот же вид, что
 * клавиши 1–7. Геометрия граней — в `orient.ts`, её проверяет тест.
 */
import { useEffect, useRef } from "react";
import * as THREE from "three";

import { viewport } from "./control";
import { css, FACES, faceMatrix, screenOf } from "./orient";
import type { CameraListener } from "./scene";

type Subscribe = (listener: CameraListener) => () => void;

/** Половина ребра куба в пикселях CSS. */
const HALF = 38;

export function ViewCube({ subscribe }: { subscribe: Subscribe }) {
  const cubeRef = useRef<HTMLDivElement>(null);

  useEffect(
    () =>
      subscribe((camera) => {
        const cube = cubeRef.current;
        if (cube) cube.style.transform = css(screenOf(camera));
      }),
    [subscribe],
  );

  return (
    <div className="view-cube" aria-label="Куб видов">
      <div ref={cubeRef} className="view-cube-body">
        {FACES.map((face) => (
          <button
            key={face.view}
            className={`view-cube-face face-${face.view}`}
            style={{ transform: css(faceMatrix(face.view, HALF)) }}
            title={`Вид: ${face.label.toLowerCase()}`}
            onClick={() => viewport.setView(face.view)}
          >
            {face.label}
          </button>
        ))}
      </div>
    </div>
  );
}

const AXES: { name: string; dir: [number, number, number]; className: string }[] = [
  { name: "X", dir: [1, 0, 0], className: "axis-x" },
  { name: "Y", dir: [0, 1, 0], className: "axis-y" },
  { name: "Z", dir: [0, 0, 1], className: "axis-z" },
];

/** Размер триады и длина луча оси, в пикселях. */
const GIZMO = 76;
const REACH = 26;

export function AxisGizmo({ subscribe }: { subscribe: Subscribe }) {
  const lines = useRef<(SVGLineElement | null)[]>([]);
  const labels = useRef<(SVGGElement | null)[]>([]);
  const groups = useRef<(SVGGElement | null)[]>([]);
  const svgRef = useRef<SVGSVGElement>(null);

  useEffect(
    () =>
      subscribe((camera) => {
        const screen = screenOf(camera);
        const c = GIZMO / 2;
        const depth: { i: number; z: number }[] = [];
        AXES.forEach((axis, i) => {
          const v = new THREE.Vector3(...axis.dir).applyMatrix4(screen);
          const x = c + v.x * REACH;
          const y = c + v.y * REACH;
          lines.current[i]?.setAttribute("x2", x.toFixed(2));
          lines.current[i]?.setAttribute("y2", y.toFixed(2));
          const label = labels.current[i];
          if (label) {
            const lx = c + v.x * (REACH + 9);
            const ly = c + v.y * (REACH + 9);
            label.setAttribute("transform", `translate(${lx.toFixed(2)} ${ly.toFixed(2)})`);
            // Ось, смотрящая прямо на нас, вырождается в точку — подпись гаснет.
            label.style.opacity = String(Math.min(1, Math.hypot(v.x, v.y) * 2.2));
          }
          depth.push({ i, z: v.z });
        });
        // Ближняя ось — поверх: порядок элементов SVG и есть порядок рисования.
        const svg = svgRef.current;
        if (svg) {
          depth.sort((a, b) => a.z - b.z);
          for (const { i } of depth) {
            const group = groups.current[i];
            if (group) svg.appendChild(group);
          }
        }
      }),
    [subscribe],
  );

  const c = GIZMO / 2;
  return (
    <svg ref={svgRef} className="axis-gizmo" width={GIZMO} height={GIZMO} aria-hidden="true">
      <circle cx={c} cy={c} r={2.5} className="axis-origin" />
      {AXES.map((axis, i) => (
        <g key={axis.name} ref={(el) => { groups.current[i] = el; }} className={axis.className}>
          <line ref={(el) => { lines.current[i] = el; }} x1={c} y1={c} x2={c} y2={c} />
          <g ref={(el) => { labels.current[i] = el; }}>
            <text textAnchor="middle" dominantBaseline="central">{axis.name}</text>
          </g>
        </g>
      ))}
    </svg>
  );
}
