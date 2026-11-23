/**
 * Лента вызовов: что делалось, кем и сколько заняло.
 *
 * Прямой ответ на требование «всё, что делает ИИ, видно в реальном времени».
 * Вызов агента идёт ТЕМ ЖЕ путём, что кнопка в окне — через один реестр
 * операций и один сервер, — поэтому в ленте он появляется сам собой, без
 * отдельной проводки. Подпись говорит, кто затеял.
 */
import { useState } from "react";

import { useDoc } from "../store/doc";
import { cancelJob } from "../transport/client";

const WHO: Record<string, { label: string; tone: string }> = {
  user: { label: "вы", tone: "accent" },
  agent: { label: "агент", tone: "warn" },
  external: { label: "снаружи", tone: "dim" },
};

const STATUS: Record<string, { label: string; tone: string }> = {
  done: { label: "готово", tone: "good" },
  failed: { label: "отказ", tone: "bad" },
  cancelled: { label: "отменено", tone: "dim" },
  running: { label: "идёт", tone: "busy" },
  pending: { label: "в очереди", tone: "dim" },
};

const clock = (seconds: number) =>
  seconds
    ? new Date(seconds * 1000).toLocaleTimeString("ru-RU", { hour12: false })
    : "";

/**
 * Что показывать. Служебные вызовы самого окна (отчёт о виде после каждой
 * остановки камеры, крышки среза, чтение состояния) в ленте не нужны: они
 * вытесняли из неё действия агента — то, ради чего она есть. Отказ и идущее
 * показываются всегда, чьими бы ни были.
 */
export const shownInLog = (job: { service?: boolean; status: string }, all: boolean) =>
  all || !job.service || job.status === "failed" || job.status === "running";

export function Log() {
  const jobs = useDoc((s) => s.jobs);
  const [all, setAll] = useState(false);
  const [stopping, setStopping] = useState<string | null>(null);
  const hidden = jobs.filter((job) => !shownInLog(job, false)).length;
  const recent = [...jobs].reverse().filter((job) => shownInLog(job, all));

  const cancel = async (id: string) => {
    setStopping(id);
    try {
      await cancelJob(id);
    } catch {
      /* задание уже кончилось — лента обновится сама */
    } finally {
      setStopping(null);
    }
  };

  if (jobs.length === 0) {
    return <div className="empty">Пока ничего не запускали. Здесь появится каждый вызов — ваш и агента.</div>;
  }

  return (
    <div className="log">
      {hidden > 0 && (
        <button className="log-more" onClick={() => setAll(!all)}>
          {all ? "Скрыть служебные вызовы окна" : `Служебных вызовов окна скрыто: ${hidden} — показать`}
        </button>
      )}
      {recent.map((job) => {
        const who = WHO[job.actor] ?? WHO.external;
        const status = STATUS[job.status] ?? STATUS.pending;
        return (
          <div key={job.id} className="log-row" title={job.error ?? undefined}>
            <span className={`dot ${status.tone}`} title={status.label} />
            <span className="log-at">{clock(job.started)}</span>
            <span className="log-op">{job.op}</span>
            <span className={`pill ${who.tone}`}>{who.label}</span>
            <span className="log-ms">
              {job.status === "running" ? "…" : `${job.ms.toFixed(0)} мс`}
            </span>
            {(job.status === "running" || job.status === "pending") && (
              <button
                className="log-cancel"
                data-action="job-cancel"
                disabled={stopping === job.id}
                title="Остановить задание: его процесс снимается целиком"
                onClick={() => void cancel(job.id)}
              >
                {stopping === job.id ? "Отменяю…" : "Отменить"}
              </button>
            )}
            {job.error && <span className="log-error bad">{job.error}</span>}
          </div>
        );
      })}
    </div>
  );
}
