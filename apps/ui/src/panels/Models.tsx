/**
 * Чужие модели: на стол одним шагом, и что с ними сделали.
 *
 * Выбрали файл — и каждый его объект уже стоит на столе деталью
 * (`import_to_bed`): у 3MF это объекты стола слайсера, части одного объекта
 * вместе. Прежде было три шага (диагноз, импорт, «В деталь»), а из 3MF
 * приходила одна деталь из многих. Диагноз и ремонт никуда не делись — они
 * идут внутри и показываются здесь же, после: что пришло, что починено, где
 * встало.
 *
 * Исходник при импорте сохраняется нетронутым: ремонт — это решение, а не
 * факт, и через месяц надо уметь сравнить с оригиналом.
 */
import { FileInput, PackageOpen } from "lucide-react";
import { useEffect } from "react";

import { importModel } from "../chrome/commands";
import { useDoc } from "../store/doc";
import { useUi } from "../store/ui";
import { GhostActions } from "./Ghosts";

const LEVELS: { key: string; label: string; hint: string }[] = [
  { key: "auto", label: "Ремонт: как выйдет", hint: "чистка, дыры, а при больших дырах — перестройка" },
  { key: "light", label: "Ремонт: только лёгкий", hint: "чистка и заделка дыр, без перестройки" },
  { key: "remesh", label: "Ремонт: перестроить", hint: "сразу по вокселям: замкнутость ценой точности" },
  { key: "none", label: "Без ремонта", hint: "взять как есть" },
];

export function Models() {
  const models = useDoc((s) => s.models);
  const ghosts = useDoc((s) => s.ghosts);
  const last = useDoc((s) => s.lastImport);
  const busy = useDoc((s) => s.working);
  const problem = useDoc((s) => s.problems.models);
  const level = useUi((s) => s.importRepair);
  const { loadModels, select } = useDoc.getState();
  const { setImportRepair } = useUi.getState();

  useEffect(() => {
    void loadModels();
  }, [loadModels]);

  return (
    <>
      <div className="panel-head">
        <h2>
          Модели <span className="count">{models.length}</span>
        </h2>
      </div>
      <div className="scroll pad">
        <button className="drop-target" onClick={() => void importModel()} disabled={busy}>
          <FileInput size={22} strokeWidth={1.6} />
          <span className="drop-title">{busy ? "Ставлю на стол…" : "Открыть файл…"}</span>
          <span className="sub">STL, 3MF или OBJ — каждый объект файла сразу встанет деталью на стол</span>
        </button>
        <div className="row" style={{ marginTop: 8 }}>
          <select
            className="field"
            value={level}
            onChange={(e) => setImportRepair(e.target.value)}
            title={LEVELS.find((l) => l.key === level)?.hint}
            aria-label="Ремонт при импорте"
          >
            {LEVELS.map((item) => (
              <option key={item.key} value={item.key} title={item.hint}>
                {item.label}
              </option>
            ))}
          </select>
        </div>

        {problem && <div className="alert">{problem}</div>}

        {last && (
          <>
            <div className={`verdict compact ${last.fits ? "good" : "warn"}`}>
              <div>
                <div className="verdict-title">{last.file}</div>
                <div className="verdict-sub">
                  на столе деталей: {last.parts.length}
                  {last.note ? ` · ${last.note}` : ""}
                </div>
              </div>
            </div>
            {last.file_notes.map((n) => (
              <div key={n} className="sub warn">— {n}</div>
            ))}
            {last.parts.map((p) => (
              <button key={p.part} className="model-line" onClick={() => select(p.part)}
                      title="Выделить деталь">
                <span className="param-name">{p.part}</span>
                <span className="sub">
                  {[
                    p.steps.length ? `ремонт: ${p.steps.join(" + ")}` : "без ремонта",
                    p.bodies > 1 ? `тел ${p.bodies}` : "",
                    p.sealed_voids ? `запаянных полостей ${p.sealed_voids}` : "",
                    p.healthy ? "" : "с замечаниями",
                  ].filter(Boolean).join(" · ")}
                </span>
                {p.notes.map((n) => (
                  <span key={n} className={`sub ${/НЕ |ВНИМАНИЕ/.test(n) ? "bad" : "dim"}`}>{n}</span>
                ))}
              </button>
            ))}
          </>
        )}

        <h2 className="section-title">В проекте</h2>
        {models.length === 0 && (
          <div className="empty">
            Чужих моделей пока нет. Импортированная живёт в imported/&lt;имя&gt;/ вместе
            с нетронутым исходником и журналом ремонта, а в скрипте детали берётся
            одной строкой: <code>imported("имя")</code>.
          </div>
        )}
        {models.map((model) => {
          const view = ghosts.find((g) => g.name === model.name);
          return (
            <div key={model.name} className="model">
              <div className="param-head">
                <PackageOpen size={15} className="dim" />
                <span className="param-name">{model.name}</span>
                <span className="param-value">
                  {model.triangles.toLocaleString("ru-RU")} гр.
                </span>
                {view && <GhostActions view={view} />}
              </div>
              {view && view.used_by.length > 0 && (
                <div className="sub good">деталь: {view.used_by.join(", ")}</div>
              )}
              <div className="sub">
                <span className={model.healthy ? "good" : "warn"}>
                  {model.healthy ? "цел" : "с замечаниями"}
                </span>
                {model.steps.length > 0 && <span> · ремонт: {model.steps.join(" + ")}</span>}
              </div>
              <div className="sub dim">{model.source}</div>
            </div>
          );
        })}
      </div>
    </>
  );
}
