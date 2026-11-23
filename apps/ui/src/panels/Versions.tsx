/**
 * Версии и сравнение — претензия №7 из технического задания.
 *
 * В рабочем процессе CAD версии форкались целыми файлами: build_v1.py …
 * build_v13.py, case3.py/case4.py, check3.py/check4.py. Ответить на вопрос
 * «что изменилось между v12 и v13» можно было только чтением дифов кода, а
 * вопрос-то не про код — он про то, какая проверка перестала проходить.
 *
 * Поэтому главное здесь не параметры и не объёмы, а ПЕРЕХОДЫ ПРОВЕРОК: что
 * починилось, что сломалось, что всё ещё падает. Сломавшееся показывается
 * первым и красным, даже если человек листает список ради другого.
 */
import { ArrowRight, Camera, Shapes } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import type { SnapshotDiff } from "../store/doc";
import { useDoc } from "../store/doc";
import { runOp } from "../transport/client";
import { amount } from "../units";
import { Evidence } from "./Evidence";

/** Форма картинкой: по кнопке, а не при открытии — лист большой детали
 *  рисуется секундами, а смотрят его не каждый раз. */
interface Shape {
  busy: boolean;
  error?: string;
  image?: SnapshotDiff["image"];
  geometry?: SnapshotDiff["geometry"];
}

const TRANSITION: Record<
  SnapshotDiff["checks"][number]["transition"],
  { label: string; tone: string; rank: number } | undefined
> = {
  broken: { label: "сломалось", tone: "bad", rank: 0 },
  still_failing: { label: "всё ещё падает", tone: "bad", rank: 1 },
  new: { label: "новая", tone: "warn", rank: 2 },
  fixed: { label: "починилось", tone: "good", rank: 3 },
  removed: { label: "убрана", tone: "dim", rank: 4 },
  unchanged: undefined,
};

const num = (v: number | null | undefined, unitName = "") => amount(v, unitName, 2);

export function Versions() {
  const snapshots = useDoc((s) => s.snapshots);
  const diff = useDoc((s) => s.diff);
  const busy = useDoc((s) => s.working);
  const problem = useDoc((s) => s.problems.versions);
  const { loadSnapshots, loadDiff, takeSnapshot } = useDoc.getState();
  const readable = snapshots.filter((s) => !s.error);
  const broken = snapshots.filter((s) => s.error);
  const [tag, setTag] = useState("");
  const [a, setA] = useState("");
  const [b, setB] = useState("");
  const [shape, setShape] = useState<Shape | null>(null);
  // Ответ на прежнюю пару версий не должен лечь поверх новой.
  const asked = useRef(0);

  useEffect(() => {
    void loadSnapshots();
  }, [loadSnapshots]);

  useEffect(() => {
    if (readable.length >= 2 && !diff) void loadDiff();
  }, [readable.length, diff, loadDiff]);

  const pick = (which: "a" | "b", value: string) => {
    const next = { a: which === "a" ? value : a, b: which === "b" ? value : b };
    if (which === "a") setA(value);
    else setB(value);
    asked.current += 1;
    setShape(null);
    void loadDiff(next.a || undefined, next.b || undefined);
  };

  const showShape = async () => {
    const ticket = ++asked.current;
    setShape({ busy: true });
    try {
      const { result } = await runOp<SnapshotDiff>("snapshot_diff", {
        project: useDoc.getState().project,
        ...(a ? { a } : {}),
        ...(b ? { b } : {}),
        image: true,
      });
      if (ticket !== asked.current) return;
      setShape({ busy: false, error: result.error, image: result.image, geometry: result.geometry });
    } catch (e) {
      if (ticket === asked.current) setShape({ busy: false, error: String(e) });
    }
  };

  const moved = (diff?.checks ?? [])
    .map((check) => ({ check, info: TRANSITION[check.transition] }))
    .filter((row) => row.info !== undefined)
    .sort((x, y) => x.info!.rank - y.info!.rank);

  const shoot = () => {
    if (busy || !tag.trim()) return;
    void takeSnapshot(tag.trim());
    setTag("");
  };

  return (
    <>
      <div className="panel-head">
        <h2>
          Версии <span className="count">{readable.length}</span>
        </h2>
      </div>
      <div className="scroll pad">
        <form
          className="row"
          onSubmit={(e) => {
            e.preventDefault();
            shoot();
          }}
        >
          <input
            className="field"
            placeholder="имя версии"
            aria-label="имя версии"
            value={tag}
            onChange={(e) => setTag(e.target.value)}
          />
          <button type="submit" disabled={busy || !tag.trim()} title="Заморозить параметры, метрики и прогон проверок">
            <Camera size={15} />
            Снять
          </button>
        </form>

        {problem && <div className="alert">{problem}</div>}
        {broken.map((s) => (
          <div key={s.file} className="sub bad">
            {s.file}: не читается — {s.error}
          </div>
        ))}

        {snapshots.length === 0 && (
          <div className="empty">
            Версий пока нет. Снимок замораживает параметры, метрики деталей и весь
            прогон проверок — по нему потом видно, что изменилось.
          </div>
        )}

        {readable.length > 0 && (
          <div className="snap-list">
            {[...readable].reverse().map((snap) => (
              <div key={snap.file} className="snap">
                <div className="snap-head">
                  <span className="name">{snap.tag}</span>
                  <span className="sub mono">{snap.created.replace("T", " ").slice(0, 16)}</span>
                </div>
                <div className="snap-pills">
                  <span className={`pill ${snap.print_ready ? "good" : "warn"}`}>
                    {snap.print_ready ? "к печати" : "не готов"}
                  </span>
                  <span className={`pill ${snap.checks_ok ? "good" : "bad"}`}>
                    {snap.checks_ok ? "проверки прошли" : `не прошло: ${snap.n_fail}`}
                  </span>
                </div>
              </div>
            ))}
          </div>
        )}

        {readable.length >= 2 && <h2 className="section-title">Сравнение</h2>}
        {readable.length >= 2 && (
          <div className="row compare">
            <select className="field" value={a} onChange={(e) => pick("a", e.target.value)}>
              <option value="">предпоследняя</option>
              {readable.map((s) => (
                <option key={s.file} value={s.file}>{s.tag}</option>
              ))}
            </select>
            <ArrowRight size={15} className="dim" />
            <select className="field" value={b} onChange={(e) => pick("b", e.target.value)}>
              <option value="">последняя</option>
              {readable.map((s) => (
                <option key={s.file} value={s.file}>{s.tag}</option>
              ))}
            </select>
          </div>
        )}

        {diff && (
          <>
            <div className={`verdict compact ${diff.regression ? "bad" : "good"}`}>
              <div>
                <div className="verdict-title">
                  {diff.a} → {diff.b}
                </div>
                <div className="verdict-sub">{diff.regression ? "есть регрессии" : "регрессий нет"}</div>
              </div>
            </div>

            {moved.length > 0 && (
              <>
                <h2 className="section-title">Проверки</h2>
                {moved.map(({ check, info }) => (
                  <div key={check.id} className="log-row">
                    <span className={`pill ${info!.tone}`}>{info!.label}</span>
                    <span className="log-op">{check.id}</span>
                    <span className="log-ms">
                      {num(check.before_value)} → {num(check.after_value)}
                    </span>
                  </div>
                ))}
              </>
            )}
            {moved.length === 0 && (
              <div className="sub">Ни одна проверка не изменила исхода.</div>
            )}

            {diff.params.length > 0 && (
              <>
                <h2 className="section-title">Параметры</h2>
                {diff.params.map((p) => (
                  <div key={p.name} className="log-row">
                    <span className="log-op">{p.name}</span>
                    <span className="log-ms">
                      {p.kind === "added"
                        ? `+ ${num(p.after, p.unit)}`
                        : p.kind === "removed"
                          ? `− было ${num(p.before, p.unit)}`
                          : p.kind === "provenance"
                            ? num(p.after, p.unit)
                            : `${num(p.before, p.unit)} → ${num(p.after, p.unit)}`}
                    </span>
                    {p.prov_before !== p.prov_after && (
                      <span className={p.prov_after === "measured" ? "good" : "warn"}>
                        {p.prov_before} → {p.prov_after}
                      </span>
                    )}
                  </div>
                ))}
              </>
            )}

            <h2 className="section-title">Форма</h2>
            {!shape?.image && (
              <button
                data-action="version-shape"
                disabled={shape?.busy}
                title="Нарисовать, что изменилось в форме: красное убралось, зелёное добавилось"
                onClick={() => void showShape()}
              >
                <Shapes size={15} />
                {shape?.busy ? "Рисую…" : "Показать форму"}
              </button>
            )}
            {shape?.error && <div className="alert">{shape.error}</div>}
            {shape?.geometry &&
              Object.entries(shape.geometry.volumes).map(([name, v]) => (
                <div key={name} className="log-row">
                  <span className="log-op">{name}</span>
                  <span className="log-ms">
                    <span className="good">+{num(v.added_mm3)}</span> /{" "}
                    <span className="bad">−{num(v.removed_mm3)}</span> мм³
                  </span>
                </div>
              ))}
            {shape?.geometry && shape.geometry.missing.length > 0 && (
              <div className="sub warn">
                Формы нет у версии, снятой до картинок: {shape.geometry.missing.join(", ")}
              </div>
            )}
            {shape?.image && (
              <Evidence
                url={shape.image.url}
                caption="было: красным — что убралось; стало: зелёным — что добавилось"
              />
            )}

            {diff.parts.some((p) => p.kind !== "unchanged") && (
              <>
                <h2 className="section-title">Детали</h2>
                {diff.parts
                  .filter((p) => p.kind !== "unchanged")
                  .map((p) => (
                    <div key={p.name} className="log-row">
                      <span className="log-op">{p.name}</span>
                      <span className="log-ms">
                        {p.dv_mm3 !== undefined
                          ? `${p.dv_mm3 > 0 ? "+" : ""}${num(p.dv_mm3)} мм³`
                          : p.kind}
                      </span>
                    </div>
                  ))}
              </>
            )}
          </>
        )}
      </div>
    </>
  );
}
