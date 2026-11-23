/**
 * Сценарий: диалог «Файл → Проекты…» — список, переименование (название И
 * папка), «новый из этого», открытие по метке.
 *
 * Запуск (Makefile, цель ui): свой XDG_DATA_HOME с двумя проектами в
 * datum/untitled — «коробка» (копия examples/latch, открыта в окне) и
 * «второй» (пустой). Настоящие проекты человека сценарий не видит и не трогает.
 *
 * Проверяется то, что нельзя проверить без живого окна: переименование
 * ОТКРЫТОГО проекта останавливает его сервер, двигает папку и открывает проект
 * заново в том же окне на новом порту.
 */
const fs = require('node:fs');
const path = require('node:path');

const sleep = (t) => new Promise((r) => setTimeout(r, t));

module.exports = async function run({ win }) {
  const failures = [];
  const check = (ok, text) => {
    console.log(`  ${ok ? 'OK  ' : 'ПРОВАЛ'}  ${text}`);
    if (!ok) failures.push(text);
  };
  const root = path.join(process.env.XDG_DATA_HOME, 'datum', 'untitled');
  const wc = win.webContents;
  const js = (code) => wc.executeJavaScript(code);
  // Страница в ходе смены проекта заменяется: ответа таких вызовов не ждём.
  const fire = (code) => { wc.executeJavaScript(code).catch(() => undefined); };
  const openDialog = async () => {
    wc.sendInputEvent({ type: 'keyDown', keyCode: 'O', modifiers: ['control'] });
    wc.sendInputEvent({ type: 'keyUp', keyCode: 'O', modifiers: ['control'] });
    await sleep(1500);
  };
  const rows = () => js(`[...document.querySelectorAll('.project-row')].map((r) => ({
    folder: r.dataset.project,
    name: r.querySelector('.project-name')?.childNodes[0]?.textContent ?? '',
    current: r.classList.contains('current'),
    thumb: Boolean(r.querySelector('.project-thumb img')),
  }))`);
  // Поле React: значение ставится родным сеттером, иначе onChange не узнает.
  const typeAndSubmit = (folder, action, value) => `(() => {
    const row = document.querySelector('.project-row[data-project=${JSON.stringify(folder)}]');
    row.querySelector('[data-action=${JSON.stringify(action)}]').click();
    setTimeout(() => {
      const input = document.querySelector('.project-edit input');
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set
        .call(input, ${JSON.stringify(value)});
      input.dispatchEvent(new Event('input', { bubbles: true }));
      setTimeout(() => document.querySelector('.project-edit').requestSubmit(), 150);
    }, 150);
  })()`;
  const title = () => js(`document.querySelector('.topbar-project')?.textContent ?? ''`);

  await sleep(7000);
  const id = win.id;
  const first = new URL(wc.getURL()).origin;

  // 1. Список: оба проекта, открытый — первым и помечен.
  await openDialog();
  let list = await rows();
  check(list.length === 2, `в списке два проекта: ${JSON.stringify(list.map((r) => r.folder))}`);
  check(list[0]?.folder === 'коробка' && list[0]?.current, 'открытый проект — первым и помечен');
  check(list[0]?.name === 'latch' && list[1]?.name === 'Второй', 'названия — из datum.toml');
  await sleep(1500);
  list = await rows();
  check(list[0]?.thumb, 'у открытого проекта есть картинка (снимок окна)');
  if (process.env.DATUM_OUT) {
    fs.writeFileSync(process.env.DATUM_OUT, (await wc.capturePage()).toPNG());
  }

  // 2. Переименовать ОТКРЫТЫЙ проект: название и папка, окно — заново на нём же.
  fire(typeAndSubmit('коробка', 'rename', 'Коробка v2'));
  await sleep(14000);
  const second = new URL(wc.getURL()).origin;
  check(!win.isDestroyed() && win.id === id, 'окно то же самое');
  check(second !== first, `сервер поднят заново: ${first} → ${second}`);
  check(fs.existsSync(path.join(root, 'Коробка-v2', 'datum.toml'))
        && !fs.existsSync(path.join(root, 'коробка')), 'папка переименована: коробка → Коробка-v2');
  check(/name = "Коробка v2"/.test(fs.readFileSync(path.join(root, 'Коробка-v2', 'datum.toml'), 'utf-8')),
    'название записано в datum.toml');
  check((await title()) === 'Коробка v2', `окно показывает новое название: ${await title()}`);
  const parts = await js(`document.querySelectorAll('.part').length`);
  check(parts >= 2, `проект открыт и собран после переименования (деталей в сцене ${parts})`);

  // 3. Отказ словами: занятое имя папки.
  await openDialog();
  await js(typeAndSubmit('второй', 'rename', 'Коробка v2'));
  await sleep(2500);
  const problem = await js(`document.querySelector('.projects-problem')?.textContent ?? ''`);
  check(/уже есть/.test(problem), `занятая папка — отказ в диалоге: ${problem.slice(0, 80)}`);
  check(fs.existsSync(path.join(root, 'второй')), 'проект при отказе не тронут');

  // 4. «Новый из этого» — копия открывается сразу.
  fire(typeAndSubmit('второй', 'copy', 'Третий'));
  await sleep(12000);
  check(fs.existsSync(path.join(root, 'Третий', 'datum.toml')), 'копия лежит рядом: Третий');
  check((await title()) === 'Третий', `окно на новом проекте: ${await title()}`);
  check(fs.existsSync(path.join(root, 'Коробка-v2', '.datum', 'thumb.png')),
    'уходя с проекта, окно оставило его картинку');

  // 5. Открыть другой проект по метке из списка.
  await openDialog();
  list = await rows();
  check(list.length === 3 && list[0]?.folder === 'Третий' && list[0]?.current,
    `в списке три проекта, открыт «Третий»: ${JSON.stringify(list.map((r) => r.folder))}`);
  fire(`document.querySelector('.project-row[data-project="Коробка-v2"] [data-action="open"]').click()`);
  await sleep(10000);
  check((await title()) === 'Коробка v2', `открыт проект из списка: ${await title()}`);
  check(new URL(wc.getURL()).origin !== second, 'и это новый сервер');

  if (failures.length) throw new Error(`диалог проектов: провалов ${failures.length}`);
  console.log('  диалог проектов: всё на месте');
};
