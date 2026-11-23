/**
 * Раскрыть проверку и снять окно.
 *
 * По умолчанию раскрывается первая ПРОВАЛЕННАЯ. DATUM_CHECK задаёт часть имени
 * нужной: доказательства бывают и у прошедших — у контакта и МКЭ это кривая,
 * и смотреть её хочется не только когда всё плохо.
 */
const fs = require('node:fs');
const sleep = (t) => new Promise((r) => setTimeout(r, t));

module.exports = async function run({ win }) {
  await sleep(Number(process.env.DATUM_WAIT_MS ?? 12000));
  const opened = await win.webContents.executeJavaScript(`
    (() => {
      const rows = [...document.querySelectorAll('.check-head')];
      const want = ${JSON.stringify(process.env.DATUM_CHECK ?? '')};
      const target = want
        ? rows.find(r => r.textContent.includes(want))
        : rows.find(r => r.querySelector('.bad'));
      if (target) target.click();
      return target ? target.textContent.trim() : null;
    })()`);
  console.log(opened ? `раскрыта: ${opened}` : 'проваленных нет');
  await sleep(Number(process.env.DATUM_AFTER_MS ?? 3500));
  fs.writeFileSync(process.env.DATUM_OUT, (await win.webContents.capturePage()).toPNG());
  console.log(`снимок: ${process.env.DATUM_OUT}`);
};
