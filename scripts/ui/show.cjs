/**
 * Агент показывает человеку в окне: `datum show` из командной строки при
 * открытом окне (вызов идёт мимо сервера и пересылается ему), подпись с
 * кнопкой «Вернуть как было», и сама кнопка.
 *
 * Проверяется по `.datum/window.json` — тому, что окно сообщает агенту: после
 * показа выделена показанная деталь, остальные спрятаны, срез включён; после
 * «Вернуть как было» — всё как до показа. Снимки: -1 (показ), -2 (возврат).
 *
 *   DATUM_SCENARIO=scripts/ui/show.cjs DATUM_OUT=/tmp/show.png npm start -- <копия latch>
 */
const fs = require('node:fs');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const sleep = (t) => new Promise((r) => setTimeout(r, t));

module.exports = async function run({ win, app }) {
  const out = process.env.DATUM_OUT || '/tmp/show.png';
  const project = process.argv.find((a) => fs.existsSync(path.join(a, 'datum.toml')));
  const root = path.resolve(__dirname, '..', '..');
  const js = (code) => win.webContents.executeJavaScript(code);
  const fail = (why) => {
    console.error('ПРОВАЛ: ' + why);
    return app.exit(1);
  };
  const seen = () => {
    const raw = JSON.parse(fs.readFileSync(path.join(project, '.datum', 'window.json'), 'utf-8'));
    return { selected: raw.selected ?? null, hidden: raw.hidden ?? [], section: raw.section ?? null };
  };
  const shot = async (suffix) => {
    const file = out.replace(/\.png$/, '') + suffix + '.png';
    fs.writeFileSync(file, (await win.webContents.capturePage()).toPNG());
    console.log(`  снимок: ${file}`);
  };
  if (!project) return fail('проект не передан');
  await sleep(Number(process.env.DATUM_WAIT_MS ?? 9000));
  await sleep(1500);                                   // окно сообщило о себе
  const before = seen();
  console.log('  до показа:', JSON.stringify(before));

  let said = '';
  try {
    said = execFileSync(path.join(root, '.venv', 'bin', 'python'),
      ['-m', 'datum.cli', 'show', project, '--parts', 'крышка', '--isolate',
       '--plane', 'xz', '--at', '20', '--note', 'вот губка защёлки'],
      { cwd: path.join(root, 'py'), encoding: 'utf-8', timeout: 60000 });
  } catch (e) {
    return fail('datum show отказал: ' + (e.stderr || e.message));
  }
  console.log('  ' + said.trim());
  await sleep(2500);                                   // событие, затишье, отчёт окна
  const notice = await js(`document.querySelector('.notice')?.textContent ?? null`);
  console.log('  подпись:', notice);
  const shown = seen();
  console.log('  после показа:', JSON.stringify(shown));
  await shot('-1');
  if (!notice || !notice.includes('вот губка')) return fail('подписи показа нет');
  if (shown.selected !== 'крышка') return fail('показанная деталь не выделена');
  if (JSON.stringify(shown.hidden) !== JSON.stringify(['корпус'])) return fail('isolate не спрятал корпус');
  if (!shown.section || shown.section.plane !== 'xz') return fail('срез не включён');

  const clicked = await js(`(() => {
    const b = [...document.querySelectorAll('.notice button')].find(x => x.textContent.includes('Вернуть как было'));
    if (b) b.click();
    return Boolean(b);
  })()`);
  if (!clicked) return fail('кнопки «Вернуть как было» нет');
  await sleep(2500);
  const back = seen();
  console.log('  после возврата:', JSON.stringify(back));
  await shot('-2');
  if (JSON.stringify(back) !== JSON.stringify(before)) return fail('вид не вернулся: ' + JSON.stringify(back));
  console.log('Показ агента в окне работает.');
};
