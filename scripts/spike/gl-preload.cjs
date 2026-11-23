const { contextBridge, ipcRenderer } = require('electron');
contextBridge.exposeInMainWorld('spike', { report: (i) => ipcRenderer.send('gl', i) });
