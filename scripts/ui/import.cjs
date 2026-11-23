/**
 * Сценарий: чужой файл — на стол ОДНИМ шагом, и все его объекты — деталями.
 *
 * Прежде было три шага (диагноз, импорт, «В деталь»), а из 3MF приходила одна
 * деталь из многих. Здесь «Открыть файл…» в панели моделей получает 3MF из
 * трёх объектов (один — из двух частей), и проверяется:
 *   - в списке сцены три новые детали, и все собрались;
 *   - в проекте три файла parts/ и три модели imported/;
 *   - больше ничего нажимать не пришлось.
 *
 * Диалог ОС сценарий подменяет в главном процессе — рабочий код без
 * отладочных веток. Проект — КОПИЯ: импорт пишет в него файлы (make ui делает
 * свежий пустой проект). Файл 3MF собирает тот же помощник, что у тестов
 * (`py/tests/unit/helpers3mf.py`).
 *
 *   DATUM_OUT — снимок окна после импорта (необязательна)
 */
const { execFileSync } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { dialog } = require('electron');

const sleep = (t) => new Promise((r) => setTimeout(r, t));
const repo = path.resolve(__dirname, '..', '..');
const python = path.join(repo, '.venv', 'bin', 'python');

module.exports = async function run({ win }) {
  const project = process.argv.slice(1).map((a) => path.resolve(a))
    .find((a) => fs.existsSync(path.join(a, 'datum.toml')));
  if (!project) throw new Error('сценарию нужен проект в аргументах');
  const before = fs.readdirSync(path.join(project, 'parts')).filter((f) => f.endsWith('.py'));

  const work = fs.mkdtempSync(path.join(os.tmpdir(), 'datum-import-'));
  const failures = [];
  const check = (ok, text) => {
    console.log(`  ${ok ? 'OK  ' : 'ПРОВАЛ'}  ${text}`);
    if (!ok) failures.push(text);
  };
  try {
    const plate = path.join(work, 'стол.3mf');
    execFileSync(python, ['-c',
      'import sys; sys.path.insert(0, sys.argv[2]); '
      + 'from helpers3mf import plate_of_three; plate_of_three(sys.argv[1])',
      plate, path.join(repo, 'py', 'tests', 'unit')]);
    dialog.showOpenDialog = async () => ({ canceled: false, filePaths: [plate] });

    await sleep(7000);
    const wc = win.webContents;
    await wc.executeJavaScript(`(() => {
      document.querySelector('.rail button[title="Модели"]').click();
      return true;
    })()`);
    await sleep(500);
    await wc.executeJavaScript(`document.querySelector('.drop-target').click()`);

    // Ждём, пока окно соберёт новые детали: до 60 с.
    let names = [];
    for (let i = 0; i < 120; i += 1) {
      await sleep(500);
      names = await wc.executeJavaScript(`(async () => {
        document.querySelector('.rail button[title="Сцена"]')?.click();
        await new Promise((r) => setTimeout(r, 50));
        return [...document.querySelectorAll('.part:not(.ghost) .name')].map((n) => n.textContent);
      })()`);
      if (names.length >= before.length + 3) break;
    }
    await sleep(1500);
    check(['Кубик', 'Пирамидка', 'стол-3'].every((n) => names.includes(n)),
          `в сцене новые детали: ${names.join(', ')}`);
    const failed = await wc.executeJavaScript(
      `document.querySelectorAll('.part.failed').length`);
    check(failed === 0, `все детали собрались (с ошибкой: ${failed})`);
    const parts = fs.readdirSync(path.join(project, 'parts')).filter((f) => f.endsWith('.py'));
    check(parts.length === before.length + 3, `файлов деталей: ${before.length} → ${parts.length}`);
    const models = fs.readdirSync(path.join(project, 'imported'))
      .filter((d) => !d.startsWith('.'));
    check(['Кубик', 'Пирамидка', 'стол-3'].every((n) => models.includes(n)),
          `модели в imported/: ${models.join(', ')}`);

    if (process.env.DATUM_OUT) {
      fs.writeFileSync(process.env.DATUM_OUT, (await wc.capturePage()).toPNG());
      console.log(`снимок: ${process.env.DATUM_OUT}`);
    }
  } finally {
    fs.rmSync(work, { recursive: true, force: true });
  }
  if (failures.length) throw new Error(`не прошло: ${failures.join('; ')}`);
};
