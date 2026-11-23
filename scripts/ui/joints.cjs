/**
 * Ход шарнира в окне: проект с `joints:` в assembly.yaml.
 *
 * Проверяется, что ползунок шарнира есть; что сдвинутое звено гасит замер и
 * срез (как разнесение); что окно сообщает позу агенту (`.datum/window.json`);
 * что кнопка «К задеванию» ставит звено туда, где проверка хода нашла
 * задевание (если проверка не прошла); что «на место» возвращает всё.
 *
 *   DATUM_SCENARIO=scripts/ui/joints.cjs DATUM_OUT=/tmp/j.png \
 *   DATUM_JOINT=крышка DATUM_JOINT_U=90 npm start -- <проект>
 *
 * DATUM_OUT — к имени добавляются -0 (собрано), -1 (звено сдвинуто), -2 (к задеванию),
 * -3 (разнесено после сдвига: звено обязано стоять в собранной позе).
 * Ползунок двигается присвоением значения с событием `input` — как в explode.cjs.
 */
const fs = require('node:fs');
const path = require('node:path');
const sleep = (t) => new Promise((r) => setTimeout(r, t));

module.exports = async function run({ win, app }) {
  const out = process.env.DATUM_OUT || '/tmp/joints.png';
  const name = process.env.DATUM_JOINT || 'крышка';
  const u = Number(process.env.DATUM_JOINT_U ?? 90);
  const project = process.argv.find((a) => fs.existsSync(path.join(a, 'datum.toml')));
  await sleep(Number(process.env.DATUM_WAIT_MS ?? 9000));
  const js = (code) => win.webContents.executeJavaScript(code);
  const fail = (why) => {
    console.error('ПРОВАЛ: ' + why);
    return app.exit(1);
  };

  const slider = `document.querySelector('input[aria-label=${JSON.stringify('ход шарнира ' + name)}]')`;
  const set = (value) => js(`(() => {
    const input = ${slider};
    if (!input) return 'ползунка нет';
    const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
    setter.call(input, '${value}');
    input.dispatchEvent(new Event('input', { bubbles: true }));
    return input.value;
  })()`);
  const panel = () => js(`(() => {
    const q = (css) => document.querySelector(css);
    const group = q('[data-joint=${JSON.stringify(name)}]');
    return {
      ползунок: Boolean(${slider}),
      значение: group ? group.querySelector('.deck-value')?.textContent : null,
      замер_выключен: Boolean(q('[data-tool="distance"]')?.disabled),
      срез_выключен: Boolean(q('[data-plane="xy"]')?.disabled),
      срез_включён: q('[data-plane="xy"]')?.getAttribute('aria-pressed') === 'true',
      к_задеванию: Boolean(q('[data-action="joint-worst"]')),
      на_место: Boolean(q('[data-action="joint-rest"]')),
    };
  })()`);
  const shot = async (suffix) => {
    const image = await win.webContents.capturePage();
    const file = out.replace(/\.png$/, '') + suffix + '.png';
    fs.writeFileSync(file, image.toPNG());
    console.log(`  снимок: ${file}`);
  };

  let p = await panel();
  if (!p.ползунок) return fail(`ползунка шарнира «${name}» нет`);
  await shot('-0');

  // Срез включён ДО движения звена — движение обязано его погасить.
  await js(`document.querySelector('[data-plane="xy"]').click()`);
  await sleep(600);
  if (!(await panel()).срез_включён) return fail('срез не включился до движения звена');

  console.log('положение звена:', await set(u));
  await sleep(1500);
  p = await panel();
  console.log('  после движения:', JSON.stringify(p));
  const плохо = ['замер_выключен', 'срез_выключен', 'на_место'].filter((k) => !p[k]);
  if (p.срез_включён) плохо.push('срез остался');
  if (плохо.length) return fail(плохо.join(', '));
  await shot('-1');

  // Окно сообщает позу агенту — после затишья (400 мс) и ответа сервера.
  if (project) {
    await sleep(1500);
    const file = path.join(project, '.datum', 'window.json');
    const seen = JSON.parse(fs.readFileSync(file, 'utf-8'));
    const reported = (seen.state || seen).joints || {};
    console.log('  в window.json:', JSON.stringify(reported));
    if (Math.abs((reported[name] ?? 0) - u) > 0.01) return fail('поза не дошла до window.json');
  }

  if (p.к_задеванию) {
    await js(`document.querySelector('[data-action="joint-worst"]').click()`);
    await sleep(1200);
    console.log('  к задеванию:', (await panel()).значение);
    await shot('-2');
  } else {
    console.log('  кнопки «К задеванию» нет — проверка хода прошла или ещё не прогнана');
  }

  await js(`document.querySelector('[data-action="joint-rest"]').click()`);
  await sleep(800);
  p = await panel();
  if (p.на_место || p.замер_выключен) return fail('«на место» не вернуло звено: ' + JSON.stringify(p));

  // Разнесение при сдвинутом звене: ход сбрасывается, и звено едет в
  // разнесённый вид В СОБРАННОЙ позе (снимок -3 — смотреть глазами).
  await set(u);
  await sleep(600);
  await js(`(() => {
    const input = document.querySelector('input[aria-label="разнесение деталей"]');
    const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
    setter.call(input, '1');
    input.dispatchEvent(new Event('input', { bubbles: true }));
  })()`);
  await sleep(1200);
  p = await panel();
  await shot('-3');
  if (p.на_место) return fail('разнесение не сбросило ход звена');
  console.log('Ход шарнира в окне работает.');
};
