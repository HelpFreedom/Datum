/**
 * Мост между оболочкой и окном. Всё, что окно может попросить у Node, — здесь.
 *
 * Поверхность нарочно крошечная: окно исполняет код, собранный из исходников
 * проекта, и чем меньше ему доступно, тем меньше цена ошибки в нём.
 */
import { contextBridge, ipcRenderer } from "electron";
import type { AgentKind, StartResult } from "../main/agent";

/**
 * Вызов открытия проекта с вестью «открываю <имя>»: проект открывается в том
 * же окне, и пока поднимается его сервер, окно показывает прежний проект.
 * Весть — обратным вызовом на время ОДНОГО вызова, а не отдельным ключом моста.
 */
function opening(channel: string, onOpening?: (name: string) => void, ...args: unknown[]) {
  const listener = (_event: unknown, name: string) => onOpening?.(String(name));
  ipcRenderer.on("datum:switching", listener);
  return (ipcRenderer.invoke(channel, ...args) as Promise<{ ok: boolean; error: string }>)
    .finally(() => ipcRenderer.removeListener("datum:switching", listener));
}

contextBridge.exposeInMainWorld("datum", {
  /** Токен для подписи запросов к серверу и путь открытого проекта. */
  endpoint: () => ipcRenderer.invoke("datum:endpoint"),
  platform: process.platform,

  /** Выбрать файл. Диалог рисует ОС; окно получает только выбранный путь. */
  pickFile: () => ipcRenderer.invoke("datum:pick-file") as Promise<string | null>,

  /**
   * Открыть другой проект. Каталог выбирает человек в диалоге ОС; окно пути не
   * называет и не получает — оболочка открывает выбранный проект в этом же окне.
   */
  openProject: (onOpening?: (name: string) => void) => opening("datum:open-project", onOpening),

  /** Открыть архив .datum: диалог ОС, распаковка, проект — в этом же окне. */
  openArchive: (onOpening?: (name: string) => void) => opening("datum:open-archive", onOpening),

  /**
   * Проекты как вещи («Файл → Проекты…»). Окно получает список с метками и
   * просит по метке: открыть, переименовать (название и папку), сделать новый
   * из этого. Пути оно не называет — оболочка берёт их из своего списка.
   */
  projects: {
    list: () => ipcRenderer.invoke("datum:projects:list") as Promise<unknown[]>,
    thumb: (id: string) =>
      ipcRenderer.invoke("datum:projects:thumb", id) as Promise<Uint8Array | null>,
    cache: (id: string) => ipcRenderer.invoke("datum:projects:cache", id) as Promise<number>,
    clearCache: (id: string) =>
      ipcRenderer.invoke("datum:projects:clear-cache", id) as Promise<{ ok: boolean; error: string }>,
    open: (id: string, onOpening?: (name: string) => void) =>
      opening("datum:projects:open", onOpening, id),
    rename: (id: string, name: string, onOpening?: (name: string) => void) =>
      opening("datum:projects:rename", onOpening, id, name),
    copy: (id: string, name: string, onOpening?: (name: string) => void) =>
      opening("datum:projects:copy", onOpening, id, name),
  },

  /** Куда сохранить архив: диалог ОС; null — человек передумал. */
  chooseArchivePath: () => ipcRenderer.invoke("datum:save-archive-path") as Promise<string | null>,

  /**
   * Размеры панелей между запусками. Только числа: оболочка сама решает, где
   * их хранить, и отбрасывает из присланного всё, кроме известных ключей.
   */
  layout: {
    load: () =>
      ipcRenderer.invoke("datum:layout:load") as Promise<{ left?: number; right?: number; dock?: number }>,
    save: (data: { left?: number; right?: number; dock?: number }) =>
      ipcRenderer.send("datum:layout:save", data),
  },

  /**
   * Терминал с системным `claude`. Своего агента Datum не пишет: здесь только
   * труба до процесса, который живёт в главном процессе оболочки.
   */
  agent: {
    /** backlog — хвост вывода, если агент уже работал (окно перезагрузилось). */
    start: (cols: number, rows: number, kind?: AgentKind) =>
      ipcRenderer.invoke("datum:agent:start", { cols, rows, kind }) as Promise<StartResult>,
    /** Активный агент для восстановления терминала после перезагрузки окна. */
    status: () => ipcRenderer.invoke("datum:agent:status") as Promise<{ running: boolean; kind: AgentKind | null }>,
    /** Размер контекста идущего claude (токенов на последнем ходе) или null. */
    context: () =>
      ipcRenderer.invoke("datum:agent:context") as Promise<{ tokens: number; at: number } | null>,
    write: (data: string) => ipcRenderer.send("datum:agent:write", data),
    resize: (cols: number, rows: number) =>
      ipcRenderer.send("datum:agent:resize", cols, rows),
    stop: () => ipcRenderer.send("datum:agent:stop"),
    onData: (callback: (chunk: string) => void) => {
      const listener = (_event: unknown, chunk: string) => callback(chunk);
      ipcRenderer.on("datum:agent:data", listener);
      return () => ipcRenderer.removeListener("datum:agent:data", listener);
    },
    onExit: (callback: (code: number) => void) => {
      const listener = (_event: unknown, code: number) => callback(code);
      ipcRenderer.on("datum:agent:exit", listener);
      return () => ipcRenderer.removeListener("datum:agent:exit", listener);
    },
  },
});
