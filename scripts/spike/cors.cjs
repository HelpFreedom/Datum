// Шип: может ли окно на app:// стучаться в Python-сервер на http://127.0.0.1,
// если тот намеренно не отдаёт ни одного заголовка Access-Control-*.
const { app, BrowserWindow, ipcMain, protocol, net, session } = require('electron');
const path = require('path');

const BASE = process.env.DATUM_BASE;     // http://127.0.0.1:<порт>
const TOKEN = process.env.DATUM_TOKEN;
const result = { base: BASE, electron: process.versions.electron };

protocol.registerSchemesAsPrivileged([{
  scheme: 'app',
  privileges: { standard: true, secure: true, supportFetchAPI: true, stream: true },
}]);

const PROBE = `(async (base, token) => {
  const out = {};
  try { const r = await fetch(base + '/healthz'); out['простой GET /healthz'] = r.status; }
  catch (e) { out['простой GET /healthz'] = 'ОТКАЗ: ' + e.message; }
  try { const r = await fetch(base + '/api/ops', { headers: { 'X-Datum-Token': token } });
        out['GET /api/ops с токеном'] = r.status + ' (' + (await r.text()).length + ' Б)'; }
  catch (e) { out['GET /api/ops с токеном'] = 'ОТКАЗ: ' + e.message; }
  try { const r = await fetch(base + '/api/ops/datum_status', { method: 'POST',
          headers: { 'X-Datum-Token': token, 'X-Datum-Client': '1', 'Content-Type': 'application/json' },
          body: '{}' });
        out['POST операции'] = r.status; }
  catch (e) { out['POST операции'] = 'ОТКАЗ: ' + e.message; }
  out['WebSocket'] = await new Promise(res => {
    let ws; try { ws = new WebSocket(base.replace('http', 'ws') + '/ws'); }
    catch (e) { return res('ОТКАЗ: ' + e.message); }
    ws.onopen = () => { ws.close(); res('открылся'); };
    ws.onerror = () => res('ОТКАЗ');
    setTimeout(() => res('таймаут'), 3000);
  });
  return out;
})(BASE_, TOKEN_)`;

const PAGE = '<!doctype html><meta charset="utf-8"><title>шип</title><body>шип CORS';

let injecting = false;

app.whenReady().then(async () => {
  protocol.handle('app', () => new Response(PAGE, {
    headers: { 'content-type': 'text/html; charset=utf-8' } }));

  session.defaultSession.webRequest.onHeadersReceived(
    { urls: ['http://127.0.0.1/*', 'http://127.0.0.1:*/*'] },
    (details, cb) => {
      if (!injecting) return cb({});
      const h = { ...details.responseHeaders };
      h['Access-Control-Allow-Origin'] = ['app://datum'];
      h['Access-Control-Allow-Headers'] = ['X-Datum-Token, X-Datum-Client, Content-Type'];
      h['Access-Control-Allow-Methods'] = ['GET, POST, OPTIONS'];
      h['Access-Control-Max-Age'] = ['600'];
      // Ответ на предварительную проверку сервер даёт 403 — подменим статус,
      // чтобы увидеть, достаточно ли этого.
      const isPreflight = details.method === 'OPTIONS';
      cb({ responseHeaders: h,
           statusLine: isPreflight ? 'HTTP/1.1 204 No Content' : undefined });
    });

  const win = new BrowserWindow({ show: false, width: 900, height: 600,
    webPreferences: { contextIsolation: true, nodeIntegration: false, sandbox: true } });
  await win.loadURL('app://datum/');

  const run = () => win.webContents.executeJavaScript(
    PROBE.replace('BASE_', JSON.stringify(BASE)).replace('TOKEN_', JSON.stringify(TOKEN)));

  result['A. окно на app://'] = { origin: await win.webContents.executeJavaScript('location.origin') };
  result['A. окно на app://']['без вмешательства'] = await run();
  injecting = true;
  result['A. окно на app://']['оболочка добавляет CORS'] = await run();
  injecting = false;

  // B. То же окно, но происхождение — сам сервер. Страницей служит /healthz:
  // так проверяется именно ORIGIN, без раздачи статики.
  await win.loadURL(BASE + '/healthz');
  result['B. окно на самом сервере'] = {
    origin: await win.webContents.executeJavaScript('location.origin'),
    'без вмешательства': await run(),
  };

  console.log('\n===DATUM_CORS_JSON===');
  console.log(JSON.stringify(result, null, 2));
  app.quit();
}).catch(e => { console.log('ШИП УПАЛ:', e); app.quit(); });

app.on('window-all-closed', () => app.quit());
