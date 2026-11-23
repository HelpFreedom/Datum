const { app } = require('electron');
app.whenReady().then(() => {
  console.log('ABI electron:', process.versions.modules, '| electron', process.versions.electron);
  let pty;
  try { pty = require('node-pty'); }
  catch (e) { console.log('НЕ ЗАГРУЗИЛСЯ:', e.message.split('\n')[0]); return app.quit(); }
  const sh = pty.spawn('/bin/bash', ['-lc',
      'echo привет из pty; claude --version'],
    { name: 'xterm-256color', cols: 80, rows: 24, cwd: process.env.HOME, env: process.env });
  let out = '';
  sh.onData(d => { out += d; });
  sh.onExit(({ exitCode }) => {
    console.log('pty работает, код выхода', exitCode);
    console.log(out.trim().split('\n').map(l => '  ' + l).join('\n'));
    app.quit();
  });
  setTimeout(() => { try { sh.kill(); } catch {} ; app.quit(); }, 25000);
});
