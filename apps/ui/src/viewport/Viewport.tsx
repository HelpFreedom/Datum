/**
 * Вьюпорт в дереве React.
 *
 * React владеет холстом и ничем больше: сама сцена живёт в императивном классе
 * и переживает перерисовки. Отсюда правило — ни одного объекта three в
 * состоянии React.
 *
 * Состояние инструментов (замер, срез, разнесение, скрытые детали) — в
 * `store/ui`: его делят полоса инструментов, меню и клавиши. Здесь оно только
 * передаётся сцене.
 */
import {
  Boxes,
  ChevronDown,
  DoorOpen,
  Crosshair,
  Eraser,
  Focus,
  Grid3x3,
  House,
  Maximize,
  Maximize2,
  Minimize2,
  Move,
  MousePointer2,
  Rotate3d,
  Ruler,
  SquareSplitVertical,
  X,
} from "lucide-react";
import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { Matrix4 } from "three";

import { useDoc } from "../store/doc";
import { ghostVisible, moved, useUi } from "../store/ui";
import { runOp } from "../transport/client";
import { endpoint } from "../transport/endpoint";
import { Menu } from "../chrome/Menu";
import { bindViewport, viewport, VIEWS } from "./control";
import { explodeOffsets } from "./explode";
import { clampJoint, jointLabel, jointMatrix, jointStep, sliderRange, type JointDTO } from "./joints";
import type { Readout, Tool } from "./measure";
import { AxisGizmo, ViewCube } from "./Orientation";
import { readoutOf, WindowReporter } from "./report";
import type { PlaneName, SceneSectionAnswer } from "./section";
import { Viewport as Scene, type CameraListener } from "./scene";
import { SimLauncher } from "./SimLauncher";
import { SimPlayer } from "./SimPlayer";
import { heatColor } from "./sim";

interface ProbeResponse {
  hit: { distance_mm: number } | null;
  thickness_mm: number | null;
  error?: string;
}

const PLANES: { key: PlaneName; label: string; hint: string }[] = [
  { key: "xy", label: "Z", hint: "горизонтальный срез, плоскость Z" },
  { key: "xz", label: "Y", hint: "срез вдоль Y" },
  { key: "yz", label: "X", hint: "срез вдоль X" },
];

/** Ось, вдоль которой двигается плоскость, и её индексы в габарите. */
const AXIS: Record<PlaneName, { lo: number; hi: number; name: string }> = {
  xy: { lo: 2, hi: 5, name: "Z" },
  xz: { lo: 1, hi: 4, name: "Y" },
  yz: { lo: 0, hi: 3, name: "X" },
};

export const TOOLS: { key: Tool; label: string; hint: string; key2: string; Icon: typeof Ruler }[] = [
  { key: "none", label: "Обзор", hint: "вращать и двигать деталь", key2: "Esc", Icon: MousePointer2 },
  { key: "distance", label: "Штангенциркуль", hint: "сам ловит вершины, рёбра, середины и окружности", key2: "D", Icon: Ruler },
  { key: "probe", label: "Толщина", hint: "толщина стенки, считается на сервере", key2: "T", Icon: Crosshair },
  { key: "move", label: "Перемещение", hint: "тяните деталь по столу или за ручки; Shift — шаг 0.1 мм", key2: "M", Icon: Move },
  { key: "rotate", label: "Поворот", hint: "кольца вокруг осей, шаг 15°; Shift — 1°", key2: "R", Icon: Rotate3d },
];

/** Правят положение: имеют смысл только при выделенной детали. */
const EDITS: Tool[] = ["move", "rotate"];

/** Габарит всех деталей: пределы ползунка среза. */
function contentBox(parts: Record<string, { bbox: number[] }>): number[] | null {
  return Object.values(parts).reduce<number[] | null>((acc, part) => {
    if (part.bbox.length !== 6) return acc;
    if (!acc) return [...part.bbox];
    for (let i = 0; i < 3; i += 1) acc[i] = Math.min(acc[i], part.bbox[i]);
    for (let i = 3; i < 6; i += 1) acc[i] = Math.max(acc[i], part.bbox[i]);
    return acc;
  }, null);
}

/**
 * Поставить срез: повторное нажатие той же плоскости убирает его, новая —
 * встаёт в середину габарита. Одна функция для кнопок, меню и клавиш.
 */
export function pickPlane(next: PlaneName | null): void {
  const { plane, setPlane } = useUi.getState();
  if (next === null || next === plane) {
    setPlane(null);
    return;
  }
  const box = contentBox(useDoc.getState().parts);
  const limits = AXIS[next];
  setPlane(next, box ? (box[limits.lo] + box[limits.hi]) / 2 : 0);
}

export function ViewportPanel() {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const hostRef = useRef<HTMLDivElement>(null);
  const sceneRef = useRef<Scene | null>(null);
  const [ready, setReady] = useState(false);

  const parts = useDoc((s) => s.parts);
  const selected = useDoc((s) => s.selected);
  const bed = useDoc((s) => s.bed);
  const assembly = useDoc((s) => s.info?.assembly ?? null);

  const tool = useUi((s) => s.tool);
  const plane = useUi((s) => s.plane);
  const at = useUi((s) => s.at);
  const spread = useUi((s) => s.spread);
  const jointPoses = useUi((s) => s.joints);
  const checkRun = useDoc((s) => s.checks);
  const hidden = useUi((s) => s.hidden);
  const ghosts = useDoc((s) => s.ghosts);
  const ghostShown = useUi((s) => s.ghostShown);
  const grid = useUi((s) => s.grid);
  const focused = useUi((s) => !s.leftOpen && !s.rightOpen && !s.dockOpen);
  const { setTool, setAt, setSpread, setJoint, toggleGrid, toggleFocus } = useUi.getState();

  const [readout, setReadout] = useState<Readout | null>(null);
  /** Почему у среза нет крышек (сервер отказал, деталь не собралась). */
  const [sectionProblem, setSectionProblem] = useState<string | null>(null);
  const readoutRef = useRef<Readout | null>(null);
  readoutRef.current = readout;

  // Что видит человек — серверу, чтобы видел и агент (`window_state`,
  // `scene_view`). Собирается из хранилищ в момент отправки, а не из
  // замыкания: камера зовёт это на каждый кадр, пока её крутят.
  const reporterRef = useRef<WindowReporter | null>(null);
  const report = useCallback(() => {
    const scene = sceneRef.current;
    const reporter = reporterRef.current;
    const doc = useDoc.getState();
    if (!scene || !reporter || !doc.project) return;
    const ui = useUi.getState();
    reporter.update({
      selected: doc.selected,
      parts: Object.keys(doc.parts),
      hidden: ui.hidden,
      ghosts: doc.ghosts
        .filter((g) => ghostVisible(ui.ghostShown, g.name, g.used_by.length > 0))
        .map((g) => g.name),
      tool: ui.tool,
      section: ui.plane ? { plane: ui.plane, at: ui.at } : null,
      spread: ui.spread,
      joints: ui.joints,
      camera: scene.view(),
      ...readoutOf(readoutRef.current),
    });
  }, []);
  useEffect(() => {
    const reporter = new WindowReporter(async (state) => {
      const { result } = await runOp<{ error?: string }>("window_report", {
        project: useDoc.getState().project,
        state,
      });
      if (result.error) throw new Error(result.error);
    });
    reporterRef.current = reporter;
    return () => {
      reporter.dispose();
      reporterRef.current = null;
    };
  }, []);

  // Создание сцены — один раз на жизнь окна. Стол приходит позже, из
  // project_info, поэтому пересоздавать сцену под него нельзя: это стоило бы
  // всей загруженной геометрии.
  useEffect(() => {
    const canvas = canvasRef.current;
    const host = hostRef.current;
    if (!canvas || !host) return;

    let scene: Scene | null = null;
    let observer: ResizeObserver | null = null;
    // Сцену создаём после ожидания токена; если панель успели снять за это
    // время, создавать уже нечего — иначе цикл кадров и контекст WebGL жили бы
    // без хозяина.
    let alive = true;

    void (async () => {
      const { token } = await endpoint();
      if (!alive) return;
      scene = new Scene(canvas, token, bed);
      sceneRef.current = scene;
      scene.setProbe(async (part, point, direction) => {
        // getState, а не значение из замыкания: обработчик создаётся один раз
        // на жизнь окна, и захваченный путь проекта успел бы устареть.
        const { result } = await runOp<ProbeResponse>("measure_probe", {
          project: useDoc.getState().project,
          part,
          point,
          direction,
        });
        if (result.error) throw new Error(result.error);
        return {
          distance: result.hit?.distance_mm ?? null,
          thickness: result.thickness_mm ?? null,
        };
      }, setReadout);

      scene.setSection(async (names, sectionPlane, position) => {
        const { result } = await runOp<SceneSectionAnswer & { error?: string }>("section_scene", {
          project: useDoc.getState().project,
          plane: sectionPlane,
          at: position,
          parts: names,
        });
        if (result.error) throw new Error(result.error);
        return result;
      }, setSectionProblem);
      scene.setPlaceHandler((part, move, rotate) => useDoc.getState().placePart(part, move, rotate));
      scene.setSelectHandler((name) => {
        // Щелчок по уже выделенной — снять выделение, как в списке сцены.
        const { selected: current, select } = useDoc.getState();
        select(name === current ? null : name);
      });
      scene.resize(host.clientWidth, host.clientHeight);
      // Детали могли прийти раньше, чем сцена успела создаться (ей нужен
      // токен): эффект геометрии тогда отработал вхолостую. Догоняем.
      const { parts: loaded, selected: chosen } = useDoc.getState();
      const ui = useUi.getState();
      scene.setHidden(ui.hidden);
      scene.setGrid(ui.grid);
      void scene.sync(Object.values(loaded).map((p) => ({
        name: p.name, digest: p.digest, url: p.url, bbox: p.bbox,
      })));
      scene.select(chosen);
      bindViewport({
        setView: (view) => {
          scene?.ownCamera();
          scene?.setView(view);
        },
        fit: (only) => {
          scene?.ownCamera();
          scene?.fit(only);
        },
        home: () => {
          scene?.ownCamera();
          scene?.home();
        },
        camera: () => scene?.view() ?? null,
        restore: (view) => {
          scene?.ownCamera();
          scene?.restoreView(view);
        },
        showSim: async (links) => {
          const shown = (await scene?.showSimulation(links)) ?? false;
          if (shown) scene?.fit();
          return shown;
        },
        poseSim: (poses) => scene?.setSimulationPoses(poses),
        loadSim: (load) => scene?.setSimulationLoad(load, heatColor),
        hideSim: () => scene?.hideSimulation(),
        stressSim: (link, url) => scene?.setSimulationStress(link, url) ?? Promise.resolve(false),
      });
      setReady(true);

      observer = new ResizeObserver(() => {
        scene?.resize(host.clientWidth, host.clientHeight);
      });
      observer.observe(host);
    })();

    return () => {
      alive = false;
      bindViewport(null);
      observer?.disconnect();
      scene?.dispose();
      sceneRef.current = null;
    };
    // Зависимостей нет намеренно: сцена создаётся один раз на жизнь окна.
    // `bed` читается на момент создания, и это правильно — пересоздание сцены
    // под новый стол стоило бы всей загруженной геометрии, а стол у открытого
    // проекта не меняется.
  }, []);

  const subscribe = useCallback(
    (listener: CameraListener) => sceneRef.current?.onCamera(listener) ?? (() => undefined),
    // Подписка возможна только на живую сцену: `ready` и есть её появление.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [ready],
  );

  // Геометрия: сцена сама решит, что менять, — по отпечаткам.
  useEffect(() => {
    void sceneRef.current?.sync(
      Object.values(parts).map((p) => ({
        name: p.name,
        digest: p.digest,
        url: p.url,
        bbox: p.bbox,
      })),
    );
  }, [parts]);

  useEffect(() => {
    sceneRef.current?.select(selected);
  }, [selected]);

  // Положения деталей — с сервера, из ответа сборки: от них считается жест.
  const build = useDoc((s) => s.build);
  useEffect(() => {
    const placements: Record<string, { move: number[]; rotate: number[]; centre: number[] }> = {};
    for (const [name, entry] of Object.entries(build)) {
      if (entry.placement) placements[name] = entry.placement;
    }
    sceneRef.current?.setPlacements(placements);
  }, [build, ready]);

  useEffect(() => {
    sceneRef.current?.setHidden(hidden);
  }, [hidden]);

  useEffect(() => {
    sceneRef.current?.setGrid(grid);
  }, [grid]);

  // Образцы — импортированные модели. Сцена грузит только видимые.
  useEffect(() => {
    sceneRef.current?.syncGhosts(
      ghosts.map((g) => ({
        name: g.name,
        digest: g.digest,
        url: g.url,
        visible: ghostVisible(ghostShown, g.name, g.used_by.length > 0),
      })),
    );
  }, [ghosts, ghostShown, ready]);

  // Инструмент замера. Смена инструмента сбрасывает замер — и в сцене, и
  // здесь, в карточке.
  useEffect(() => {
    sceneRef.current?.setTool(tool);
    setReadout(null);
  }, [tool, ready]);

  useEffect(() => {
    sceneRef.current?.section(plane, at);
  }, [plane, at, ready]);

  // Перемены, которые видит человек, — серверу, после затишья (см. report.ts).
  useEffect(() => {
    report();
  }, [report, selected, hidden, ghosts, ghostShown, tool, plane, at, spread, jointPoses, readout, ready]);
  useEffect(() => {
    const scene = sceneRef.current;
    if (!ready || !scene) return undefined;
    const off = scene.onCamera(() => report());
    return () => {
      off();
    };
  }, [ready, report]);

  // Смещения при ПОЛНОМ разнесении. Промежуточные — доля от них, поэтому на
  // каждое движение ползунка пересчитывать нечего.
  const offsets = useMemo(() => {
    const boxes: Record<string, number[]> = {};
    for (const part of Object.values(parts)) boxes[part.name] = part.bbox;
    return explodeOffsets(
      assembly && assembly.order.length > 0
        ? { order: assembly.order, explode_gap: assembly.explode_gap, steps: assembly.steps }
        : null,
      boxes,
    );
  }, [assembly, parts]);

  useEffect(() => {
    sceneRef.current?.explode(offsets, spread);
  }, [offsets, spread, ready]);

  // Шарниры из assembly.yaml. Звено, которого больше нет (файл поправили),
  // из показанных убирается — иначе осталась бы погашенной вся панель замера.
  const joints: Record<string, JointDTO> = useMemo(() => assembly?.joints ?? {}, [assembly]);
  useEffect(() => {
    for (const name of Object.keys(useUi.getState().joints)) {
      if (!joints[name]) setJoint(name, 0);
    }
  }, [joints, setJoint]);
  useEffect(() => {
    const poses: Record<string, Matrix4> = {};
    for (const [name, u] of Object.entries(jointPoses)) {
      const joint = joints[name];
      if (joint) poses[name] = jointMatrix(joint, u);
    }
    sceneRef.current?.poseJoints(poses);
  }, [joints, jointPoses, ready]);
  // Где проверка хода нашла задевание — туда ставит кнопка «к худшему».
  const worstOf = (joint: JointDTO): number | null => {
    const result = checkRun?.results.find((r) => r.id === joint.check);
    if (!result || result.status !== "fail") return null;
    const pose = result.evidence?.find((e) => e.kind === "pose");
    const u = (pose?.data as { u?: unknown } | undefined)?.u;
    return typeof u === "number" && Number.isFinite(u) ? u : null;
  };

  const box = contentBox(parts);
  const axis = plane ? AXIS[plane] : null;
  const lo = axis && box ? box[axis.lo] : 0;
  const hi = axis && box ? box[axis.hi] : 100;
  // Детали не на своих местах: разнесены или звено шарнира сдвинуто.
  const displaced = moved({ spread, joints: jointPoses });
  const offHint = spread > 0 ? "при разнесённом виде" : "пока звено не в собранном положении";
  const orderHint =
    assembly && assembly.order.length > 0
      ? `порядок сборки: ${assembly.order.join(" → ")}`
      : "порядка сборки нет: разносит радиально, о последовательности не утверждая";

  return (
    <div ref={hostRef} className="viewport-host">
      <canvas ref={canvasRef} className="viewport-canvas" />

      {/* ------------------------------------------------ виды и вписывание */}
      <div className="vp-bar vp-top-left">
        <Menu
          label={
            <>
              Виды <ChevronDown size={14} />
            </>
          }
          buttonClass="vp-select"
          items={VIEWS.map((view) => ({
            label: view.label,
            keys: view.hint,
            onSelect: () => viewport.setView(view.key),
          }))}
        />
        <span className="vp-sep" />
        <button
          className="icon-button"
          data-action="home"
          title="Вернуть камеру (H): изометрия на всё, как при открытии"
          aria-label="Вернуть камеру"
          onClick={() => viewport.home()}
        >
          <House size={16} />
        </button>
        <button className="icon-button" title="Вписать всё (F)" aria-label="Вписать всё" onClick={() => viewport.fit()}>
          <Maximize size={16} />
        </button>
        <button
          className="icon-button"
          title="Вписать выделенное (Shift+F)"
          aria-label="Вписать выделенное"
          disabled={!selected}
          onClick={() => viewport.fit(selected)}
        >
          <Focus size={16} />
        </button>
        <button
          className="icon-button"
          title="Сетка и объём печати (G)"
          aria-label="Сетка"
          aria-pressed={grid}
          onClick={toggleGrid}
        >
          <Grid3x3 size={16} />
        </button>
        <span className="vp-sep" />
        <SimLauncher />
      </div>

      {/* --------------------------------------------------------- замеры */}
      <div className="vp-bar vp-tools viewport-tools" role="toolbar" aria-label="Инструменты замера">
        {TOOLS.map(({ key, label, hint, key2, Icon }) => (
          <Fragment key={key}>
            {/* Замеры — отдельно от правки положения: первые ничего не меняют. */}
            {key === "move" && <span className="vp-sep-h" />}
            <button
              className="icon-button tool-button"
              data-tool={key}
              title={displaced && key !== "none"
                ? `${offHint} замеры и перенос выключены`
                : EDITS.includes(key) && !selected
                  ? `${label} — сначала выделите деталь (${key2})`
                  : `${label} — ${hint} (${key2})`}
              aria-label={label}
              aria-pressed={tool === key}
              disabled={displaced && key !== "none"}
              onClick={() => setTool(key)}
            >
              <Icon size={18} />
            </button>
          </Fragment>
        ))}
        {readout && (
          <>
            <span className="vp-sep-h" />
            <button
              className="icon-button tool-button"
              data-action="clear"
              title="Убрать замеры"
              aria-label="Сбросить замеры"
              onClick={() => {
                sceneRef.current?.clearMeasurements();
                setReadout(null);
              }}
            >
              <Eraser size={18} />
            </button>
          </>
        )}
      </div>

      {ready && <ViewCube subscribe={subscribe} />}
      {ready && (
        <div className="vp-gizmo">
          <AxisGizmo subscribe={subscribe} />
        </div>
      )}

      {readout && (
        <div className="readout">
          <div className="readout-kind">{readout.tool === "probe" ? "Толщина стенки" : "Штангенциркуль"}</div>
          <div className="readout-value">{readout.text}</div>
          <div className="readout-detail">{readout.detail}</div>
          {Object.values(parts).some((p) => p.draft) && (
            // Черновая сетка грубее точной: число — для глаза, не для посадки.
            <div className="readout-detail warn">по черновику — точное число после обычной сборки</div>
          )}
        </div>
      )}

      {/* ------------------------------------------------ срез и разнесение */}
      <div className="vp-bar vp-deck">
        <div className="deck-group" role="group" aria-label="Срез">
          <span className="deck-label">
            <SquareSplitVertical size={15} /> Срез
          </span>
          <div className="segmented">
            {PLANES.map((item) => (
              <button
                key={item.key}
                data-plane={item.key}
                title={displaced ? `${offHint} срез выключен` : item.hint}
                aria-pressed={plane === item.key}
                disabled={displaced}
                onClick={() => pickPlane(item.key)}
              >
                {item.label}
              </button>
            ))}
          </div>
          {plane && axis && (
            <>
              <input
                type="range"
                className="slider"
                aria-label={`положение среза по ${axis.name}`}
                min={lo}
                max={hi}
                step={(hi - lo) / 400 || 0.1}
                value={at}
                onChange={(e) => setAt(Number(e.target.value))}
              />
              <span className="deck-value">
                {axis.name} {at.toLocaleString("ru-RU", { maximumFractionDigits: 2 })} мм
              </span>
              <button
                className="icon-button small"
                title="Убрать срез"
                aria-label="Убрать срез"
                onClick={() => pickPlane(null)}
              >
                <X size={14} />
              </button>
              {sectionProblem && (
                // Отказ сервера прежде глотался: срез оставался без крышек молча.
                <span className="deck-problem" role="status" title={sectionProblem}>
                  крышки среза не посчитаны
                </span>
              )}
            </>
          )}
        </div>

        <span className="vp-sep" />

        <div className="deck-group" role="group" aria-label="Разнесение">
          <span className="deck-label" title={orderHint}>
            <Boxes size={15} /> Разнести
          </span>
          <input
            type="range"
            className="slider"
            min={0}
            max={1}
            step={0.02}
            value={spread}
            onChange={(e) => setSpread(Number(e.target.value))}
            aria-label="разнесение деталей"
            title={`0 — собрано. ${orderHint}`}
          />
          {spread > 0 && (
            <button className="chip-button" data-action="assemble" title="Вернуть детали на места" onClick={() => setSpread(0)}>
              {/* НЕ «Собрать»: этим словом в панели деталей названа пересборка
                  геометрии, и два разных смысла на одном экране путали бы. */}
              В сборку
            </button>
          )}
        </div>

        {/* Ход шарниров из assembly.yaml — вид, а не правка: в проект не пишется. */}
        {Object.entries(joints).map(([name, joint]) => {
          const u = jointPoses[name] ?? 0;
          const [jlo, jhi] = sliderRange(joint);
          const worst = worstOf(joint);
          const what = joint.kind === "revolute" ? "поворот" : "сдвиг";
          return (
            <Fragment key={name}>
              <span className="vp-sep" />
              <div className="deck-group" role="group" aria-label={`Шарнир ${name}`} data-joint={name}>
                <span className="deck-label" title={`${what} по объявлению в assembly.yaml; проверка — ${joint.check}`}>
                  <DoorOpen size={15} /> {name}
                </span>
                <input
                  type="range"
                  className="slider"
                  min={jlo}
                  max={jhi}
                  step={jointStep(joint)}
                  value={u}
                  onChange={(e) => setJoint(name, clampJoint(joint, Number(e.target.value)))}
                  aria-label={`ход шарнира ${name}`}
                  title={`${jointLabel(joint, joint.range[0])} … ${jointLabel(joint, joint.range[1])}; 0 — собранное положение`}
                />
                <span className="deck-value">{jointLabel(joint, u)}</span>
                {worst !== null && (
                  <button
                    className="chip-button"
                    data-action="joint-worst"
                    title={`Проверка ${joint.check} нашла задевание: показать звено там, где хуже всего`}
                    onClick={() => setJoint(name, clampJoint(joint, worst))}
                  >
                    К задеванию
                  </button>
                )}
                {u !== 0 && (
                  <button
                    className="icon-button small"
                    data-action="joint-rest"
                    title="Вернуть звено в собранное положение"
                    aria-label={`Вернуть ${name} на место`}
                    onClick={() => setJoint(name, 0)}
                  >
                    <X size={14} />
                  </button>
                )}
              </div>
            </Fragment>
          );
        })}
      </div>

      <SimPlayer />

      <div className="vp-bar vp-bottom-right">
        <button
          className="icon-button"
          title={focused ? "Вернуть панели (Shift+Space)" : "Только модель — убрать панели (Shift+Space)"}
          aria-label={focused ? "Вернуть панели" : "Только модель"}
          aria-pressed={focused}
          onClick={toggleFocus}
        >
          {focused ? <Minimize2 size={16} /> : <Maximize2 size={16} />}
        </button>
      </div>
    </div>
  );
}
