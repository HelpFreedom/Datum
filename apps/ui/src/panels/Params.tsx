/**
 * Параметры и реестр незамеренного — претензия №5 из технического задания.
 *
 * В рабочем процессе CAD конфликт двух измерений жил в комментарии:
 *     PHONE_T = 8.2   # пользователь намерил 9.0 — перемерить
 * Его нельзя было ни запросить, ни прогнать по диапазону, ни превратить в
 * красную строку. Здесь происхождение каждого числа — первого класса:
 * «намерено» отличается от «предположено», а предположенное, которое блокирует
 * печать, видно сразу и без поиска.
 *
 * ПРАВКА идёт той же операцией `param_set`, что и у агента: файл параметров
 * меняется по месту, с комментариями, а правила реестра действуют и тут —
 * производный не правится, угаданный держится своего диапазона, замер приходит
 * с источником. Окно ничего не проверяет само: что можно, решает сервер, а
 * окно показывает его ответ.
 */
import { Pencil, TriangleAlert } from "lucide-react";
import { useState } from "react";

import type { Param } from "../store/doc";
import { useDoc } from "../store/doc";
import { amount, unit } from "../units";

const LABEL: Record<Param["provenance"], string> = {
  measured: "намерено",
  assumed: "предположено",
  derived: "выведено",
  standard: "стандарт",
  chosen: "выбрано",
};

const TONE: Record<Param["provenance"], string> = {
  measured: "good",
  assumed: "warn",
  derived: "dim",
  standard: "info",
  chosen: "accent",
};

/** Число из поля ввода: запятая как десятичный разделитель — тоже число. */
function parseNumber(text: string): number | null {
  const cleaned = text.trim().replace(",", ".");
  if (!/^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$/.test(cleaned)) return null;
  const value = Number(cleaned);
  return Number.isFinite(value) ? value : null;
}

function Editor({ name, param, onDone }: { name: string; param: Param; onDone: () => void }) {
  const saving = useDoc((s) => s.savingParam === name);
  const [text, setText] = useState(param.value !== undefined ? String(param.value) : "");
  // У измеренного новое значение без источника сервер не примет — поле
  // источника сразу открыто и обязательно.
  const [measured, setMeasured] = useState(param.provenance === "measured");
  const [source, setSource] = useState("");
  const value = parseNumber(text);
  const canMeasure = param.provenance === "assumed" || param.provenance === "measured";
  const ready = value !== null && (!measured || source.trim().length > 0) && !saving;

  const save = async () => {
    if (!ready || value === null) return;
    const ok = await useDoc.getState().setParam(name, value, measured ? source : "");
    if (ok) onDone();
  };

  return (
    <form
      className="param-edit"
      onSubmit={(e) => {
        e.preventDefault();
        void save();
      }}
      onKeyDown={(e) => {
        if (e.key === "Escape") onDone();
      }}
    >
      <div className="row">
        <input
          className="field"
          autoFocus
          inputMode="decimal"
          aria-label={`${name}, ${unit(param.unit) || "без единицы"}`}
          aria-invalid={value === null}
          value={text}
          onChange={(e) => setText(e.target.value)}
        />
        <span className="dim">{unit(param.unit)}</span>
      </div>
      {value === null && <div className="sub bad">нужно число</div>}
      {param.provenance === "assumed" && param.range && !measured && (
        <div className="sub dim">
          догадка: от {amount(param.range[0])} до {amount(param.range[1], param.unit)}
        </div>
      )}
      {canMeasure && (
        <label className="toggle">
          <input
            type="checkbox"
            checked={measured}
            disabled={param.provenance === "measured"}
            onChange={(e) => setMeasured(e.target.checked)}
          />
          это замер
        </label>
      )}
      {measured && (
        <input
          className="field"
          placeholder={param.measure_hint ? `чем мерили — ${param.measure_hint}` : "чем и как мерили"}
          aria-label="источник замера"
          value={source}
          onChange={(e) => setSource(e.target.value)}
        />
      )}
      <div className="row">
        <button type="submit" disabled={!ready}>
          {saving ? "Записываю…" : "Записать"}
        </button>
        <button type="button" onClick={onDone}>
          Отмена
        </button>
      </div>
    </form>
  );
}

export function Params() {
  const params = useDoc((s) => s.params);
  const checks = useDoc((s) => s.checks);
  const problem = useDoc((s) => s.problems.params);
  const [onlyUnknown, setOnlyUnknown] = useState(false);
  const [editing, setEditing] = useState<string | null>(null);

  if (!params) {
    return problem ? (
      <div className="alert">{problem}</div>
    ) : (
      <div className="empty">Параметры ещё не загружены.</div>
    );
  }

  // Сколько проверок опирается на параметр. Это не пересчёт физики, а подсчёт
  // ссылок: ответ на вопрос «если я перемеряю это, что пересчитается».
  const usage = new Map<string, number>();
  for (const result of checks?.results ?? []) {
    for (const name of result.depends_on) {
      usage.set(name, (usage.get(name) ?? 0) + 1);
    }
  }

  const names = Object.keys(params.params).filter(
    (name) => !onlyUnknown || params.summary.unknowns.includes(name),
  );

  return (
    <>
      {problem && <div className="alert">{problem}</div>}

      {!params.summary.print_ready && (
        <div className="verdict warn">
          <span className="verdict-icon">
            <TriangleAlert size={20} strokeWidth={2.2} />
          </span>
          <div>
            <div className="verdict-title">Не готов к печати</div>
            <div className="verdict-sub">не измерено: {params.summary.blocking_unknowns.join(", ")}</div>
          </div>
        </div>
      )}

      <div className="segmented wide" role="group" aria-label="Какие параметры показать">
        <button aria-pressed={!onlyUnknown} onClick={() => setOnlyUnknown(false)}>
          Все <span className="count">{params.summary.total}</span>
        </button>
        <button aria-pressed={onlyUnknown} onClick={() => setOnlyUnknown(true)}>
          Неизмеренные <span className="count">{params.summary.unknowns.length}</span>
        </button>
      </div>

      {names.map((name) => {
        const p = params.params[name];
        const uses = usage.get(name) ?? 0;
        // Сервер знает чтения точно (сборка и проверки); окно только показывает.
        const unread = checks?.unread_params?.includes(name) ?? false;
        const editable = p.provenance !== "derived";
        return (
          <div key={name} className={`param ${editing === name ? "editing" : ""}`}>
            <div className="param-head">
              <span className="param-name">{name}</span>
              {/* Плашка, значение и карандаш переносятся вместе: по отдельности
                  в узкой панели карандаш уезжал на строку один. */}
              <span className="param-meta">
                <span className={`pill ${TONE[p.provenance]}`}>{LABEL[p.provenance]}</span>
                <span className="param-value">
                  {p.value !== undefined
                    ? amount(p.value, p.unit)
                    : p.resolved !== undefined
                      ? amount(p.resolved, p.unit)
                      : p.expr ?? ""}
                </span>
                {editable && editing !== name && (
                  <button
                    className="param-change"
                    title="изменить значение или записать замер"
                    aria-label={`изменить ${name}`}
                    onClick={() => setEditing(name)}
                  >
                    <Pencil size={13} />
                  </button>
                )}
              </span>
            </div>
            {(p.blocking || p.range || p.tolerance !== undefined || uses > 0 || unread) && (
              <div className="sub">
                {[
                  p.blocking && <span key="b" className="warn">блокирует печать</span>,
                  unread && (
                    <span key="n" className="warn" title="его не читает ни одна деталь и ни одна проверка: правка ничего не изменит">
                      никто не читает
                    </span>
                  ),
                  p.range && (
                    <span key="r">
                      диапазон {amount(p.range[0])}…{amount(p.range[1], p.unit)}
                    </span>
                  ),
                  p.tolerance !== undefined && <span key="t">допуск ±{amount(p.tolerance, p.unit)}</span>,
                  uses > 0 && <span key="u">в {uses} проверках</span>,
                ]
                  .filter(Boolean)
                  .flatMap((el, i) => (i ? [" · ", el] : [el]))}
              </div>
            )}
            {p.provenance === "derived" && p.expr && (
              <div className="sub dim">= {p.expr}</div>
            )}
            {(p.reason || p.source || p.ref) && (
              <div className="sub dim">
                {[p.source && `источник: ${p.source}`, p.instrument, p.reason, p.ref]
                  .filter(Boolean)
                  .join(" · ")}
              </div>
            )}
            {p.provenance === "assumed" && p.measure_hint && (
              <div className="sub">как мерить: {p.measure_hint}</div>
            )}
            {p.note && <div className="sub dim">{p.note}</div>}
            {p.conflicts?.map((c, i) => (
              <div key={i} className="sub bad">
                расходится: {amount(c.value, p.unit)} — {c.source}
                {c.date ? ` (${c.date})` : ""}
              </div>
            ))}
            {editing === name && (
              <Editor name={name} param={p} onDone={() => setEditing(null)} />
            )}
          </div>
        );
      })}
    </>
  );
}
