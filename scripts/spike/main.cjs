// Шип S1+S3: Electron стартует, node-pty работает под его ABI,
// системный claude запускается в pty и видит MCP-конфиг Datum.
const { app, BrowserWindow, ipcMain } = require('electron');
const path = require('path');
const os = require('os');
const fs = require('fs');

const result = { electron: process.versions.electron, chrome: process.versions.chrome,
                 node: process.versions.node, abi: process.versions.modules };
let ptyOut = '';

// временный MCP-конфиг — ровно то, чем Datum будет подключать себя к вашему claude
const mcpCfg = path.join(os.tmpdir(), 'datum-spike-mcp.json');
fs.writeFileSync(mcpCfg, JSON.stringify({
  mcpServers: { datum: { command: '/bin/echo', args: ['datum-mcp-stub'] } }
}, null, 2));
result.mcpConfig = mcpCfg;

function runPty() {
  let pty;
  try { pty = require('node-pty'); }
  catch (e) { result.pty = 'ОШИБКА ЗАГРУЗКИ: ' + e.message; return finish(); }
  result.pty = 'загрузился';

  const sh = pty.spawn('/bin/bash', ['-lc',
      `claude --version; echo "--- mcp list ---"; claude --mcp-config ${mcpCfg} mcp list 2>&1 | head -5`], {
    name: 'xterm-256color', cols: 100, rows: 30,
    cwd: process.env.HOME,
    env: { ...process.env, DISPLAY: process.env.DISPLAY || '',
           HTTP_PROXY: 'http://127.0.0.1:10808', HTTPS_PROXY: 'http://127.0.0.1:10808',
           NO_PROXY: '127.0.0.1,localhost,::1' },
  });
  result.ptyPid = sh.pid;
  sh.onData(d => { ptyOut += d; const w = BrowserWindow.getAllWindows()[0];
                   if (w) w.webContents.send('pty', d); });
  sh.onExit(({ exitCode }) => { result.ptyExit = exitCode; result.ptyOutput = ptyOut.trim(); finish(); });
  setTimeout(() => { try { sh.kill(); } catch {} }, 25000);
}

let done = false;
function finish() {
  if (done) return; done = true;
  setTimeout(() => {
    console.log('\n===DATUM_SPIKE_JSON===');
    console.log(JSON.stringify(result, null, 2));
    app.quit();
  }, 1200);
}

ipcMain.on('gl', (_e, info) => { result.webgl = info; });

app.whenReady().then(() => {
  result.appReady = true;
  const win = new BrowserWindow({
    width: 1000, height: 640, show: true, title: 'Datum — шип',
    webPreferences: { preload: path.join(__dirname, 'preload.cjs'),
                      contextIsolation: true, nodeIntegration: false, sandbox: false },
  });
  win.loadFile(path.join(__dirname, 'spike.html'));
  win.webContents.once('did-finish-load', () => { result.windowLoaded = true; runPty(); });
});
app.on('window-all-closed', () => app.quit());
setTimeout(() => { result.timeout = true; finish(); }, 40000);
