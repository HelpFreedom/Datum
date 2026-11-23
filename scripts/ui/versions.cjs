/**
 * Сравнение версий картинкой: вкладка «Версии» проекта с двумя снимками,
 * кнопка «Показать форму» — и в панели появляется лист «было/стало»
 * (`snapshot_diff` с image) и объёмы по деталям.
 *
 *   DATUM_SCENARIO=scripts/ui/versions.cjs DATUM_OUT=/tmp/v.png npm start -- <проект>
 *
 * Проекту нужны два снимка (`datum snapshot <проект> v1`, правка, `… v2`).
 */
const fs = require('node:fs');
const sleep = (t) => new Promise((r) => setTimeout(r, t));

module.exports = async function run({ win, app }) {
  const out = process.env.DATUM_OUT || '/tmp/versions.png';
  const js = (code) => win.webContents.executeJavaScript(code);
  const fail = (why) => {
    console.error('ПРОВАЛ: ' + why);
    return app.exit(1);
  };
  await sleep(Number(process.env.DATUM_WAIT_MS ?? 9000));

  const opened = await js(`(() => {
    const tab = [...document.querySelectorAll('.tabs button, .rail button')]
      .find(b => b.textContent.trim().startsWith('Версии'));
    if (tab) tab.click();
    return Boolean(tab);
  })()`);
  if (!opened) return fail('вкладки «Версии» нет');

  // Сравнение двух последних грузится само; кнопка — после него.
  let button = false;
  for (let i = 0; i < 40 && !button; i++) {
    await sleep(500);
    button = await js(`Boolean(document.querySelector('[data-action="version-shape"]'))`);
  }
  if (!button) return fail('кнопки «Показать форму» нет — сравнение не загрузилось');
  await js(`document.querySelector('[data-action="version-shape"]').click()`);

  let state = null;
  for (let i = 0; i < 120; i++) {
    await sleep(500);
    state = await js(`(() => ({
      картинка: Boolean([...document.querySelectorAll('img.evidence')].length),
      объёмы: [...document.querySelectorAll('.log-row')].map(r => r.textContent).filter(t => /мм³/.test(t)),
      ошибка: document.querySelector('.alert')?.textContent ?? null,
    }))()`);
    if (state.картинка || state.ошибка) break;
  }
  console.log('  форма:', JSON.stringify(state));
  fs.writeFileSync(out, (await win.webContents.capturePage()).toPNG());
  console.log(`  снимок: ${out}`);
  if (state?.ошибка) return fail(state.ошибка);
  if (!state?.картинка) return fail('картинка формы не появилась');
  console.log('Сравнение формы в окне работает.');
};
