/**
 * Закалка окна — замером, а не на слово.
 *
 * Это оставшаяся половина приёмочного теста H: первая (сервер — Host, токен,
 * CSRF, обход пути, перила скриптов) давно закрыта питоновскими тестами, а про
 * окно в плане так и стояло «ждёт фазы 3–5».
 *
 * Сценарий запускается в НАСТОЯЩЕМ окне настоящей сборки и спрашивает у него
 * самого: видно ли Node, работает ли CSP, куда пускают уходить. Ответ — код
 * возврата: 0, если все пробы дали ожидаемое.
 *
 * Почему изнутри окна, а не чтением исходников: `sandbox: true` в исходнике —
 * это намерение. Проверять надо результат, потому что отменить его может и
 * флаг запуска, и версия Electron, и preload.
 */
const sleep = (t) => new Promise((r) => setTimeout(r, t));

/** Пробы, исполняемые В ОКНЕ. Каждая возвращает строку — что там на самом деле. */
const PROBES = `(async () => {
  const out = {};
  const typeOf = (name) => {
    try { return typeof globalThis[name]; } catch (e) { return 'бросило:' + e.name; }
  };
  for (const name of ['require', 'process', 'module', 'exports', 'Buffer', 'global', '__dirname'])
    out['есть_' + name] = typeOf(name);

  // Поверхность моста. Должна быть ровно объявленной и ничем больше.
  out.мост = (() => {
    const d = globalThis.datum;
    if (!d) return 'нет';
    return Object.keys(d).sort().join(',');
  })();
  // ipcRenderer наружу не отдан: с ним окно звало бы ЛЮБОЙ канал, а не только
  // те, что отданы мостом поимённо.
  out.есть_ipcRenderer = typeOf('ipcRenderer');

  // CSP без 'unsafe-eval' обязан ронять eval. Если он проходит — заголовок не
  // доехал, и весь остальной CSP тоже фикция.
  out.eval = (() => {
    try { (0, eval)('1+1'); return 'выполнился'; }
    catch (e) { return 'отказ:' + e.name; }
  })();

  // Внедрение встроенного скрипта: script-src 'self' обязан его не выполнить.
  out.встроенный_скрипт = await (async () => {
    delete globalThis.__проникло;
    const s = document.createElement('script');
    s.textContent = 'globalThis.__проникло = true;';
    document.head.appendChild(s);
    await new Promise((r) => setTimeout(r, 50));
    const got = globalThis.__проникло === true;
    s.remove(); delete globalThis.__проникло;
    return got ? 'выполнился' : 'не выполнился';
  })();

  // connect-src перечисляет только свой сервер. Чужой адрес обязан не уйти.
  //
  // Ловить это по исключению нельзя, и это выяснилось саботажем: fetch на
  // несуществующий адрес бросает TypeError и БЕЗ всякого CSP, так что проба
  // «бросило — значит запретили» проходила бы на снятой охране. Спрашивать
  // надо у самой политики: событие securitypolicyviolation возникает ТОЛЬКО
  // от её запрета и называет нарушенную директиву.
  out.чужой_fetch = await (async () => {
    const нарушения = [];
    const слушатель = (e) => нарушения.push(e.violatedDirective + ' ' + e.blockedURI);
    document.addEventListener('securitypolicyviolation', слушатель);
    let бросило = '';
    try { await fetch('https://datum-csp-proba.invalid/'); }
    catch (e) { бросило = e.name; }
    await new Promise((r) => setTimeout(r, 50));
    document.removeEventListener('securitypolicyviolation', слушатель);
    const запрет = нарушения.find((n) => n.startsWith('connect-src'));
    if (запрет) return 'запрещён политикой';
    return бросило ? 'бросило ' + бросило + ', но политика молчала' : 'ушёл';
  })();

  // УХОД СО СТРАНИЦЫ. Начинать его обязана страница: loadURL из оболочки в
  // will-navigate не попадает и попадать не должен — так ходит только свой
  // код. Схема file: выбрана нарочно: её блокируют оба рубежа (и Chromium, и
  // наш обработчик), и ни один из них не откроет настоящий браузер.
  out.уход_со_страницы = await (async () => {
    const было = location.href;
    try { location.href = 'file:///etc/passwd'; } catch (e) { /* и так не пустили */ }
    await new Promise((r) => setTimeout(r, 400));
    return location.href === было ? 'заблокирован' : 'состоялся';
  })();

  // Второе окно не открывается никогда. Адрес не http — значит и наружу
  // ничего не откроется, проба безобидна.
  out.второе_окно = (() => {
    const w = window.open('file:///etc/passwd', '_blank');
    if (w) { try { w.close(); } catch (e) {} return 'открылось'; }
    return 'отказано';
  })();

  // Свой сервер обязан отвечать — иначе предыдущая проба ничего не значит.
  // Живость (/healthz) открыта без токена, и ответ на неё — ровно 200: прежняя
  // проба ходила на несуществующий адрес и принимала любой 4xx.
  out.свой_fetch = await (async () => {
    try { const r = await fetch('/healthz'); return 'ответ ' + r.status; }
    catch (e) { return 'отказ:' + e.name; }
  })();

  out.происхождение = location.origin;
  out.изолирован = String(globalThis.crossOriginIsolated);
  return out;
})()`;

/** Что обязано получиться. Значение — либо строка, либо проверяющая функция. */
const ОЖИДАНИЯ = {
  есть_require: 'undefined',
  есть_process: 'undefined',
  есть_module: 'undefined',
  есть_exports: 'undefined',
  есть_Buffer: 'undefined',
  есть_global: 'undefined',
  есть___dirname: 'undefined',
  есть_ipcRenderer: 'undefined',
  // openArchive и chooseArchivePath — архив .datum: путь называет человек в
  // диалоге ОС, окно ничего не читает с диска само (как pickFile).
  // projects — диалог «Файл → Проекты…»: окно получает список с метками и
  // просит по метке; путей оно не называет, оболочка сверяет метку со СВОИМ
  // списком проектов (main/projects.ts).
  мост: 'agent,chooseArchivePath,endpoint,layout,openArchive,openProject,pickFile,platform,projects',
  уход_со_страницы: 'заблокирован',
  второе_окно: 'отказано',
  eval: (v) => v.startsWith('отказ:'),
  встроенный_скрипт: 'не выполнился',
  чужой_fetch: 'запрещён политикой',
  свой_fetch: 'ответ 200',
};

module.exports = async function run({ win, app }) {
  await sleep(Number(process.env.DATUM_WAIT_MS ?? 9000));
  const провалы = [];

  // --- со стороны окна
  const факты = await win.webContents.executeJavaScript(PROBES, true);

  // --- со стороны главного процесса: настройки и заголовок
  const wp = win.webContents.getLastWebPreferences() ?? {};
  факты.contextIsolation = String(wp.contextIsolation);
  факты.nodeIntegration = String(wp.nodeIntegration);
  факты.sandbox = String(wp.sandbox);
  факты.webSecurity = String(wp.webSecurity);
  Object.assign(ОЖИДАНИЯ, {
    contextIsolation: 'true', nodeIntegration: 'false',
    sandbox: 'true', webSecurity: 'true',
  });

  // --- окно осталось там, где его открыли
  факты.окно_осталось_на = win.webContents.getURL().startsWith('http://127.0.0.1:')
    ? 'своём сервере' : win.webContents.getURL();
  ОЖИДАНИЯ.окно_осталось_на = 'своём сервере';

  const ширина = Math.max(...Object.keys(факты).map((k) => k.length));
  for (const [ключ, значение] of Object.entries(факты)) {
    const ждём = ОЖИДАНИЯ[ключ];
    const ок = ждём === undefined ? null
      : typeof ждём === 'function' ? ждём(String(значение)) : String(значение) === ждём;
    const метка = ок === null ? '   ' : ок ? ' ОК' : 'ПЛОХО';
    if (ок === false) провалы.push(`${ключ}: ${значение}` + (typeof ждём === 'string' ? ` (ждали ${ждём})` : ''));
    console.log(`  ${метка}  ${ключ.padEnd(ширина)}  ${значение}`);
  }

  if (провалы.length) {
    console.error(`\nЗАКАЛКА ПРОБИТА, ${провалы.length}:`);
    for (const p of провалы) console.error('  · ' + p);
    app.exit(1);
    return;
  }
  console.log(`\nЗакалка держит: ${Object.keys(ОЖИДАНИЯ).length} проб, все ожидаемые.`);
};
