/**
 * Проигрыватель симуляции в живом окне: агент запускает `sim_run` (через
 * сервер — так, как его зовёт MCP), окно получает событие `sim.result` и
 * показывает «Симуляция готова»; «Показать» — звенья механизма вместо
 * деталей, пуск двигает время; «Закрыть» — детали на месте.
 *
 * Снимки: -1 (уведомление), -2 (идёт проигрывание), -3 (закрыто).
 *
 *   DATUM_SCENARIO=scripts/ui/sim.cjs DATUM_OUT=/tmp/sim.png npm start -- <копия examples/arm>
 */
const fs = require('node:fs');
const path = require('node:path');
const sleep = (t) => new Promise((r) => setTimeout(r, t));

module.exports = async function run({ win, app }) {
  const out = process.env.DATUM_OUT || '/tmp/sim.png';
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
  if (!project) return fail('проект не передан');
  await sleep(Number(process.env.DATUM_WAIT_MS ?? 9000));

  // Запуск — как у агента: операция на сервере, с меткой агента.
  const answer = await js(`(async () => {
    const ep = await window.datum.endpoint();
    const r = await fetch('/api/ops/sim_run', {
      method: 'POST',
      headers: { 'X-Datum-Token': ep.token, 'X-Datum-Client': '1', 'X-Datum-Actor': 'agent',
                 'Content-Type': 'application/json' },
      body: JSON.stringify({ project: ${JSON.stringify(project)}, mechanism: 'рука', scenario: 'перенос' }),
    });
    const body = await r.json();
    return { status: r.status, error: body.result?.error ?? null, ok: body.result?.ok ?? null };
  })()`);
  console.log('  sim_run:', JSON.stringify(answer));
  if (answer.status !== 200 || answer.error) return fail('sim_run отказал: ' + JSON.stringify(answer));

  let notice = null;
  for (let i = 0; i < 20 && !notice; i++) {
    await sleep(500);
    notice = await js(`document.querySelector('[data-sim="notice"]')?.textContent ?? null`);
  }
  console.log('  уведомление:', notice);
  await shot('-1');
  if (!notice || !notice.includes('перенос')) return fail('уведомления о прогоне нет');

  const opened = await js(`(() => { const b = document.querySelector('[data-action="sim-open"]'); if (b) b.click(); return Boolean(b); })()`);
  if (!opened) return fail('кнопки «Показать» нет');
  let player = false;
  for (let i = 0; i < 30 && !player; i++) {
    await sleep(500);
    player = await js(`Boolean(document.querySelector('[data-sim="player"]'))`);
  }
  if (!player) return fail('проигрыватель не открылся: ' + await js(`document.querySelector('[data-sim="notice"]')?.textContent ?? ''`));
  const t0 = await js(`document.querySelector('.sim-time')?.textContent ?? ''`);
  await sleep(2000);
  const t1 = await js(`document.querySelector('.sim-time')?.textContent ?? ''`);
  const charts = await js(`document.querySelectorAll('.sim-chart').length`);
  console.log(`  время: ${t0} → ${t1}, графиков: ${charts}`);
  await shot('-2');
  if (t0 === t1) return fail('время не идёт');
  if (charts !== 2) return fail('графиков моментов не два (плечо, предплечье)');

  await js(`document.querySelector('[data-action="sim-close"]')?.click()`);
  await sleep(1200);
  const gone = await js(`!document.querySelector('[data-sim="player"]')`);
  await shot('-3');
  if (!gone) return fail('проигрыватель не закрылся');
  console.log('Проигрыватель симуляции работает.');
};
