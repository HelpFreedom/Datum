/**
 * Откуда окно берёт токен.
 *
 * Окно живёт на ТОМ ЖЕ происхождении, что и сервер, поэтому адрес ему не нужен
 * вовсе: все запросы относительные. Нужен только токен — и он приходит не из
 * страницы, а из preload Electron по IPC.
 *
 * Отсюда полезное свойство: открытая в обычном браузере, страница загрузится
 * (она отдаётся без токена — иначе браузеру нечем было бы подписать переход),
 * но работать не станет: подписать запрос к /api ей нечем.
 */

import type { SavedLayout } from "../store/ui";

export interface Endpoint {
  token: string;
  project: string;
}

/** Проект в списке «Файл → Проекты…» — как его отдаёт оболочка. */
export interface ProjectEntry {
  /** Метка: окно просит по ней, путей не называя. */
  id: string;
  name: string;
  folder: string;
  where: string;
  modified: number;
  parts: number;
  current: boolean;
  empty: boolean;
  thumb: boolean;
}

type Answer = Promise<{ ok: boolean; error: string }>;

export interface ProjectsBridge {
  list: () => Promise<ProjectEntry[]>;
  thumb: (id: string) => Promise<Uint8Array | null>;
  cache: (id: string) => Promise<number>;
  clearCache: (id: string) => Answer;
  open: (id: string, onOpening?: (name: string) => void) => Answer;
  rename: (id: string, name: string, onOpening?: (name: string) => void) => Answer;
  copy: (id: string, name: string, onOpening?: (name: string) => void) => Answer;
}

interface ShellBridge {
  endpoint?: () => Promise<Endpoint>;
  platform?: string;
  pickFile?: () => Promise<string | null>;
  openProject?: (onOpening?: (name: string) => void) => Promise<{ ok: boolean; error: string }>;
  openArchive?: (onOpening?: (name: string) => void) => Promise<{ ok: boolean; error: string }>;
  chooseArchivePath?: () => Promise<string | null>;
  projects?: ProjectsBridge;
  layout?: {
    load: () => Promise<SavedLayout>;
    save: (data: SavedLayout) => void;
  };
}

function bridge(): ShellBridge | undefined {
  return (window as unknown as { datum?: ShellBridge }).datum;
}

/** Открыты ли мы из оболочки Datum, а не из постороннего браузера. */
export const insideShell = (): boolean => Boolean(bridge()?.endpoint);

/** Выбрать файл. Диалог рисует ОС; окно получает только выбранный путь. */
export async function pickFile(): Promise<string | null> {
  const api = bridge();
  if (!api?.pickFile) return null;
  return api.pickFile();
}

/**
 * Открыть другой проект: диалог ОС, затем оболочка открывает его в этом же
 * окне. `onOpening` — имя, пока поднимается сервер проекта.
 */
export async function openProject(onOpening?: (name: string) => void): Promise<{ ok: boolean; error: string }> {
  const api = bridge();
  if (!api?.openProject) return { ok: false, error: "окно открыто не из Datum" };
  return api.openProject(onOpening);
}

/** Открыть архив .datum: диалог ОС, распаковка, проект — в этом же окне. */
export async function openArchive(onOpening?: (name: string) => void): Promise<{ ok: boolean; error: string }> {
  const api = bridge();
  if (!api?.openArchive) return { ok: false, error: "окно открыто не из Datum" };
  return api.openArchive(onOpening);
}

/** Проекты как вещи: список, открытие, переименование. Вне оболочки — нет. */
export function projectsBridge(): ProjectsBridge | null {
  return bridge()?.projects ?? null;
}

/** Размеры панелей с прошлого запуска; вне оболочки или без файла — пусто. */
export async function loadLayout(): Promise<SavedLayout> {
  try {
    return (await bridge()?.layout?.load()) ?? {};
  } catch {
    return {};
  }
}

/** Запомнить размеры панелей — файл пишет оболочка. */
export function saveLayout(data: SavedLayout): void {
  bridge()?.layout?.save(data);
}

/** Куда сохранить архив — путь называет человек в диалоге ОС. */
export async function chooseArchivePath(): Promise<string | null> {
  const api = bridge();
  if (!api?.chooseArchivePath) return null;
  return api.chooseArchivePath();
}

let cached: Endpoint | null = null;

export async function endpoint(): Promise<Endpoint> {
  if (cached) return cached;
  const api = bridge();
  if (!api?.endpoint) {
    throw new Error(
      "окно открыто не из Datum: токена неоткуда взять. " +
        "Запустите приложение командой npm start",
    );
  }
  cached = await api.endpoint();
  return cached;
}
