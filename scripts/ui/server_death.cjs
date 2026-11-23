/**
 * Окно переживает гибель СЕРВЕРА проекта (аудит 2026-11-21).
 *
 * Память ест сервер, и ядро убивает его, а не окно. Прежде оболочка этого не
 * замечала: окно оставалось с надписью «нет связи» и вечно стучалось в мёртвый
 * порт. Теперь оболочка поднимает сервер того же проекта заново, в том же окне
 * (на новом порту), а отменённое задание из ленты действительно снимается.
 *
 * Код 1 — если что-то не пережито.
 */
const { execFileSync } = require('node:child_process');

const sleep = (t) => new Promise((r) => setTimeout(r, t));

module.exports = async function run({ win, app }) {
  const js = (c) => Promise.race([win.webContents.executeJavaScript(c), sleep(5000).then(() => null)]);
  const results = [];
  const check = (name, ok, detail = '') => {
    results.push(ok);
    console.log(`   ${ok ? 'ОК  ' : 'ПРОВАЛ'} ${name.padEnd(40)} ${detail}`);
  };
  const serverPid = () => {
    try {
      const out = execFileSync('pgrep', ['-P', String(process.pid), '-f', 'datum.cli serve'],
        { encoding: 'utf8' });
      return Number(out.trim().split('\n')[0]) || 0;
    } catch {
      return 0;
    }
  };

  await sleep(8000);
  const parts = await js(`document.querySelectorAll('.part .name').length`);
  const originBefore = await js(`location.origin`);
  const pid = serverPid();
  check('сервер найден', pid > 0, `pid ${pid}`);
  check('проект открыт', parts > 0, `${parts} дет.`);

  // Лента: служебные вызовы окна скрыты, а кнопка показа их есть.
  await js(`[...document.querySelectorAll('button')].find(b => b.innerText.trim().startsWith('Лента'))?.click()`);
  await sleep(700);
  const rows = await js(`[...document.querySelectorAll('.log-op')].map(e => e.innerText)`);
  check('в ленте нет служебного window_report', Array.isArray(rows) && !rows.includes('window_report'),
    JSON.stringify((rows || []).slice(0, 6)));
  const more = await js(`!!document.querySelector('.log-more')`);
  check('служебные вызовы можно показать', more === true);

  process.kill(pid, 'SIGKILL');                      // так сервер убивает нехватка памяти
  let back = false;
  let originAfter = originBefore;
  for (let i = 0; i < 80 && !back; i++) {
    await sleep(500);
    originAfter = (await js(`location.origin`)) || originAfter;
    const again = serverPid();
    back = again > 0 && again !== pid && originAfter !== originBefore
      && (await js(`document.readyState`)) === 'complete';
  }
  check('сервер поднят заново, окно на нём', back, `${originBefore} → ${originAfter}`);
  await sleep(6000);
  const partsAfter = await js(`document.querySelectorAll('.part .name').length`);
  check('проект на месте', partsAfter === parts, `${partsAfter} дет.`);
  const link = await js(`document.body.innerText.includes('нет связи')`);
  check('связь с сервером есть', link === false);

  const ok = results.every(Boolean);
  console.log(ok ? 'Окно переживает гибель сервера.' : 'Гибель сервера не пережита.');
  app.exit(ok ? 0 : 1);
};
