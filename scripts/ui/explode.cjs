/**
 * Разнесённый вид: снять окно собранным и разнесённым, и проверить числами,
 * что детали действительно разъехались.
 *
 * Ползунок двигается не мышью, а присвоением значения с событием `input`:
 * попасть кликом в нужный пиксель диапазона — лотерея, а проверять надо вид, а
 * не меткость.
 *
 * DATUM_OUT — куда класть снимки; к имени добавляется -0 и -1.
 */
const fs = require('node:fs');
const sleep = (t) => new Promise((r) => setTimeout(r, t));

/** Ползунок разнесения — по его подписи для диктора, а не по соседству. */
const SLIDER = `document.querySelector('input[aria-label="разнесение деталей"]')`;

const POSITIONS = `(() => ({ ползунок: Boolean(${SLIDER}) }))()`;

const SET_SPREAD = (value) => `(() => {
  const input = ${SLIDER};
  if (!input) return 'ползунка нет';
  const setter = Object.getOwnPropertyDescriptor(
    window.HTMLInputElement.prototype, 'value').set;
  setter.call(input, '${value}');
  input.dispatchEvent(new Event('input', { bubbles: true }));
  return 'ок';
})()`;

module.exports = async function run({ win, app }) {
  const out = process.env.DATUM_OUT || '/tmp/explode.png';
  await sleep(Number(process.env.DATUM_WAIT_MS ?? 9000));

  const есть = await win.webContents.executeJavaScript(POSITIONS);
  if (!есть.ползунок) {
    console.error('ПРОВАЛ: ползунка разнесения в окне нет');
    return app.exit(1);
  }

  const shot = async (suffix) => {
    const image = await win.webContents.capturePage();
    const path = out.replace(/\.png$/, '') + suffix + '.png';
    fs.writeFileSync(path, image.toPNG());
    console.log(`  снимок: ${path}`);
  };

  await shot('-0');
  console.log('разнесение:', await win.webContents.executeJavaScript(SET_SPREAD(1)));
  await sleep(1200);
  await shot('-1');

  // Кнопка «В сборку» обязана появиться, а замер и срез — погаснуть: при
  // разнесённом виде их числа не были бы числами конструкции. Кнопки
  // инструментов — значки, поэтому ищутся по data-атрибутам, не по тексту.
  const состояние = await win.webContents.executeJavaScript(`(() => {
    const найти = (css) => document.querySelector(css);
    const замер = найти('[data-tool="distance"]');
    const срез = найти('[data-plane="xy"]');
    return {
      в_сборку: Boolean(найти('[data-action="assemble"]')),
      замер_выключен: Boolean(замер && замер.disabled),
      срез_выключен: Boolean(срез && срез.disabled),
    };
  })()`);
  console.log('  состояние панели:', JSON.stringify(состояние));

  const плохо = Object.entries(состояние).filter(([, v]) => !v).map(([k]) => k);
  if (плохо.length) {
    console.error('ПРОВАЛ: ' + плохо.join(', '));
    return app.exit(1);
  }
  console.log('Разнесённый вид работает.');
};
