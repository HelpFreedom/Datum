/**
 * Окно переживает гибель процесса отрисовки (так его убивает нехватка памяти —
 * SIGKILL, им сценарий и убивает): перезагружается само, а терминал подключается к ТОМУ ЖЕ claude — с прежним
 * экраном и живым вводом. Проект — от того же сервера.
 *
 * claude — поддельный (DATUM_CLAUDE=scripts/ui/fake-claude.sh): проверяется
 * труба, а не агент. Код 1 — если что-то не пережило падения.
 */
const sleep = (t) => new Promise((r) => setTimeout(r, t));

module.exports = async function run({ win, app }) {
  // Мёртвая страница на запрос не отвечает вовсе: без предела сценарий
  // висел бы, а не проваливался.
  const js = (c) => Promise.race([win.webContents.executeJavaScript(c), sleep(5000).then(() => null)]);
  // Не ответила — пустой экран: проверка скажет «ПРОВАЛ», а не упадёт на null.
  const screen = async () => (await js(`(document.querySelector('.xterm-rows') || {}).innerText || ''`)) ?? '';
  const type = async (text) => {
    for (const ch of text) {
      win.webContents.sendInputEvent({ type: 'keyDown', keyCode: ch });
      win.webContents.sendInputEvent({ type: 'char', keyCode: ch });
      win.webContents.sendInputEvent({ type: 'keyUp', keyCode: ch });
      await sleep(40);
    }
    win.webContents.sendInputEvent({ type: 'keyDown', keyCode: 'Return' });
    win.webContents.sendInputEvent({ type: 'char', keyCode: 'Return' });
    win.webContents.sendInputEvent({ type: 'keyUp', keyCode: 'Return' });
    await sleep(600);
  };
  const results = [];
  const check = (name, ok, detail = '') => {
    results.push(ok);
    console.log(`   ${ok ? 'ОК  ' : 'ПРОВАЛ'} ${name.padEnd(36)} ${detail}`);
  };

  await sleep(8000);
  // Журнал сессии «claude» — свой, в CLAUDE_CONFIG_DIR сценария: по нему строка
  // состояния показывает размер контекста агента. Настоящих журналов сценарий
  // не читает и не пишет.
  const config = process.env.CLAUDE_CONFIG_DIR;
  if (config) {
    const fs = require('node:fs');
    const path = require('node:path');
    const { project } = await js('window.datum.endpoint()');
    const dir = path.join(config, 'projects', project.replace(/[^A-Za-z0-9]/g, '-'));
    fs.mkdirSync(dir, { recursive: true });
    fs.writeFileSync(path.join(dir, 'session.jsonl'), JSON.stringify({
      type: 'assistant', isSidechain: false, timestamp: new Date().toISOString(),
      message: { usage: { input_tokens: 2, cache_read_input_tokens: 417000, cache_creation_input_tokens: 998 } },
    }) + '\n');
  }
  await js(`[...document.querySelectorAll('button')].find(b => b.innerText.includes('Запустить Claude Code')).click()`);
  await sleep(2500);
  if (config) {
    const note = await js(`(() => { const e = document.querySelector('[data-status="context"]');
      return e ? { text: e.textContent, hint: e.title, warn: e.classList.contains('warn') } : null; })()`);
    check('контекст агента в строке состояния',
      Boolean(note) && note.text === 'контекст 418 тыс.' && note.warn && /42 тыс\. свежих/.test(note.hint),
      note ? note.text : 'строки нет');
  }
  await js(`document.querySelector('.xterm-helper-textarea')?.focus()`);
  await type('abc');
  const before = await screen();
  check('терминал до падения', before.includes('до падения') && before.includes('эхо: abc'));
  const parts = await js(`document.querySelectorAll('.part .name').length`);

  // Убиваем SIGKILL — так процесс отрисовки убивает нехватка памяти, ради
  // которой восстановление и сделано. `forcefullyCrashRenderer` для этого не
  // годится: он роняет процесс аварийно (FATAL «Crashing because hung»), и
  // systemd-coredump ~45 с пишет его дамп — всё это время мёртвый процесс
  // числится живым, `render-process-gone` не приходит, а в системе после
  // каждого прогона оставался дамп. Замерено: с ним окно поднималось через
  // 52 с, с SIGKILL — через 0.6 с.
  const old = win.webContents.getOSProcessId();
  process.kill(old, 'SIGKILL');
  // Ждём не вслепую, а новую страницу: другой процесс и ответ на вопрос.
  let reloaded = false;
  for (let i = 0; i < 60 && !reloaded; i++) {
    await sleep(500);
    const now = win.webContents.getOSProcessId();
    reloaded = now !== 0 && now !== old && (await js(`document.readyState`)) === 'complete';
  }
  check('окно перезагрузилось само', reloaded);
  await sleep(4000);                                   // подключение терминала и проекта

  const after = await screen();
  check('экран терминала после падения', after.includes('до падения') && after.includes('эхо: abc'));
  await js(`document.querySelector('.xterm-helper-textarea')?.focus()`);
  await type('xyz');
  check('ввод идёт тому же claude', (await screen()).includes('эхо: xyz'));
  const partsAfter = await js(`document.querySelectorAll('.part .name').length`);
  check('проект на месте', partsAfter === parts && parts > 0, `${partsAfter} дет.`);

  const ok = results.every(Boolean);
  console.log(ok ? 'Окно переживает падение отрисовки.' : 'Падение отрисовки не пережито.');
  app.exit(ok ? 0 : 1);
};
