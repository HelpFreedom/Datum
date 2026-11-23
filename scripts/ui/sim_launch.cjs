/**
 * Кнопка «Симуляция» во вьюпорте — запуск человеком, без агента: меню механизмов
 * и сценариев (`sim_list`), прогон (`sim_run`) — проигрыватель открывается сам, с
 * картой загрузки и легендой; затем окно само считает напряжения (`sim_stress`) —
 * появляются кнопки звеньев, по нажатию звено красится полем МКЭ, время встаёт на
 * худший момент, проигрывание — на паузу. Карта загрузки выключается и включается.
 *
 * Снимки: -1 (меню), -2 (проигрывание с картой загрузки), -3 (поле напряжений).
 *
 *   DATUM_SCENARIO=scripts/ui/sim_launch.cjs DATUM_OUT=/tmp/launch.png npm start -- <копия examples/arm>
 */
const fs = require('node:fs');
const path = require('node:path');
const sleep = (t) => new Promise((r) => setTimeout(r, t));

module.exports = async function run({ win, app }) {
  const out = process.env.DATUM_OUT || '/tmp/launch.png';
  const project = process.argv.find((a) => fs.existsSync(path.join(a, 'datum.toml')));
  const js = (code) => win.webContents.executeJavaScript(code);
  const fail = (why) => {
    console.error('ПРОВАЛ: ' + why);
    return app.exit(1);
  };
  const shot = async (suffix) => {
    const file = out.replace(/\.png$/, '') + suffix + '.png';
    fs.writeFileSync(file, (await win.webContents.capturePage()).toPNG());
    console.log(`  снимок: ${file}`);
  };
  const waitFor = async (expr, seconds) => {
    for (let i = 0; i < seconds * 2; i++) {
      const v = await js(expr);
      if (v) return v;
      await sleep(500);
    }
    return null;
  };
  if (!project) return fail('проект не передан');
  await sleep(Number(process.env.DATUM_WAIT_MS ?? 9000));

  // 1. Меню: механизмы и сценарии проекта.
  const pressed = await js(`(() => { const b = document.querySelector('[data-action="sim-launch"]'); if (b) b.click(); return Boolean(b); })()`);
  if (!pressed) return fail('кнопки «Симуляция» во вьюпорте нет');
  const item = await waitFor(`document.querySelector('[data-sim-scenario="перенос"]')?.textContent ?? null`, 60);
  await shot('-1');
  if (!item) return fail('в меню нет сценария «перенос»: ' + await js(`document.querySelector('[data-sim="menu"]')?.textContent ?? 'меню не открылось'`));
  console.log('  меню:', await js(`document.querySelector('[data-sim="menu"]').textContent`));

  // 2. Запуск — проигрыватель открывается сам, с картой загрузки.
  await js(`document.querySelector('[data-sim-scenario="перенос"]').click()`);
  const running = await waitFor(`Boolean(document.querySelector('[data-sim="running"]'))`, 10);
  console.log('  «Считаю симуляцию…» показано:', Boolean(running));
  const player = await waitFor(`Boolean(document.querySelector('[data-sim="player"]'))`, 300);
  if (!player) return fail('проигрыватель не открылся: ' + await js(`document.body.innerText.slice(0, 300)`));
  const heatOn = await js(`document.querySelector('[data-action="sim-heat"]')?.getAttribute('aria-pressed')`);
  const legend = await waitFor(`document.querySelector('[data-sim="legend"]')?.textContent ?? null`, 5);
  console.log(`  карта загрузки: ${heatOn}; легенда: ${legend}`);
  if (heatOn !== 'true' || !legend) return fail('карты загрузки или легенды нет');
  if (!/больше всех: .+ — \d+ %/.test(legend)) return fail('легенда не называет самое загруженное звено');
  const t0 = await js(`document.querySelector('.sim-time')?.textContent ?? ''`);
  await sleep(1500);
  const t1 = await js(`document.querySelector('.sim-time')?.textContent ?? ''`);
  await shot('-2');
  if (t0 === t1) return fail('время не идёт');

  // Карта выключается и включается.
  await js(`document.querySelector('[data-action="sim-heat"]').click()`);
  await sleep(300);
  if (await js(`Boolean(document.querySelector('[data-sim="legend"]'))`)) return fail('легенда не ушла с выключенной картой');
  await js(`document.querySelector('[data-action="sim-heat"]').click()`);

  // 3. Напряжения — окно досчитывает само; кнопки звеньев появляются.
  const busy = await waitFor(`Boolean(document.querySelector('[data-sim="stress-running"]')) || Boolean(document.querySelector('[data-sim-stress]'))`, 20);
  if (!busy) return fail('расчёт напряжений не начался: ' + await js(`document.querySelector('[data-sim="error"]')?.textContent ?? ''`));
  const links = await waitFor(`(() => { const b = [...document.querySelectorAll('[data-sim-stress]')]; return b.length ? b.map((x) => x.textContent) : null; })()`, 600);
  if (!links) return fail('кнопок напряжений нет: ' + await js(`document.querySelector('[data-sim="error"]')?.textContent ?? 'без ошибки'`));
  console.log('  напряжения звеньев:', JSON.stringify(links));
  if (links.length !== 2) return fail('ожидались два звена (плечо, предплечье)');
  await js(`document.querySelector('[data-sim-stress="плечо"]').click()`);
  const note = await waitFor(`document.querySelector('[data-sim="stress-note"]')?.textContent ?? null`, 30);
  const pressedStress = await js(`document.querySelector('[data-sim-stress="плечо"]').getAttribute('aria-pressed')`);
  const playing = await js(`document.querySelector('[data-action="sim-play"]').getAttribute('aria-label')`);
  const time = await js(`document.querySelector('.sim-time')?.textContent ?? ''`);
  console.log(`  поле: ${note}; нажато: ${pressedStress}; кнопка: ${playing}; время: ${time}`);
  await sleep(800);
  await shot('-3');
  if (!note || pressedStress !== 'true') return fail('поле напряжений не показалось');
  if (playing !== 'Пуск') return fail('проигрывание не встало на паузу');
  const err = await js(`document.querySelector('[data-sim="error"]')?.textContent ?? null`);
  if (err) return fail('ошибка в проигрывателе: ' + err);

  // Пуск снимает поле: оно верно только в свой момент.
  await js(`document.querySelector('[data-action="sim-play"]').click()`);
  await sleep(500);
  if (await js(`document.querySelector('[data-sim-stress="плечо"]').getAttribute('aria-pressed')`) !== 'false')
    return fail('поле не снялось при пуске');

  await js(`document.querySelector('[data-action="sim-close"]')?.click()`);
  await sleep(800);
  if (!(await js(`!document.querySelector('[data-sim="player"]')`))) return fail('проигрыватель не закрылся');
  console.log('Кнопка симуляции, карта загрузки и поле напряжений работают.');
};
