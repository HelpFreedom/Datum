// Шип S1: реальный WebGL2 в Electron на живом X-сервере.
const { app, BrowserWindow, ipcMain } = require('electron');
const path = require('path');
const out = { electron: process.versions.electron, chrome: process.versions.chrome };

// Флаги Chromium из окружения: только так их можно подать до инициализации GPU.
const extra = (process.env.DATUM_GL_FLAGS || '').split(/\s+/).filter(Boolean);
out.flags = extra;
for (const f of extra) {
  const [k, v] = f.replace(/^--/, '').split('=');
  if (v === undefined) app.commandLine.appendSwitch(k);
  else app.commandLine.appendSwitch(k, v);
}
let done = false;

ipcMain.on('gl', (_e, info) => {
  out.gl = info;
  if (done) return; done = true;
  console.log('\n===GL_JSON===');
  console.log(JSON.stringify(out, null, 2));
  setTimeout(() => app.quit(), 300);
});

app.whenReady().then(() => {
  const win = new BrowserWindow({
    width: 900, height: 560, title: 'Datum — замер GL',
    webPreferences: { preload: path.join(__dirname, 'gl-preload.cjs'),
                      contextIsolation: true, nodeIntegration: false, sandbox: false },
  });
  win.loadFile(path.join(__dirname, 'gl.html'));
});
setTimeout(() => { if (!done) { console.log('\n===GL_JSON===');
  console.log(JSON.stringify({ ...out, timeout: true }, null, 2)); app.quit(); } }, 25000);
