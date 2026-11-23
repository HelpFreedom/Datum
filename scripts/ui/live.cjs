/**
 * Живой документ: правки на диске и из окна доходят туда, куда должны.
 *
 * Четыре проверки, каждая — про обещание, которое долго не выполнялось:
 *
 *   1. Правка params/base.yaml РЕДАКТОРОМ (или агентом) видна окну без
 *      «Собрать»: сервер следит за файлами и шлёт doc.changed.
 *   2. Синтаксическая ошибка в скрипте детали — карточка «проект не открылся»,
 *      а не чёрный экран. Исправили файл — карточка ушла сама.
 *   3. Правка параметра ИЗ ОКНА записывается в файл по месту, с комментариями.
 *   4. «Выгрузить» пишет 3MF и STL в output/ и называет, почему проект не готов.
 *
 * Запуск — на КОПИИ проекта, потому что сценарий правит его файлы:
 *   cp -r examples/latch /tmp/latch-live
 *   DATUM_SCENARIO=scripts/ui/live.cjs DATUM_OUT=/tmp/live npm start -- /tmp/latch-live
 * Код возврата 1, если какая-то из проверок не прошла.
 */
const fs = require('node:fs');
const path = require('node:path');

const sleep = (t) => new Promise((r) => setTimeout(r, t));

module.exports = async function run({ win }) {
  const out = process.env.DATUM_OUT || '/tmp/datum-live';
  const js = (code) => win.webContents.executeJavaScript(code);
  const { project } = await js('window.datum.endpoint()');
  if (!project.includes('live')) {
    throw new Error(`сценарий правит файлы проекта — запускайте его на копии, а не на ${project}`);
  }
  const params = path.join(project, 'params', 'base.yaml');
  const lid = path.join(project, 'parts', 'lid.py');
  const failures = [];
  const check = (name, ok, detail) => {
    console.log(`   ${ok ? 'ОК  ' : 'ПЛОХО'} ${name.padEnd(34)} ${detail}`);
    if (!ok) failures.push(name);
  };
  const shot = async (name) => {
    const image = await win.webContents.capturePage();
    fs.writeFileSync(`${out}-${name}.png`, image.toPNG());
  };
  const partsText = () => js(`[...document.querySelectorAll('.left .part')].map(p => p.innerText).join(' | ')`);
  const until = async (probe, ms = 15000) => {
    const end = Date.now() + ms;
    while (Date.now() < end) {
      if (await probe()) return true;
      await sleep(250);
    }
    return false;
  };

  await sleep(Number(process.env.DATUM_WAIT_MS ?? 9000));
  const before = await partsText();

  // 1 --------------------------------------------------------- правка на диске
  fs.writeFileSync(params, fs.readFileSync(params, 'utf8').replace(
    'CASE_W:  {value: 60.0', 'CASE_W:  {value: 72.0'));
  const moved = await until(async () => (await partsText()) !== before);
  check('правка на диске видна окну', moved, moved ? 'объём пересчитан без «Собрать»' : before);
  await shot('1-disk');

  // 2 ------------------------------------------------ сломанный скрипт детали
  const good = fs.readFileSync(lid, 'utf8');
  fs.writeFileSync(lid, good.replace('P.LID_Z - P.LID_T, P.LID_Z)', 'P.LID_Z - P.LID_T, P.LID_Z'));
  const card = await until(() => js(`!!document.querySelector('.project-problem')`));
  const cardText = card ? await js(`document.querySelector('.project-problem').innerText`) : '';
  const alive = await js(`!!document.querySelector('.viewport-tools') && !!document.querySelector('.tabs')`);
  check('ошибка скрипта — карточка', card && cardText.includes('lid.py'), cardText.split('\n')[1] ?? '');
  check('окно при этом живо', alive, alive ? 'панели на месте' : 'чёрный экран');
  await shot('2-broken');
  fs.writeFileSync(lid, good);
  const gone = await until(async () => !(await js(`!!document.querySelector('.project-problem')`)));
  check('исправили — карточка ушла', gone, gone ? 'сама, без перезапуска' : 'висит');

  // 3 ------------------------------------------------- правка параметра из окна
  await js(`document.querySelector('#tab-params').click()`);
  await sleep(600);
  const opened = await js(`(() => {
    const row = [...document.querySelectorAll('.param')].find(r => r.querySelector('.param-name')?.innerText === 'GAP');
    const button = row?.querySelector('.param-change');
    if (!button) return false;
    button.click();
    return true;
  })()`);
  await sleep(300);
  // Ввод через настоящие нажатия: React слушает события ввода, а не присваивание.
  await js(`document.querySelector('.param-edit input.field').select()`);
  for (const ch of '0,25') {
    win.webContents.sendInputEvent({ type: 'char', keyCode: ch });
    await sleep(40);
  }
  await js(`document.querySelector('.param-edit button[type=submit]').click()`);
  const written = await until(() => fs.readFileSync(params, 'utf8').includes('GAP:     {value: 0.25'));
  const kept = fs.readFileSync(params, 'utf8').includes('# Единственное угаданное число');
  check('правка из окна — в файл', opened && written, written ? 'GAP = 0.25, запятая принята' : 'файл не изменился');
  check('комментарии файла целы', kept, kept ? 'да' : 'стёрты');
  await shot('3-param');

  // 4 ------------------------------------------------ выгрузка из окна
  await js(`[...document.querySelectorAll('.left button')].find(b => b.innerText === 'Выгрузить').click()`);
  const done = await until(() => js(`!!document.querySelector('.export-result')`), 60000);
  const said = done ? await js(`document.querySelector('.export-result').innerText`) : '';
  const files = ['корпус.3mf', 'корпус.stl', 'крышка.3mf', 'крышка.stl']
    .filter((f) => fs.existsSync(path.join(project, 'output', f)));
  check('выгрузка из окна — файлы', done && files.length === 4, `${files.length} из 4 в output/`);
  check('«не готов» сказано', said.includes('не готов') && said.includes('LIP_D'),
    said.split('\n')[0] ?? '');
  await shot('4-export');

  console.log(failures.length ? `ПРОВАЛЕНО: ${failures.join(', ')}` : 'Живой документ работает.');
  if (failures.length) process.exitCode = 1;
};
