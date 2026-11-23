/**
 * Проверки — то, ради чего Datum и затевался.
 *
 * Показывается ровно то, что посчитал сервер: значение, порог и ЗАПАС. Запас
 * важнее статуса: «прошло» с запасом в две десятых процента и «прошло» с
 * запасом вдвое — разные вещи, и в рабочем процессе CAD именно неразличение
 * этих случаев стоило трёх неудачных печатей. Поэтому список по умолчанию
 * отсортирован худшим вперёд, а не по алфавиту.
 *
 * Запас — ДОЛЯ ПОРОГА (так он сравним между мм³ и градусами), и показывается
 * в процентах. Рядом в раскрытой строке — значение и порог в своих единицах.
 */
import { ChevronRight, CircleCheck, CircleX, Play, TriangleAlert } from "lucide-react";
import { useState } from "react";

import { Evidence } from "./Evidence";

import type { CheckResult } from "../store/doc";
import { useDoc } from "../store/doc";
import { amount, margin } from "../units";

const LABEL: Record<CheckResult["status"], string> = {
  pass: "прошла",
  fail: "не прошла",
  warn: "внимание",
  skip: "пропущена",
  error: "ошибка",
};

const TONE: Record<CheckResult["status"], string> = {
  pass: "good",
  fail: "bad",
  warn: "warn",
  skip: "dim",
  error: "bad",
};

const num = (v: number | null | undefined, unitName = "") => amount(v, unitName, 3);

/** Порог по-человечески: одно число или вилка «от…до». */
function threshold(result: CheckResult): string {
  const t = result.threshold;
  if (Array.isArray(t)) {
    const [lo, hi] = t;
    if (lo !== null && hi !== null) return `${num(lo)}…${num(hi, result.unit)}`;
    if (lo !== null) return `≥ ${num(lo, result.unit)}`;
    if (hi !== null) return `≤ ${num(hi, result.unit)}`;
    return "";
  }
  return `${result.op} ${num(t, result.unit)}`;
}

function Row({ result }: { result: CheckResult }) {
  const [open, setOpen] = useState(false);
  // Имя детали в начале id — приглушённо: глаз ищет, ЧТО проверено.
  const dot = result.id.indexOf(".");
  const prefix = dot > 0 ? result.id.slice(0, dot) : "";
  const rest = dot > 0 ? result.id.slice(dot + 1) : result.id;
  return (
    <div className={`check ${open ? "open" : ""}`}>
      <button className="check-head" aria-expanded={open} onClick={() => setOpen(!open)}>
        <span className={`dot ${TONE[result.status]}`} title={LABEL[result.status]}>
          {/* Знак для сценариев и диктора: цвет точки виден не всем. */}
          <span className={`sr-only ${TONE[result.status]}`}>{LABEL[result.status]}</span>
        </span>
        <span className="check-id">
          {prefix && <span className="check-prefix">{prefix}.</span>}
          {rest}
        </span>
        <span className="check-margin" title="запас до порога, в долях самого порога">
          {margin(result.margin)}
        </span>
        <ChevronRight size={14} className="check-chevron" />
      </button>
      {open && (
        <div className="check-body">
          <div className="check-message">{result.message}</div>
          <div className="sub">
            {result.value !== null && (
              <>
                {num(result.value, result.unit)} {threshold(result)}
                {" · "}
              </>
            )}
            {result.severity === "blocking"
              ? "блокирует печать"
              : result.severity === "warn"
                ? "предупреждение"
                : "для сведения"}
            {" · "}
            {result.duration_ms.toFixed(1)} мс
          </div>
          {result.depends_on.length > 0 && (
            <div className="chips">
              {result.depends_on.map((name) => (
                <span key={name} className="chip" title="параметр, от которого зависит">
                  {name}
                </span>
              ))}
            </div>
          )}
          {result.evidence
            ?.filter((a) => a.kind === "png" && a.url)
            .map((a) => <Evidence key={a.url} url={a.url} caption={a.caption} />)}
        </div>
      )}
    </div>
  );
}

export function Checks() {
  const checks = useDoc((s) => s.checks);
  const checking = useDoc((s) => s.checking);
  const problem = useDoc((s) => s.problems.checks);
  const { runChecks } = useDoc.getState();
  const [onlyBad, setOnlyBad] = useState(false);

  // «Только требующие внимания» — это и предупреждения: раньше фильтр прятал
  // их вместе с прошедшими, а «порог внутри вилки» — как раз про внимание.
  const needsAttention = (r: CheckResult) =>
    r.status === "fail" || r.status === "error" || r.status === "warn";
  const results = [...(checks?.results ?? [])]
    .filter((r) => !onlyBad || needsAttention(r))
    .sort((a, b) => {
      const rank = { error: 0, fail: 1, warn: 2, pass: 3, skip: 4 } as const;
      if (rank[a.status] !== rank[b.status]) return rank[a.status] - rank[b.status];
      return (a.margin ?? Infinity) - (b.margin ?? Infinity);
    });

  const total = checks?.results.length ?? 0;
  // Столько же строк покажет фильтр: считаются строки, а не новая мера.
  const attention = checks?.results.filter(needsAttention).length ?? 0;
  const skipped = checks?.results.filter((r) => r.status === "skip").length ?? 0;

  const tone = !checks ? "" : checks.empty ? "warn" : !checks.ok ? "bad" : checks.n_warn ? "warn" : "good";
  const Icon = tone === "bad" ? CircleX : tone === "good" ? CircleCheck : TriangleAlert;
  const title = !checks
    ? ""
    : checks.empty
      ? "Нечего проверять"
      : !checks.ok
        ? `Не прошло: ${checks.n_fail} из ${total}`
        : checks.n_warn
          ? "Прошло, но есть внимание"
          : "Всё в порядке";

  return (
    <>
      <button className="run-checks" onClick={() => void runChecks()} disabled={checking}>
        {checking ? "Проверяю…" : "Запустить проверку"} <Play size={13} fill="currentColor" />
      </button>

      {problem && <div className="alert">{problem}</div>}

      {checks && (
        <div className={`verdict ${tone}`} role="status">
          <span className="verdict-icon">
            <Icon size={22} strokeWidth={2.2} />
          </span>
          <div>
            <div className="verdict-title">{title}</div>
            <div className="verdict-sub">
              {checks.empty
                ? "в проекте нет ни одной проверки — это не «OK»"
                : [
                    `прошло ${checks.n_pass} из ${total}`,
                    checks.n_warn > 0 && `внимание: ${checks.n_warn}`,
                    skipped > 0 && `пропущено: ${skipped}`,
                  ]
                    .filter(Boolean)
                    .join(" · ")}
            </div>
          </div>
        </div>
      )}

      {checks && total > 0 && (
        <div className="segmented wide" role="group" aria-label="Какие проверки показать">
          <button aria-pressed={!onlyBad} onClick={() => setOnlyBad(false)}>
            Все <span className="count">{total}</span>
          </button>
          <button aria-pressed={onlyBad} onClick={() => setOnlyBad(true)}>
            Внимание <span className="count">{attention}</span>
          </button>
        </div>
      )}

      {!checks && !problem && <div className="empty">Проверки ещё не прогоняли.</div>}
      {checks && total > 0 && results.length === 0 && (
        <div className="empty">Всё прошло — внимания ничто не требует.</div>
      )}

      <div className="check-list">
        {results.map((result) => (
          <Row key={result.id} result={result} />
        ))}
      </div>
    </>
  );
}
