/**
 * Сценарий: размеры панелей тянутся сколько угодно, а вьюпорт не пропадает.
 *
 * Каждая ручка протаскивается до противоположного края окна (события мыши
 * ЗА окном синтетический ввод не доставляет — поэтому до края, а не за него).
 * Панель обязана забрать весь запас вьюпорта: вьюпорт после этого — ровно
 * VIEWPORT_MIN (160), не меньше; нижняя — выше прежнего «потолка» 32 % окна.
 * Двойной щелчок по ручке возвращает размер по умолчанию.
 *
 * Раскладку человека сценарий не трогает: под DATUM_SCENARIO оболочка файл
 * раскладки не читает и не пишет (`main/layout.ts`).
 *
 *   DATUM_OUT — куда сохранить снимок растянутого окна (необязательна)
 */
const fs = require('node:fs');

const sleep = (t) => new Promise((r) => setTimeout(r, t));
const VIEWPORT_MIN = 160;

module.exports = async function run({ win }) {
  await sleep(6000);
  const wc = win.webContents;
  const rect = (selector) => wc.executeJavaScript(`(() => {
    const el = document.querySelector(${JSON.stringify(selector)});
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return { x: r.x, y: r.y, w: r.width, h: r.height };
  })()`);

  /** Протащить ручку к точке окна (tx, ty); null — эта ось не меняется. */
  async function drag(selector, tx, ty) {
    const g = await rect(selector);
    if (!g) throw new Error(`нет ручки ${selector}`);
    const x = Math.round(g.x + g.w / 2);
    const y = Math.round(g.y + g.h / 2);
    const dx = tx === null ? 0 : tx - x;
    const dy = ty === null ? 0 : ty - y;
    wc.sendInputEvent({ type: 'mouseMove', x, y });
    wc.sendInputEvent({ type: 'mouseDown', x, y, button: 'left', clickCount: 1 });
    const steps = 30;
    for (let i = 1; i <= steps; i += 1) {
      wc.sendInputEvent({ type: 'mouseMove', x: x + Math.round(dx * i / steps),
                          y: y + Math.round(dy * i / steps), button: 'left',
                          modifiers: ['leftButtonDown'] });
      await sleep(30);
    }
    wc.sendInputEvent({ type: 'mouseUp', x: x + dx, y: y + dy, button: 'left', clickCount: 1 });
    await sleep(400);
  }

  async function doubleClick(selector) {
    const g = await rect(selector);
    const x = Math.round(g.x + g.w / 2);
    const y = Math.round(g.y + g.h / 2);
    for (const clickCount of [1, 2]) {
      wc.sendInputEvent({ type: 'mouseDown', x, y, button: 'left', clickCount });
      wc.sendInputEvent({ type: 'mouseUp', x, y, button: 'left', clickCount });
    }
    await sleep(400);
  }

  const failures = [];
  const check = (ok, text) => {
    console.log(`  ${ok ? 'OK  ' : 'ПРОВАЛ'}  ${text}`);
    if (!ok) failures.push(text);
  };
  const [width, height] = win.getContentSize();

  const before = { left: await rect('.left'), right: await rect('.right'), dock: await rect('.dock'),
                   centre: await rect('.center') };
  // Панель берёт РОВНО то, что отдал вьюпорт сверх своего минимума. Прежде
  // здесь стояло «выросла больше чем на 300 px» — число зависело от ширины
  // экрана и однажды не сошлось на 2 px при верной работе.
  const surplus = before.centre.w - VIEWPORT_MIN;
  const near = (a, b) => Math.abs(a - b) <= 2;

  await drag('.grip-right', width - 5, null);          // левую — до правого края
  const left = { left: await rect('.left'), centre: await rect('.center') };
  check(surplus > 100 && near(left.left.w, before.left.w + surplus),
        `левая панель забрала у вьюпорта всё сверх минимума: ${before.left.w} → ${left.left.w} (+${surplus})`);
  check(near(left.centre.w, VIEWPORT_MIN), `вьюпорт ужат ровно до ${VIEWPORT_MIN}: ${left.centre.w}`);
  await doubleClick('.grip-right');

  await drag('.grip-left', 5, null);                   // правую — до левого края
  const right = { right: await rect('.right'), centre: await rect('.center') };
  check(surplus > 100 && near(right.right.w, before.right.w + surplus),
        `правая панель забрала у вьюпорта всё сверх минимума: ${before.right.w} → ${right.right.w} (+${surplus})`);
  check(near(right.centre.w, VIEWPORT_MIN), `вьюпорт ужат ровно до ${VIEWPORT_MIN}: ${right.centre.w}`);
  await doubleClick('.grip-left');

  await drag('.grip-top', null, 5);                    // нижнюю — до верха
  const after = { left: await rect('.left'), right: await rect('.right'), dock: await rect('.dock'),
                  centre: await rect('.center') };
  check(after.dock.h > height * 0.32 + 20,
        `нижняя выше прежнего потолка 32 %: ${after.dock.h} из ${height}`);
  check(near(after.centre.h, VIEWPORT_MIN), `вьюпорт по высоте ровно ${VIEWPORT_MIN}: ${after.centre.h}`);
  check(after.left.x + after.left.w <= width && after.right.x + after.right.w <= width + 1,
        'панели не вылезли за окно');

  if (process.env.DATUM_OUT) {
    const image = await win.capturePage();
    fs.writeFileSync(process.env.DATUM_OUT, image.toPNG());
    console.log(`снимок: ${process.env.DATUM_OUT}`);
  }

  await doubleClick('.grip-right');
  await doubleClick('.grip-left');
  await doubleClick('.grip-top');
  const reset = { left: await rect('.left'), right: await rect('.right'), dock: await rect('.dock') };
  check(Math.abs(reset.left.w - before.left.w) <= 1, `двойной щелчок — левая по умолчанию: ${reset.left.w}`);
  check(Math.abs(reset.right.w - before.right.w) <= 1, `правая по умолчанию: ${reset.right.w}`);
  check(Math.abs(reset.dock.h - before.dock.h) <= 1, `нижняя по умолчанию: ${reset.dock.h}`);

  if (failures.length) throw new Error(`не прошло: ${failures.join('; ')}`);
};
