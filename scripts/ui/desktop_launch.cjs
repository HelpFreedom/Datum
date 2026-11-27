const assert = require('node:assert/strict');
const { execFile } = require('node:child_process');
const { once } = require('node:events');
const { setTimeout: sleep } = require('node:timers/promises');
const { promisify } = require('node:util');

module.exports = async function run({ app, win }) {
  if (!win.isVisible()) await once(win, 'ready-to-show', { signal: AbortSignal.timeout(15000) });
  const project = win.webContents.getURL();
  win.minimize();
  assert.ok(win.isMinimized(), 'окно свёрнуто перед повторным запуском');

  const env = { ...process.env };
  delete env.DATUM_SCENARIO;
  await Promise.all([
    once(app, 'second-instance', { signal: AbortSignal.timeout(15000) }),
    promisify(execFile)(process.execPath, [process.argv[1]], { env, timeout: 15000 }),
  ]);

  for (let i = 0; i < 20 && win.isMinimized(); i++) await sleep(100);
  assert.ok(!win.isMinimized() && win.isVisible(), 'повторный запуск вернул окно');
  assert.equal(win.webContents.getURL(), project, 'текущий проект сохранён');
  console.log('ОК: повторный запуск вернул окно с тем же проектом');
};
