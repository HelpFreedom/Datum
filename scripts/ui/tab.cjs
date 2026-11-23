/**
 * Открыть вкладку по названию и снять окно. Вкладки — правой и нижней панелей
 * и рейки слева; у вкладки бывает счётчик («Проверки 2»), поэтому название
 * сравнивается по началу.
 */
const fs = require('node:fs');
const sleep = (t) => new Promise((r) => setTimeout(r, t));

module.exports = async function run({ win }) {
  await sleep(Number(process.env.DATUM_WAIT_MS ?? 9000));
  const name = process.env.DATUM_TAB ?? '';
  const opened = await win.webContents.executeJavaScript(`
    (() => {
      const tab = [...document.querySelectorAll('.tabs button, .rail button')]
        .find(b => b.textContent.trim().startsWith(${JSON.stringify(name)}));
      if (tab) tab.click();
      return Boolean(tab);
    })()`);
  console.log(opened ? `вкладка «${name}» открыта` : `вкладки «${name}» нет`);
  await sleep(Number(process.env.DATUM_AFTER_MS ?? 2500));
  fs.writeFileSync(process.env.DATUM_OUT, (await win.webContents.capturePage()).toPNG());
  console.log(`снимок: ${process.env.DATUM_OUT}`);
};
