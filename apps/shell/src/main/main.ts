/**
 * Оболочка Datum.
 *
 * Делает ровно три вещи: поднимает Python-сервер, открывает окно на ЕГО
 * происхождении и отдаёт окну токен по IPC. Вся содержательная работа — в
 * сервере, потому что те же операции должны быть доступны из командной строки
 * и агенту.
 *
 * Другой проект открывается В ТОМ ЖЕ ОКНЕ (`switchProject`): новый сервер,
 * охрана — на его происхождение, окно загружается с него, старый сервер
 * гасится. Прежде оболочка перезапускалась целиком, и окно на секунду
 * пропадало с экрана.
 */
import { app, BrowserWindow, dialog, ipcMain, Menu, screen } from "electron";
import * as fs from "node:fs";
import * as path from "node:path";

import { Agent } from "./agent";
import { ARCHIVE_SUFFIX, archiveFromArgv, unpackArchive } from "./archive";
import { Backend, type Handshake } from "./backend";
import { contextSize } from "./context";
import { chooseGpu } from "./gpu";
import { loadLayout, saveLayout } from "./layout";
import { projectFromArgv } from "./paths";
import {
  cacheBytes, clearCache, listProjects, loadRecent, projectCli, rememberRecent, replaceRecent,
  thumbFile,
} from "./projects";
import { RestartBudget } from "./restarts";
import { untitledProject } from "./untitled";
import { lockDownSession, lockDownWindow } from "./security";

const gpuMode = chooseGpu(app);

let backend = new Backend();
const agent = new Agent();
let handshake: Handshake | null = null;
let win: BrowserWindow | null = null;
/** Происхождение окна — меняется вместе с сервером при открытии другого проекта. */
let origin = "";
/** Идёт ли смена проекта: второй запрос, пока первый не кончился, — отказ. */
let switching = false;
/** Падения процесса отрисовки: столько перезагрузок за окно времени, потом — отказ. */
const MAX_RELOADS = 3;
const CRASH_WINDOW_MS = 2 * 60_000;
let crashes: number[] = [];
/** Сервер проекта, умерший сам: столько раз поднимаем заново, потом — отказ. */
const serverRestarts = new RestartBudget(3, 5 * 60_000);
/** Приложение закрывается (меню, сигнал) — окно о работающем агенте не спрашивает. */
let quitting = false;

// Токен живёт ТОЛЬКО здесь и в окне. Наружу — ни строкой запроса, ни
// аргументом, ни переменной окружения.
ipcMain.handle("datum:endpoint", () => {
  if (!handshake) throw new Error("сервер ещё не готов");
  return { token: handshake.token, project: handshake.project };
});

// Выбор файла. Диалог открывает ОС, окно не получает ни списка каталогов, ни
// права читать что-либо само: только путь, который человек назвал сам.
ipcMain.handle("datum:pick-file", async () => {
  if (!win) return null;
  const result = await dialog.showOpenDialog(win, {
    title: "Чужая модель",
    properties: ["openFile"],
    filters: [
      { name: "Меши", extensions: ["stl", "3mf", "obj", "ply"] },
      { name: "Все файлы", extensions: ["*"] },
    ],
  });
  return result.canceled ? null : (result.filePaths[0] ?? null);
});

// ------------------------------------------------------------- проект
// Другой проект — в том же окне: `switchProject`. Пока новый сервер
// поднимается, окно показывает прежний проект и «Открываю…» (имя приходит
// обратным вызовом); не поднялся — прежний проект остаётся, с объяснением.
ipcMain.handle("datum:open-project", async () => {
  if (!win) return { ok: false, error: "окна нет" };
  const result = await dialog.showOpenDialog(win, {
    title: "Открыть проект Datum",
    properties: ["openDirectory"],
  });
  if (result.canceled || !result.filePaths[0]) return { ok: false, error: "" };
  const chosen = result.filePaths[0];
  if (!fs.existsSync(path.join(chosen, "datum.toml"))) {
    return {
      ok: false,
      error: `${chosen} — не проект Datum: в нём нет datum.toml. ` +
        `Новый проект: datum init ${chosen}`,
    };
  }
  return switchProject(chosen);
});

// Архив .datum: распаковать рядом с ним (или открыть уже распакованный) и
// открыть каталог — той же дорогой, что и папку.
ipcMain.handle("datum:open-archive", async () => {
  if (!win) return { ok: false, error: "окна нет" };
  const result = await dialog.showOpenDialog(win, {
    title: "Открыть архив проекта",
    properties: ["openFile"],
    filters: [{ name: "Проект Datum", extensions: ["datum"] }],
  });
  if (result.canceled || !result.filePaths[0]) return { ok: false, error: "" };
  win.webContents.send("datum:switching", path.basename(result.filePaths[0]));
  const unpacked = await unpackArchive(result.filePaths[0]);
  if (!unpacked.ok) return { ok: false, error: unpacked.error };
  return switchProject(unpacked.project);
});

/**
 * Открыть проект в том же окне. Порядок важен: сначала НОВЫЙ сервер (не
 * поднялся — прежний проект на месте), потом охрана сессии на его
 * происхождение, загрузка окна с него — и только после этого прежний сервер
 * гасится. `claude` останавливается: он работал в каталоге прежнего проекта и
 * говорил с прежним сервером; снова он запускается по просьбе, как и при
 * открытии окна.
 */
async function switchProject(
  project: string, keepAgent = false,
): Promise<{ ok: boolean; error: string }> {
  if (!win) return { ok: false, error: "окна нет" };
  if (switching) return { ok: false, error: "проект уже открывается" };
  switching = true;
  try {
    win.webContents.send("datum:switching", path.basename(project));
    const next = new Backend();
    let shake: Handshake;
    try {
      shake = await next.start(project);
    } catch (e) {
      return { ok: false, error: e instanceof Error ? e.message : String(e) };
    }
    watchBackend(next);
    // Картинка для списка проектов — пока окно ещё показывает прежний.
    if (!keepAgent) await saveThumb();
    // Тот же проект после гибели сервера — `claude` остаётся: его MCP-клиент
    // выбирает сервер на каждый вызов и найдёт новый по объявлению проекта.
    if (!keepAgent) agent.stop();
    const previous = backend;
    backend = next;
    handshake = shake;
    origin = `http://127.0.0.1:${shake.port}`;
    crashes = [];
    lockDownSession(origin);
    rememberRecent(shake.project);
    console.log(`Datum: проект ${project}, сервер ${origin}`);
    try {
      await win.loadURL(`${origin}/`);
    } catch (e) {
      // Прерванная загрузка (ERR_ABORTED) — не отказ: окно уже на новом сервере.
      console.error(`  окно: ${e instanceof Error ? e.message : String(e)}`);
    }
    previous.stop();
    return { ok: true, error: "" };
  } finally {
    switching = false;
  }
}

// ------------------------------------------------------------- проекты
// Диалог «Файл → Проекты…». Окно путей не называет: оно получает список с
// метками и просит «открой метку»; оболочка сверяет её со своим списком.
let projectPaths = new Map<string, string>();

/** Снимок окна — картинкой проекта в `.datum/thumb.png`. Не вышло — не беда. */
async function saveThumb(): Promise<void> {
  const project = handshake?.project;
  if (!win || win.isDestroyed() || !project) return;
  // Только в существующий проект: папку могли только что переименовать, и
  // `mkdir -p` по прежнему пути воссоздал бы её пустой (поймано сценарием).
  if (!fs.existsSync(path.join(project, "datum.toml"))) return;
  try {
    const image = await win.webContents.capturePage();
    if (image.isEmpty()) return;
    const file = thumbFile(project);
    fs.mkdirSync(path.dirname(file), { recursive: true });
    fs.writeFileSync(file, image.resize({ width: 480, quality: "good" }).toPNG());
  } catch (e) {
    console.error(`  картинка проекта: ${e instanceof Error ? e.message : String(e)}`);
  }
}

function refreshProjects() {
  const listed = listProjects(handshake?.project ?? "", loadRecent());
  projectPaths = listed.paths;
  return listed.entries;
}

/** Путь по метке — только из списка, который оболочка составила сама. */
function projectById(id: unknown): string | null {
  if (typeof id !== "string") return null;
  if (!projectPaths.has(id)) refreshProjects();
  return projectPaths.get(id) ?? null;
}

function isCurrent(dir: string): boolean {
  try {
    return Boolean(handshake) && fs.realpathSync(handshake!.project) === fs.realpathSync(dir);
  } catch {
    return false;
  }
}

ipcMain.handle("datum:projects:list", async () => {
  await saveThumb();                       // у открытого проекта картинка — свежая
  return refreshProjects();
});

ipcMain.handle("datum:projects:thumb", (_event, id: unknown) => {
  const dir = projectById(id);
  if (!dir) return null;
  try {
    const file = thumbFile(dir);
    return fs.statSync(file).size <= 4 * 1024 * 1024 ? new Uint8Array(fs.readFileSync(file)) : null;
  } catch {
    return null;
  }
});

ipcMain.handle("datum:projects:cache", async (_event, id: unknown) => {
  const dir = projectById(id);
  return dir ? cacheBytes(dir) : 0;
});

ipcMain.handle("datum:projects:clear-cache", async (_event, id: unknown) => {
  const dir = projectById(id);
  if (!dir) return { ok: false, error: "такого проекта в списке нет" };
  // У открытого проекта кешем пользуется работающий сервер: вынимать его из-под
  // идущей сборки нельзя.
  if (isCurrent(dir)) {
    return { ok: false, error: "проект открыт: его кеш чистит сам Datum, когда тот перерастает бюджет" };
  }
  await clearCache(dir);
  return { ok: true, error: "" };
});

ipcMain.handle("datum:projects:open", async (_event, id: unknown) => {
  const dir = projectById(id);
  if (!dir) return { ok: false, error: "такого проекта в списке нет" };
  if (isCurrent(dir)) return { ok: true, error: "" };
  return switchProject(dir);
});

/**
 * Переименовать проект: название И папку (решение владельца). У открытого
 * проекта для этого останавливаются агент и сервер (каталог уезжает из-под
 * них), папку и datum.toml правит `datum rename`, и проект открывается снова
 * уже по новому пути. Не вышло — открывается как был.
 */
ipcMain.handle("datum:projects:rename", async (_event, id: unknown, name: unknown) => {
  const dir = projectById(id);
  const title = typeof name === "string" ? name.trim() : "";
  if (!dir) return { ok: false, error: "такого проекта в списке нет" };
  if (!title) return { ok: false, error: "название пустое" };
  if (!isCurrent(dir)) {
    const done = await projectCli("rename", dir, title);
    if (done.ok) replaceRecent(dir, done.path);
    return { ok: done.ok, error: done.ok ? "" : done.error };
  }
  if (!win) return { ok: false, error: "окна нет" };
  if (switching) return { ok: false, error: "проект уже открывается" };
  win.webContents.send("datum:switching", title);
  await saveThumb();                       // пока проект ещё на прежнем месте
  agent.stop();
  const stopped = backend;
  stopped.onDeath = null;                  // свой выход — не «сервер умер»
  await stopped.stopAndWait();
  const done = await projectCli("rename", dir, title);
  const target = done.ok ? done.path : dir;
  if (done.ok) replaceRecent(dir, target);
  const opened = await switchProject(target);
  if (!opened.ok) {
    fatal("Проект не открылся после переименования",
      `${opened.error}\n\nПроект лежит в ${target}: откройте его «Файл → Открыть проект…».`);
    return opened;
  }
  if (!done.ok) {
    // Окно уже загрузилось заново, и ответ этого вызова прежняя страница не
    // получит: причина — в журнал и человеку, диалогом ОС.
    console.error(`  проект не переименован: ${done.error}`);
    if (!process.env.DATUM_SCENARIO && win && !win.isDestroyed()) {
      void dialog.showMessageBox(win, {
        type: "warning", title: "Datum", message: "Проект не переименован",
        detail: `${done.error}\n\nПроект открыт как был.`,
      });
    }
  }
  return { ok: done.ok, error: done.ok ? "" : done.error };
});

/** «Новый из этого»: копия без кеша и выгрузки рядом, под новым названием. */
ipcMain.handle("datum:projects:copy", async (_event, id: unknown, name: unknown) => {
  const dir = projectById(id);
  const title = typeof name === "string" ? name.trim() : "";
  if (!dir) return { ok: false, error: "такого проекта в списке нет" };
  if (!title) return { ok: false, error: "название пустое" };
  if (!win) return { ok: false, error: "окна нет" };
  win.webContents.send("datum:switching", title);
  const done = await projectCli("copy", dir, title);
  if (!done.ok) return { ok: false, error: done.error };
  return switchProject(done.path);
});

/**
 * Сервер проекта умер сам — поднять заново тот же проект в том же окне.
 * Разговор агента и файлы проекта целы; потеряны только идущие задания.
 */
function watchBackend(watched: Backend): void {
  watched.onDeath = (why) => {
    if (watched !== backend || quitting) return;
    const project = handshake?.project;
    console.error(`  сервер проекта умер: ${why}`);
    if (!project || !win || win.isDestroyed()) return;
    if (!serverRestarts.allow()) {
      fatal("Сервер проекта падает раз за разом",
        `сервер остановился ${serverRestarts.count + 1}-й раз за пять минут (${why}). ` +
        `Проект и разговор агента сохранены на диске. Частая причина — нехватка памяти ` +
        `на тяжёлой детали: закройте лишнее и откройте проект снова; подробности — ` +
        `в ~/.local/state/datum/window.log.`);
      return;
    }
    console.error("  сервер проекта поднимается заново");
    void switchProject(project, true).then((result) => {
      if (!result.ok && result.error) {
        fatal("Сервер проекта не поднялся заново", result.error);
      }
    });
  };
}

// Куда сохранить архив. Путь называет человек в диалоге ОС; окно предлагает
// только имя, а каталог по умолчанию — рядом с проектом.
ipcMain.handle("datum:save-archive-path", async () => {
  if (!win || !handshake) return null;
  const project = handshake.project;
  const result = await dialog.showSaveDialog(win, {
    title: "Сохранить проект как",
    defaultPath: path.join(path.dirname(project), path.basename(project) + ARCHIVE_SUFFIX),
    filters: [{ name: "Проект Datum", extensions: ["datum"] }],
    properties: ["showOverwriteConfirmation", "createDirectory"],
  });
  if (result.canceled || !result.filePath) return null;
  const chosen = result.filePath;
  return chosen.toLowerCase().endsWith(ARCHIVE_SUFFIX) ? chosen : chosen + ARCHIVE_SUFFIX;
});

// ------------------------------------------------------------- раскладка
// Размеры панелей — в файле оболочки (`layout.ts`): у окна нет постоянного
// хранилища, его происхождение меняется с портом сервера.
ipcMain.handle("datum:layout:load", () => loadLayout());
ipcMain.on("datum:layout:save", (_event, data: unknown) => saveLayout(data));

// ------------------------------------------------------------- терминал
// Мост до системного агента. Поверхность узкая нарочно: окно исполняет код,
// собранный из исходников проекта, и чем меньше ему доступно, тем меньше цена
// ошибки в нём.
ipcMain.handle("datum:agent:start", (_event, size: { cols: number; rows: number; kind?: unknown }) => {
  if (!handshake) return { ok: false, error: "сервер ещё не готов" };
  const kind = size.kind ?? "claude";
  if (kind !== "claude" && kind !== "codex") {
    return { ok: false, error: "неизвестный агент" };
  }
  return agent.start(
    { port: handshake.port, token: handshake.token, project: handshake.project },
    size,
    (chunk) => win?.webContents.send("datum:agent:data", chunk),
    (code) => win?.webContents.send("datum:agent:exit", code),
    kind,
  );
});
ipcMain.handle("datum:agent:status", () => ({ running: agent.running, kind: agent.kind }));
// Сколько контекста занято у идущего `claude` — число для строки состояния
// (из журнала его сессии; текст разговора окну не отдаётся). Минута запаса на
// расхождение часов записи: журнал, не тронутый с запуска, — прошлая сессия.
ipcMain.handle("datum:agent:context", () => {
  if (agent.kind !== "claude" || !handshake) return null;
  return contextSize(handshake.project, agent.startedAt - 60_000);
});
ipcMain.on("datum:agent:write", (_event, data: string) => agent.write(data));
ipcMain.on("datum:agent:resize", (_event, cols: number, rows: number) =>
  agent.resize(cols, rows));
ipcMain.on("datum:agent:stop", () => agent.stop());

async function createWindow(): Promise<void> {
  // Окно открывается на ОСНОВНОМ экране и по его размеру. Без этого Electron
  // сажает его туда, где начало координат рабочего стола, — у портретного
  // второго монитора это дало окно 1029 пикселей шириной, где панели съели
  // вьюпорт.
  const area = screen.getPrimaryDisplay().workArea;
  const width = Math.min(1600, Math.round(area.width * 0.9));
  const height = Math.min(1000, Math.round(area.height * 0.9));

  win = new BrowserWindow({
    x: area.x + Math.round((area.width - width) / 2),
    y: area.y + Math.round((area.height - height) / 2),
    width,
    height,
    minWidth: 900,
    minHeight: 600,
    show: false,
    backgroundColor: "#06090e",
    title: "Datum",
    webPreferences: {
      preload: path.join(__dirname, "..", "preload", "index.js"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      webSecurity: true,
      spellcheck: false,
    },
  });

  lockDownWindow(win, () => origin);

  // Консоль окна — в терминал. Без этого ошибка в рендерере видна только если
  // человек сам откроет инструменты разработчика, то есть обычно никогда.
  win.webContents.on("console-message", (...args: unknown[]) => {
    const first = args[0] as { level?: unknown; message?: unknown } | undefined;
    const structured = first && typeof first === "object" && "message" in first;
    const level = structured ? String(first.level ?? "") : String(args[1] ?? "");
    const text = structured ? String(first.message ?? "") : String(args[2] ?? "");
    console.log(`  окно [${level}] ${text}`);
  });
  // Процесс отрисовки убит (чаще всего — нехваткой памяти): окно
  // перезагружается само. Сервер проекта и claude живут в этом процессе и
  // его потомках, так что страница подключается к ним же — проект, агент и
  // его разговор на месте. Падает снова и снова — не крутимся, а говорим.
  win.webContents.on("render-process-gone", (_e, details) => {
    console.error(`  окно упало: ${details.reason}`);
    if (details.reason === "clean-exit") return;
    const now = Date.now();
    crashes = crashes.filter((t) => now - t < CRASH_WINDOW_MS);
    crashes.push(now);
    if (crashes.length > MAX_RELOADS) {
      fatal("Окно падает раз за разом",
        `процесс отрисовки убит ${crashes.length} раза за ${CRASH_WINDOW_MS / 60_000} мин ` +
        `(${details.reason}). Проект и разговор агента сохранены на диске: откройте ` +
        `проект снова и возобновите сеанс командой вашего агента.`);
      return;
    }
    setTimeout(() => {
      if (win && !win.isDestroyed()) {
        console.error("  окно перезагружается");
        win.webContents.reload();
      }
    }, 500);
  });

  win.once("ready-to-show", () => win?.show());

  // Сценарий проверки. Одна точка входа вместо россыпи переменных окружения:
  // модуль ЗА пределами приложения получает окно и делает с ним что нужно —
  // снимки, нажатия, клики. Так рабочий код не обрастает отладочными ветками,
  // а проверять окно без человека у экрана всё равно можно.
  const scenario = process.env.DATUM_SCENARIO;
  if (scenario) {
    type Scenario = (ctx: { win: BrowserWindow; app: typeof app }) => Promise<void>;
    let run: Scenario;
    try {
      // eslint-disable-next-line @typescript-eslint/no-var-requires
      run = require(path.resolve(scenario)) as Scenario;
    } catch (e) {
      // Неверный путь к сценарию — не «Datum не запустился»: так однажды и
      // всплыло окно с ошибкой на экране человека.
      fatal("Сценарий не загрузился", e instanceof Error ? e.message : String(e));
      return;
    }
    // Упавший сценарий — код 1: прежде он выходил с 0, и `make ui` мог
    // отчитаться об успехе проверки, которая не дошла до конца.
    void Promise.resolve()
      .then(() => run({ win: win as BrowserWindow, app }))
      .then(() => app.quit(), (e: unknown) => {
        console.error("сценарий упал:", e);
        agent.stop();
        backend.stop();
        app.exit(1);
      });
  }

  // Крестик окна при работающем агенте — с вопросом: закрытие останавливает
  // `claude` посреди работы (разговор потом возвращается через /resume, а
  // начатое им действие — нет). Закрытие самого приложения (сигнал, сценарий)
  // не спрашивает: там ждать ответа некому.
  win.on("close", (event) => {
    if (quitting || process.env.DATUM_SCENARIO || !agent.running || !win) return;
    event.preventDefault();
    const answer = dialog.showMessageBoxSync(win, {
      type: "question",
      title: "Datum",
      message: `${agent.kind === "codex" ? "Codex" : "Claude Code"} ещё работает`,
      detail: "Закрытие окна остановит агента. Проект останется на диске. " +
        "Разговор можно продолжить командой возобновления сеанса.",
      buttons: ["Закрыть и остановить", "Остаться"],
      defaultId: 1,
      cancelId: 1,
    });
    if (answer === 0) {
      quitting = true;
      win.close();
    }
  });
  win.on("closed", () => {
    win = null;
  });
  await win.loadURL(`${origin}/`);
}

app.whenReady().then(async () => {
  // Меню Electron по умолчанию (File, Edit, View…) — НЕ наше: своё меню окно
  // рисует само (chrome/Menu.tsx), и две строки меню, одна из них английская,
  // были бы ошибкой. Его ускорители тоже уходят, и это к лучшему: Ctrl+R
  // перезагружал бы окно посреди работы, а Ctrl+± менял масштаб, который
  // Chromium запоминает для хоста. Правка в полях (копировать, вставить)
  // от меню не зависит — её ведёт сам Chromium.
  Menu.setApplicationMenu(null);
  let project = projectFromArgv(process.argv);
  // `datum проект.datum` (dmenu, файловый менеджер): распаковать и открыть.
  const archive = archiveFromArgv(process.argv);
  if (archive) {
    const unpacked = await unpackArchive(archive);
    if (!unpacked.ok) {
      fatal("Архив не открылся", unpacked.error);
      return;
    }
    project = unpacked.project;
  }
  // Ни проекта, ни архива — пустой проект «Без названия».
  if (!project) {
    const untitled = await untitledProject();
    if (!untitled.ok) {
      fatal("Пустой проект не создался", untitled.error);
      return;
    }
    project = untitled.project;
  }
  try {
    handshake = await backend.start(project);
  } catch (e) {
    fatal("Datum не запустился", e instanceof Error ? e.message : String(e));
    return;
  }
  watchBackend(backend);
  rememberRecent(handshake.project);

  origin = `http://127.0.0.1:${handshake.port}`;
  lockDownSession(origin);
  await createWindow();
  console.log(`Datum: проект ${project}, сервер ${origin}, графика ${gpuMode}`);
}).catch((e: unknown) => {
  fatal("Datum не запустился", e instanceof Error ? e.stack ?? e.message : String(e));
});

/**
 * Отказ на старте. Причина пишется в журнал ВСЕГДА: диалог закрыли — и текста
 * больше нет, так однажды и потерялась причина. В прогоне сценария диалога
 * нет вовсе: модальное окно висело бы на экране человека и держало процесс,
 * а сценарию нужен код выхода.
 */
function fatal(title: string, message: string): void {
  quitting = true;
  console.error(`${title}: ${message}`);
  if (process.env.DATUM_SCENARIO) {
    app.exit(1);
    return;
  }
  dialog.showErrorBox(title, message);
  app.quit();
}

// Сервер обязан умереть вместе с окном: иначе он останется сиротой, держа
// порт и процессы сборки.
app.on("window-all-closed", () => app.quit());
app.on("before-quit", () => {
  quitting = true;
  agent.stop();
  backend.stop();
});
process.on("exit", () => {
  agent.stop();
  backend.stop();
});

// Сигналы обязаны вести к обычному закрытию, а не к обрыву: иначе `before-quit`
// не выполняется и потомки остаются сиротами. Второй рубеж (сторож родителя в
// самом сервере) всё равно нужен — на случай SIGKILL, который не перехватить.
for (const signal of ["SIGTERM", "SIGINT", "SIGHUP"] as const) {
  process.on(signal, () => app.quit());
}
