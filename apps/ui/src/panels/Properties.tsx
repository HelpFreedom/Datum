/**
 * Свойства: выделенная деталь либо, без выделения, проект целиком.
 *
 * Все числа — сервера: объём и топология из сборки, габарит и сетка из
 * присланной геометрии. Окно их не пересчитывает, только раскладывает; длины
 * габарита — разность присланных границ, не новая мера.
 */
import { Box, FolderOpen, RotateCcw, Trash2 } from "lucide-react";
import type { ReactNode } from "react";

import type { CheckResult } from "../store/doc";
import { useDoc } from "../store/doc";
import { useUi } from "../store/ui";
import { deleteNow } from "../chrome/commands";
import { amount, margin } from "../units";

const TONE: Record<CheckResult["status"], string> = {
  pass: "good",
  fail: "bad",
  warn: "warn",
  skip: "dim",
  error: "bad",
};

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="kv">
      <span className="kv-key">{label}</span>
      <span className="kv-value">{children}</span>
    </div>
  );
}

const count = (n: number | undefined) => (n === undefined ? "—" : n.toLocaleString("ru-RU"));

function PartProps({ name }: { name: string }) {
  const view = useDoc((s) => s.parts[name]);
  const entry = useDoc((s) => s.build[name]);
  const checks = useDoc((s) => s.checks);
  const hidden = useUi((s) => s.hidden.includes(name));
  const { toggleHidden, showRight } = useUi.getState();
  const box = view?.bbox.length === 6 ? view.bbox : null;
  const volume = entry?.topology?.volume ?? view?.volumeMm3;
  const mine = (checks?.results ?? []).filter((r) => r.inputs.includes(name));

  return (
    <>
      <div className="props-title">
        <span className="props-icon">
          <Box size={18} />
        </span>
        <div>
          <div className="props-name">{name}</div>
          <div className="sub">
            {entry && !entry.ok ? (
              <span className="bad">сборка не удалась</span>
            ) : entry?.cached ? (
              "из кеша"
            ) : entry ? (
              `собрана за ${entry.ms.toFixed(0)} мс`
            ) : (
              "деталь"
            )}
            {hidden && " · спрятана"}
            {entry?.printed === false && (
              <span title="@part(printed=False): участвует в проверках, но не выгружается и не режется">
                {" · не печатается"}
              </span>
            )}
          </div>
        </div>
      </div>

      {entry?.error && <div className="alert">{entry.error.message}</div>}

      <div className="card">
        <div className="card-title">
          Геометрия
          {/* Черновая сборка грубее: её объём — не объём детали (как и у
              штангенциркуля, который пишет «по черновику»). */}
          {view?.draft && <span className="warn"> · по черновику</span>}
        </div>
        <Row label="Объём">{volume === undefined ? "—" : amount(volume, "mm3", 1)}</Row>
        {box && (
          <>
            <Row label="Габарит">
              {[0, 1, 2].map((i) => amount(box[i + 3] - box[i], "", 2)).join(" × ")} мм
            </Row>
            <Row label="От">
              {[0, 1, 2].map((i) => amount(box[i], "", 2)).join(", ")}
            </Row>
            <Row label="До">
              {[3, 4, 5].map((i) => amount(box[i], "", 2)).join(", ")}
            </Row>
          </>
        )}
      </div>

      <div className="card">
        <div className="card-title">Сетка и топология</div>
        <Row label="Треугольники">{count(view?.tris)}</Row>
        <Row label="Вершины">{count(view?.verts)}</Row>
        <Row label="Тел">{count(entry?.topology?.bodies ?? view?.bodies)}</Row>
        <Row label="Сквозных отверстий (род)">{count(entry?.topology?.genus)}</Row>
        <Row label="Файл вида">{view ? `${(view.bytes / 1024).toLocaleString("ru-RU", { maximumFractionDigits: 1 })} КБ` : "—"}</Row>
      </div>

      {entry && entry.depends_on.length > 0 && (
        <div className="card">
          <div className="card-title">Зависит от параметров</div>
          <div className="chips">
            {entry.depends_on.map((p) => (
              <button key={p} className="chip" title="открыть параметры" onClick={() => showRight("params")}>
                {p}
              </button>
            ))}
          </div>
        </div>
      )}

      {mine.length > 0 && (
        <div className="card">
          <div className="card-title">Проверки с её участием</div>
          {mine.map((r) => (
            <div key={r.id} className="mini-check">
              <span className={`dot ${TONE[r.status]}`} />
              <span className="mini-check-id">{r.id}</span>
              <span className="mono dim">{margin(r.margin)}</span>
            </div>
          ))}
        </div>
      )}

      {entry?.placement && <PlacementCard name={name} placement={entry.placement} />}

      <div className="button-row">
        <button onClick={() => toggleHidden(name)}>{hidden ? "Показать" : "Спрятать"}</button>
        <button onClick={() => void deleteNow(name)} title="Удалить из проекта (Del); вернуть — Ctrl+Z">
          <Trash2 size={14} />
          Удалить
        </button>
      </div>
    </>
  );
}

/** Где деталь стоит — как записано в placement.yaml. */
function PlacementCard({ name, placement }: { name: string; placement: { move: number[]; rotate: number[] } }) {
  const moved = placement.move.some(Boolean) || placement.rotate.some(Boolean);
  const nums = (v: number[], unitName: string) => v.map((x) => amount(x, "", 3)).join(", ") + unitName;
  return (
    <div className="card">
      <div className="card-title">Положение на столе</div>
      <Row label="Сдвиг">{nums(placement.move, " мм")}</Row>
      <Row label="Поворот X→Y→Z">{nums(placement.rotate, "°")}</Row>
      <div className="sub" style={{ marginTop: 6 }}>
        {moved ? "записано в placement.yaml — скрипт детали не менялся"
          : "там, где её поставил скрипт; двигать — M, поворачивать — R"}
      </div>
      {moved && (
        <div className="button-row">
          <button onClick={() => void useDoc.getState().placePart(name, [0, 0, 0], [0, 0, 0])}>
            <RotateCcw size={14} />
            Сбросить положение
          </button>
        </div>
      )}
    </div>
  );
}

function ProjectProps() {
  const info = useDoc((s) => s.info);
  const project = useDoc((s) => s.project);
  const projectName = useDoc((s) => s.projectName);
  const parts = useDoc((s) => s.parts);
  const checks = useDoc((s) => s.checks);
  const printer = info?.printer;
  const params = info?.params;

  return (
    <>
      <div className="props-title">
        <span className="props-icon">
          <FolderOpen size={18} />
        </span>
        <div>
          <div className="props-name">{projectName || "Проект"}</div>
          <div className="sub mono" title={project}>{project}</div>
        </div>
      </div>

      <div className="card">
        <div className="card-title">Проект</div>
        <Row label="Деталей">{count(Object.keys(parts).length)}</Row>
        <Row label="Проверок">{count(info?.checks)}</Row>
        {checks && !checks.empty && (
          <Row label="Прошло">
            <span className={checks.ok ? "good" : "bad"}>
              {checks.n_pass} из {checks.results.length}
            </span>
          </Row>
        )}
        {checks && checks.n_warn > 0 && (
          <Row label="Внимание">
            <span className="warn">{checks.n_warn}</span>
          </Row>
        )}
        {params && (
          <>
            <Row label="Параметров">{count(params.total)}</Row>
            <Row label="Не измерено">
              <span className={params.unknowns.length ? "warn" : "good"}>{params.unknowns.length || "нет"}</span>
            </Row>
            <Row label="К печати">
              <span className={params.print_ready ? "good" : "warn"}>
                {params.print_ready ? "готов" : "не готов"}
              </span>
            </Row>
          </>
        )}
      </div>

      {printer && (
        <div className="card">
          <div className="card-title">Принтер</div>
          <Row label="Стол">{printer.bed.join(" × ")} мм</Row>
          <Row label="Сопло">{amount(printer.nozzle, "mm", 2)}</Row>
          <Row label="Материал">{printer.material}</Row>
        </div>
      )}

      <div className="empty">Выберите деталь в сцене или щёлкните по ней во вьюпорте — здесь будут её свойства.</div>
    </>
  );
}

export function Properties() {
  const selected = useDoc((s) => s.selected);
  const exists = useDoc((s) => Boolean(s.selected && (s.parts[s.selected] || s.build[s.selected])));
  return selected && exists ? <PartProps name={selected} /> : <ProjectProps />;
}
