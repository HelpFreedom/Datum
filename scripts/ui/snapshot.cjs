/**
 * Сценарий проверки окна: подождать, нажать клавиши, кликнуть, снять PNG.
 *
 * Живёт ЗА пределами приложения и подключается переменной DATUM_SCENARIO —
 * поэтому в рабочем коде оболочки нет ни одной отладочной ветки.
 *
 * Переменные:
 *   DATUM_OUT     — куда сохранить снимок (обязательна)
 *   DATUM_WAIT_MS — сколько ждать до действий (по умолчанию 8000)
 *   DATUM_KEYS    — клавиши подряд, например "2" или "f"
 *   DATUM_CLICKS  — клики «x,y;x,y» в координатах окна
 *   DATUM_AFTER_MS— сколько ждать после действий (по умолчанию 600)
 *   DATUM_TYPE    — строка, которую напечатать (после кликов).
 *                   ВНИМАНИЕ: sendInputEvent принимает коды клавиш из набора
 *                   Accelerator, и знаки препинания вроде «/» в него не входят
 *                   — они молча теряются. Буквы и цифры проходят. Это
 *                   ограничение проверочного сценария, а не терминала: живая
 *                   клавиатура работает полностью.
 *   DATUM_ENTER   — «1», чтобы нажать Enter после печати
 */
const fs = require('node:fs');

const ms = (name, fallback) => Number(process.env[name] ?? fallback);
const sleep = (t) => new Promise((r) => setTimeout(r, t));

module.exports = async function run({ win }) {
  const out = process.env.DATUM_OUT;
  if (!out) throw new Error('не задана DATUM_OUT');

  await sleep(ms('DATUM_WAIT_MS', 8000));

  for (const key of process.env.DATUM_KEYS ?? '') {
    win.webContents.sendInputEvent({ type: 'keyDown', keyCode: key });
    win.webContents.sendInputEvent({ type: 'char', keyCode: key });
    win.webContents.sendInputEvent({ type: 'keyUp', keyCode: key });
    await sleep(250);
  }

  for (const pair of (process.env.DATUM_CLICKS ?? '').split(';').filter(Boolean)) {
    const [x, y] = pair.split(',').map(Number);
    for (const type of ['mouseDown', 'mouseUp']) {
      win.webContents.sendInputEvent({ type, x, y, button: 'left', clickCount: 1 });
    }
    await sleep(700);
  }

  const text = process.env.DATUM_TYPE ?? '';
  for (const ch of text) {
    win.webContents.sendInputEvent({ type: 'keyDown', keyCode: ch });
    win.webContents.sendInputEvent({ type: 'char', keyCode: ch });
    win.webContents.sendInputEvent({ type: 'keyUp', keyCode: ch });
    await sleep(60);
  }
  if (process.env.DATUM_ENTER === '1') {
    await sleep(400);
    win.webContents.sendInputEvent({ type: 'keyDown', keyCode: 'Return' });
    win.webContents.sendInputEvent({ type: 'char', keyCode: 'Return' });
    win.webContents.sendInputEvent({ type: 'keyUp', keyCode: 'Return' });
  }

  await sleep(ms('DATUM_AFTER_MS', 600));
  const image = await win.webContents.capturePage();
  fs.writeFileSync(out, image.toPNG());
  console.log(`снимок: ${out} (${image.getSize().width}×${image.getSize().height})`);
};
