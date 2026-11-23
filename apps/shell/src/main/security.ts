/**
 * Защита браузера внутри оболочки.
 *
 * Окно живёт на том же происхождении, что и сервер (http://127.0.0.1:<порт>),
 * поэтому ни CORS, ни подмены заголовков здесь нет и не нужно. Задача этого
 * модуля другая: не дать окну стать обычным браузером — уйти на посторонний
 * сайт, открыть второе окно, спросить камеру или выполнить чужой скрипт.
 */
import { type BrowserWindow, session, shell } from "electron";

import { navigationVerdict } from "./navigation";

/**
 * Повторный вызов (другой проект в том же окне) ЗАМЕНЯЕТ прежние обработчики:
 * у `webRequest` и обработчиков разрешений действует только последний.
 */
export function lockDownSession(origin: string): void {
  const s = session.defaultSession;

  // Никаких разрешений: ни камеры, ни микрофона, ни геолокации, ни
  // уведомлений, ни чтения буфера обмена. Инструменту проектирования они не
  // нужны ни одного.
  s.setPermissionRequestHandler((_wc, _permission, callback) => callback(false));
  s.setPermissionCheckHandler(() => false);

  s.webRequest.onHeadersReceived((details, callback) => {
    callback({
      responseHeaders: {
        ...details.responseHeaders,
        "Content-Security-Policy": [
          [
            "default-src 'none'",
            "script-src 'self'",
            // Vite вставляет стили тегом, поэтому inline-стили разрешены.
            // Скрипты — нет: именно они опасны.
            "style-src 'self' 'unsafe-inline'",
            `img-src 'self' data: blob: ${origin}`,
            "font-src 'self'",
            `connect-src ${origin} ${origin.replace(/^http/, "ws")}`,
            "worker-src 'self' blob:",
            "form-action 'none'",
            "base-uri 'none'",
            "frame-ancestors 'none'",
          ].join("; "),
        ],
      },
    });
  });
}

/**
 * `origin` — функцией: происхождение окна меняется, когда в том же окне
 * открывают другой проект (новый сервер — новый порт), и охрана навигации
 * обязана сверять со ТЕКУЩИМ, а не с тем, что было при создании окна.
 */
export function lockDownWindow(win: BrowserWindow, origin: () => string): void {
  // Второе окно не открывается никогда. Ссылка наружу уходит в системный
  // браузер, и только если это http(s): «file:» открыл бы что угодно с диска.
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (navigationVerdict(url, origin()) === "наружу") void shell.openExternal(url);
    return { action: "deny" };
  });

  // Уход СО СТРАНИЦЫ: клик по ссылке, form, присваивание location. Вызов
  // `loadURL` из самой оболочки сюда не попадает — и не должен: так ходит
  // только собственный код, открывающий окно на происхождении сервера.
  win.webContents.on("will-navigate", (event, url) => {
    const verdict = navigationVerdict(url, origin());
    if (verdict === "свой") return;
    event.preventDefault();
    if (verdict === "наружу") void shell.openExternal(url);
  });

  // Чужой preload может прийти только из подделанного окна; запрещаем явно.
  win.webContents.on("will-attach-webview", (event) => event.preventDefault());
}
