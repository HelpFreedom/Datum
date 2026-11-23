/**
 * Состояние документа в окне.
 *
 * Правило одно: здесь НЕТ ничего, чего не прислал сервер. Окно не пересчитывает
 * объёмы, не решает, прошла ли проверка, и не догадывается о геометрии — оно
 * показывает то, что посчитано на канонической геометрии. Иначе на экране и в
 * проверках оказались бы разные числа, а именно этого Datum и не должен
 * допускать.
 *
 * Три правила, выученные на ошибках.
 *
 * ОТВЕТ МОЖЕТ ОПОЗДАТЬ. Выбрал снимок A, тут же B — ответ на A пришёл последним
 * и затёр B. Поэтому у каждой загрузки свой счётчик, и ответ старше последнего
 * запроса выбрасывается.
 *
 * ПРАВКА МОЖЕТ ПРИЙТИ ВО ВРЕМЯ РАБОТЫ. Сборка идёт, а агент уже поправил
 * параметр. Раньше вторая сборка молча отбрасывалась («уже собираю»), и окно
 * показывало геометрию до правки. Теперь запрос запоминается и выполняется
 * сразу после текущей.
 *
 * ОШИБКА — ПРО СВОЮ ОБЛАСТЬ. Одно общее поле `problem` показывалось только под
 * кнопкой «Собрать»: провал снимка или сравнения версий был не виден там, где
 * его ждали.
 */
import { create } from "zustand";

import { listJobs, runOp, type JobRecord } from "../transport/client";
import { chooseArchivePath } from "../transport/endpoint";
import type { DatumEvent } from "../transport/ws";
import type { JointDTO } from "../viewport/joints";
import type { SimNotice } from "../viewport/sim";
import { useSim } from "./sim";
import { applyShow, parseShow } from "../viewport/show";
import { useUi } from "./ui";

/** Объявление о прогоне симуляции из события — только своя ссылка `/obj/…json`. */
export function parseNotice(event: Record<string, unknown>): SimNotice | null {
  const url = String(event.url ?? "");
  if (!/^\/obj\/[0-9a-f]{64}\.json$/.test(url)) return null;
  return {
    url,
    mechanism: String(event.mechanism ?? ""),
    scenario: String(event.scenario ?? ""),
    ok: Boolean(event.ok),
  };
}

export interface PartView {
  name: string;
  digest: string;
  url: string;
  bytes: number;
  tris: number;
  verts: number;
  /** [x0, y0, z0, x1, y1, z1] в миллиметрах. */
  bbox: number[];
  volumeMm3?: number;
  bodies?: number;
  /** Собрана черновиком (`draft()`): для глаза, не для чисел. */
  draft?: boolean;
}

/** Положение детали на столе (placement.yaml) и ось её поворота. */
export interface PlacementInfo {
  move: number[];
  rotate: number[];
  centre: number[];
}

/** Что унесло удаление: файл, детали в нём, модель и проверки. */
export interface Deleted {
  part: string;
  file: string;
  parts: string[];
  /** Модели imported/, которые брала только эта деталь. */
  models: string[];
  checks: string[];
  /** Проверки не вырезались из checks.yaml (файл устроен неоднозначно). */
  checks_kept: boolean;
  trash: string;
}

/** Правка, которую отменяет Ctrl+Z. */
export type UndoEntry =
  | { kind: "place"; part: string; before: { move: number[]; rotate: number[] } }
  | { kind: "delete"; part: string; trash: string };

export interface PartBuildEntry {
  name: string;
  ok: boolean;
  placement?: PlacementInfo;
  ms: number;
  cached: boolean;
  depends_on: string[];
  topology?: { volume: number; bodies: number; triangles: number; genus: number };
  error?: { reason: string; message: string };
  /** false — покупное или показное (`@part(printed=False)`): в проверках, не в выгрузке. */
  printed?: boolean;
}

interface BuildResponse {
  ok: boolean;
  parts: Record<string, PartBuildEntry>;
  report: string;
  error?: string;
}

export interface CheckResult {
  id: string;
  title: string;
  group: string;
  status: "pass" | "fail" | "warn" | "skip" | "error";
  message: string;
  value: number | null;
  unit: string;
  op: string;
  threshold: number | [number | null, number | null] | null;
  margin: number | null;
  depends_on: string[];
  inputs: string[];
  severity: "blocking" | "warn" | "info";
  duration_ms: number;
  /** У «pose» — данные положения звена: `{part, u}`. */
  evidence?: { kind: string; caption: string; url: string; data?: unknown }[];
}

export interface CheckRun {
  ok: boolean;
  /** Проверок нет вовсе — это не «OK». */
  empty?: boolean;
  n_pass: number;
  n_fail: number;
  n_warn: number;
  results: CheckResult[];
  /** Параметры, которых не читает ни деталь, ни проверка; null — судить нельзя. */
  unread_params?: string[] | null;
  report: string;
  error?: string;
}

export interface Param {
  provenance: "measured" | "assumed" | "derived" | "standard" | "chosen";
  unit?: string;
  value?: number;
  /** Вычисленное значение производного — в единицах параметра. */
  resolved?: number;
  expr?: string;
  reason?: string;
  ref?: string;
  source?: string;
  instrument?: string;
  note?: string;
  /** Чем и как мерить — для угаданного, которое стоит измерить. */
  measure_hint?: string;
  validate_with?: string;
  tolerance?: number;
  tags?: string[];
  range?: [number, number];
  blocking?: boolean;
  conflicts?: { value: number; source: string; date?: string }[];
}

export interface ParamList {
  summary: {
    total: number;
    unknowns: string[];
    blocking_unknowns: string[];
    conflicted: string[];
    print_ready: boolean;
  };
  params: Record<string, Param>;
  error?: string;
}

export interface SnapshotInfo {
  file: string;
  tag: string;
  created: string;
  print_ready: boolean;
  checks_ok: boolean;
  n_fail: number;
  /** Снимок не читается — остальные при этом видны. */
  error?: string;
}

export interface SnapshotDiff {
  a: string;
  b: string;
  regression: boolean;
  params: {
    name: string;
    kind: "added" | "removed" | "changed" | "provenance";
    /** В единице параметра, как и `unit` рядом. */
    before?: number | null;
    after?: number | null;
    unit?: string;
    prov_before?: string;
    prov_after?: string;
  }[];
  parts: { name: string; kind: string; dv_mm3?: number; dv_frac?: number }[];
  checks: {
    id: string;
    transition: "fixed" | "broken" | "still_failing" | "new" | "removed" | "unchanged";
    before: string;
    after: string;
    before_value?: number | null;
    after_value?: number | null;
  }[];
  report: string;
  error?: string;
  /** Форма картинкой — только при `image: true` (`doc/geomdiff.py`). */
  image?: { url: string; path: string };
  geometry?: {
    volumes: Record<string, { added_mm3: number; removed_mm3: number }>;
    unchanged: string[];
    /** Формы этих деталей для одной из версий нет (снимок старше картинок). */
    missing: string[];
  };
}

export interface ImportedModel {
  name: string;
  source: string;
  imported: string;
  steps: string[];
  triangles: number;
  watertight: boolean;
  healthy: boolean;
}

/** Итог импорта на стол (`import_to_bed`): что встало деталями и где. */
export interface BedImport {
  file: string;
  parts: {
    part: string;
    file: string;
    centre: number[];
    bodies: number;
    sealed_voids: number;
    healthy: boolean;
    steps: string[];
    /** Что сделано при чтении, как у слайсера: вычтено, слито, пропущено. */
    notes: string[];
  }[];
  /** О файле целиком: например, пропущенные непечатаемые объекты. */
  file_notes: string[];
  fits: boolean;
  kept_file_layout: boolean;
  note: string;
  report: string;
}

/** Порядок сборки. Пустой `order` — `assembly.yaml` в проекте нет. */
export interface AssemblyInfoDTO {
  order: string[];
  derived_checks: number;
  overridden: number;
  /** Детали, не попавшие в порядок: на собираемость они НЕ проверялись. */
  unlisted: string[];
  explode_gap: number;
  steps: Record<string, { method: string; insert: string[]; distance: number }>;
  /** Шарниры (`joints:` в assembly.yaml) — окно проигрывает их ход. */
  joints?: Record<string, JointDTO>;
}

/** Где проект сохранён архивом .datum и есть ли несохранённое. */
export interface ArchiveState {
  path: string;
  name: string;
  saved: string;
  exists: boolean;
  dirty: boolean;
}

export interface ProjectInfo {
  name: string;
  archive?: ArchiveState | null;
  /** Отпечаток файлов проекта: изменились ли с последнего обновления окна. */
  stamp?: string;
  parts: string[];
  checks: number;
  assembly?: AssemblyInfoDTO;
  printer?: { bed: [number, number, number]; nozzle: number; material: string };
  params?: {
    total: number;
    unknowns: string[];
    blocking_unknowns: string[];
    print_ready: boolean;
  };
  error?: string;
}

/** Импортированная модель как образец на столе — вид и кто её берёт. */
export interface ImportedView {
  name: string;
  digest: string;
  url: string;
  bytes: number;
  tris: number;
  bbox: number[];
  /** Файлы проекта с `imported("имя")`. Непусто — модель уже стала деталью. */
  used_by: string[];
}

/** Итог выгрузки для слайсера — как его вернул сервер. */
export interface ExportResult {
  ready: boolean;
  warnings: string[];
  checks: string;
  parts: Record<string, { files: string[]; print_up?: string; time?: string; grams?: number }>;
  /** Нарезано слайсером (`slice`), а не просто выгружено (`export`). */
  sliced?: boolean;
}

/** Области, у каждой из которых своя ошибка. */
export type Area = "project" | "build" | "checks" | "params" | "versions" | "models" | "archive";

type Problems = Partial<Record<Area, string | null>>;

interface DocState {
  project: string;
  projectName: string;
  bed: [number, number, number];
  info: ProjectInfo | null;
  checks: CheckRun | null;
  params: ParamList | null;
  jobs: JobRecord[];
  snapshots: SnapshotInfo[];
  models: ImportedModel[];
  ghosts: ImportedView[];
  /** Последний импорт на стол — показывается в панели моделей. */
  lastImport: BedImport | null;
  diff: SnapshotDiff | null;
  connected: boolean;
  building: boolean;
  /**
   * Ход идущей сборки: сколько деталей готово и какая последняя. Приходит
   * событием по ходу операции — прежде о сборке было известно только «идёт».
   */
  buildProgress: { done: number; total: number; part: string } | null;
  checking: boolean;
  /** Идёт снимок версии или импорт — долгие правки проекта. */
  working: boolean;
  savingParam: string | null;
  /** Идёт выгрузка в output/. */
  exporting: boolean;
  exported: ExportResult | null;
  problems: Problems;
  parts: Record<string, PartView>;
  build: Record<string, PartBuildEntry>;
  report: string;
  selected: string | null;

  setConnected: (value: boolean) => void;
  setProject: (value: string) => void;
  setProblem: (area: Area, message: string | null) => void;
  loadInfo: () => Promise<void>;
  loadParams: () => Promise<void>;
  runChecks: () => Promise<void>;
  loadJobs: () => Promise<void>;
  loadSnapshots: () => Promise<void>;
  loadModels: () => Promise<void>;
  loadGhosts: () => Promise<void>;
  /** Сделать деталь из импортированной модели; вернёт имя детали или null. */
  makePart: (name: string) => Promise<string | null>;
  /**
   * Чужой файл — на стол деталями одним шагом (`import_to_bed`): каждый объект
   * файла — своя деталь. Вернёт имена новых деталей (пусто — не вышло).
   */
  importToBed: (path: string, level: string) => Promise<string[]>;
  loadDiff: (a?: string, b?: string) => Promise<void>;
  takeSnapshot: (tag: string) => Promise<void>;
  /** Выгрузить все детали в output/ той же операцией `export`, что у агента. */
  exportParts: () => Promise<void>;
  /** Нарезать консольным ElegooSlicer: проект 3MF и G-code в output/. */
  sliceParts: () => Promise<void>;
  /** Идёт сохранение архива. */
  saving: boolean;
  /**
   * Сохранить проект архивом .datum: туда же, где он сохранён, а если ещё
   * нигде или `asNew` — туда, куда человек укажет в диалоге ОС.
   */
  saveProject: (asNew: boolean) => Promise<void>;
  /** Отменяемые правки — последняя в конце. */
  history: UndoEntry[];
  /** Записать положение детали; true — сервер принял. */
  placePart: (part: string, move: number[], rotate: number[]) => Promise<boolean>;
  /** Удалить деталь из проекта (в корзину); вернёт, что унесено, или null. */
  deletePart: (part: string) => Promise<Deleted | null>;
  /** Отменить последнюю правку; вернёт, что отменено. */
  undo: () => Promise<string | null>;
  setParam: (name: string, value: number, source?: string) => Promise<boolean>;
  select: (name: string | null) => void;
  applyEvent: (event: DatumEvent) => void;
  rebuild: () => Promise<void>;
  /** Перечитать всё: сводку, параметры, геометрию и проверки. */
  refreshAll: () => Promise<void>;
}

const message = (e: unknown) => (e instanceof Error ? e.message : String(e));

// ---------------------------------------------------------------- свежесть
// Счётчик запросов по видам загрузки: ответ, пришедший после более нового
// запроса того же вида, выбрасывается.
const latest: Record<string, number> = {};
const ticket = (kind: string) => (latest[kind] = (latest[kind] ?? 0) + 1);
const isCurrent = (kind: string, t: number) => latest[kind] === t;

// Отложенные повторы: правка пришла, пока шла сборка или прогон.
let rebuildAgain = false;
let checksAgain = false;
let refreshing: Promise<void> | null = null;
let refreshAgain = false;
/** Отпечаток файлов проекта, на котором стоит показанное (`project_info.stamp`). */
let shownStamp = "";

/**
 * Обновить всё — только если файлы проекта правда изменились с показанного.
 *
 * Правка из окна обновляет окно сама, а следом сервер присылает `doc.changed`
 * про ту же правку; при открытии страницы `hello` с `resync` приходит рядом с
 * первым обновлением. Прежде каждый раз шло ВТОРОЕ полное обновление, то есть
 * второй прогон всех проверок — у МКЭ и хода это вдвое дольше (найдено
 * обзором). Отпечаток — дешёвый (stat, без чтения): тот же — обновлять нечего.
 */
async function refreshIfChanged(): Promise<void> {
  if (refreshing) await refreshing.catch(() => undefined);
  const before = shownStamp;
  const doc = useDoc.getState();
  await doc.loadInfo();
  const now = useDoc.getState().info?.stamp ?? "";
  if (!now || now !== before) await useDoc.getState().refreshAll();
}

// Правки файлов приходят пачками; перечитываем один раз на пачку.
let refreshTimer: ReturnType<typeof setTimeout> | null = null;
let jobsTimer: ReturnType<typeof setTimeout> | null = null;

export const useDoc = create<DocState>((set, get) => ({
  project: "",
  projectName: "",
  bed: [256, 256, 256],
  info: null,
  checks: null,
  params: null,
  jobs: [],
  snapshots: [],
  models: [],
  ghosts: [],
  lastImport: null,
  diff: null,
  connected: false,
  building: false,
  buildProgress: null,
  checking: false,
  working: false,
  exporting: false,
  exported: null,
  saving: false,
  history: [],
  savingParam: null,
  problems: {},
  parts: {},
  build: {},
  report: "",
  selected: null,

  setConnected: (value) => set({ connected: value }),
  setProject: (value) => set({ project: value }),
  setProblem: (area, text) =>
    set((state) => ({ problems: { ...state.problems, [area]: text } })),

  loadInfo: async () => {
    const t = ticket("info");
    try {
      const { result } = await runOp<ProjectInfo>("project_info", {
        project: get().project,
      });
      if (!isCurrent("info", t)) return;
      if (result.error) {
        // Проект не открылся: чаще всего синтаксическая ошибка в скрипте
        // детали. Раньше окно падало на этом в чёрный экран, потому что
        // ждало поля, которых в ответе с ошибкой нет.
        set({ info: null });
        get().setProblem("project", result.error);
        return;
      }
      set({
        info: result,
        projectName: result.name ?? "",
        bed: result.printer?.bed ?? [256, 256, 256],
      });
      get().setProblem("project", null);
    } catch (e) {
      if (isCurrent("info", t)) get().setProblem("project", message(e));
    }
  },

  select: (name) => set({ selected: name }),

  applyEvent: (event) => {
    if (event.t === "hello") {
      set({ project: String(event.project ?? get().project) });
      // Сервер не смог дослать пропущенное (кольцо событий короче разрыва):
      // достраивать состояние из дырявой ленты нельзя — перечитываем целиком.
      if (event.resync) {
        void refreshIfChanged();
        void get().loadJobs();
      }
      return;
    }
    if (event.t === "doc.changed") {
      // Файлы проекта правит агент или редактор. Правки идут пачками — ждём
      // затишья и перечитываем один раз.
      if (refreshTimer) clearTimeout(refreshTimer);
      refreshTimer = setTimeout(() => {
        refreshTimer = null;
        void refreshIfChanged();
      }, 250);
      return;
    }
    if (event.t.startsWith("job.")) {
      // Сборка кончилась как угодно (готово, отказ, отмена) — хода больше нет:
      // оборванная сборка агента иначе оставила бы «собрано 3 из 18…» навсегда.
      if (event.t !== "job.started" && event.op === "part_build" && get().buildProgress) {
        set({ buildProgress: null });
      }
      // Ленту перечитываем кратко и с затишьем: на операцию приходит три
      // события, и перечитывать её трижды с результатами было расточительно.
      if (jobsTimer) clearTimeout(jobsTimer);
      jobsTimer = setTimeout(() => {
        jobsTimer = null;
        void get().loadJobs();
      }, 150);
      return;
    }
    if (event.t === "sim.result") {
      // Прогон симуляции готов (`sim_run`): объявить, а не открыть самим —
      // человек может быть посреди замера.
      const notice = parseNotice(event);
      if (notice) useSim.getState().announce(notice);
      return;
    }
    if (event.t === "window.show") {
      // Показ агента — вид, а не документ: камера, выделение, срез, шарнир.
      const show = parseShow(event);
      if (show) applyShow(show);
      return;
    }
    if (event.t === "build.progress") {
      const done = Number(event.done ?? 0);
      const total = Number(event.total ?? 0);
      // Последняя деталь готова — хода больше нет: дальше скажет сама сборка.
      set({ buildProgress: total > 0 && done < total ? { done, total, part: String(event.part ?? "") } : null });
      return;
    }
    if (event.t === "geom.updated") {
      const part = String(event.part ?? "");
      if (!part) return;
      set((state) => ({
        parts: {
          ...state.parts,
          [part]: {
            name: part,
            digest: String(event.digest ?? ""),
            url: String(event.url ?? ""),
            bytes: Number(event.bytes ?? 0),
            tris: Number(event.tris ?? 0),
            verts: Number(event.verts ?? 0),
            bbox: (event.bbox as number[]) ?? [0, 0, 0, 0, 0, 0],
            volumeMm3: Number(event.volume_mm3 ?? 0),
            bodies: Number(event.bodies ?? 0),
            draft: Boolean(event.draft),
          },
        },
      }));
    }
  },

  loadParams: async () => {
    const t = ticket("params");
    try {
      const { result } = await runOp<ParamList>("params_list", {
        project: get().project,
      });
      if (!isCurrent("params", t)) return;
      if (result.error) {
        get().setProblem("params", result.error);
        return;
      }
      set({ params: result });
      get().setProblem("params", null);
    } catch (e) {
      if (isCurrent("params", t)) get().setProblem("params", message(e));
    }
  },

  setParam: async (name, value, source = "") => {
    set({ savingParam: name });
    try {
      const { result } = await runOp<{ error?: string }>("param_set", {
        project: get().project,
        name,
        value,
        ...(source.trim() ? { source: source.trim() } : {}),
      });
      if (result.error) {
        get().setProblem("params", result.error);
        return false;
      }
      get().setProblem("params", null);
      // Наблюдатель за файлами пришлёт doc.changed и так, но ждать его
      // незачем: мы знаем, что проект изменился.
      void get().refreshAll();
      return true;
    } catch (e) {
      get().setProblem("params", message(e));
      return false;
    } finally {
      set({ savingParam: null });
    }
  },

  loadJobs: async () => {
    const t = ticket("jobs");
    try {
      const jobs = await listJobs();
      if (isCurrent("jobs", t)) set({ jobs });
    } catch {
      /* лента не критична: молчим, чтобы не забивать экран */
    }
  },

  loadModels: async () => {
    const t = ticket("models");
    try {
      const { result } = await runOp<{ models: ImportedModel[]; error?: string }>(
        "imported_list",
        { project: get().project },
      );
      if (!isCurrent("models", t)) return;
      set({ models: result.models ?? [] });
      get().setProblem("models", result.error ?? null);
    } catch (e) {
      if (isCurrent("models", t)) get().setProblem("models", message(e));
    }
  },

  placePart: async (part, move, rotate) => {
    const before = get().build[part]?.placement;
    get().setProblem("build", null);
    try {
      const { result } = await runOp<{ error?: string }>("part_place", {
        project: get().project, part, move, rotate,
      });
      if (result.error) {
        get().setProblem("build", result.error);
        return false;
      }
      if (before) {
        const entry: UndoEntry = { kind: "place", part, before: { move: before.move, rotate: before.rotate } };
        set((state) => ({ history: [...state.history, entry].slice(-50) }));
      }
      // Наблюдатель пришлёт doc.changed и так; ждать его — лишняя секунда,
      // пока деталь стоит на экране сдвинутой, а в проверках — на старом месте.
      await get().rebuild();
      void get().runChecks();
      return true;
    } catch (e) {
      get().setProblem("build", message(e));
      return false;
    }
  },

  deletePart: async (part) => {
    get().setProblem("build", null);
    try {
      const { result } = await runOp<Partial<Deleted> & { error?: string }>("part_delete", {
        project: get().project, part,
      });
      if (result.error || !result.trash) {
        get().setProblem("build", result.error ?? "деталь не удалена");
        return null;
      }
      const entry: UndoEntry = { kind: "delete", part, trash: result.trash };
      // Со стола деталь уходит сразу, не дожидаясь пересборки проекта: та
      // бывает и в секунды, а удалённое всё это время стояло бы на месте.
      const gone = new Set(result.parts ?? [part]);
      const without = <T,>(map: Record<string, T>) =>
        Object.fromEntries(Object.entries(map).filter(([name]) => !gone.has(name)));
      set((state) => ({
        history: [...state.history, entry].slice(-50),
        selected: null,
        parts: without(state.parts),
        build: without(state.build),
      }));
      void get().refreshAll();
      return {
        part, trash: result.trash, file: result.file ?? "", parts: result.parts ?? [part],
        models: result.models ?? [], checks: result.checks ?? [],
        checks_kept: Boolean(result.checks_kept),
      };
    } catch (e) {
      get().setProblem("build", message(e));
      return null;
    }
  },

  undo: async () => {
    const entry = get().history.at(-1);
    if (!entry) return null;
    // Запись вынимается сразу — иначе быстрый двойной Ctrl+Z применил бы её
    // дважды; а при отказе возвращается: прежде одна неудачная отмена (сервер
    // занят) навсегда теряла это действие из истории (найдено обзором).
    set((state) => ({ history: state.history.slice(0, -1) }));
    let undone = false;                            // сервер уже отменил — не возвращать
    const putBack = () => {
      if (!undone) set((state) => ({ history: [...state.history, entry] }));
    };
    try {
      const { result } = entry.kind === "place"
        ? await runOp<{ error?: string }>("part_place", {
          project: get().project, part: entry.part, ...entry.before,
        })
        : await runOp<{ error?: string }>("part_restore", {
          project: get().project, trash: entry.trash,
        });
      if (result.error) {
        putBack();
        get().setProblem("build", result.error);
        return null;
      }
      undone = true;
      await get().refreshAll();
      if (entry.kind === "delete") set({ selected: entry.part });
      return entry.kind === "place" ? `положение «${entry.part}» возвращено`
        : `деталь «${entry.part}» возвращена`;
    } catch (e) {
      putBack();
      get().setProblem("build", message(e));
      return null;
    }
  },

  saveProject: async (asNew) => {
    if (get().saving) return;
    const known = get().info?.archive;
    let dest: string | null = null;
    if (asNew || !known) {
      dest = await chooseArchivePath();
      if (!dest) return;                             // передумал
    }
    set({ saving: true });
    get().setProblem("archive", null);
    try {
      // Перезапись согласована в диалоге ОС; «туда же» — перезапись своего архива.
      const { result } = await runOp<{ error?: string }>("project_save", {
        project: get().project,
        ...(dest ? { dest, overwrite: true } : {}),
      });
      if (result.error) get().setProblem("archive", result.error);
      await get().loadInfo();
    } catch (e) {
      get().setProblem("archive", message(e));
    } finally {
      set({ saving: false });
    }
  },

  loadGhosts: async () => {
    const t = ticket("ghosts");
    try {
      const { result } = await runOp<{ models?: ImportedView[]; error?: string }>(
        "imported_views", { project: get().project });
      if (!isCurrent("ghosts", t)) return;
      if (result.error) {
        get().setProblem("models", result.error);
        return;
      }
      set({ ghosts: result.models ?? [] });
    } catch (e) {
      get().setProblem("models", message(e));
    }
  },

  makePart: async (name) => {
    set({ working: true });
    get().setProblem("models", null);
    try {
      const { result } = await runOp<{ part?: string; error?: string }>("imported_to_part", {
        project: get().project,
        name,
      });
      if (result.error || !result.part) {
        get().setProblem("models", result.error ?? "деталь не создана");
        return null;
      }
      // Наблюдатель пришлёт doc.changed и так, но ждать его — это секунда
      // пустого экрана после нажатия. Перечитываем сразу.
      await get().refreshAll();
      set({ selected: result.part });
      return result.part;
    } catch (e) {
      get().setProblem("models", message(e));
      return null;
    } finally {
      set({ working: false });
    }
  },

  importToBed: async (path, level) => {
    set({ working: true });
    get().setProblem("models", null);
    try {
      const { result } = await runOp<Omit<BedImport, "file"> & { error?: string }>("import_to_bed", {
        project: get().project,
        path,
        repair_level: level,
      });
      if (result.error) {
        get().setProblem("models", result.error);
        return [];
      }
      set({ lastImport: { ...result, file: path.split("/").pop() ?? path } });
      // Наблюдатель пришлёт doc.changed и так, но ждать его — секунда пустого
      // стола после выбора файла. Перечитываем сразу.
      await get().refreshAll();
      await Promise.all([get().loadModels(), get().loadGhosts()]);
      const made = result.parts.map((p) => p.part);
      if (made.length) set({ selected: made[0] });
      return made;
    } catch (e) {
      get().setProblem("models", message(e));
      return [];
    } finally {
      set({ working: false });
    }
  },

  loadSnapshots: async () => {
    const t = ticket("snapshots");
    try {
      const { result } = await runOp<{ snapshots: SnapshotInfo[]; error?: string }>(
        "snapshot_list",
        { project: get().project },
      );
      if (!isCurrent("snapshots", t)) return;
      set({ snapshots: result.snapshots ?? [] });
      get().setProblem("versions", result.error ?? null);
    } catch (e) {
      if (isCurrent("snapshots", t)) get().setProblem("versions", message(e));
    }
  },

  loadDiff: async (a, b) => {
    const t = ticket("diff");
    if (get().snapshots.filter((s) => !s.error).length < 2) {
      set({ diff: null });
      return;
    }
    try {
      const { result } = await runOp<SnapshotDiff>("snapshot_diff", {
        project: get().project,
        ...(a ? { a } : {}),
        ...(b ? { b } : {}),
      });
      if (!isCurrent("diff", t)) return;
      set({ diff: result.error ? null : result });
      get().setProblem("versions", result.error ?? null);
    } catch (e) {
      if (isCurrent("diff", t)) get().setProblem("versions", message(e));
    }
  },

  exportParts: async () => {
    if (get().exporting) return;
    set({ exporting: true, exported: null });
    get().setProblem("build", null);
    try {
      const { result } = await runOp<ExportResult & { error?: string }>("export", {
        project: get().project,
      });
      if (result.error) get().setProblem("build", result.error);
      else set({ exported: result });
    } catch (e) {
      get().setProblem("build", message(e));
    } finally {
      set({ exporting: false });
    }
  },

  sliceParts: async () => {
    if (get().exporting) return;
    set({ exporting: true, exported: null });
    get().setProblem("build", null);
    try {
      const { result } = await runOp<ExportResult & { error?: string }>("slice", {
        project: get().project,
      });
      if (result.error) get().setProblem("build", result.error);
      else set({ exported: { ...result, sliced: true } });
    } catch (e) {
      get().setProblem("build", message(e));
    } finally {
      set({ exporting: false });
    }
  },

  takeSnapshot: async (tag) => {
    set({ working: true });
    get().setProblem("versions", null);
    try {
      const { result } = await runOp<{ error?: string }>("snapshot_create", {
        project: get().project,
        tag,
      });
      if (result.error) get().setProblem("versions", result.error);
      // Снимок лежит в проекте, но наблюдатель его не видит (он следит за
      // тем, что меняет документ): отметку «не сохранено» обновляем сами.
      await Promise.all([get().loadSnapshots(), get().loadInfo()]);
      await get().loadDiff();
    } catch (e) {
      get().setProblem("versions", message(e));
    } finally {
      set({ working: false });
    }
  },

  runChecks: async () => {
    if (get().checking) {
      checksAgain = true;
      return;
    }
    set({ checking: true });
    try {
      do {
        checksAgain = false;
        const { result } = await runOp<CheckRun>("check_run", { project: get().project });
        set({ checks: result.error ? null : result });
        get().setProblem("checks", result.error ?? null);
      } while (checksAgain);
    } catch (e) {
      get().setProblem("checks", message(e));
    } finally {
      set({ checking: false });
    }
  },

  rebuild: async () => {
    if (get().building) {
      rebuildAgain = true;
      return;
    }
    set({ building: true });
    try {
      do {
        rebuildAgain = false;
        const { result } = await runOp<BuildResponse>("part_build", {
          project: get().project,
          draft: useUi.getState().draft,
        });
        const built = result.parts ?? {};
        // Деталь, которой больше нет в проекте, или та, что не собралась,
        // не должна оставаться во вьюпорте со вчерашней геометрией: раньше
        // провалившаяся деталь показывалась целой, а удалённая жила до
        // перезапуска окна.
        set((state) => ({
          build: built,
          report: result.report ?? "",
          parts: result.error
            ? state.parts
            : Object.fromEntries(
                Object.entries(state.parts).filter(([name]) => built[name]?.ok),
              ),
        }));
        get().setProblem("build", result.error ?? null);
      } while (rebuildAgain);
    } catch (e) {
      get().setProblem("build", message(e));
    } finally {
      set({ building: false, buildProgress: null });
    }
  },

  refreshAll: async () => {
    // Одно обновление за раз; позвали во время идущего — ещё один круг после.
    if (refreshing) {
      refreshAgain = true;
      return refreshing;
    }
    refreshing = (async () => {
      try {
        do {
          refreshAgain = false;
          await Promise.all([get().loadInfo(), get().loadParams()]);
          shownStamp = get().info?.stamp ?? "";
          if (get().problems.project) break;     // не открылся — собирать нечего
          await get().rebuild();
          await Promise.all([get().runChecks(), get().loadJobs(), get().loadGhosts()]);
        } while (refreshAgain);
      } finally {
        refreshing = null;
      }
    })();
    return refreshing;
  },
}));
