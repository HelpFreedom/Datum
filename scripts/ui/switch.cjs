/**
 * Сценарий: архив .datum открывается В ТОМ ЖЕ ОКНЕ, и окно не пропадает.
 *
 * Прежде оболочка перезапускалась целиком (`app.relaunch`), и окно на секунду
 * исчезало с экрана. Здесь проверяется:
 *   - окно то же самое (не уничтожено и не создано заново) и всё время видно;
 *   - оно на НОВОМ сервере (другое происхождение) и показывает новый проект;
 *   - прежний сервер погашен, а охрана навигации сверяет уже с новым
 *     происхождением.
 *
 * Диалог ОС сценарий подменяет: он живёт в том же главном процессе, что и
 * оболочка, — так в рабочем коде нет ни одной отладочной ветки. Подменяется
 * и `shell.openExternal`: уход на чужой http-адрес оболочка отдаёт системному
 * браузеру, и проба «уйти на прежний сервер» однажды открыла настоящий Chrome
 * на столе человека. Теперь адрес только записывается — и проверяется, что
 * он ушёл браузеру, а не в окно. Архив — свежий
 * пустой проект, упакованный тем же CLI (`datum init`, `datum save`).
 */
const { execFileSync } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { dialog, shell } = require('electron');

const sleep = (t) => new Promise((r) => setTimeout(r, t));
const repo = path.resolve(__dirname, '..', '..');
const python = path.join(repo, '.venv', 'bin', 'python');

module.exports = async function run({ win }) {
  const failures = [];
  const check = (ok, text) => {
    console.log(`  ${ok ? 'OK  ' : 'ПРОВАЛ'}  ${text}`);
    if (!ok) failures.push(text);
  };

  const work = fs.mkdtempSync(path.join(os.tmpdir(), 'datum-switch-'));
  try {
    const project = path.join(work, 'второй');
    const archive = path.join(work, 'второй.datum');
    execFileSync(python, ['-m', 'datum.cli', 'init', '--name', 'Второй', project], { cwd: repo });
    execFileSync(python, ['-m', 'datum.cli', 'save', project, '--dest', archive], { cwd: repo });
    fs.rmSync(project, { recursive: true, force: true });   // открываться будет из архива

    await sleep(7000);
    const wc = win.webContents;
    const id = win.id;
    const before = new URL(wc.getURL()).origin;
    const oldPort = Number(new URL(before).port);

    dialog.showOpenDialog = async () => ({ canceled: false, filePaths: [archive] });

    // Окно обязано быть видно на протяжении всей смены — опрос каждые 20 мс.
    let hidden = 0;
    let polls = 0;
    const watch = setInterval(() => {
      polls += 1;
      if (win.isDestroyed() || !win.isVisible()) hidden += 1;
    }, 20);
    // Ответа не ждём: страница, которая просила, в ходе смены заменяется, и её
    // обещание может не разрешиться никогда. Отказ при этом пришёл бы — его ловим.
    let result = { ok: true, error: '' };
    wc.executeJavaScript('window.datum.openArchive()').then(
      (r) => { if (r && !r.ok) result = r; }, () => undefined);
    await sleep(9000);
    clearInterval(watch);

    check(!win.isDestroyed() && win.id === id, 'окно то же самое — не создано заново');
    check(hidden === 0, `окно видно всё время смены (${polls} опросов, скрыто ${hidden})`);
    check(result.ok, `открытие архива: ${result.error || 'ок'}`);
    const after = new URL(wc.getURL()).origin;
    check(after !== before, `окно на новом сервере: ${before} → ${after}`);
    const title = await wc.executeJavaScript(
      `document.querySelector('.topbar, header')?.textContent ?? document.body.textContent`);
    check(/Без названия|Второй/.test(title) && !/latch/.test(title.slice(0, 400)),
          'окно показывает новый проект');

    const alive = await fetch(`${before}/healthz`).then(() => true, () => false);
    check(!alive, `прежний сервер (порт ${oldPort}) погашен`);
    // Путь проекта живость отдаёт только предъявившему токен.
    const { token } = await wc.executeJavaScript('window.datum.endpoint()');
    const fresh = await fetch(`${after}/healthz`, { headers: { 'X-Datum-Token': token } })
      .then((r) => r.json(), () => null);
    check(Boolean(fresh?.ok) && String(fresh.project).includes('второй'),
          `новый сервер отвечает: ${fresh?.project}`);

    // Охрана навигации — по НОВОМУ происхождению: уход на прежний адрес — чужой.
    const external = [];
    const openExternal = shell.openExternal;
    shell.openExternal = async (url) => { external.push(url); };
    try {
      const stayed = await wc.executeJavaScript(`(async () => {
        location.href = ${JSON.stringify(`${before}/`)};
        await new Promise((r) => setTimeout(r, 800));
        return location.origin;
      })()`);
      check(stayed === after, `уход на прежний сервер отказан (окно на ${stayed})`);
      check(external.length === 1 && external[0].startsWith(before),
            `чужой адрес отдан браузеру, а не окну: ${JSON.stringify(external)}`);
    } finally {
      shell.openExternal = openExternal;
    }

  } finally {
    fs.rmSync(work, { recursive: true, force: true });
  }
  if (failures.length) throw new Error(`не прошло: ${failures.join('; ')}`);
};
