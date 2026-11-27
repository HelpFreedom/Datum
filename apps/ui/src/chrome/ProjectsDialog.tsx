/**
 * «Файл → Проекты…» — проекты как вещи (решение владельца: диалог из меню,
 * запуск по-прежнему открывает пустой проект).
 *
 * Список составляет оболочка: окно получает метки, а не пути, и просит по
 * метке — открыть, переименовать (название И папку), сделать новый из этого.
 * Переименование открытого проекта останавливает агента и переоткрывает
 * проект по новому пути — об этом сказано до нажатия.
 */
import { Copy, FolderOpen, Pencil, Trash2, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { useUi } from "../store/ui";
import { type ProjectEntry, projectsBridge } from "../transport/endpoint";
import { opened, opening } from "./commands";
import { ago, bytesText, partsText } from "./projects";

type Edit = { id: string; kind: "rename" | "copy"; value: string } | null;

export function ProjectsDialog() {
  const open = useUi((s) => s.projectsOpen);
  const { setProjectsOpen } = useUi.getState();
  const [entries, setEntries] = useState<ProjectEntry[] | null>(null);
  const [thumbs, setThumbs] = useState<Record<string, string>>({});
  const [cache, setCache] = useState<Record<string, number>>({});
  const [edit, setEdit] = useState<Edit>(null);
  const [problem, setProblem] = useState("");
  const [busy, setBusy] = useState(false);
  const urls = useRef<string[]>([]);

  const load = async () => {
    const api = projectsBridge();
    if (!api) {
      setProblem("окно открыто не из Datum — список проектов даёт оболочка");
      setEntries([]);
      return;
    }
    try {
      const list = await api.list();
      setEntries(list);
      // Картинки и размер кеша — следом, по одному: список виден сразу.
      for (const entry of list) {
        if (entry.thumb) {
          void api.thumb(entry.id).then((bytes) => {
            if (!bytes) return;
            const url = URL.createObjectURL(new Blob([bytes as BlobPart], { type: "image/png" }));
            urls.current.push(url);
            setThumbs((prev) => ({ ...prev, [entry.id]: url }));
          });
        }
        void api.cache(entry.id).then((bytes) =>
          setCache((prev) => ({ ...prev, [entry.id]: bytes })));
      }
    } catch (e) {
      setProblem(e instanceof Error ? e.message : String(e));
      setEntries([]);
    }
  };

  useEffect(() => {
    if (!open) return undefined;
    setProblem("");
    setEdit(null);
    void load();
    return () => {
      for (const url of urls.current) URL.revokeObjectURL(url);
      urls.current = [];
      setThumbs({});
      setCache({});
      setEntries(null);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  if (!open) return null;
  const close = () => setProjectsOpen(false);

  const act = async (run: () => Promise<{ ok: boolean; error: string }>, closing: boolean) => {
    setBusy(true);
    setProblem("");
    try {
      const result = await run();
      if (!result.ok) {
        setProblem(result.error || "не вышло");
        return;
      }
      if (closing) close();
      else await load();
    } catch (e) {
      setProblem(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const openProject = (entry: ProjectEntry) => {
    const api = projectsBridge();
    if (!api || entry.current) {
      close();
      return;
    }
    close();
    void api.open(entry.id, opening).then(opened);
  };

  const submit = (entry: ProjectEntry) => {
    const api = projectsBridge();
    const value = edit?.value.trim() ?? "";
    if (!api || !edit || !value) return;
    if (edit.kind === "copy") {
      // Новый проект открывается сразу — диалог закрывается.
      void act(() => api.copy(entry.id, value, opening), true);
    } else if (value === entry.name) {
      setEdit(null);
    } else {
      // Открытый проект переоткроется по новому пути — окно загрузится заново.
      void act(() => api.rename(entry.id, value, opening), entry.current);
      setEdit(null);
    }
  };

  return (
    <div className="modal-backdrop" onClick={close}>
      <div
        className="modal projects"
        role="dialog"
        aria-modal="true"
        aria-label="Проекты"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="modal-head">
          <h3>Проекты</h3>
          <button className="icon-button small" aria-label="Закрыть" onClick={close}>
            <X size={15} />
          </button>
        </div>
        {problem && <div className="projects-problem" role="alert">{problem}</div>}
        {entries === null && <div className="sub">Читаю список…</div>}
        {entries !== null && entries.length === 0 && !problem && (
          <div className="sub">Проектов пока нет.</div>
        )}
        <div className="projects-list">
          {(entries ?? []).map((entry) => {
            const editing = edit?.id === entry.id ? edit : null;
            const bytes = cache[entry.id];
            return (
              <div
                key={entry.id}
                className={`project-row${entry.current ? " current" : ""}`}
                data-project={entry.folder}
              >
                <button
                  className="project-thumb"
                  title={entry.current ? "Этот проект открыт" : "Открыть в этом окне"}
                  onClick={() => openProject(entry)}
                  disabled={busy}
                >
                  {thumbs[entry.id]
                    ? <img src={thumbs[entry.id]} alt="" />
                    : <FolderOpen size={22} />}
                </button>
                <div className="project-text">
                  {editing ? (
                    <form
                      className="project-edit"
                      onSubmit={(e) => {
                        e.preventDefault();
                        submit(entry);
                      }}
                    >
                      <input
                        autoFocus
                        aria-label={editing.kind === "copy" ? "Название нового проекта" : "Новое название"}
                        value={editing.value}
                        maxLength={80}
                        onChange={(e) => setEdit({ ...editing, value: e.target.value })}
                        onKeyDown={(e) => {
                          if (e.key === "Escape") {
                            e.stopPropagation();
                            setEdit(null);
                          }
                        }}
                      />
                      <button type="submit" className="primary" disabled={busy || !editing.value.trim()}>
                        {editing.kind === "copy" ? "Создать" : "Переименовать"}
                      </button>
                      <button type="button" className="ghost" onClick={() => setEdit(null)}>
                        Отмена
                      </button>
                      <div className="sub">
                        {editing.kind === "copy"
                          ? "Копия без кеша и выгрузки ляжет рядом и откроется в этом окне."
                          : entry.current
                            ? "Изменятся название и папка. Проект откроется заново, агент остановится. Его прежние сессии связаны со старой папкой."
                            : "Изменятся название и папка проекта."}
                      </div>
                    </form>
                  ) : (
                    <>
                      <div className="project-name">
                        {entry.name}
                        {entry.current && <span className="project-mark">открыт</span>}
                        {entry.empty && <span className="project-mark dim">пустой</span>}
                      </div>
                      <div className="sub">
                        {partsText(entry.parts)} · {ago(entry.modified)}
                        {bytes !== undefined && bytes > 0 && ` · кеш ${bytesText(bytes)}`}
                      </div>
                      {/* Папка — первой: длинный путь обрезается с конца. */}
                      <div className="sub project-where">{entry.folder} — {entry.where}</div>
                    </>
                  )}
                </div>
                {!editing && (
                  <div className="project-actions">
                    {!entry.current && (
                      <button className="chip-button" data-action="open" disabled={busy} onClick={() => openProject(entry)}>
                        Открыть
                      </button>
                    )}
                    <button
                      className="icon-button small"
                      data-action="rename"
                      title="Переименовать: название и папку"
                      aria-label={`Переименовать ${entry.name}`}
                      disabled={busy}
                      onClick={() => setEdit({ id: entry.id, kind: "rename", value: entry.name })}
                    >
                      <Pencil size={14} />
                    </button>
                    <button
                      className="icon-button small"
                      data-action="copy"
                      title="Новый проект из этого: копия без кеша и выгрузки"
                      aria-label={`Новый проект из ${entry.name}`}
                      disabled={busy}
                      onClick={() => setEdit({ id: entry.id, kind: "copy", value: `${entry.name} — копия` })}
                    >
                      <Copy size={14} />
                    </button>
                    {!entry.current && bytes !== undefined && bytes > 0 && (
                      <button
                        className="icon-button small"
                        data-action="clear-cache"
                        title={`Очистить кеш (${bytesText(bytes)}): детали пересоберутся при открытии`}
                        aria-label={`Очистить кеш ${entry.name}`}
                        disabled={busy}
                        onClick={() => {
                          const api = projectsBridge();
                          if (api) void act(() => api.clearCache(entry.id), false);
                        }}
                      >
                        <Trash2 size={14} />
                      </button>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
        <div className="sub">
          Здесь проекты «Без названия» и те, что вы открывали. Другую папку — «Файл → Открыть
          проект…». Запуск Datum по-прежнему начинает с пустого проекта.
        </div>
      </div>
    </div>
  );
}
