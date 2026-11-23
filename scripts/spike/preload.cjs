const { contextBridge, ipcRenderer } = require('electron');
contextBridge.exposeInMainWorld('spike', {
  onPty: (cb) => ipcRenderer.on('pty', (_e, d) => cb(d)),
  reportGL: (info) => ipcRenderer.send('gl', info),
});
