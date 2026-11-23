/**
 * Корзина проекта: удалённые детали, которые можно вернуть.
 *
 * Удаление в Datum — целиком и без вопроса (деталь, её модель и проверки
 * уходят в `.datum/trash/`), и держится оно на возврате. Но возврат был только
 * через Ctrl+Z, а история отмены живёт в памяти страницы: после перезагрузки
 * окна или открытия проекта заново деталь лежала в корзине, и достать её из
 * окна было нечем (аудит 2026-11-21). Список читается с сервера — он знает
 * корзину и после перезапуска.
 */
import { ChevronDown, ChevronRight, RotateCcw, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";

import { useDoc } from "../store/doc";
import { runOp } from "../transport/client";

export interface TrashEntry {
  id: string;
  parts: string[];
  file: string;
  deleted: string;
  models: string[];
}

/** «сегодня 14:05» / «21.11 14:05» — когда удалено. */
export function deletedWhen(iso: string, now: Date = new Date()): string {
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return "";
  const time = at.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit", hour12: false });
  const same = at.toDateString() === now.toDateString();
  return same ? `сегодня ${time}`
    : `${at.toLocaleDateString("ru-RU", { day: "2-digit", month: "2-digit" })} ${time}`;
}

export function Trash() {
  const project = useDoc((s) => s.project);
  // Отпечаток файлов проекта: изменился (удалили, вернули) — список перечитывается.
  const stamp = useDoc((s) => s.info?.stamp ?? "");
  const [entries, setEntries] = useState<TrashEntry[]>([]);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);

  useEffect(() => {
    if (!project) return;
    let stale = false;
    runOp<{ entries?: TrashEntry[]; error?: string }>("trash_list", { project })
      .then(({ result }) => {
        if (!stale) setEntries(result.entries ?? []);
      })
      .catch(() => {
        /* корзина не критична: нет списка — нет и раздела */
      });
    return () => {
      stale = true;
    };
  }, [project, stamp]);

  if (entries.length === 0) return null;

  const restore = async (entry: TrashEntry) => {
    setBusy(entry.id);
    setProblem(null);
    try {
      const { result } = await runOp<{ error?: string }>("part_restore", { project, trash: entry.id });
      if (result.error) {
        setProblem(result.error);
        return;
      }
      setEntries((list) => list.filter((e) => e.id !== entry.id));
      await useDoc.getState().refreshAll();
      if (entry.parts[0]) useDoc.getState().select(entry.parts[0]);
    } catch (e) {
      setProblem(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="trash" data-section="trash">
      <button className="section-toggle" aria-expanded={open} onClick={() => setOpen(!open)}>
        {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        <Trash2 size={14} />
        Корзина <span className="count">{entries.length}</span>
      </button>
      {open && (
        <div className="trash-list">
          {problem && <div className="alert">{problem}</div>}
          {[...entries].reverse().map((entry) => (
            <div key={entry.id} className="trash-row">
              <span className="trash-text">
                <span className="name">{entry.parts.join(", ") || entry.file}</span>
                <span className="sub">
                  {[deletedWhen(entry.deleted),
                    entry.models.length ? `модель ${entry.models.join(", ")}` : ""]
                    .filter(Boolean).join(" · ")}
                </span>
              </span>
              <button
                className="chip-button"
                data-action="trash-restore"
                disabled={busy !== null}
                title="Вернуть деталь в проект: файл, модель, проверки и положение"
                onClick={() => void restore(entry)}
              >
                <RotateCcw size={13} />
                {busy === entry.id ? "Возвращаю…" : "Вернуть"}
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
