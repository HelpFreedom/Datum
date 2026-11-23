/**
 * Сцена: детали проекта, их видимость и порядок сборки.
 *
 * Скрытие детали — вид, а не правка: сервер о нём не знает, проверки идут по
 * всем деталям. Поэтому глаз живёт в сторе окна, а не в документе.
 */
import { Box, ChevronDown, ChevronRight, Download, Eye, EyeOff, Hammer, Search } from "lucide-react";
import { useState } from "react";

import { useDoc } from "../store/doc";
import { useUi } from "../store/ui";
import { GhostList } from "./Ghosts";
import { Trash } from "./Trash";

const mm3 = (v: number | undefined) =>
  v === undefined ? "" : `${v.toLocaleString("ru-RU", { maximumFractionDigits: 1 })} мм³`;

/** Как деталь встаёт на место — теми словами, что в assembly.yaml. */
function stepText(step: { method: string; insert: string[] } | undefined): string {
  if (!step) return "основание";
  const method = step.method === "snap" ? "защёлкой" : step.method === "rigid" ? "вдвигается" : step.method;
  return step.insert.length ? `${method}, вставка ${step.insert.join(" / ")}` : method;
}

export function Parts() {
  const parts = useDoc((s) => s.parts);
  const build = useDoc((s) => s.build);
  const selected = useDoc((s) => s.selected);
  const building = useDoc((s) => s.building);
  const problem = useDoc((s) => s.problems.build);
  const assembly = useDoc((s) => s.info?.assembly);
  const declared = useDoc((s) => s.info?.parts.length ?? -1);
  const exporting = useDoc((s) => s.exporting);
  const exported = useDoc((s) => s.exported);
  const hidden = useUi((s) => s.hidden);
  const { select, rebuild, exportParts } = useDoc.getState();
  const { toggleHidden } = useUi.getState();
  const [query, setQuery] = useState("");
  const [orderOpen, setOrderOpen] = useState(true);

  const all = Object.keys(build).length
    ? Object.keys(build).sort((a, b) => a.localeCompare(b, "ru"))
    : Object.keys(parts).sort((a, b) => a.localeCompare(b, "ru"));
  const needle = query.trim().toLocaleLowerCase("ru");
  const names = needle ? all.filter((n) => n.toLocaleLowerCase("ru").includes(needle)) : all;
  // Детали вне порядка сборки на собираемость НЕ проверялись. Сервер это
  // сообщал, а окно выбрасывало.
  const order = assembly?.order ?? [];
  const unlisted = order.length > 0 ? assembly?.unlisted ?? [] : [];

  return (
    <>
      <div className="panel-head">
        <h2>
          Сцена <span className="count">{all.length}</span>
        </h2>
      </div>
      <div className="scroll pad">
        <label className="search">
          <Search size={15} />
          <input
            className="field bare"
            placeholder="Поиск деталей…"
            aria-label="Поиск деталей"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </label>

        <div className="button-row">
          <button onClick={() => void rebuild()} disabled={building} title="Пересобрать детали (B)">
            <Hammer size={15} />
            {building ? "Собираю…" : "Собрать"}
          </button>
          <button
            onClick={() => void exportParts()}
            disabled={exporting || building}
            title="3MF и STL в output/, в позе печати; с вердиктом проверок"
          >
            <Download size={15} />
            {exporting ? "Выгружаю…" : "Выгрузить"}
          </button>
        </div>

        {problem && <div className="alert">{problem}</div>}

        <div className="part-list">
          {all.length === 0 && (
            // В проекте нет ни одной детали (новый, пустой) — «Собрать» тут
            // ничего бы не дал; есть детали, но не собраны — дал бы.
            <div className="empty">
              {declared === 0 ? (
                <>
                  В проекте пока нет деталей.
                  <br />
                  Импортируйте модель (Файл) или попросите агента.
                </>
              ) : building ? (
                // Сборка идёт — не «нажмите „Собрать“»: кнопка и так занята.
                <>
                  Собираю детали…
                  <br />
                  Каждая появится здесь и на столе, как только будет готова.
                </>
              ) : (
                <>
                  Пока ничего не собрано.
                  <br />
                  Нажмите «Собрать».
                </>
              )}
            </div>
          )}
          {all.length > 0 && names.length === 0 && <div className="empty">Ничего не нашлось.</div>}
          {names.map((name) => {
            const entry = build[name];
            const view = parts[name];
            const failed = entry && !entry.ok;
            const off = hidden.includes(name);
            return (
              <div
                key={name}
                className={`part ${failed ? "failed" : ""} ${off ? "off" : ""}`}
                aria-selected={selected === name}
              >
                <button
                  className="part-main"
                  aria-pressed={selected === name}
                  onClick={() => select(selected === name ? null : name)}
                >
                  <span className="part-icon">
                    <Box size={16} />
                  </span>
                  <span className="part-text">
                    <span className="name">{name}</span>
                    <span className="sub">
                      {failed
                        ? entry.error?.message ?? "сборка не удалась"
                        : [
                            mm3(entry?.topology?.volume ?? view?.volumeMm3),
                            view ? `${view.tris.toLocaleString("ru-RU")} треуг.` : "",
                            entry?.cached ? "из кеша" : entry ? `${entry.ms.toFixed(0)} мс` : "",
                          ]
                            .filter(Boolean)
                            .join(" · ")}
                    </span>
                  </span>
                </button>
                <button
                  className="part-eye"
                  title={off ? "Показать" : "Спрятать (проверки идут по всем деталям)"}
                  aria-label={off ? `Показать ${name}` : `Спрятать ${name}`}
                  aria-pressed={!off}
                  onClick={() => toggleHidden(name)}
                >
                  {off ? <EyeOff size={15} /> : <Eye size={15} />}
                </button>
              </div>
            );
          })}
        </div>

        <GhostList />
        <Trash />

        {exported && (
          <div className={`export-result ${exported.ready ? "good" : "warn"}`}>
            {/* Выгружается всегда, но «не готов» говорится первым — как в отчёте. */}
            <div className="export-title">
              {exported.sliced
                ? exported.ready ? "Нарезано, проект готов к печати" : "Нарезано, но не готов к печати:"
                : exported.ready ? "Выгружено, проект готов к печати" : "Выгружено, но не готов к печати:"}
            </div>
            {exported.warnings.map((w) => (
              <div key={w} className="sub warn">
                · {w}
              </div>
            ))}
            {Object.entries(exported.parts).map(([name, p]) => (
              <div key={name} className="sub dim">
                {name}: {p.files.join(", ")}
                {exported.sliced
                  ? ` · ${p.time ?? "?"}, ${p.grams?.toFixed(1) ?? "?"} г`
                  : p.print_up ? ` · ${p.print_up} вверх` : " · поза печати не объявлена"}
              </div>
            ))}
          </div>
        )}

        {order.length > 0 && (
          <div className="section">
            <button className="section-head" aria-expanded={orderOpen} onClick={() => setOrderOpen(!orderOpen)}>
              <span>Порядок сборки</span>
              {orderOpen ? <ChevronDown size={15} /> : <ChevronRight size={15} />}
            </button>
            {orderOpen && (
              <ol className="order">
                {order.map((name, i) => (
                  <li key={name}>
                    <span className="order-index">{i + 1}</span>
                    <span className="order-text">
                      <span className="name">{name}</span>
                      <span className="sub">{stepText(assembly?.steps[name])}</span>
                    </span>
                  </li>
                ))}
              </ol>
            )}
          </div>
        )}

        {unlisted.length > 0 && (
          <div className="sub warn" style={{ marginTop: 10 }}>
            Нет в порядке сборки, собираемость не проверялась: {unlisted.join(", ")}
          </div>
        )}
      </div>
    </>
  );
}
