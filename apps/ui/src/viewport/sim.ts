/**
 * Проигрыватель симуляции — чистая логика без three и React (её берут тесты).
 *
 * Сервер (`sim_run`) кладёт запись прогона JSON-объектом: сетки звеньев в
 * НУЛЕВОЙ позе (координаты сцены, мм) и положения звеньев по кадрам —
 * [x, y, z, qw, qx, qy, qz]. Рамка звена в нулевой позе совпадает со сценой,
 * поэтому сетку достаточно поставить по положению кадра — без пересчёта.
 *
 * Между кадрами — линейно для положения и сферически для поворота (slerp):
 * кадров до 400 на прогон, и при паузе на ползунке звено не должно прыгать.
 */

export interface SimLink {
  name: string;
  url: string;
  color: string;
}

export interface SimVerdict {
  id: string;
  status: string;
  message: string;
}

export interface SimPlayback {
  mechanism: string;
  scenario: string;
  links: SimLink[];
  t: number[];
  /** кадр → звено → [x, y, z, qw, qx, qy, qz] */
  poses: number[][][];
  joints: string[];
  q: number[][];
  series: {
    t: number[];
    required: number[][];
    applied: number[][] | null;
    limits: (number | null)[];
    units: string[];
    /** Суставы рядов моментов — независимые (у ведомого своего привода нет). */
    joints: string[];
  };
  verdict: SimVerdict[];
  /** Загрузка звеньев по кадрам (0 — свободно, 1 — на пределе): кадр → звено. */
  load: number[][] | null;
  /** МКЭ печатных звеньев в худший момент (пусто — не считали). */
  stress: SimStress[];
}

/** Поле напряжений звена в худший момент: сетка с цветами вершин в системе звена. */
export interface SimStress {
  link: string;
  /** Худший момент, с. */
  t: number;
  url: string;
  /** Сравниваемое с пределом напряжение, МПа, и что это (пик или 99-й процентиль). */
  valueMpa: number;
  basis: string;
  peakMpa: number;
  /** Предел между слоями, МПа (null — материала нет, шкала до пика). */
  strengthMpa: number | null;
  /** Доля предела (null — не с чем сравнить). */
  u: number | null;
}

/** Объявление о готовом прогоне (событие `sim.result`). */
export interface SimNotice {
  url: string;
  mechanism: string;
  scenario: string;
  ok: boolean;
}

const isNum = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

/** Запись прогона — или null, если её форма не та (чужое не показываем). */
export function parsePlayback(raw: unknown): SimPlayback | null {
  if (!raw || typeof raw !== "object") return null;
  const r = raw as Record<string, unknown>;
  const links = r.links;
  const t = r.t;
  const poses = r.poses;
  if (!Array.isArray(links) || !Array.isArray(t) || !Array.isArray(poses)) return null;
  if (t.length === 0 || t.length !== poses.length || !t.every(isNum)) return null;
  for (let i = 1; i < t.length; i++) if (!(t[i] >= t[i - 1])) return null;
  const okLinks = links.every(
    (l) => l && typeof l.name === "string" && typeof l.url === "string" && l.url.startsWith("/obj/"),
  );
  if (!okLinks) return null;
  const okPoses = poses.every(
    (frame) =>
      Array.isArray(frame) &&
      frame.length === links.length &&
      frame.every((p: unknown) => Array.isArray(p) && p.length === 7 && p.every(isNum)),
  );
  if (!okPoses) return null;
  const s = (r.series ?? {}) as Record<string, unknown>;
  return {
    mechanism: String(r.mechanism ?? ""),
    scenario: String(r.scenario ?? ""),
    links: links.map((l) => ({ name: String(l.name), url: String(l.url), color: String(l.color ?? "#9aa4b2") })),
    t: t as number[],
    poses: poses as number[][][],
    joints: Array.isArray(r.joints) ? (r.joints as unknown[]).map(String) : [],
    q: Array.isArray(r.q) ? (r.q as number[][]) : [],
    series: {
      t: Array.isArray(s.t) ? (s.t as number[]) : [],
      required: Array.isArray(s.required) ? (s.required as number[][]) : [],
      applied: Array.isArray(s.applied) ? (s.applied as number[][]) : null,
      limits: Array.isArray(s.limits) ? (s.limits as (number | null)[]) : [],
      units: Array.isArray(s.units) ? (s.units as unknown[]).map(String) : [],
      joints: Array.isArray(s.joints) ? (s.joints as unknown[]).map(String) : [],
    },
    load: parseLoad(r.load, links.length, t.length),
    stress: parseStress(r.stress, links.map((l) => String(l.name))),
    verdict: Array.isArray(r.verdict)
      ? (r.verdict as Record<string, unknown>[]).map((v) => ({
          id: String(v.id ?? ""),
          status: String(v.status ?? ""),
          message: String(v.message ?? ""),
        }))
      : [],
  };
}

/** Что сервер сказал о звеньях, которые НЕ посчитались (пропуск, отказ), — одной строкой;
 * всё посчиталось — null. Первая строка каждого сообщения: подробности — в отчёте. */
export function stressProblems(checks: unknown): string | null {
  if (!Array.isArray(checks)) return null;
  const bad = (checks as { status?: unknown; message?: unknown }[])
    .filter((c) => c && (c.status === "skip" || c.status === "error"))
    .map((c) => String(c.message ?? "").split("\n")[0])
    .filter(Boolean);
  return bad.length ? bad.join("; ") : null;
}

/** Записи МКЭ: только своих звеньев и только с адресом сетки на сервере. */
export function parseStress(raw: unknown, links: string[]): SimStress[] {
  if (!Array.isArray(raw)) return [];
  const out: SimStress[] = [];
  for (const v of raw as Record<string, unknown>[]) {
    if (!v || typeof v !== "object") continue;
    const url = v.url;
    if (typeof v.link !== "string" || !links.includes(v.link)) continue;
    if (typeof url !== "string" || !url.startsWith("/obj/")) continue;
    if (!isNum(v.t) || !isNum(v.value_mpa) || !isNum(v.peak_mpa)) continue;
    out.push({
      link: v.link,
      t: v.t,
      url,
      valueMpa: v.value_mpa,
      basis: String(v.basis ?? ""),
      peakMpa: v.peak_mpa,
      strengthMpa: isNum(v.strength_mpa) ? v.strength_mpa : null,
      u: isNum(v.u) ? v.u : null,
    });
  }
  return out;
}

/** Индекс отрезка [i, i+1], содержащего t, и доля внутри него. */
export function segment(times: number[], t: number): { i: number; f: number } {
  const n = times.length;
  if (n === 0) return { i: 0, f: 0 };
  if (t <= times[0]) return { i: 0, f: 0 };
  if (t >= times[n - 1]) return { i: n - 1, f: 0 };
  let lo = 0;
  let hi = n - 1;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (times[mid] <= t) lo = mid;
    else hi = mid;
  }
  const span = times[hi] - times[lo];
  return { i: lo, f: span > 0 ? (t - times[lo]) / span : 0 };
}

/** Сферическая интерполяция кватернионов (w, x, y, z), кратчайшим путём. */
export function slerp(a: number[], b: number[], f: number): number[] {
  let dot = a[0] * b[0] + a[1] * b[1] + a[2] * b[2] + a[3] * b[3];
  const bb = dot < 0 ? b.map((v) => -v) : b;
  dot = Math.abs(dot);
  if (dot > 0.9995) {
    const q = a.map((v, k) => v + (bb[k] - v) * f);
    const n = Math.hypot(...q);
    return q.map((v) => v / n);
  }
  const th = Math.acos(Math.min(dot, 1));
  const s = Math.sin(th);
  const wa = Math.sin((1 - f) * th) / s;
  const wb = Math.sin(f * th) / s;
  return a.map((v, k) => v * wa + bb[k] * wb);
}

/** Положения всех звеньев в момент t: звено → [x, y, z, qw, qx, qy, qz]. */
export function posesAt(p: SimPlayback, t: number): number[][] {
  const { i, f } = segment(p.t, t);
  const a = p.poses[i];
  if (f === 0 || i + 1 >= p.poses.length) return a;
  const b = p.poses[i + 1];
  return a.map((pa, k) => {
    const pb = b[k];
    const pos = [0, 1, 2].map((c) => pa[c] + (pb[c] - pa[c]) * f);
    return [...pos, ...slerp(pa.slice(3), pb.slice(3), f)];
  });
}

/** Значение ряда в момент t (линейно). */
export function valueAt(times: number[], values: number[], t: number): number {
  const { i, f } = segment(times, t);
  if (f === 0 || i + 1 >= values.length) return values[i] ?? 0;
  return values[i] + (values[i + 1] - values[i]) * f;
}

export function duration(p: SimPlayback): number {
  return p.t.length ? p.t[p.t.length - 1] : 0;
}

function parseLoad(raw: unknown, nLinks: number, nFrames: number): number[][] | null {
  if (!raw || typeof raw !== "object") return null;
  const u = (raw as Record<string, unknown>).u;
  if (!Array.isArray(u) || u.length !== nFrames) return null;
  const ok = u.every((row) => Array.isArray(row) && row.length === nLinks && row.every(isNum));
  return ok ? (u as number[][]) : null;
}

/** Загрузка каждого звена в момент t (линейно между кадрами). */
export function loadAt(p: SimPlayback, t: number): number[] | null {
  if (!p.load) return null;
  const { i, f } = segment(p.t, t);
  const a = p.load[i];
  if (f === 0 || i + 1 >= p.load.length) return a;
  const b = p.load[i + 1];
  return a.map((v, k) => v + (b[k] - v) * f);
}

/**
 * Цвет карты загрузки: зелёный — свободно, жёлтый — половина предела, оранжевый
 * — 80 %, красный — предел и выше. Ступени, а не радуга: «на пределе» должно
 * читаться сразу.
 */
const HEAT: [number, [number, number, number]][] = [
  [0.0, [47, 214, 138]],
  [0.5, [255, 212, 71]],
  [0.8, [255, 157, 60]],
  [1.0, [255, 59, 78]],
];

export function heatColor(u: number): [number, number, number] {
  const v = Number.isFinite(u) ? Math.max(0, u) : 1;
  if (v >= 1) return HEAT[HEAT.length - 1][1];
  for (let k = 1; k < HEAT.length; k++) {
    const [u1, c1] = HEAT[k];
    const [u0, c0] = HEAT[k - 1];
    if (v <= u1) {
      const f = (v - u0) / (u1 - u0);
      return [0, 1, 2].map((c) => Math.round(c0[c] + (c1[c] - c0[c]) * f)) as [number, number, number];
    }
  }
  return HEAT[HEAT.length - 1][1];
}
